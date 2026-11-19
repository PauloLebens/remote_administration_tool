"""Ponto de entrada do agente. Início manual: nada roda sozinho."""

import argparse
import sys
from pathlib import Path

from agent.server import AgentServer
from shared.auth import get_secret
from shared.config import AgentConfig, ConfigError
from shared.log import setup_logging


def main(config: AgentConfig) -> None:
    """Ponto de entrada do agente. O início é sempre manual (Seção 3: nada de execução
    automática); o segredo é pedido aqui e o Ctrl+C encerra com registro no log.
    """
    log = setup_logging("agente", config.arquivo_log)
    server = AgentServer(config, secret=get_secret(), log=log)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("agente encerrado manualmente")
        server.stop()


def _config_da_linha_de_comando(argv: list[str] | None = None) -> AgentConfig:
    """Monta a configuração: padrões, depois o arquivo, depois os argumentos informados.

    Sai com mensagem clara (sem traceback) se algo estiver inválido.
    """
    ap = argparse.ArgumentParser(description="Agente de administração remota (Trabalho de SO)")
    ap.add_argument("--config", help="arquivo JSON de configuração (padrão: ./config.json se existir)")
    ap.add_argument("--host", help="interface onde escutar")
    ap.add_argument("--port", type=int, help="porta onde escutar")
    ap.add_argument(
        "--raiz",
        help="restringe LS/GET/PUT a este diretório. Atenção: NÃO restringe o EXEC, "
        "que roda comandos livremente; não é um sandbox",
    )
    args = ap.parse_args(argv)
    try:
        config = AgentConfig.carregar(args.config).com(host=args.host, porta=args.port, raiz=args.raiz)
        if config.raiz and not Path(config.raiz).is_dir():
            raise ConfigError(f"'raiz' não é um diretório existente: {config.raiz}")
    except ConfigError as exc:
        sys.exit(f"configuração inválida: {exc}")
    return config


if __name__ == "__main__":
    main(_config_da_linha_de_comando())
