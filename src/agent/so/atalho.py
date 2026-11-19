"""Atalho global do kill switch: encerra sessão e programa mesmo com a janela fora de foco.

A Seção 3 exige "um atalho na própria máquina do agente [que] encerra a sessão e o programa
a qualquer momento". Para isso é preciso observar o teclado fora da nossa janela, o que
exige cuidado, porque observar o teclado é exatamente o que um keylogger faz. Por isso:

  - o atalho só COMPARA uma combinação fixa; não traduz teclas, não guarda nem registra
    nenhuma tecla, e nada é enviado pela rede;
  - no macOS a escuta é "somente leitura" (listen-only) e feita direto pela API de eventos
    do sistema, sem tradução de teclas. O pynput não serve ali: o listener dele chama a
    API TIS em outra thread, o que pode derrubar o processo no macOS 14 ou mais novo;
  - se o sistema negar a permissão, o agente avisa e segue: a janela, o atalho com foco e o
    Ctrl+C continuam encerrando.

O que NÃO existe aqui, de propósito: callback por tecla (on_press/on_release) e qualquer log.
"""

from __future__ import annotations

import sys
import threading
from typing import Any, Callable, Protocol

DESCRICAO_ATALHO = "Ctrl+Alt+Shift+K"  # no macOS: Control+Option+Shift+K

KEYCODE_K_MACOS = 40  # posição física da tecla K (kVK_ANSI_K), sem passar por layout/tradução


class AtalhoGlobal(Protocol):
    """Observador de um atalho global que dispara uma ação única (o kill switch)."""

    def iniciar(self) -> str | None:
        """Começa a observar. Devolve None se funcionou, ou a explicação de por que não pôde."""

    def parar(self) -> None:
        """Para de observar."""


def e_o_atalho(keycode: int, flags: int, ctrl: int, alt: int, shift: int, cmd: int) -> bool:
    """True se `keycode`/`flags` são exatamente Ctrl+Alt+Shift+K (macOS).

    Exige os três modificadores e a ausência do Command; outros bits (Caps Lock, Fn, teclado
    numérico) são ignorados. Função pura, sem tocar no sistema, para poder ser testada.
    """
    exigidos = ctrl | alt | shift
    return keycode == KEYCODE_K_MACOS and (flags & (exigidos | cmd)) == exigidos


class AtalhoQuartz:
    """Atalho global no macOS, por um event tap somente leitura do Quartz."""

    def __init__(self, ao_acionar: Callable[[], None], quartz: Any = None) -> None:
        """`ao_acionar` é chamada (de outra thread) quando a combinação é pressionada.

        `quartz` existe para os testes injetarem um módulo falso; no uso real é o Quartz.
        """
        self._ao_acionar = ao_acionar
        self._q = quartz
        self._tap: Any = None
        self._loop: Any = None
        self._thread: threading.Thread | None = None
        self._pronto = threading.Event()

    def iniciar(self) -> str | None:
        """Cria o event tap e a thread que o escuta. Devolve o motivo se não for possível."""
        if self._q is None:
            try:
                import Quartz
            except ImportError as exc:
                return f"atalho global indisponível: Quartz não carregou ({exc})"
            self._q = Quartz
        q = self._q
        self._tap = q.CGEventTapCreate(
            q.kCGSessionEventTap,
            q.kCGHeadInsertEventTap,
            q.kCGEventTapOptionListenOnly,  # só observa: não bloqueia nem altera nenhuma tecla
            q.CGEventMaskBit(q.kCGEventKeyDown),
            self._callback,
            None,
        )
        if self._tap is None:
            return (
                "atalho global indisponível: o macOS negou a escuta do teclado. Conceda em Ajustes do "
                "Sistema > Privacidade e Segurança > Monitoramento de Entrada ao aplicativo que roda o agente."
            )
        self._thread = threading.Thread(target=self._rodar, name="atalho-global", daemon=True)
        self._thread.start()
        self._pronto.wait(timeout=2)
        return None

    def parar(self) -> None:
        """Interrompe a escuta e libera o event tap."""
        q = self._q
        if q is None or self._tap is None:
            return
        q.CGEventTapEnable(self._tap, False)
        if self._loop is not None:
            q.CFRunLoopStop(self._loop)
        q.CFMachPortInvalidate(self._tap)
        self._tap = None

    def _rodar(self) -> None:
        """Thread do atalho: liga o tap ao laço de execução desta thread e o executa."""
        q = self._q
        self._loop = q.CFRunLoopGetCurrent()
        fonte = q.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        q.CFRunLoopAddSource(self._loop, fonte, q.kCFRunLoopCommonModes)
        q.CGEventTapEnable(self._tap, True)
        self._pronto.set()
        q.CFRunLoopRun()

    def _callback(self, proxy: Any, tipo: int, evento: Any, refcon: Any) -> Any:
        """Chamada pelo sistema a cada tecla pressionada. Só compara com a combinação fixa.

        Não lê o caractere, não guarda o código da tecla e não registra nada: o código da
        tecla vive só nesta chamada. Devolve sempre o evento intacto.
        """
        q = self._q
        try:
            if tipo in (q.kCGEventTapDisabledByTimeout, q.kCGEventTapDisabledByUserInput):
                q.CGEventTapEnable(self._tap, True)  # o sistema desliga taps lentos; religa
            elif tipo == q.kCGEventKeyDown and e_o_atalho(
                q.CGEventGetIntegerValueField(evento, q.kCGKeyboardEventKeycode),
                q.CGEventGetFlags(evento),
                q.kCGEventFlagMaskControl,
                q.kCGEventFlagMaskAlternate,
                q.kCGEventFlagMaskShift,
                q.kCGEventFlagMaskCommand,
            ):
                self._ao_acionar()
        except Exception:  # um erro aqui nunca pode afetar a digitação de ninguém
            pass
        return evento


class AtalhoPynput:
    """Atalho global no Windows e no Linux, pelo GlobalHotKeys do pynput.

    GlobalHotKeys só dispara a ação da combinação cadastrada; não entrega as teclas ao nosso
    código. Não usado no macOS (ver a docstring do módulo).
    """

    def __init__(self, ao_acionar: Callable[[], None]) -> None:
        """`ao_acionar` é chamada (de outra thread) quando a combinação é pressionada."""
        self._ao_acionar = ao_acionar
        self._escuta: Any = None

    def iniciar(self) -> str | None:
        """Começa a escutar a combinação. Devolve o motivo se o pynput não puder rodar."""
        try:
            from pynput import keyboard

            self._escuta = keyboard.GlobalHotKeys({"<ctrl>+<alt>+<shift>+k": self._ao_acionar})
            self._escuta.start()
        except Exception as exc:  # sem pynput, sem ambiente gráfico ou sem permissão
            return f"atalho global indisponível: {exc}"
        return None

    def parar(self) -> None:
        """Para de escutar."""
        if self._escuta is not None:
            self._escuta.stop()
            self._escuta = None


def criar_atalho(ao_acionar: Callable[[], None]) -> AtalhoGlobal:
    """O atalho global adequado ao sistema operacional atual."""
    if sys.platform == "darwin":
        return AtalhoQuartz(ao_acionar)
    return AtalhoPynput(ao_acionar)
