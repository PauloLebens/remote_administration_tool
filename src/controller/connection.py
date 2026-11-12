"""Conexão persistente do controlador com um agente.

Duas threads por conexão, ligadas ao resto do programa por filas:
  - leitura: recv_msg em laço -> `incoming`
  - escrita: `_outgoing` -> send_msg
Quem usa (CLI hoje, interface depois) nunca bloqueia na rede: chama `send()`
e consome `incoming`. Respostas são casadas com os comandos pelo `id`.
"""

from __future__ import annotations

import logging
import queue
import socket
import threading
from typing import Any

from shared.protocol import DEFAULT_PORT, ProtocolError, make_command, make_event
from shared.receiver import recv_msg
from shared.sender import send_msg

# Evento local (não vem do agente) colocado em `incoming` quando a conexão acaba.
EVENTO_DESCONECTADO = "DESCONECTADO"


class Connection:
    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        log: logging.Logger | None = None,
    ) -> None:
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

    @property
    def connected(self) -> bool:
        return self._sock is not None and not self._closed.is_set()

    def connect(self, timeout: float = 5.0) -> None:
        """Conecta (levanta OSError se falhar) e inicia as threads."""
        self.log.info("conectando a %s:%d", self.host, self.port)
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        sock.settimeout(None)
        self._sock = sock
        self._threads = [
            threading.Thread(target=self._read_loop, name="ctl-leitura", daemon=True),
            threading.Thread(target=self._write_loop, name="ctl-escrita", daemon=True),
        ]
        for t in self._threads:
            t.start()
        self.log.info("conectado a %s:%d", self.host, self.port)

    def send(self, acao: str, args: dict[str, Any] | None = None) -> str:
        """Enfileira um comando e devolve seu id (a resposta virá com o mesmo id)."""
        if not self.connected:
            raise ConnectionError("não conectado")
        cmd = make_command(acao, args)
        self._outgoing.put(cmd)
        return cmd["id"]

    def close(self) -> None:
        if self._closed.is_set():
            self._close_socket()  # a queda pode ter vindo do agente: garante o fechamento
            return
        self._closed.set()
        self._outgoing.put(None)  # acorda a thread de escrita
        self._close_socket()
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(timeout=2)
        self._notify_disconnect("encerrada localmente")

    def _close_socket(self) -> None:
        if self._sock is None:
            return
        for op in (lambda: self._sock.shutdown(socket.SHUT_RDWR), self._sock.close):
            try:
                op()
            except OSError:
                pass

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- threads ---------------------------------------------------------

    def _read_loop(self) -> None:
        reason = "agente encerrou a conexão"
        try:
            while True:
                msg = recv_msg(self._sock)
                if msg is None:
                    break
                self.log.info("recebido %s (id=%s)", msg.get("acao"), msg.get("id"))
                self.incoming.put(msg)
        except (ProtocolError, ConnectionError, OSError) as exc:
            reason = "encerrada localmente" if self._closed.is_set() else f"erro: {exc}"
        finally:
            self._closed.set()
            self._outgoing.put(None)
            self._close_socket()
            self._notify_disconnect(reason)

    def _write_loop(self) -> None:
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
        with self._notify_lock:  # avisa uma única vez
            if self._notified:
                return
            self._notified = True
        self.log.info("desconectado: %s", motivo)
        self.incoming.put(make_event(EVENTO_DESCONECTADO, {"motivo": motivo}))
