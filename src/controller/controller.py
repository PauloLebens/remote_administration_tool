"""CLI provisória do controlador (a interface PySide6 vem na Etapa 8).

Comandos: ajuda | info | exec <cmd> | ls [caminho] | get <remoto> [local]
          | put <local> [remoto] | echo <texto> | sair
"""

import argparse
import json
import queue
import shlex
import sys
import threading
from pathlib import Path
from typing import Any

from controller.connection import EVENTO_DESCONECTADO, AuthError, Connection
from controller.transfer import TransferError, download, upload
from shared.auth import get_secret
from shared.config import ConfigError, ControllerConfig
from shared.log import setup_logging
from shared.protocol import Acao, Status

AJUDA = """\
  info                       informações do sistema do agente
  exec <comando>             executa um comando de shell no agente
  ls [caminho]               lista um diretório do agente
  get <remoto> [local]       baixa um arquivo do agente (verifica SHA-256)
  put <local> [remoto]       envia um arquivo ao agente (verifica SHA-256)
  echo <texto>               teste de ida e volta
  sair                       encerra"""


def _tamanho(n: float) -> str:
    """Formata uma quantidade de bytes de forma legível (ex.: 195.3 KB)."""
    for unidade in ("B", "KB", "MB", "GB"):
        if n < 1024 or unidade == "GB":
            return f"{n:.0f} {unidade}" if unidade == "B" else f"{n:.1f} {unidade}"
        n /= 1024
    return ""


def _resultado(resp: dict[str, Any]) -> Any:
    """Extrai o `resultado` de uma resposta. Se o agente respondeu erro, levanta
    TransferError com a mensagem dele.
    """
    if resp.get("status") != Status.OK:
        raise TransferError(resp.get("erro", "erro desconhecido"))
    return resp["resultado"]


def formatar_info(i: dict[str, Any]) -> str:
    """Texto legível do resultado do SYSINFO: host, SO, CPU, memória e discos."""
    so, cpu, mem = i["so"], i["cpu"], i["memoria"]
    linhas = [
        f"{i['hostname']}  ({i['usuario']})",
        f"SO:       {so['sistema']} {so['release']} {so['arquitetura']}",
        f"CPU:      {cpu['nucleos_logicos']} núcleos, uso {cpu['uso_percentual']}%",
        f"Memória:  {_tamanho(mem['usada'])} de {_tamanho(mem['total'])} ({mem['percentual']}%)",
        "Discos:",
    ]
    for d in i["discos"]:
        linhas.append(f"  {d['ponto']:<28} {_tamanho(d['usado']):>9} / {_tamanho(d['total']):<9} {d['percentual']}%")
    return "\n".join(linhas)


def formatar_ls(r: dict[str, Any]) -> str:
    """Texto legível do resultado do LS: permissões, tamanho, data e nome (diretórios com `/`)."""
    linhas = [r["caminho"] + ("/" if r["e_diretorio"] else "")]
    for e in r["entradas"]:
        if "erro" in e:
            linhas.append(f"  ?  {e['nome']}  ({e['erro']})")
            continue
        nome = e["nome"] + ("/" if e["tipo"] == "dir" else "")
        if e["tipo"] == "link":
            nome += f" -> {e.get('link_para', '?')}"
        linhas.append(f"  {e['permissoes']}  {_tamanho(e['tamanho']):>9}  {e['modificado'][:19]}  {nome}")
    if r["truncado"]:
        linhas.append("  … listagem cortada (diretório muito grande)")
    return "\n".join(linhas)


