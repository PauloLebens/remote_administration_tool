"""Conexão persistente do controlador com um agente.

Duas threads por conexão, ligadas ao resto do programa por filas:
  - leitura: recv_msg em laço -> `incoming`
  - escrita: `_outgoing` -> send_msg
Duas formas de usar, sempre casando resposta e comando pelo `id`:
  - `send()` (assíncrono): enfileira e devolve o id; a resposta chega em `incoming`.
  - `request()` (síncrono): envia e espera a resposta daquele id. A resposta vai
    direto a quem esperou, sem passar por `incoming`, então uma transferência
    de arquivo não rouba mensagens do consumidor da fila (a interface).
"""

from __future__ import annotations

import logging
import queue
import socket
import ssl
import threading
from typing import Any

from controller.confianca import Verificador
from shared.auth import compute_proof
from shared.config import ControllerConfig
from shared.protocol import (
    DEFAULT_PORT, Acao, ProtocolError, Status, Tipo, make_command, make_event, validate,
)
from shared.receiver import recv_msg
from shared.sender import send_msg
from shared.tls import contexto_cliente, fingerprint_do_par

# Evento local (não vem do agente) colocado em `incoming` quando a conexão acaba.
EVENTO_DESCONECTADO = "DESCONECTADO"


class AuthError(ConnectionError):
    """O agente recusou as credenciais ou não seguiu o handshake."""


