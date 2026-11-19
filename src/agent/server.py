"""Servidor TCP do agente: escuta, aceita um controlador por vez."""

from __future__ import annotations

import logging
import socket
import ssl
import threading

from agent.handlers.exec_handler import ExecHandler
from agent.handlers.files_handler import FileHandlers
from agent.handlers.input_handler import InputHandlers
from agent.handlers.sysinfo_handler import handle_sysinfo
from agent.session import Handler, Observador, Session
from agent.so.entrada import InputBackend
from shared.config import AgentConfig
from shared.protocol import Acao


def default_handlers(config: AgentConfig, entrada: InputBackend | None = None) -> dict[str, Handler]:
    """Tratadores disponíveis, já com os limites da configuração. Cada etapa nova registra
    suas ações aqui.

    Mouse e teclado só são registrados se houver um `entrada` (backend de injeção).
    """
    handlers: dict[str, Handler] = {
        Acao.ECHO: lambda msg: {"eco": msg.get("args", {})},
        Acao.EXEC: ExecHandler.de_config(config).handle,
        Acao.SYSINFO: handle_sysinfo,
    }
    FileHandlers.de_config(config).registrar(handlers)
    if entrada is not None:
        InputHandlers(entrada).registrar(handlers)
    return handlers


class AgentServer:
    """Servidor TCP do agente: escuta numa porta e atende um controlador por vez (Session).

    Uso: open() abre a porta, serve_forever() atende e stop() encerra, de qualquer thread,
    derrubando também a sessão ativa. É a base do kill switch.
    """
    def __init__(
        self,
        config: AgentConfig,
        *,
        secret: str,
        contexto_tls: ssl.SSLContext,
        handlers: dict[str, Handler] | None = None,
        log: logging.Logger | None = None,
        observador: Observador | None = None,
        entrada: InputBackend | None = None,
    ) -> None:
        """`secret` é obrigatório: sem autenticação o agente não sobe, e ele não faz parte da
        configuração (nunca fica em arquivo). `contexto_tls` também é obrigatório: o canal é
        sempre cifrado, não existe modo de texto claro (ver shared/tls.py). `handlers` substitui o conjunto padrão de
        ações. Com `config.porta == 0` o SO escolhe a porta (útil em testes).

        `observador` é o indicador visível. `entrada` é o backend de mouse e teclado, e só
        vale junto com o `observador`: a entrada remota existe somente dentro de uma sessão
        visível (Seção 3). Sem indicador, MOUSE_* e KEY nem são registrados.
        """
        if not secret:  # sem autenticação o agente não sobe
            raise ValueError("o agente exige um segredo compartilhado")
        self._secret = secret
        self._contexto_tls = contexto_tls
        self.config = config
        self.auth_timeout = config.auth_timeout
        self.host = config.host
        self.port = config.porta
        self.observador = observador
        self.handlers = (
            handlers if handlers is not None
            else default_handlers(config, entrada if observador is not None else None)
        )
        self.log = log or logging.getLogger("agente")
        self._listener: socket.socket | None = None
        self._session: Session | None = None
        self._stop = threading.Event()

    def open(self) -> None:
        """Abre a porta. Separado de serve_forever para que quem chama saiba
        a porta real (útil com port=0 nos testes) antes de começar a aceitar."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(1)
        sock.settimeout(0.5)  # o accept acorda a cada 0,5 s para checar stop()
        self.port = sock.getsockname()[1]
        self._listener = sock
        self.log.info("agente escutando em %s:%d", self.host, self.port)

    def serve_forever(self) -> None:
        """Aceita controladores, um por vez, até stop() ser chamado."""
        if self._listener is None:
            self.open()
        assert self._listener is not None
        try:
            while not self._stop.is_set():
                try:
                    conn, addr = self._listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                conn = self._negociar_tls(conn, addr)
                if conn is None:
                    continue
                self._session = Session(
                    conn, addr, self.handlers, self.log, self._secret, self.auth_timeout,
                    self.observador,
                )
                try:
                    self._session.run()
                finally:
                    self._session = None
        finally:
            self._listener.close()
            self.log.info("agente parou de escutar")

    def _negociar_tls(self, conn: socket.socket, addr: tuple) -> ssl.SSLSocket | None:
        """Faz o handshake TLS com o cliente recém-aceito, dentro de um prazo.

        O prazo (o mesmo do handshake de autenticação) impede que um cliente mudo, ou que
        fale texto claro, segure o único lugar do agente. Devolve o socket cifrado, ou None
        se o handshake falhou (a conexão é fechada e o agente volta a escutar).
        """
        conn.settimeout(self.auth_timeout)
        try:
            cifrado = self._contexto_tls.wrap_socket(conn, server_side=True)
        except (ssl.SSLError, OSError) as exc:
            self.log.warning("handshake TLS falhou com %s:%s: %s", addr[0], addr[1], exc)
            conn.close()
            return None
        cifrado.settimeout(None)
        return cifrado

    def stop(self) -> None:
        """Para o servidor e derruba a sessão ativa. Seguro para chamar de
        outra thread: é o que o kill switch usará."""
        self._stop.set()
        session = self._session
        if session is not None:
            session.close()
