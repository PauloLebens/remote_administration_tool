"""Envio de mensagens com length-prefixing (4 bytes big-endian + JSON UTF-8)."""

from __future__ import annotations

import json
import socket
import struct
import threading
from typing import Any

from shared.protocol import MAX_MESSAGE_SIZE, ProtocolError


def encode_frame(msg: dict[str, Any]) -> bytes:
    """Serializa `msg` e prefixa o tamanho do corpo."""
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_MESSAGE_SIZE:
        raise ProtocolError(
            f"mensagem de {len(body)} bytes excede o limite de {MAX_MESSAGE_SIZE}"
        )
    return struct.pack("!I", len(body)) + body


def send_msg(
    sock: socket.socket,
    msg: dict[str, Any],
    lock: threading.Lock | None = None,
) -> None:
    """Envia uma mensagem inteira.

    Várias threads podem escrever no mesmo socket (respostas e quadros de
    tela, por exemplo). Passe o mesmo `lock` em todas as chamadas para que os
    quadros não se intercalem no fluxo de bytes.
    """
    frame = encode_frame(msg)
    if lock is None:
        sock.sendall(frame)
    else:
        with lock:
            sock.sendall(frame)