def executar(
    conn: Connection, linha: str, progresso=None, config: ControllerConfig | None = None
) -> str:
    """Executa uma linha de comando do operador e devolve o texto a mostrar.

    `config` define o timeout do `exec` e a pasta de destino padrão do `get`. Levanta
    TransferError/TimeoutError/ConnectionError/OSError em caso de falha; quem chama
    decide como exibir.
    """
    config = config or ControllerConfig()
    partes = linha.strip().split(None, 1)
    if not partes:
        return ""
    cmd, resto = partes[0].lower(), (partes[1] if len(partes) > 1 else "")

    if cmd == "ajuda":
        return AJUDA
    if cmd == "info":
        return formatar_info(_resultado(conn.request(Acao.SYSINFO)))
    if cmd == "echo":
        return json.dumps(_resultado(conn.request(Acao.ECHO, {"texto": resto})), ensure_ascii=False)
    if cmd == "exec":
        if not resto:
            return "uso: exec <comando>"
        r = _resultado(conn.request(Acao.EXEC, {"cmd": resto}, timeout=config.timeout_exec))
        saida = r["stdout"] + (f"[stderr]\n{r['stderr']}" if r["stderr"] else "")
        notas = [f"código de saída: {r['codigo']}", f"{r['tempo_s']}s"]
        if r["expirou"]:
            notas.append("EXPIROU (comando encerrado)")
        if r["truncado"]:
            notas.append("saída truncada")
        return f"{saida.rstrip()}\n({', '.join(notas)})"
    if cmd == "ls":
        return formatar_ls(_resultado(conn.request(Acao.LS, {"caminho": resto.strip() or "."})))
    if cmd in ("get", "put"):
        try:
            args = shlex.split(resto)
        except ValueError as exc:
            return f"argumentos inválidos: {exc}"
        if not 1 <= len(args) <= 2:
            return f"uso: {cmd} <{'remoto' if cmd == 'get' else 'local'}> [{'local' if cmd == 'get' else 'remoto'}]"
        if cmd == "get":
            if len(args) > 1:
                destino = Path(args[1])
            else:  # sem caminho local: vai para a pasta de downloads da configuração
                pasta = Path(config.pasta_downloads)
                pasta.mkdir(parents=True, exist_ok=True)
                destino = pasta / Path(args[0]).name
            r = download(conn, args[0], destino, progresso=progresso)
            return f"baixado: {r['caminho']} ({_tamanho(r['tamanho'])}, sha256 {r['sha256'][:16]}… conferido)"
        r = upload(conn, args[0], args[1] if len(args) > 1 else Path(args[0]).name, progresso=progresso)
        return f"enviado: {r['caminho']} ({_tamanho(r['tamanho'])}, sha256 {r['sha256'][:16]}… conferido)"
    return f"comando desconhecido: {cmd} (digite 'ajuda')"


def run_controller(config: ControllerConfig) -> None:
    """Laço interativo da CLI: conecta, autentica e lê comandos do operador até 'sair' ou a
    conexão cair.
    """
    log = setup_logging("controlador", config.arquivo_log)
    host, port = config.host, config.porta
    conn = Connection(host, port, secret=get_secret(), log=log, config=config)
    try:
        conn.connect()
    except AuthError as exc:
        log.error("%s", exc)
        return
    except OSError as exc:
        log.error("não foi possível conectar a %s:%d (%s) — o agente está rodando?", host, port, exc)
        return

    done = threading.Event()

    def mensagens_avulsas() -> None:  # eventos que não são resposta a um `request`
        """Thread que mostra o que chega em `incoming` (eventos sem pedido) e detecta a
        desconexão. As respostas dos comandos não passam por aqui: voltam direto pelo request.
        """
        while not done.is_set():
            try:
                msg = conn.incoming.get(timeout=0.2)
            except queue.Empty:
                continue
            if msg["acao"] == EVENTO_DESCONECTADO:
                print(f"\n(conexão encerrada: {msg['args']['motivo']} — pressione Enter para sair)")
                done.set()
            else:
                print(json.dumps(msg, indent=2, ensure_ascii=False))

    mostrou_progresso: list[bool] = []

    def progresso(feito: int, total: int) -> None:
        """Desenha a porcentagem da transferência na mesma linha (stderr)."""
        mostrou_progresso.append(True)
        pct = 100 if total == 0 else feito * 100 // total
        print(f"\r  {pct:3d}%  {_tamanho(feito)} / {_tamanho(total)}", end="", file=sys.stderr, flush=True)

    threading.Thread(target=mensagens_avulsas, daemon=True).start()

    print(f"Conectado a {host}:{port}. Digite 'ajuda' para ver os comandos.")
    try:
        while not done.is_set():
            linha = input("> ")
            if done.is_set() or linha.strip().lower() == "sair":
                break
            try:
                saida = executar(conn, linha, progresso, config)
                if mostrou_progresso:  # termina a linha da barra de progresso
                    print(file=sys.stderr)
                    mostrou_progresso.clear()
                if saida:
                    print(saida)
            except (TransferError, TimeoutError, OSError) as exc:
                print(f"\nerro: {exc}")
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        done.set()
        conn.close()


def _config_da_linha_de_comando(argv: list[str] | None = None) -> ControllerConfig:
    """Monta a configuração: padrões, depois o arquivo, depois os argumentos informados.

    Sai com mensagem clara (sem traceback) se algo estiver inválido.
    """
    ap = argparse.ArgumentParser(description="Controlador de administração remota")
    ap.add_argument("--config", help="arquivo JSON de configuração (padrão: ./config.json se existir)")
    ap.add_argument("--host", help="endereço do agente")
    ap.add_argument("--port", type=int, help="porta do agente")
    args = ap.parse_args(argv)
    try:
        return ControllerConfig.carregar(args.config).com(host=args.host, porta=args.port)
    except ConfigError as exc:
        sys.exit(f"configuração inválida: {exc}")


if __name__ == "__main__":
    run_controller(_config_da_linha_de_comando())
