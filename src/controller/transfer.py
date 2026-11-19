"""Transferência de arquivos pelo lado do controlador (protocolo em agent/handlers/files_handler.py).

Os dois sentidos gravam em `<destino>.parte` e só renomeiam para o nome final
depois de conferir o SHA-256 do arquivo inteiro: um arquivo corrompido ou
incompleto nunca aparece com o nome verdadeiro.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from pathlib import Path
from typing import Any, Callable

from controller.connection import Connection
from shared.protocol import CHUNK_SIZE, Acao, Status

Progresso = Callable[[int, int], None]  # (bytes feitos, total)


class TransferError(Exception):
    """Falha na transferência (erro do agente, resposta inesperada, rede)."""


class IntegrityError(TransferError):
    """O SHA-256 do que chegou não confere com o do arquivo original."""


def _resultado(resp: dict[str, Any], acao: str) -> dict[str, Any]:
    if resp.get("status") != Status.OK:
        raise TransferError(f"{acao}: {resp.get('erro', 'erro desconhecido')}")
    resultado = resp.get("resultado")
    if not isinstance(resultado, dict):
        raise TransferError(f"{acao}: resposta sem resultado")
    return resultado


def download(
    conn: Connection,
    remoto: str,
    local: str | Path,
    *,
    sobrescrever: bool = False,
    progresso: Progresso | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Baixa `remoto` (no agente) para `local`, verificando o SHA-256."""
    local = Path(local)
    if local.exists() and not sobrescrever:
        raise FileExistsError(f"{local} já existe")
    parte = local.with_name(local.name + ".parte")

    h = hashlib.sha256()
    offset = 0
    total = 0
    sha_remoto = ""
    try:
        with open(parte, "wb") as f:
            while True:
                args: dict[str, Any] = {"caminho": remoto, "offset": offset, "tamanho": CHUNK_SIZE}
                if offset == 0:
                    args["sha256"] = True  # o hash do arquivo todo vem junto do 1º bloco
                r = _resultado(conn.request(Acao.GET, args, timeout=timeout), "GET")
                try:
                    if r["offset"] != offset:
                        raise TransferError("GET: bloco fora de ordem")
                    if offset == 0:
                        total, sha_remoto = r["total"], r["sha256"]
                    elif r["total"] != total:
                        raise TransferError("GET: o arquivo mudou durante a transferência")
                    dados = base64.b64decode(r["dados"], validate=True)
                    eof = r["eof"] is True
                except (KeyError, TypeError, ValueError) as exc:
                    raise TransferError(f"GET: resposta inválida do agente ({exc!r})") from exc

                if not dados and not eof:
                    raise TransferError("GET: bloco vazio antes do fim do arquivo")
                f.write(dados)
                h.update(dados)
                offset += len(dados)
                if offset > total:
                    raise TransferError("GET: o agente enviou mais bytes que o tamanho declarado")
                if progresso:
                    progresso(offset, total)
                if eof:
                    break

        obtido = h.hexdigest()
        if offset != total or not hmac.compare_digest(obtido.encode(), str(sha_remoto).lower().encode()):
            raise IntegrityError(
                f"SHA-256 não confere (esperado {sha_remoto}, obtido {obtido}; "
                f"{offset}/{total} bytes)"
            )
        os.replace(parte, local)
    except BaseException:
        parte.unlink(missing_ok=True)  # nada de arquivo pela metade
        raise
    return {"caminho": str(local), "tamanho": offset, "sha256": obtido}


def upload(
    conn: Connection,
    local: str | Path,
    remoto: str,
    *,
    sobrescrever: bool = False,
    progresso: Progresso | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Envia `local` para `remoto` (no agente); o agente verifica o SHA-256."""
    local = Path(local)
    if not local.is_file():
        raise FileNotFoundError(f"{local} não é um arquivo")
    total = local.stat().st_size

    h = hashlib.sha256()
    offset = 0
    with open(local, "rb") as f:
        bloco = f.read(CHUNK_SIZE)
        while True:
            proximo = f.read(CHUNK_SIZE)  # ler adiante: só assim se sabe qual é o último bloco
            final = not proximo
            h.update(bloco)
            args: dict[str, Any] = {
                "caminho": remoto,
                "offset": offset,
                "dados": base64.b64encode(bloco).decode("ascii"),
                "final": final,
                "sobrescrever": sobrescrever,
            }
            if final:
                args["sha256"] = h.hexdigest()
            r = _resultado(conn.request(Acao.PUT, args, timeout=timeout), "PUT")

            offset += len(bloco)
            if not final and r.get("recebido") != offset:
                raise TransferError(f"PUT: o agente confirmou {r.get('recebido')} bytes, esperado {offset}")
            if progresso:
                progresso(offset, max(total, offset))
            if final:
                break
            bloco = proximo

    if r.get("sha256") != h.hexdigest():
        raise IntegrityError("PUT: o agente confirmou um SHA-256 diferente do enviado")
    return r
