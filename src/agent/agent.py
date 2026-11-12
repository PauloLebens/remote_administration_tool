"""Agente (servidor TCP). Etapa 1: eco sobre o protocolo com framing.

Atende um controlador por vez. Mensagem malformada ou queda do controlador
encerram só aquela sessão; o agente volta a escutar.
"""

import socket

from shared.log import setup_logging
from shared.protocol import DEFAULT_PORT, ProtocolError, make_response, validate
from shared.receiver import recv_msg
from shared.sender import send_msg

log = setup_logging("agente", "logs/agente.log")


def handle_session(conn: socket.socket, addr: tuple) -> None:
    origem = f"{addr[0]}:{addr[1]}"
    log.info("conexão aceita de %s", origem)
    try:
        while True:
            try:
                msg = recv_msg(conn)
                if msg is None:  # controlador encerrou de forma limpa
                    break
                validate(msg)
            except ProtocolError as exc:
                log.warning("%s enviou mensagem inválida: %s", origem, exc)
                break
            log.info("%s -> %s (id=%s)", origem, msg["acao"], msg["id"])
            send_msg(conn, make_response(msg, resultado={"eco": msg.get("args", {})}))
    except (ConnectionError, OSError) as exc:
        log.warning("conexão com %s interrompida: %s", origem, exc)
    finally:
        conn.close()
        log.info("sessão com %s encerrada", origem)


def run_agent(host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(1)
        log.info("agente escutando em %s:%d", host, port)
        try:
            while True:
                conn, addr = server.accept()
                handle_session(conn, addr)  # um controlador por vez
        except KeyboardInterrupt:
            log.info("agente encerrado manualmente")


if __name__ == "__main__":
    run_agent()
