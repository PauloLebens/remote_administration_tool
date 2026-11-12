"""Log em arquivo legível (requisito da Seção 3: toda conexão e comando).

Formato: 2026-11-11T21:00:00-0300 | INFO | origem | mensagem
Também escreve no console para acompanhamento durante a demo.
"""

import logging
from pathlib import Path

_FMT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"


def setup_logging(nome: str, arquivo: str | Path) -> logging.Logger:
    """Configura e devolve o logger `nome` gravando em `arquivo` (modo append)."""
    logger = logging.getLogger(nome)
    if logger.handlers:  # evita handlers duplicados se chamado duas vezes
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False

    fmt = logging.Formatter(_FMT, _DATEFMT)
    path = Path(arquivo)
    path.parent.mkdir(parents=True, exist_ok=True)
    for handler in (logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler()):
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    return logger
