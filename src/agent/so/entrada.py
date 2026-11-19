"""Interface de injeção de entrada (mouse e teclado), independente de sistema operacional.

Quem valida os pedidos (agent/handlers/input_handler.py) só conhece esta interface. A
implementação real (pynput) fica em entrada_pynput.py, e nos testes entra uma falsa: assim
nenhum teste mexe no mouse ou no teclado de quem está rodando.
"""

from __future__ import annotations

from typing import Protocol

BOTOES = ("esq", "dir", "meio")

# Nomes de tecla aceitos em combinações. Além destes, vale qualquer caractere único.
TECLAS_MODIFICADORAS = ("ctrl", "alt", "shift", "cmd", "win", "super", "meta")
TECLAS_ESPECIAIS = (
    "enter", "esc", "tab", "space", "backspace", "delete",
    "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
    *(f"f{n}" for n in range(1, 13)),
)
TECLAS_NOMEADAS = TECLAS_MODIFICADORAS + TECLAS_ESPECIAIS


class EntradaIndisponivel(RuntimeError):
    """O sistema operacional não permite injetar entrada (ex.: falta permissão)."""


class InputBackend(Protocol):
    """O que o agente precisa do sistema operacional para controlar mouse e teclado."""

    def mover_absoluto(self, x: int, y: int) -> None:
        """Posiciona o cursor nas coordenadas `x`, `y` da tela."""

    def mover_relativo(self, dx: int, dy: int) -> None:
        """Desloca o cursor `dx`, `dy` pixels a partir da posição atual."""

    def posicao(self) -> tuple[int, int]:
        """Posição atual do cursor."""

    def clicar(self, botao: str, duplo: bool) -> None:
        """Clica com `botao` (um de BOTOES), uma ou duas vezes."""

    def rolar(self, dx: int, dy: int) -> None:
        """Rola `dy` linhas na vertical e `dx` na horizontal (dy > 0 rola para cima)."""

    def digitar(self, texto: str) -> None:
        """Digita `texto`, caractere por caractere."""

    def combinacao(self, teclas: list[str]) -> None:
        """Pressiona as teclas na ordem dada e as solta na ordem inversa (ex.: ctrl, c)."""
