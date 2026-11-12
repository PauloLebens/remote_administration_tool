"""Ponto de entrada do agente. Início manual: nada roda sozinho."""

from agent.server import AgentServer
from shared.log import setup_logging
from shared.protocol import DEFAULT_PORT


def main(host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> None:
    log = setup_logging("agente", "logs/agente.log")
    server = AgentServer(host, port, log=log)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("agente encerrado manualmente")
        server.stop()


if __name__ == "__main__":
    main()
