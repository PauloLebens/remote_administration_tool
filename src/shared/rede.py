"""Informações de rede da máquina local."""

from __future__ import annotations

import ipaddress
import socket

import psutil


def enderecos_locais() -> list[str]:
    """Endereços IPv4 desta máquina pelos quais outro computador poderia chegar a ela.

    Exclui o loopback (127.x) e os endereços de link local (169.254.x), que não servem para
    conectar de fora. Uma máquina costuma ter mais de um (Wi-Fi, cabo, rede virtual): o
    operador precisa saber qual usar, então o agente mostra todos.
    """
    achados: list[str] = []
    for enderecos in psutil.net_if_addrs().values():
        for e in enderecos:
            if e.family != socket.AF_INET:
                continue
            ip = ipaddress.ip_address(e.address)
            if ip.is_loopback or ip.is_link_local or e.address in achados:
                continue
            achados.append(e.address)
    return sorted(achados, key=ipaddress.ip_address)
