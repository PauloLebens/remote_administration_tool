"""SYSINFO (RF3): SO, usuário, hostname, CPU, memória e discos."""

from __future__ import annotations

import getpass
import platform
import socket
import sys
import time
from typing import Any

import psutil


def _discos() -> list[dict[str, Any]]:
    discos = []
    for p in psutil.disk_partitions(all=False):
        try:
            uso = psutil.disk_usage(p.mountpoint)
        except (PermissionError, OSError):  # ex.: leitor vazio, volume sem acesso
            continue
        discos.append(
            {
                "ponto": p.mountpoint,
                "dispositivo": p.device,
                "sistema_arquivos": p.fstype,
                "total": uso.total,
                "usado": uso.used,
                "livre": uso.free,
                "percentual": uso.percent,
            }
        )
    return discos


def get_sysinfo() -> dict[str, Any]:
    mem = psutil.virtual_memory()
    return {
        "so": {
            "sistema": platform.system(),
            "release": platform.release(),
            "versao": platform.version(),
            "arquitetura": platform.machine(),
        },
        "usuario": getpass.getuser(),
        "hostname": socket.gethostname(),
        "python": sys.version.split()[0],
        "uptime_s": int(time.time() - psutil.boot_time()),
        "cpu": {
            "nucleos_logicos": psutil.cpu_count(logical=True),
            "nucleos_fisicos": psutil.cpu_count(logical=False),
            "uso_percentual": psutil.cpu_percent(interval=0.2),  # média em 0,2 s
        },
        "memoria": {
            "total": mem.total,
            "disponivel": mem.available,
            "usada": mem.used,
            "percentual": mem.percent,
        },
        "discos": _discos(),
    }


def handle_sysinfo(msg: dict[str, Any]) -> dict[str, Any]:
    return get_sysinfo()