class Connection:
    """Sessão do controlador com um agente: conecta, autentica e mantém duas threads
    (leitura e escrita) ligadas por filas.

    Use `send()` para comandos assíncronos (a resposta chega em `incoming`) ou `request()`
    para esperar a resposta. Funciona como gerenciador de contexto: sair do `with` fecha
    a conexão.
    """
    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        *,
        secret: str,
        verificador: Verificador,
        log: logging.Logger | None = None,
        config: ControllerConfig | None = None,
    ) -> None:
        """`secret` é o segredo compartilhado do handshake. `verificador` decide se a identidade
        (impressão digital do certificado) do agente é aceita: é obrigatório, porque sem ele
        o TLS cifraria a conversa com um agente qualquer, inclusive um falso. `config` fornece os tempos limite
        padrão (conexão e requisição); sem ela valem os padrões de ControllerConfig. Só
        prepara o objeto: a rede só é usada em connect().
        """
        self._secret = secret
        self._verificador = verificador
        self.config = config or ControllerConfig()
        self.host = host
        self.port = port
        self.log = log or logging.getLogger("controlador")
        self.incoming: queue.Queue[dict[str, Any]] = queue.Queue()
        self._outgoing: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._sock: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._closed = threading.Event()
        self._notified = False
        self._notify_lock = threading.Lock()
        self._pending: dict[str, queue.Queue[dict[str, Any] | None]] = {}
        self._pending_lock = threading.Lock()

    @property
    def connected(self) -> bool:
        """True enquanto a conexão está aberta e nenhum dos lados a encerrou."""
        return self._sock is not None and not self._closed.is_set()

    def connect(self, timeout: float | None = None) -> None:
        """Conecta e autentica (levanta OSError ou AuthError se falhar) e inicia as threads.
        Sem `timeout`, vale `timeout_conexao` da configuração."""
        timeout = self.config.timeout_conexao if timeout is None else timeout
        self.log.info("conectando a %s:%d", self.host, self.port)
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        try:
            sock = self._negociar_tls(sock)
            self._authenticate(sock)
        except BaseException:
            sock.close()
            raise
        sock.settimeout(None)
        self._sock = sock
        self._threads = [
            threading.Thread(target=self._read_loop, name="ctl-leitura", daemon=True),
            threading.Thread(target=self._write_loop, name="ctl-escrita", daemon=True),
        ]
        for t in self._threads:
            t.start()
        self.log.info("conectado a %s:%d", self.host, self.port)

    def _negociar_tls(self, sock: socket.socket) -> ssl.SSLSocket:
        """Cifra a conexão (TLS 1.3) e confere a identidade do agente ANTES de qualquer outra
        coisa, inclusive da autenticação por segredo.

        O contexto do cliente não valida cadeia nem nome; a garantia vem de `verificar`, que
        compara a impressão digital do certificado e levanta IdentidadeRecusada se não confiar.
        """
        try:
            cifrado = contexto_cliente().wrap_socket(sock, server_hostname=self.host)
        except ssl.SSLError as exc:
            raise ConnectionError(f"falha no handshake TLS com {self.host}:{self.port}: {exc}") from exc
        try:
            fingerprint = fingerprint_do_par(cifrado)
            self._verificador.verificar(self.host, self.port, fingerprint)
        except BaseException:
            cifrado.close()
            raise
        self.log.info("canal %s; identidade do agente confirmada: %s", cifrado.version(), fingerprint)
        return cifrado

    def _authenticate(self, sock: socket.socket) -> None:
        """Handshake síncrono, antes de existirem as threads (ver shared/auth.py)."""
        try:
            desafio = recv_msg(sock)
            if (
                not desafio
                or desafio.get("tipo") != Tipo.EVENTO
                or desafio.get("acao") != Acao.DESAFIO
                or not isinstance((desafio.get("args") or {}).get("nonce"), str)
            ):
                raise AuthError("o agente não iniciou o handshake esperado")
            prova = compute_proof(self._secret, desafio["args"]["nonce"])
            send_msg(sock, make_command(Acao.AUTH, {"prova": prova}))
            resp = recv_msg(sock)
        except socket.timeout as exc:
            raise AuthError("tempo esgotado no handshake") from exc
        if resp is None or resp.get("status") != Status.OK:
            motivo = (resp or {}).get("erro", "conexão encerrada pelo agente")
            self.log.error("autenticação recusada: %s", motivo)
            raise AuthError(f"autenticação recusada: {motivo}")
        self.log.info("autenticado em %s:%d", self.host, self.port)

    def send(self, acao: str, args: dict[str, Any] | None = None) -> str:
        """Enfileira um comando e devolve seu id (a resposta virá com o mesmo id)."""
        if not self.connected:
            raise ConnectionError("não conectado")
        cmd = make_command(acao, args)
        self._outgoing.put(cmd)
        return cmd["id"]

    def request(
        self, acao: str, args: dict[str, Any] | None = None, timeout: float | None = None
    ) -> dict[str, Any]:
        """Envia um comando e espera a resposta com o mesmo id.

        Sem `timeout`, vale `timeout_requisicao` da configuração. Levanta TimeoutError se
        o agente não responder a tempo e ConnectionError se a conexão cair. Não chame da thread de leitura (ela é quem entrega).
        """
        timeout = self.config.timeout_requisicao if timeout is None else timeout
        cmd = make_command(acao, args)
        espera: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)
        with self._pending_lock:
            if not self.connected:
                raise ConnectionError("não conectado")
            self._pending[cmd["id"]] = espera
        self._outgoing.put(cmd)
        try:
            resp = espera.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"sem resposta a {acao} em {timeout:g}s") from None
        finally:
            with self._pending_lock:
                self._pending.pop(cmd["id"], None)
        if resp is None:
            raise ConnectionError("conexão encerrada antes da resposta")
        return resp

    def _fail_pending(self) -> None:
        """Acorda quem espera resposta quando a conexão acaba."""
        with self._pending_lock:
            for espera in self._pending.values():
                try:
                    espera.put_nowait(None)
                except queue.Full:
                    pass

    def close(self) -> None:
        """Encerra a conexão. Pode ser chamado mais de uma vez: acorda quem espera resposta, para
        as threads e avisa a desconexão uma única vez.
        """
        if self._closed.is_set():
            self._close_socket()  # a queda pode ter vindo do agente: garante o fechamento
            return
        self._closed.set()
        self._outgoing.put(None)  # acorda a thread de escrita
        self._close_socket()
        self._fail_pending()
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(timeout=2)
        self._notify_disconnect("encerrada localmente")

    def _close_socket(self) -> None:
        """Fecha o socket ignorando erros: ele pode já ter sido fechado pelo outro lado ou por
        outra thread.
        """
        if self._sock is None:
            return
        for op in (lambda: self._sock.shutdown(socket.SHUT_RDWR), self._sock.close):
            try:
                op()
            except OSError:
                pass

    def __enter__(self) -> "Connection":
        """Permite `with Connection(...) as conn`."""
        return self

    def __exit__(self, *exc) -> None:
        """Fecha a conexão ao sair do `with`, mesmo se houve exceção."""
        self.close()

    # --- threads ---------------------------------------------------------

    def _read_loop(self) -> None:
        """Thread de leitura: entrega cada resposta a quem espera por ela (request) e o resto à
        fila `incoming`. Ao terminar, por qualquer motivo, fecha tudo e avisa a desconexão.
        """
        reason = "agente encerrou a conexão"
        try:
            while True:
                msg = recv_msg(self._sock)
                if msg is None:  # fim limpo: foi o agente, ou fomos nós em close()?
                    if self._closed.is_set():
                        reason = "encerrada localmente"
                    break
                validate(msg)
                self.log.info("recebido %s (id=%s)", msg["acao"], msg["id"])
                if msg["tipo"] == Tipo.RESPOSTA:
                    with self._pending_lock:
                        espera = self._pending.get(msg["id"])
                    if espera is not None:  # alguém está esperando por esta resposta
                        espera.put(msg)
                        continue
                self.incoming.put(msg)
        except (ProtocolError, ConnectionError, OSError) as exc:
            reason = "encerrada localmente" if self._closed.is_set() else f"erro: {exc}"
        finally:
            self._closed.set()
            self._outgoing.put(None)
            self._close_socket()
            self._fail_pending()
            self._notify_disconnect(reason)

    def _write_loop(self) -> None:
        """Thread de escrita: envia os comandos da fila, um por vez e na ordem. Um `None` na fila
        é o sinal para parar.
        """
        while True:
            cmd = self._outgoing.get()
            if cmd is None:
                break
            try:
                send_msg(self._sock, cmd)
                self.log.info("enviado %s (id=%s)", cmd["acao"], cmd["id"])
            except (OSError, ProtocolError) as exc:
                self.log.error("falha ao enviar %s: %s", cmd["acao"], exc)
                break

    def _notify_disconnect(self, motivo: str) -> None:
        """Coloca o evento DESCONECTADO em `incoming`, uma única vez, para a interface saber que
        a conexão acabou e por quê.
        """
        with self._notify_lock:  # avisa uma única vez
            if self._notified:
                return
            self._notified = True
        self.log.info("desconectado: %s", motivo)
        self.incoming.put(make_event(EVENTO_DESCONECTADO, {"motivo": motivo}))
