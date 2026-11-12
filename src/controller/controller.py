"""CLI provisória do controlador (a interface PySide6 vem na Etapa 8).

Cada linha digitada vira um ECHO; as respostas aparecem quando chegam.
"""

import argparse
import json
import queue
import threading

from controller.connection import EVENTO_DESCONECTADO, AuthError, Connection
from shared.auth import get_secret
from shared.log import setup_logging
from shared.protocol import DEFAULT_PORT, Acao


def run_controller(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
    log = setup_logging("controlador", "logs/controlador.log")
    conn = Connection(host, port, secret=get_secret(), log=log)
    try:
        conn.connect()
    except AuthError as exc:
        log.error("%s", exc)
        return
    except OSError as exc:
        log.error("não foi possível conectar a %s:%d (%s) — o agente está rodando?", host, port, exc)
        return

    done = threading.Event()

    def printer() -> None:
        while not done.is_set():
            try:
                msg = conn.incoming.get(timeout=0.2)
            except queue.Empty:
                continue
            print(json.dumps(msg, indent=2, ensure_ascii=False))
            if msg["acao"] == EVENTO_DESCONECTADO:
                print("(conexão encerrada — pressione Enter para sair)")
                done.set()

    threading.Thread(target=printer, daemon=True).start()

    print("Digite uma linha para enviar ECHO; 'sair' encerra.")
    try:
        while not done.is_set():
            line = input("> ")
            if line.strip() == "sair" or done.is_set():
                break
            conn.send(Acao.ECHO, {"texto": line})
    except (EOFError, KeyboardInterrupt, ConnectionError):
        pass
    finally:
        done.set()
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Controlador de administração remota")
    ap.add_argument("--host", default="127.0.0.1", help="endereço do agente")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    run_controller(args.host, args.port)
