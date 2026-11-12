"""Ponto de entrada do agente. Início manual: nada roda sozinho."""

import argparse

from agent.server import AgentServer
from shared.auth import get_secret
from shared.log import setup_logging
from shared.protocol import DEFAULT_PORT


def main(host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> None:
    log = setup_logging("agente", "logs/agente.log")
    server = AgentServer(host, port, secret=get_secret(), log=log)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("agente encerrado manualmente")
        server.stop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Agente de administração remota (Trabalho de SO)")
    ap.add_argument("--host", default="0.0.0.0", help="interface onde escutar")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    main(args.host, args.port)
