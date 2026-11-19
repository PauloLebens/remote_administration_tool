"""Impressão digital (SHA-256) de um certificado: cálculo, formato e validação.

Fica fora de tls.py para a configuração poder validar o formato sem importar a biblioteca
de criptografia.
"""

from __future__ import annotations

import hashlib
import re

_HEX64 = re.compile(r"^[0-9A-F]{64}$")


def formatar(der: bytes) -> str:
    """Impressão digital de um certificado em DER, no formato `SHA256:AB:CD:...`.

    É o mesmo formato que o agente mostra na janela e que o operador confere.
    """
    h = hashlib.sha256(der).hexdigest().upper()
    return "SHA256:" + ":".join(h[i : i + 2] for i in range(0, 64, 2))


def normalizar(texto: str) -> str:
    """Valida uma impressão digital digitada e a devolve no formato canônico.

    Aceita com ou sem o prefixo `SHA256:`, com ou sem dois-pontos ou espaços, em maiúsculas
    ou minúsculas. Levanta ValueError se não forem exatamente 64 dígitos hexadecimais: uma
    impressão digital truncada ou com lixo nunca pode ser aceita como "parecida".
    """
    if not isinstance(texto, str):
        raise ValueError("a impressão digital deve ser um texto")
    limpo = re.sub(r"^sha-?256\s*:?", "", texto.strip(), flags=re.IGNORECASE)
    limpo = re.sub(r"[\s:]", "", limpo).upper()
    if not _HEX64.match(limpo):
        raise ValueError(
            "impressão digital inválida: esperados 64 dígitos hexadecimais (SHA-256), "
            f"recebido {len(limpo)} caracteres"
        )
    return "SHA256:" + ":".join(limpo[i : i + 2] for i in range(0, 64, 2))
