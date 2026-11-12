"""Autenticação por desafio-resposta (HMAC-SHA256).

Fluxo (antes de qualquer outra ação):
    agente      -> evento DESAFIO {nonce}
    controlador -> comando AUTH   {prova = HMAC-SHA256(segredo, nonce)}
    agente      -> resposta ok, ou erro + encerra a conexão

O segredo nunca trafega, e como o nonce é novo a cada conexão, uma prova
capturada na rede não serve para autenticar outra sessão (replay).
"""

import getpass
import hashlib
import hmac
import os
import secrets

SECRET_ENV = "RAT_SECRET"
MIN_SECRET_LEN = 8


def make_nonce() -> str:
    return secrets.token_hex(16)


def compute_proof(secret: str, nonce: str) -> str:
    return hmac.new(secret.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_proof(secret: str, nonce: str, proof: object) -> bool:
    if not isinstance(proof, str):
        return False
    esperado = compute_proof(secret, nonce)
    # compare_digest: tempo constante, não vaza quantos caracteres batem
    return hmac.compare_digest(esperado.encode("utf-8"), proof.encode("utf-8"))


def check_secret(secret: str) -> str:
    if len(secret) < MIN_SECRET_LEN:
        raise ValueError(f"o segredo deve ter pelo menos {MIN_SECRET_LEN} caracteres")
    return secret


def get_secret(prompt: str = "Segredo compartilhado: ") -> str:
    """Lê o segredo da variável RAT_SECRET ou, se ausente, pergunta sem ecoar.
    Nunca por argumento de linha de comando (apareceria em `ps` e no histórico)."""
    return check_secret(os.environ.get(SECRET_ENV) or getpass.getpass(prompt))
