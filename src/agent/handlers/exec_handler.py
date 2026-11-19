"""EXEC (RF2): roda um comando de shell no agente e devolve saída e código de saída.

Pontos de robustez:
  - timeout: o comando é morto, com seus filhos, se passar do prazo;
  - stdin fechado: um comando que pergunta algo não trava esperando teclado;
  - saída limitada: um `yes` da vida não esgota a memória do agente (o excesso
    é lido e descartado, para o processo não bloquear num pipe cheio).
"""

from __future__ import annotations

import codecs
import locale
import logging
import os
import signal
import subprocess
import threading
import time
from typing import Any

log = logging.getLogger("agente.exec")

MAX_OUTPUT = 1024 * 1024  # bytes guardados por stream (stdout e stderr, cada um)
DEFAULT_TIMEOUT = 30.0
MAX_TIMEOUT = 300.0


class _Capture(threading.Thread):
    """Lê um pipe até o fim, guardando no máximo `limit` bytes."""

    def __init__(self, stream, limit: int) -> None:
        """`stream` é o pipe a drenar; `limit`, o máximo de bytes guardados."""
        super().__init__(daemon=True)
        self.stream, self.limit = stream, limit
        self.buf = bytearray()
        self.total = 0

    def run(self) -> None:
        """Lê o pipe até o fim. Continua lendo (e descartando) depois do limite: se parasse,
        o processo travaria escrevendo num pipe cheio.
        """
        with self.stream:
            while chunk := self.stream.read1(65536):
                self.total += len(chunk)
                room = self.limit - len(self.buf)
                if room > 0:
                    self.buf += chunk[:room]

    @property
    def truncated(self) -> bool:
        """True se o processo escreveu mais do que o limite guardado."""
        return self.total > self.limit


def _decode(data: bytes) -> str:
    """UTF-8 se for UTF-8; senão a codificação local (ex.: cp850 no Windows).
    final=False tolera um caractere cortado no fim por causa do limite de saída."""
    try:
        return codecs.getincrementaldecoder("utf-8")("strict").decode(data, final=False)
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False), errors="replace")


def _kill_tree(proc: subprocess.Popen) -> None:
    """Mata o shell e os processos que ele criou."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False
        )
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # o grupo foi criado em run_command
    except (ProcessLookupError, PermissionError):
        proc.kill()


def run_command(cmd: str, timeout: float, max_output: int = MAX_OUTPUT) -> dict[str, Any]:
    """Executa `cmd` num shell, com timeout e captura limitada de stdout/stderr, e devolve o
    dicionário de resultado. Ao estourar o prazo mata a árvore inteira de processos.
    """
    popen_kw: dict[str, Any] = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}  # grupo próprio, para matar a árvore inteira
    )
    inicio = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        shell=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **popen_kw,
    )
    out, err = _Capture(proc.stdout, max_output), _Capture(proc.stderr, max_output)
    out.start()
    err.start()

    expirou = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        expirou = True
        _kill_tree(proc)
        proc.wait()
    # Um filho em segundo plano pode manter o pipe aberto; não esperamos por ele.
    out.join(2)
    err.join(2)

    return {
        "stdout": _decode(bytes(out.buf)),
        "stderr": _decode(bytes(err.buf)),
        "codigo": proc.returncode,  # negativo no POSIX = morto por esse sinal
        "expirou": expirou,
        "truncado": out.truncated or err.truncated,
        "tempo_s": round(time.monotonic() - inicio, 3),
    }


def handle_exec(msg: dict[str, Any]) -> dict[str, Any]:
    """Tratador da ação EXEC (RF2): valida `cmd` e `timeout`, executa e registra o desfecho
    no log. Argumento inválido levanta ValueError, que vira resposta de erro.
    """
    args = msg.get("args") or {}
    cmd = args.get("cmd")
    if not isinstance(cmd, str) or not cmd.strip():
        raise ValueError("'cmd' deve ser um texto não vazio")
    timeout = args.get("timeout", DEFAULT_TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("'timeout' deve ser um número positivo (segundos)")
    timeout = min(float(timeout), MAX_TIMEOUT)

    resultado = run_command(cmd, timeout)
    log.info(
        "EXEC terminou: codigo=%s expirou=%s truncado=%s tempo=%.2fs",
        resultado["codigo"], resultado["expirou"], resultado["truncado"], resultado["tempo_s"],
    )
    return resultado
