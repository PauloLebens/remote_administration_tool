"""Ponto de entrada do agente. Início manual: nada roda sozinho."""

import argparse

from agent.server import AgentServer
from shared.auth import get_secret
from shared.log import setup_logging
from shared.protocol import DEFAULT_PORT


def main(host: str = "0.0.0.0", port: int = DEFAULT_PORT, raiz: str | None = None) -> None:
    """Ponto de entrada do agente. O início é sempre manual (Seção 3: nada de execução
    automática); o segredo é pedido aqui e o Ctrl+C encerra com registro no log.
    """
    log = setup_logging("agente", "logs/agente.log")
    server = AgentServer(host, port, secret=get_secret(), log=log, raiz=raiz)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("agente encerrado manualmente")
        server.stop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Agente de administração remota (Trabalho de SO)")
    ap.add_argument("--host", default="0.0.0.0", help="interface onde escutar")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--raiz", help="restringe LS/GET/PUT a este diretório. Atenção: NÃO restringe o EXEC, "
        "que roda comandos livremente; não é um sandbox")
    args = ap.parse_args()
    main(args.host, args.port, args.raiz)
