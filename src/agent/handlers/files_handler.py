"""LS, GET e PUT (RF4 e RF5): listar, baixar e enviar arquivos.

Transferência em blocos independentes
-------------------------------------
Cada bloco é um comando comum com `offset`, então o agente não guarda estado
de transferência entre mensagens e uma transferência interrompida pode ser
retomada do ponto onde parou.

  GET {caminho, offset, tamanho, sha256?}
      -> {caminho, offset, tamanho, total, dados(base64), eof, sha256?}
  PUT {caminho, offset, dados(base64), final?, sha256?, sobrescrever?}
      -> {recebido}   (bloco comum)
      -> {caminho, tamanho, sha256}   (bloco final, já verificado)

Integridade: o SHA-256 do arquivo inteiro. No PUT os blocos são gravados em
`<destino>.parte`; só depois de o hash conferir no bloco final o arquivo é
renomeado (atomicamente) para o destino. Se não conferir, a parte é apagada e
o destino nunca aparece corrompido.

Restrição opcional (`raiz`): todo caminho, depois de resolvidos `..` e links
simbólicos, precisa ficar dentro dela.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.protocol import CHUNK_SIZE, Acao

log = logging.getLogger("agente.arquivos")

MAX_ENTRADAS = 5000  # uma listagem maior é cortada (e marcada como truncada)
HASH_BLOCO = 1024 * 1024


def sha256_arquivo(caminho: Path | str) -> str:
    """SHA-256 do arquivo inteiro, lido em blocos de 1 MB para não carregá-lo na memória."""
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        while bloco := f.read(HASH_BLOCO):
            h.update(bloco)
    return h.hexdigest()


def _int(args: dict[str, Any], nome: str, padrao: int, minimo: int = 0) -> int:
    """Lê um argumento inteiro de `args`, com valor padrão e mínimo. Recusa booleano (que em
    Python também é int), texto e valores abaixo do mínimo.
    """
    valor = args.get(nome, padrao)
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < minimo:
        raise ValueError(f"'{nome}' deve ser um inteiro >= {minimo}")
    return valor


def _decodificar(dados: Any) -> bytes:
    """Decodifica base64 estrito. Entrada que não é texto ou não é base64 válido vira ValueError."""
    if not isinstance(dados, str):
        raise ValueError("'dados' deve ser texto base64")
    try:
        return base64.b64decode(dados, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"'dados' não é base64 válido: {exc}") from exc


def _iso(timestamp: float) -> str:
    """Data/hora de um arquivo em ISO-8601, UTC."""
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


class FileHandlers:
    """Tratadores de LS, GET e PUT, com restrição opcional a um diretório raiz.

    Existe como classe porque os três compartilham a raiz e a resolução de caminhos.
    A raiz limita arquivos, não execução: ela não restringe o EXEC.
    """
    def __init__(self, raiz: str | Path | None = None) -> None:
        """`raiz`, se informada, limita todos os caminhos a esse diretório."""
        self.raiz = Path(raiz).resolve() if raiz else None

    def registrar(self, handlers: dict[str, Any]) -> None:
        """Registra LS, GET e PUT no dicionário de tratadores do servidor."""
        handlers[Acao.LS] = self.ls
        handlers[Acao.GET] = self.get
        handlers[Acao.PUT] = self.put

    # --- caminhos --------------------------------------------------------

    def _resolver(self, caminho: Any) -> Path:
        """Transforma o `caminho` do pedido num Path absoluto e seguro.

        Caminho relativo vale a partir da raiz (ou do diretório atual do agente, sem raiz).
        `..` e links simbólicos são resolvidos antes da checagem; sair da raiz levanta
        PermissionError.
        """
        if not isinstance(caminho, str) or not caminho.strip():
            raise ValueError("'caminho' deve ser um texto não vazio")
        p = Path(caminho).expanduser()
        if not p.is_absolute():
            p = (self.raiz or Path.cwd()) / p
        p = p.resolve()  # colapsa '..' e segue links simbólicos
        if self.raiz is not None and not p.is_relative_to(self.raiz):
            raise PermissionError("caminho fora da raiz permitida neste agente")
        return p

    def _pai(self, p: Path) -> str | None:
        """Diretório pai, para navegação. None no topo do sistema de arquivos ou na raiz (não se
        sobe acima dela).
        """
        if p == p.parent or (self.raiz is not None and p == self.raiz):
            return None  # não se navega acima da raiz
        return str(p.parent)

    # --- LS --------------------------------------------------------------

    @staticmethod
    def _atributos(nome: str, caminho: str, entrada: os.DirEntry | None = None) -> dict[str, Any]:
        """Monta os atributos de uma entrada (tipo, tamanho, data, permissões). Se não der para
        ler (link quebrado, sem permissão), devolve a entrada com o campo `erro` em vez de
        derrubar a listagem inteira.
        """
        try:
            st = entrada.stat(follow_symlinks=False) if entrada else os.stat(caminho)
            if stat.S_ISLNK(st.st_mode):
                tipo = "link"
            elif stat.S_ISDIR(st.st_mode):
                tipo = "dir"
            elif stat.S_ISREG(st.st_mode):
                tipo = "arquivo"
            else:
                tipo = "outro"
            attrs = {
                "nome": nome,
                "tipo": tipo,
                "tamanho": st.st_size,
                "modificado": _iso(st.st_mtime),
                "permissoes": stat.filemode(st.st_mode),
            }
            if tipo == "link":
                try:
                    attrs["link_para"] = os.readlink(caminho)
                except OSError:
                    pass
            return attrs
        except OSError as exc:  # link quebrado, sem permissão...
            return {"nome": nome, "tipo": "desconhecido", "erro": str(exc)}

    def ls(self, msg: dict[str, Any]) -> dict[str, Any]:
        """LS (RF4): lista um diretório, ou mostra os atributos se o caminho for um arquivo.
        Diretórios vêm primeiro, depois ordem alfabética. Listagens muito grandes são cortadas
        e marcadas como `truncado`.
        """
        args = msg.get("args") or {}
        p = self._resolver(args.get("caminho", "."))
        if not p.exists():
            raise FileNotFoundError(f"não encontrado: {args.get('caminho', '.')}")

        if not p.is_dir():  # RF4: "ver atributos" também vale para um arquivo
            return {
                "caminho": str(p),
                "pai": self._pai(p),
                "e_diretorio": False,
                "entradas": [self._atributos(p.name, str(p))],
                "truncado": False,
            }

        entradas: list[dict[str, Any]] = []
        truncado = False
        with os.scandir(p) as it:
            for e in it:
                if len(entradas) >= MAX_ENTRADAS:
                    truncado = True
                    break
                entradas.append(self._atributos(e.name, e.path, e))
        entradas.sort(key=lambda a: (a["tipo"] != "dir", a["nome"].lower()))
        return {
            "caminho": str(p),
            "pai": self._pai(p),
            "e_diretorio": True,
            "entradas": entradas,
            "truncado": truncado,
        }

    # --- GET -------------------------------------------------------------

    def get(self, msg: dict[str, Any]) -> dict[str, Any]:
        """GET (RF5): devolve um bloco do arquivo a partir de `offset` (em base64), com `total` e
        `eof`. O SHA-256 do arquivo inteiro vem se o pedido incluir `sha256=true`. Recusa
        diretórios e arquivos que não sejam regulares.
        """
        args = msg.get("args") or {}
        p = self._resolver(args.get("caminho"))
        if p.is_dir():
            raise IsADirectoryError("é um diretório; use LS")
        if not p.exists():
            raise FileNotFoundError(f"não encontrado: {args.get('caminho')}")
        if not p.is_file():  # dispositivos e FIFOs poderiam bloquear a leitura
            raise ValueError("não é um arquivo regular")

        offset = _int(args, "offset", 0)
        tamanho = min(_int(args, "tamanho", CHUNK_SIZE, minimo=1), CHUNK_SIZE)
        total = p.stat().st_size
        if offset > total:
            raise ValueError(f"offset {offset} além do fim do arquivo ({total} bytes)")

        with open(p, "rb") as f:
            f.seek(offset)
            dados = f.read(tamanho)
        eof = offset + len(dados) >= total

        resultado: dict[str, Any] = {
            "caminho": str(p),
            "offset": offset,
            "tamanho": len(dados),
            "total": total,
            "dados": base64.b64encode(dados).decode("ascii"),
            "eof": eof,
        }
        if args.get("sha256") is True:
            resultado["sha256"] = sha256_arquivo(p)
        if offset == 0:
            log.info("GET iniciado: %s (%d bytes)", p, total)
        if eof:
            log.info("GET concluído: %s", p)
        return resultado

    # --- PUT -------------------------------------------------------------

    def put(self, msg: dict[str, Any]) -> dict[str, Any]:
        """PUT (RF5): recebe um bloco e o grava em `<destino>.parte`.

        No bloco final confere o SHA-256 e só então renomeia para o destino; se não
        conferir, apaga a parte. Não sobrescreve sem `sobrescrever=true` e só aceita o bloco
        que continua exatamente de onde a parte parou.
        """
        args = msg.get("args") or {}
        destino = self._resolver(args.get("caminho"))
        offset = _int(args, "offset", 0)
        sobrescrever = args.get("sobrescrever", False) is True
        final = args.get("final", False) is True
        dados = _decodificar(args.get("dados", ""))
        if len(dados) > CHUNK_SIZE:
            raise ValueError(f"bloco de {len(dados)} bytes excede o máximo de {CHUNK_SIZE}")

        if destino.is_dir():
            raise IsADirectoryError("o destino é um diretório")
        if not destino.parent.is_dir():
            raise FileNotFoundError("o diretório de destino não existe")
        if destino.exists() and not sobrescrever:
            raise FileExistsError(f"{destino.name} já existe (envie sobrescrever=true)")

        parte = destino.with_name(destino.name + ".parte")
        if offset == 0:
            modo = "wb"
            log.info("PUT iniciado: %s", destino)
        else:
            # Só se aceita o bloco que continua exatamente de onde a parte parou:
            # sem buracos, sem sobreposição.
            tem = parte.stat().st_size if parte.is_file() else 0
            if tem != offset:
                raise ValueError(f"offset inesperado: o agente tem {tem} bytes recebidos")
            modo = "ab"
        with open(parte, modo) as f:
            f.write(dados)
        recebido = offset + len(dados)

        if not final:
            return {"recebido": recebido}

        esperado = args.get("sha256")
        if not isinstance(esperado, str) or len(esperado) != 64:
            parte.unlink(missing_ok=True)
            raise ValueError("o bloco final exige 'sha256' (64 caracteres hexadecimais)")
        obtido = sha256_arquivo(parte)
        if not hmac.compare_digest(obtido.encode(), esperado.lower().encode()):
            parte.unlink(missing_ok=True)
            log.warning("PUT recusado (integridade): %s", destino)
            raise ValueError(f"integridade: sha256 não confere (esperado {esperado}, obtido {obtido})")
        os.replace(parte, destino)  # atômico: o destino nunca aparece pela metade
        log.info("PUT concluído: %s (%d bytes, sha256=%s)", destino, recebido, obtido)
        return {"caminho": str(destino), "tamanho": recebido, "sha256": obtido}
