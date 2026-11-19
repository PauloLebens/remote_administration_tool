"""Ponto de entrada do agente. Início manual: nada roda sozinho."""

import argparse
import errno
import signal
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from agent.server import AgentServer
from agent.so.atalho import DESCRICAO_ATALHO, criar_atalho
from agent.so.entrada import EntradaIndisponivel
from agent.so.entrada_pynput import PynputBackend, diagnostico
from agent.ui.despachante import EntradaNaThreadPrincipal
from agent.ui.indicador import JanelaIndicador
from shared.auth import get_secret
from shared.config import AgentConfig, ConfigError
from shared.log import setup_logging
from shared.rede import enderecos_locais
from shared.tls import TlsError, carregar_ou_gerar


def mensagem_ao_escutar(exc: OSError, config: AgentConfig) -> str:
    """Texto para o operador quando o agente não consegue abrir a porta.

    Porta ocupada é o caso comum, e vale dizer a causa provável (no macOS, o AirPlay) e a
    saída, em vez de só repetir o erro do sistema.
    """
    base = f"não foi possível escutar em {config.host}:{config.porta}: {exc}"
    if exc.errno == errno.EADDRINUSE:
        base += (
            f"\nA porta {config.porta} já está em uso: outro agente já rodando, ou outro programa "
            "(no macOS, a 5000 costuma ser o receptor AirPlay). Use --port, ou o campo "
            "'porta' do config.json, para escolher outra."
        )
    return base


def main(config: AgentConfig) -> None:
    """Ponto de entrada do agente. O início é sempre manual (Seção 3: nada de execução
    automática).

    A janela do indicador visível roda na thread principal e o servidor numa thread à parte.
    Sem ambiente gráfico o agente se recusa a rodar: controle remoto sem o aviso na tela
    violaria a Seção 3. Quatro saídas de emergência encerram sessão e programa: o botão da
    janela, o atalho com a janela em foco, o atalho global e o Ctrl+C no terminal.

    Todo o tráfego é cifrado (TLS 1.3). O certificado é gerado na primeira execução e a
    impressão digital dele aparece na janela, para o operador do controlador conferir.
    """
    log = setup_logging("agente", config.arquivo_log)
    secret = get_secret()  # pedido antes da janela: usa o terminal, sem eco

    app = QApplication(sys.argv[:1])
    if not app.screens():
        sys.exit("sem ambiente gráfico: o agente não roda sem o indicador visível (Seção 3)")

    def ao_encerrar() -> None:
        """Kill switch: derruba a sessão e fecha o programa."""
        log.warning("encerramento local acionado (kill switch)")
        server.stop()
        app.quit()

    janela = JanelaIndicador(config.porta, ao_encerrar)

    enderecos = enderecos_locais()
    try:
        contexto_tls, fingerprint, gerado = carregar_ou_gerar(config.certificado, config.chave, enderecos)
    except TlsError as exc:
        sys.exit(f"certificado do agente: {exc}")
    if gerado:
        log.warning("certificado novo gerado em %s (a identidade deste agente é nova)", config.certificado)
    log.info("conecte em: %s", ", ".join(f"{ip}:{config.porta}" for ip in enderecos) or f"porta {config.porta}")
    log.info("impressão digital do agente: %s", fingerprint)
    janela.mostrar_identidade(enderecos, config.porta, fingerprint)

    try:
        entrada = EntradaNaThreadPrincipal(PynputBackend(), janela.despachante)
    except EntradaIndisponivel as exc:
        entrada = None  # o agente sobe, mas sem mouse e teclado
        log.warning("mouse e teclado indisponíveis: %s", exc)
        janela.avisar(f"Mouse e teclado indisponíveis: {exc}")
    aviso = diagnostico()
    if aviso:
        log.warning(aviso)
        janela.avisar(aviso)

    server = AgentServer(
        config, secret=secret, contexto_tls=contexto_tls, log=log, observador=janela, entrada=entrada
    )
    try:
        server.open()  # falha aqui (porta em uso) aparece na hora, e não numa thread
    except OSError as exc:
        sys.exit(mensagem_ao_escutar(exc, config))

    def servir() -> None:
        """Roda o servidor; se ele terminar por qualquer motivo, fecha o programa."""
        try:
            server.serve_forever()
        except Exception:
            log.exception("o servidor do agente falhou")
        finally:
            janela.fechar_de_qualquer_thread()

    thread = threading.Thread(target=servir, name="agente-servidor", daemon=True)
    thread.start()

    # Ctrl+C no terminal: o Python só trata sinais quando executa código, e o laço do Qt
    # fica em C++. O temporizador devolve o controle ao Python a cada 200 ms.
    signal.signal(signal.SIGINT, lambda *_: janela.encerrar())
    pulso = QTimer()
    pulso.timeout.connect(lambda: None)
    pulso.start(200)

    atalho = criar_atalho(janela.fechar_de_qualquer_thread)  # kill switch global (Seção 3)
    motivo = atalho.iniciar()
    if motivo:
        log.warning(motivo)
        janela.avisar(motivo)
    else:
        janela.mostrar_atalho_global(DESCRICAO_ATALHO)

    janela.show()
    app.exec()
    atalho.parar()
    server.stop()
    thread.join(timeout=3)
    log.info("agente encerrado")


def _config_da_linha_de_comando(argv: list[str] | None = None) -> AgentConfig:
    """Monta a configuração: padrões, depois o arquivo, depois os argumentos informados.

    Sai com mensagem clara (sem traceback) se algo estiver inválido.
    """
    ap = argparse.ArgumentParser(description="Agente de administração remota (Trabalho de SO)")
    ap.add_argument("--config", help="arquivo JSON de configuração (padrão: ./config.json se existir)")
    ap.add_argument("--host", help="interface onde escutar")
    ap.add_argument("--port", type=int, help="porta onde escutar")
    ap.add_argument(
        "--raiz",
        help="restringe LS/GET/PUT a este diretório. Atenção: NÃO restringe o EXEC, "
        "que roda comandos livremente; não é um sandbox",
    )
    args = ap.parse_args(argv)
    try:
        config = AgentConfig.carregar(args.config).com(host=args.host, porta=args.port, raiz=args.raiz)
        if config.raiz and not Path(config.raiz).is_dir():
            raise ConfigError(f"'raiz' não é um diretório existente: {config.raiz}")
    except ConfigError as exc:
        sys.exit(f"configuração inválida: {exc}")
    return config


if __name__ == "__main__":
    main(_config_da_linha_de_comando())
