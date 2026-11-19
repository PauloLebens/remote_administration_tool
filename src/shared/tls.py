"""TLS do canal de controle (RFC 8446): certificado autoassinado e contextos de cliente e servidor.

Modelo de confiança: não há autoridade certificadora. O agente gera o próprio certificado e
o controlador o reconhece pela IMPRESSÃO DIGITAL (SHA-256), que o operador confere com a que
aparece na janela do agente, como na chave de host do SSH. Por isso o cliente desliga a
verificação por cadeia/nome e a substitui por uma comparação obrigatória da impressão digital
logo depois do handshake (controller/confianca.py). Esse passo nunca pode ser omitido.

Só TLS 1.3. Não existe modo de texto claro.
"""

from __future__ import annotations

import datetime
import ipaddress
import os
import socket
import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from shared.fingerprint import formatar

VALIDADE_DIAS = 825


class TlsError(Exception):
    """Problema com o certificado ou a chave (arquivo ausente pela metade, ilegível...)."""


def _nomes_alternativos(nomes: list[str]) -> list[x509.GeneralName]:
    """Converte nomes e IPs em entradas SAN do certificado (IP vira IPAddress, o resto DNSName)."""
    saida: list[x509.GeneralName] = []
    for nome in dict.fromkeys(["localhost", "127.0.0.1", socket.gethostname(), *nomes]):
        try:
            saida.append(x509.IPAddress(ipaddress.ip_address(nome)))
        except ValueError:
            saida.append(x509.DNSName(nome))
    return saida


def gerar_certificado(crt: Path, chave: Path, nomes: list[str] | None = None) -> str:
    """Gera um par chave/certificado autoassinado (ECDSA P-256) e devolve a impressão digital.

    A chave privada é criada com permissão 0600 já na criação (sem janela em que outro
    usuário da máquina possa lê-la). `nomes` entram como nomes alternativos do certificado.
    """
    privada = ec.generate_private_key(ec.SECP256R1())
    nome = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Trabalho de SO"),
            x509.NameAttribute(NameOID.COMMON_NAME, "agente-de-administracao-remota"),
        ]
    )
    agora = datetime.datetime.now(datetime.timezone.utc)
    certificado = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(privada.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - datetime.timedelta(days=1))
        .not_valid_after(agora + datetime.timedelta(days=VALIDADE_DIAS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(_nomes_alternativos(nomes or [])), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(privada, hashes.SHA256())
    )

    crt.parent.mkdir(parents=True, exist_ok=True)
    chave.parent.mkdir(parents=True, exist_ok=True)
    pem_chave = privada.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    fd = os.open(chave, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem_chave)
    crt.write_bytes(certificado.public_bytes(serialization.Encoding.PEM))
    return formatar(certificado.public_bytes(serialization.Encoding.DER))


def fingerprint_do_arquivo(crt: Path) -> str:
    """Impressão digital de um certificado PEM em disco."""
    try:
        return formatar(ssl.PEM_cert_to_DER_cert(Path(crt).read_text()))
    except (OSError, ValueError) as exc:
        raise TlsError(f"não foi possível ler o certificado {crt}: {exc}") from exc


def carregar_ou_gerar(
    crt: str | Path, chave: str | Path, nomes: list[str] | None = None
) -> tuple[ssl.SSLContext, str, bool]:
    """Contexto TLS do SERVIDOR (agente), gerando o certificado se ainda não existir.

    Devolve (contexto, impressão digital, gerado_agora). Se só um dos dois arquivos existir,
    levanta TlsError em vez de sobrescrever: apagar uma chave ou um certificado em silêncio
    mudaria a identidade do agente sem o operador saber.
    """
    crt, chave = Path(crt), Path(chave)
    if crt.exists() != chave.exists():
        faltando = chave if crt.exists() else crt
        raise TlsError(
            f"só um dos arquivos do certificado existe; falta {faltando}. "
            "Apague o outro para gerar um par novo (a identidade do agente vai mudar)."
        )
    gerado = not crt.exists()
    if gerado:
        gerar_certificado(crt, chave, nomes)

    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.minimum_version = ssl.TLSVersion.TLSv1_3
    try:
        contexto.load_cert_chain(crt, chave)
    except (ssl.SSLError, OSError) as exc:
        raise TlsError(f"certificado ou chave inválidos ({crt}, {chave}): {exc}") from exc
    return contexto, fingerprint_do_arquivo(crt), gerado


def contexto_cliente() -> ssl.SSLContext:
    """Contexto TLS do CLIENTE (controlador): só TLS 1.3, sem verificação por cadeia ou nome.

    A verificação é a impressão digital fixada, feita por controller/confianca.py depois do
    handshake. Quem usar este contexto é OBRIGADO a conferir `fingerprint_do_par`.
    """
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    contexto.minimum_version = ssl.TLSVersion.TLSv1_3
    contexto.check_hostname = False
    contexto.verify_mode = ssl.CERT_NONE
    return contexto


def fingerprint_do_par(sock: ssl.SSLSocket) -> str:
    """Impressão digital do certificado que o outro lado apresentou no handshake."""
    der = sock.getpeercert(binary_form=True)
    if not der:
        raise TlsError("o outro lado não apresentou certificado")
    return formatar(der)
