"""Identidade TLS usada pelos testes: gerada uma vez por execução (ECDSA P-256 é rápido).

Não é um teste. Fica em tests/ para ser importado pelos módulos de teste.
"""

import atexit
import socket
import ssl
import tempfile
from pathlib import Path

from controller.confianca import VerificadorFixo
from shared.tls import carregar_ou_gerar, contexto_cliente

_pasta = tempfile.TemporaryDirectory(prefix="rat-tls-teste-")
atexit.register(_pasta.cleanup)


def nova_identidade(nome: str) -> tuple[ssl.SSLContext, str]:
    """Gera um certificado novo e devolve (contexto do servidor, impressão digital)."""
    base = Path(_pasta.name)
    contexto, fingerprint, _ = carregar_ou_gerar(base / f"{nome}.crt", base / f"{nome}.key", ["127.0.0.1"])
    return contexto, fingerprint


CONTEXTO, FINGERPRINT = nova_identidade("principal")


def verificador() -> VerificadorFixo:
    """Verificador que aceita exatamente o agente de teste principal."""
    return VerificadorFixo(FINGERPRINT)


def socket_tls(porta: int, timeout: float = 3.0) -> ssl.SSLSocket:
    """Socket já cifrado com o agente, para testes que falam o protocolo na mão."""
    cru = socket.create_connection(("127.0.0.1", porta), timeout=timeout)
    return contexto_cliente().wrap_socket(cru, server_hostname="127.0.0.1")
