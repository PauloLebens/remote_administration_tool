"""Uma sessão = um controlador conectado ao agente."""

from __future__ import annotations

import logging
import socket
from typing import Any, Callable

from shared.protocol import Tipo, ProtocolError, make_response, validate
from shared.receiver import recv_msg
from shared.sender import send_msg

# Um tratador recebe a mensagem validada e devolve o `resultado` da resposta.
# Para sinalizar erro, basta levantar uma exceção (vira resposta com status "erro").
Handler = Callable[[dict[str, Any]], Any]


class Session:
    def __init__(
        self,
        conn: socket.socket,
        addr: tuple,
        handlers: dict[str, Handler],
        log: logging.Logger,
    ) -> None:
        self.conn = conn
        self.origem = f"{addr[0]}:{addr[1]}"
        self.handlers = handlers
        self.log = log
        self._closing = False

    def run(self) -> None:
        """Atende o controlador até ele sair, cair ou a sessão ser fechada."""
        self.log.info("conexão aceita de %s", self.origem)
        try:
            while True:
                msg = recv_msg(self.conn)
                if msg is None:  # controlador encerrou de forma limpa
                    break
                validate(msg)
                self._dispatch(msg)
        except ProtocolError as exc:
            self.log.warning("%s enviou mensagem inválida: %s", self.origem, exc)
        except (ConnectionError, OSError) as exc:
            if self._closing:
                self.log.info("sessão com %s fechada localmente", self.origem)
            else:
                self.log.warning("conexão com %s interrompida: %s", self.origem, exc)
        finally:
            self.close()
            self.log.info("sessão com %s encerrada", self.origem)

    def close(self) -> None:
        """Encerra a conexão. Pode ser chamado de outra thread (kill switch):
        o shutdown acorda o recv bloqueado no laço de `run`."""
        self._closing = True
        for op in (lambda: self.conn.shutdown(socket.SHUT_RDWR), self.conn.close):
            try:
                op()
            except OSError:
                pass

    def _dispatch(self, msg: dict[str, Any]) -> None:
        self.log.info("%s -> %s (id=%s)", self.origem, msg["acao"], msg["id"])
        if msg["tipo"] != Tipo.COMANDO:
            resp = make_response(msg, erro="o agente só aceita mensagens do tipo 'comando'")
        elif (handler := self.handlers.get(msg["acao"])) is None:
            resp = make_response(msg, erro=f"ação desconhecida: {msg['acao']}")
        else:
            try:
                resp = make_response(msg, handler(msg))
            except Exception as exc:  # um tratador com bug não derruba a sessão
                self.log.exception("falha ao executar %s", msg["acao"])
                resp = make_response(msg, erro=f"{type(exc).__name__}: {exc}")
        send_msg(self.conn, resp)
