"""Injeção de entrada real, via pynput (API pública de acessibilidade de cada SO).

No Windows usa SendInput, no Linux/X11 usa XTest e no macOS usa os eventos do Quartz. É a
mesma API que as tecnologias assistivas usam: a diferença para um malware não está aqui,
e sim no consentimento e na visibilidade (Seção 3 do enunciado).

O pynput só é importado quando esta classe é criada, porque em máquinas sem ambiente
gráfico a importação falha.
"""

from __future__ import annotations

import sys

from agent.so.entrada import BOTOES, TECLAS_NOMEADAS, EntradaIndisponivel


def diagnostico() -> str | None:
    """Avisa se o sistema operacional vai ignorar a injeção de entrada, ou None se está tudo ok.

    No macOS, sem a permissão de Acessibilidade o sistema descarta os eventos em silêncio:
    o comando "funciona" e nada acontece. Melhor dizer isso na partida.
    """
    if sys.platform != "darwin":
        return None
    try:
        from ApplicationServices import AXIsProcessTrusted
    except ImportError:
        return None
    if AXIsProcessTrusted():
        return None
    return (
        "Sem permissão de Acessibilidade: o macOS vai ignorar mouse e teclado. "
        "Conceda em Ajustes do Sistema > Privacidade e Segurança > Acessibilidade "
        "ao aplicativo que roda o agente (Terminal, iTerm, VS Code...)."
    )


class PynputBackend:
    """Implementação de InputBackend sobre o pynput.

    Crie na thread principal: no macOS parte da API de teclado exige isso.
    """

    def __init__(self) -> None:
        """Importa o pynput e cria os controladores. Levanta EntradaIndisponivel se não houver
        ambiente gráfico para controlar.
        """
        try:
            from pynput import keyboard, mouse
        except ImportError as exc:  # sem DISPLAY no Linux, por exemplo
            raise EntradaIndisponivel(f"não foi possível carregar o pynput: {exc}") from exc
        self._mouse = mouse.Controller()
        self._teclado = keyboard.Controller()
        self._botoes = {
            "esq": mouse.Button.left, "dir": mouse.Button.right, "meio": mouse.Button.middle,
        }
        assert set(self._botoes) == set(BOTOES)
        # nome do protocolo -> atributo de pynput.keyboard.Key. Nem todo atributo existe em
        # todo sistema operacional (ex.: não há Key.insert no macOS), então só entra na tabela
        # o que existe aqui; o resto vira erro claro em combinacao(), e não uma queda na partida.
        atributos = {
            "ctrl": "ctrl", "alt": "alt", "shift": "shift",
            "cmd": "cmd", "win": "cmd", "super": "cmd", "meta": "cmd",
            "enter": "enter", "esc": "esc", "tab": "tab", "space": "space",
            "backspace": "backspace", "delete": "delete",
            "up": "up", "down": "down", "left": "left", "right": "right",
            "home": "home", "end": "end", "pageup": "page_up", "pagedown": "page_down",
            **{f"f{n}": f"f{n}" for n in range(1, 13)},
        }
        self._teclas: dict[str, object] = {
            nome: getattr(keyboard.Key, attr)
            for nome, attr in atributos.items()
            if hasattr(keyboard.Key, attr)
        }

    def mover_absoluto(self, x: int, y: int) -> None:
        """Posiciona o cursor em (x, y)."""
        self._mouse.position = (x, y)

    def mover_relativo(self, dx: int, dy: int) -> None:
        """Desloca o cursor (dx, dy) a partir da posição atual."""
        self._mouse.move(dx, dy)

    def posicao(self) -> tuple[int, int]:
        """Posição atual do cursor, arredondada para inteiros."""
        x, y = self._mouse.position
        return round(x), round(y)

    def clicar(self, botao: str, duplo: bool) -> None:
        """Clica uma ou duas vezes com o botão indicado."""
        self._mouse.click(self._botoes[botao], 2 if duplo else 1)

    def rolar(self, dx: int, dy: int) -> None:
        """Rola a roda do mouse."""
        self._mouse.scroll(dx, dy)

    def digitar(self, texto: str) -> None:
        """Digita o texto no foco atual."""
        self._teclado.type(texto)

    def combinacao(self, teclas: list[str]) -> None:
        """Pressiona as teclas em ordem e as solta em ordem inversa.

        O `finally` garante que nenhum modificador fique preso se algo falhar no meio.
        """
        for t in teclas:  # confere tudo ANTES de apertar qualquer coisa
            if t in TECLAS_NOMEADAS and t not in self._teclas:
                raise ValueError(f"a tecla '{t}' não existe neste sistema operacional")
        resolvidas = [self._teclas.get(t, t) for t in teclas]  # nome especial ou o próprio caractere
        apertadas = []
        try:
            for tecla in resolvidas:
                self._teclado.press(tecla)
                apertadas.append(tecla)
        finally:
            for tecla in reversed(apertadas):
                self._teclado.release(tecla)
