"""Uma sessão = um controlador conectado ao agente."""

from __future__ import annotations

import json
import logging
import socket
from typing import Any, Callable

from shared.auth import make_nonce, verify_proof
from shared.protocol import (
    Acao, ProtocolError, Tipo, make_event, make_response, validate,
)
from shared.receiver import recv_msg
from shared.sender import send_msg

# Um tratador recebe a mensagem validada e devolve o `resultado` da resposta.
# Para sinalizar erro, basta levantar uma exceção (vira resposta com status "erro").
Handler = Callable[[dict[str, Any]], Any]


def _resumo(args: Any, limite: int = 300) -> str:
    """Argumentos em uma linha, cortados para não inundar o log (ex.: blocos de arquivo)."""
    texto = json.dumps(args, ensure_ascii=False, default=repr)
    return texto if len(texto) <= limite else f"{texto[:limite]}…(+{len(texto) - limite} caracteres)"


class Session:
    """Um controlador conectado ao agente, do handshake ao encerramento.

    Roda na thread do servidor: autentica o controlador, lê comandos em laço e despacha
    cada um ao tratador da ação. As falhas ficam isoladas aqui e nunca derrubam o agente.
    """
    def __init__(
        self,
        conn: socket.socket,
        addr: tuple,
        handlers: dict[str, Handler],
        log: logging.Logger,
        secret: str,
        auth_timeout: float = 10.0,
    ) -> None:
        """`conn` é o socket já aceito e `addr` o endereço do controlador (usado em logs).
        `handlers` mapeia ação -> função; `secret` e `auth_timeout` controlam o handshake.
        """
        self.conn = conn
        self.origem = f"{addr[0]}:{addr[1]}"
        self.handlers = handlers
        self.log = log
        self._secret = secret
        self._auth_timeout = auth_timeout
        self._closing = False

    def run(self) -> None:
        """Autentica o controlador e o atende até ele sair, cair ou a sessão ser fechada."""
        self.log.info("conexão aceita de %s", self.origem)
        try:
            if not self._handshake():
                return
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

    def _handshake(self) -> bool:
        """Desafio-resposta. Nenhuma outra ação é aceita antes dele, e quem não
        responder a tempo é derrubado (senão uma conexão muda ocuparia o único
        lugar do agente)."""
        nonce = make_nonce()
        self.conn.settimeout(self._auth_timeout)
        try:
            send_msg(self.conn, make_event(Acao.DESAFIO, {"nonce": nonce}))
            msg = recv_msg(self.conn)
            if msg is None:
                self.log.warning("%s desconectou sem se autenticar", self.origem)
                return False
            validate(msg)
            if msg["tipo"] != Tipo.COMANDO or msg["acao"] != Acao.AUTH:
                self.log.warning(
                    "%s enviou %s antes de autenticar; conexão recusada", self.origem, msg["acao"]
                )
                send_msg(self.conn, make_response(msg, erro="autenticação necessária"))
                return False
            if not verify_proof(self._secret, nonce, (msg.get("args") or {}).get("prova")):
                self.log.warning("falha de autenticação de %s; conexão recusada", self.origem)
                send_msg(self.conn, make_response(msg, erro="credenciais inválidas"))
                return False
            send_msg(self.conn, make_response(msg, {"autenticado": True}))
        except socket.timeout:
            self.log.warning("%s não concluiu a autenticação a tempo", self.origem)
            return False
        self.conn.settimeout(None)
        self.log.info("%s autenticado", self.origem)
        return True

    def _dispatch(self, msg: dict[str, Any]) -> None:
        """Executa um comando e envia a resposta, registrando o comando no log (Seção 3).

        Ação desconhecida, argumento inválido ou falha esperada viram resposta de erro. Um
        bug no tratador é logado com traceback e também vira resposta de erro, sem derrubar
        a sessão.
        """
        self.log.info(
            "%s -> %s (id=%s) args=%s", self.origem, msg["acao"], msg["id"], _resumo(msg.get("args"))
        )
        if msg["tipo"] != Tipo.COMANDO:
            resp = make_response(msg, erro="o agente só aceita mensagens do tipo 'comando'")
        elif (handler := self.handlers.get(msg["acao"])) is None:
            resp = make_response(msg, erro=f"ação desconhecida: {msg['acao']}")
        else:
            try:
                resp = make_response(msg, handler(msg))
            except (ValueError, OSError) as exc:  # recusa ou falha esperada: sem traceback
                self.log.warning("%s recusado/falhou para %s: %s: %s",
                                 msg["acao"], self.origem, type(exc).__name__, exc)
                resp = make_response(msg, erro=f"{type(exc).__name__}: {exc}")
            except Exception as exc:  # um tratador com bug não derruba a sessão
                self.log.exception("falha inesperada ao executar %s", msg["acao"])
                resp = make_response(msg, erro=f"{type(exc).__name__}: {exc}")
        send_msg(self.conn, resp)
