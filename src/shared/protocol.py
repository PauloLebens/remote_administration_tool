"""Contrato do protocolo entre Controlador e Agente.

Formato no fio (framing): [4 bytes big-endian: tamanho N][N bytes: JSON UTF-8]

Envelope:
    {"id": uuid, "tipo": "comando|resposta|evento", "acao": "...",
     "args": {...}, "timestamp": ISO-8601 UTC}

Respostas carregam ainda "status" ("ok" | "erro") e, em caso de erro, "erro".
"""

import uuid
from datetime import datetime, timezone
from typing import Any

# Limite de uma mensagem inteira (corpo JSON). Protege o agente contra um
# cabeçalho forjado que o faria alocar memória sem fim. Arquivos e imagens
# devem ser fatiados em blocos bem menores (ver CHUNK_SIZE).
MAX_MESSAGE_SIZE = 16 * 1024 * 1024
CHUNK_SIZE = 64 * 1024
HEADER_SIZE = 4

DEFAULT_PORT = 5000


class Tipo:
    COMANDO = "comando"
    RESPOSTA = "resposta"
    EVENTO = "evento"

    TODOS = frozenset({COMANDO, RESPOSTA, EVENTO})


class Acao:
    DESAFIO = "DESAFIO"  # evento do agente com o nonce, logo após a conexão
    AUTH = "AUTH"
    ECHO = "ECHO"
    EXEC = "EXEC"
    SYSINFO = "SYSINFO"
    LS = "LS"
    GET = "GET"
    PUT = "PUT"
    MOUSE_MOVE = "MOUSE_MOVE"
    MOUSE_CLICK = "MOUSE_CLICK"
    MOUSE_SCROLL = "MOUSE_SCROLL"
    KEY = "KEY"
    SCREENSHOT = "SCREENSHOT"
    PS = "PS"
    KILL = "KILL"
    BYE = "BYE"

    TODAS = frozenset(
        {DESAFIO, AUTH, ECHO, EXEC, SYSINFO, LS, GET, PUT, MOUSE_MOVE, MOUSE_CLICK,
         MOUSE_SCROLL, KEY, SCREENSHOT, PS, KILL, BYE}
    )


class Status:
    OK = "ok"
    ERRO = "erro"


class ProtocolError(Exception):
    """Mensagem malformada, grande demais ou fora do contrato."""


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_command(acao: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Cria o envelope de um comando com id novo."""
    return {
        "id": str(uuid.uuid4()),
        "tipo": Tipo.COMANDO,
        "acao": acao,
        "args": args or {},
        "timestamp": _agora(),
    }


def make_response(
    request: dict[str, Any],
    resultado: Any = None,
    *,
    erro: str | None = None,
) -> dict[str, Any]:
    """Cria a resposta de `request`, reaproveitando seu id para correlação."""
    resp: dict[str, Any] = {
        "id": request.get("id"),
        "tipo": Tipo.RESPOSTA,
        "acao": request.get("acao"),
        "status": Status.ERRO if erro is not None else Status.OK,
        "timestamp": _agora(),
    }
    if erro is not None:
        resp["erro"] = erro
    else:
        resp["resultado"] = resultado
    return resp


def make_event(acao: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mensagem não solicitada (ex.: quadro de tela contínua)."""
    return {
        "id": str(uuid.uuid4()),
        "tipo": Tipo.EVENTO,
        "acao": acao,
        "args": args or {},
        "timestamp": _agora(),
    }


def validate(msg: Any) -> dict[str, Any]:
    """Confere a estrutura mínima do envelope; devolve a própria mensagem.

    Levanta ProtocolError se algo estiver fora do contrato. A validação de
    argumentos específicos de cada ação fica no tratador da ação.
    """
    if not isinstance(msg, dict):
        raise ProtocolError("a mensagem deve ser um objeto JSON")
    if not isinstance(msg.get("id"), str) or not msg["id"]:
        raise ProtocolError("campo 'id' ausente ou inválido")
    if msg.get("tipo") not in Tipo.TODOS:
        raise ProtocolError("campo 'tipo' ausente ou inválido")
    if not isinstance(msg.get("acao"), str) or not msg["acao"]:
        raise ProtocolError("campo 'acao' ausente ou inválido")
    if msg["tipo"] != Tipo.RESPOSTA and not isinstance(msg.get("args", {}), dict):
        raise ProtocolError("campo 'args' deve ser um objeto")
    return msg
