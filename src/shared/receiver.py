"""Recebimento de mensagens enquadradas (ver sender.py)."""

import json
import socket
import struct
from typing import Any

from shared.protocol import HEADER_SIZE, MAX_MESSAGE_SIZE, ProtocolError


def recv_exact(sock: socket.socket, n: int) -> bytes | None:
    """Lê exatamente `n` bytes, juntando os pedaços que o TCP entregar.

    Devolve None se o par fechou a conexão antes de enviar o primeiro byte.
    Se fechar no meio dos `n` bytes, levanta ConnectionError (mensagem cortada).
    """
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            if not buf:
                return None
            raise ConnectionError(
                f"conexão encerrada no meio da mensagem ({len(buf)}/{n} bytes)"
            )
        buf.extend(chunk)
    return bytes(buf)


def recv_msg(sock: socket.socket) -> dict[str, Any] | None:
    """Lê uma mensagem completa.

    Retorna:
        dict  -- mensagem decodificada
        None  -- o par encerrou a conexão de forma limpa (entre mensagens)

    Levanta:
        ProtocolError    -- tamanho acima do limite, UTF-8 ou JSON inválido
        ConnectionError  -- conexão caiu no meio de uma mensagem
        socket.timeout / OSError -- repassados do socket
    """
    header = recv_exact(sock, HEADER_SIZE)
    if header is None:
        return None
    (size,) = struct.unpack("!I", header)

    # Checa antes de ler o corpo: um cabeçalho forjado não pode nos fazer
    # alocar ou esperar gigabytes.
    if size == 0:
        raise ProtocolError("mensagem vazia")
    if size > MAX_MESSAGE_SIZE:
        raise ProtocolError(
            f"tamanho declarado {size} excede o limite de {MAX_MESSAGE_SIZE}"
        )

    body = recv_exact(sock, size)
    if body is None:
        raise ConnectionError("conexão encerrada antes do corpo da mensagem")

    try:
        msg = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"corpo inválido: {exc}") from exc
    return msg
