"""Servidor TCP do agente: escuta, aceita um controlador por vez."""

from __future__ import annotations

import logging
import socket
import threading

from agent.session import Handler, Session
from shared.protocol import DEFAULT_PORT, Acao


def default_handlers() -> dict[str, Handler]:
    """Tratadores disponíveis. Cada etapa nova registra suas ações aqui."""
    return {
        Acao.ECHO: lambda msg: {"eco": msg.get("args", {})},
    }


class AgentServer:
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        handlers: dict[str, Handler] | None = None,
        log: logging.Logger | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.handlers = handlers if handlers is not None else default_handlers()
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
                conn.settimeout(None)
                self._session = Session(conn, addr, self.handlers, self.log)
                try:
                    self._session.run()
                finally:
                    self._session = None
        finally:
            self._listener.close()
            self.log.info("agente parou de escutar")

    def stop(self) -> None:
        """Para o servidor e derruba a sessão ativa. Seguro para chamar de
        outra thread: é o que o kill switch usará."""
        self._stop.set()
        session = self._session
        if session is not None:
            session.close()
