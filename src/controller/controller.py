"""Controlador (cliente TCP). Etapa 1: envia um ECHO e imprime a resposta."""

import json
import socket

from shared.log import setup_logging
from shared.protocol import DEFAULT_PORT, Acao, make_command
from shared.receiver import recv_msg
from shared.sender import send_msg

log = setup_logging("controlador", "logs/controlador.log")


def run_controller(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
    log.info("conectando a %s:%d", host, port)
    try:
        with socket.create_connection((host, port), timeout=5) as sock:
            cmd = make_command(Acao.ECHO, {"texto": "olá do controlador"})
            send_msg(sock, cmd)
            log.info("enviado %s (id=%s)", cmd["acao"], cmd["id"])
            resp = recv_msg(sock)
            print(json.dumps(resp, indent=2, ensure_ascii=False))
    except ConnectionRefusedError:
        log.error("agente indisponível em %s:%d — ele está rodando?", host, port)


if __name__ == "__main__":
    run_controller()
