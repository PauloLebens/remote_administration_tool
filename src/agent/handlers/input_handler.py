"""MOUSE_MOVE, MOUSE_CLICK, MOUSE_SCROLL e KEY (RF6 a RF8): controle remoto de mouse e teclado.

Este módulo só valida e traduz pedidos; quem toca no sistema operacional é um InputBackend
(agent/so/entrada.py). Os argumentos vêm da rede, então tudo é conferido (tipo e faixa)
antes de chegar ao backend.

Estes tratadores só existem quando o agente tem um indicador visível (ver
agent/server.py): a entrada remota só é permitida dentro de uma sessão visível e
consentida (Seção 3 e observação do RF8).
"""

from __future__ import annotations

from typing import Any

from agent.handlers.args import booleano, escolha, inteiro
from agent.so.entrada import BOTOES, TECLAS_NOMEADAS, InputBackend
from shared.protocol import Acao

COORD_ABSOLUTA = (-32768, 32767)  # negativo existe: monitor à esquerda ou acima do principal
DESLOCAMENTO_MAX = 10_000
ROLAGEM_MAX = 1_000
MAX_TEXTO = 1_000  # digitar bloqueia a sessão; um texto enorme a prenderia por muito tempo
MAX_TECLAS_COMBO = 5


class InputHandlers:
    """Tratadores das ações de mouse e teclado, sobre um InputBackend."""

    def __init__(self, backend: InputBackend) -> None:
        """`backend` é quem realmente injeta a entrada (o real, ou um falso nos testes)."""
        self.backend = backend

    def registrar(self, handlers: dict[str, Any]) -> None:
        """Registra as quatro ações no dicionário de tratadores do servidor."""
        handlers[Acao.MOUSE_MOVE] = self.mouse_move
        handlers[Acao.MOUSE_CLICK] = self.mouse_click
        handlers[Acao.MOUSE_SCROLL] = self.mouse_scroll
        handlers[Acao.KEY] = self.key

    def mouse_move(self, msg: dict[str, Any]) -> dict[str, Any]:
        """MOUSE_MOVE (RF6): move o cursor, em coordenadas absolutas ou relativas.

        Com `abs` verdadeiro (o padrão), `x` e `y` são a posição na tela; com `abs` falso,
        são o deslocamento a partir da posição atual. Devolve a posição final, para o
        controlador poder conferir onde o cursor realmente parou.
        """
        args = msg.get("args") or {}
        absoluto = booleano(args, "abs", True)
        if absoluto:
            x = inteiro(args, "x", minimo=COORD_ABSOLUTA[0], maximo=COORD_ABSOLUTA[1])
            y = inteiro(args, "y", minimo=COORD_ABSOLUTA[0], maximo=COORD_ABSOLUTA[1])
            self.backend.mover_absoluto(x, y)
        else:
            dx = inteiro(args, "x", minimo=-DESLOCAMENTO_MAX, maximo=DESLOCAMENTO_MAX)
            dy = inteiro(args, "y", minimo=-DESLOCAMENTO_MAX, maximo=DESLOCAMENTO_MAX)
            self.backend.mover_relativo(dx, dy)
        px, py = self.backend.posicao()
        return {"x": px, "y": py, "abs": absoluto}

    def mouse_click(self, msg: dict[str, Any]) -> dict[str, Any]:
        """MOUSE_CLICK (RF7): clica na posição atual do cursor.

        `botao` é esq, dir ou meio (padrão esq); `duplo` faz um clique duplo.
        """
        args = msg.get("args") or {}
        botao = escolha(args, "botao", BOTOES, "esq")
        duplo = booleano(args, "duplo", False)
        self.backend.clicar(botao, duplo)
        return {"botao": botao, "duplo": duplo}

    def mouse_scroll(self, msg: dict[str, Any]) -> dict[str, Any]:
        """MOUSE_SCROLL (RF7): rola a roda do mouse, `dy` na vertical e `dx` na horizontal.

        Valores em linhas; `dy` positivo rola para cima. Pelo menos um dos dois precisa ser
        diferente de zero.
        """
        args = msg.get("args") or {}
        dy = inteiro(args, "dy", 0, minimo=-ROLAGEM_MAX, maximo=ROLAGEM_MAX)
        dx = inteiro(args, "dx", 0, minimo=-ROLAGEM_MAX, maximo=ROLAGEM_MAX)
        if dx == 0 and dy == 0:
            raise ValueError("informe 'dy' ou 'dx' diferente de zero")
        self.backend.rolar(dx, dy)
        return {"dx": dx, "dy": dy}

    def key(self, msg: dict[str, Any]) -> dict[str, Any]:
        """KEY (RF8): digita um texto ou pressiona uma combinação de teclas.

        Exatamente um dos dois: `texto` (digita os caracteres) ou `combo` (lista de teclas
        pressionadas juntas, ex.: ["ctrl", "c"]). Cada tecla é um nome conhecido (ctrl,
        enter, f5...) ou um caractere único.
        """
        args = msg.get("args") or {}
        if ("texto" in args) == ("combo" in args):
            raise ValueError("informe exatamente um entre 'texto' e 'combo'")
        if "texto" in args:
            texto = self._validar_texto(args["texto"])
            self.backend.digitar(texto)
            return {"digitados": len(texto)}
        teclas = self._validar_combo(args["combo"])
        self.backend.combinacao(teclas)
        return {"combo": teclas}

    @staticmethod
    def _validar_texto(texto: Any) -> str:
        """Confere o texto a digitar: não vazio, até MAX_TEXTO e só caracteres imprimíveis
        (mais quebra de linha e tab). Caracteres de controle (ESC, NUL...) são recusados.
        """
        if not isinstance(texto, str) or not texto:
            raise ValueError("'texto' deve ser um texto não vazio")
        if len(texto) > MAX_TEXTO:
            raise ValueError(f"'texto' excede o máximo de {MAX_TEXTO} caracteres")
        if not all(c.isprintable() or c in "\n\t" for c in texto):
            raise ValueError("'texto' contém caracteres de controle")
        return texto

    @staticmethod
    def _validar_combo(combo: Any) -> list[str]:
        """Confere a combinação: lista de 1 a MAX_TECLAS_COMBO teclas conhecidas.

        Normaliza para minúsculas, o que faz "Ctrl" e "ctrl" valerem o mesmo.
        """
        if not isinstance(combo, list) or not 1 <= len(combo) <= MAX_TECLAS_COMBO:
            raise ValueError(f"'combo' deve ser uma lista de 1 a {MAX_TECLAS_COMBO} teclas")
        teclas = []
        for t in combo:
            if not isinstance(t, str) or not t:
                raise ValueError(f"tecla inválida em 'combo': {t!r}")
            nome = t.lower() if len(t) > 1 else t  # caractere único mantém a caixa
            if len(nome) > 1 and nome not in TECLAS_NOMEADAS:
                raise ValueError(
                    f"tecla desconhecida: {t!r} (use um caractere único ou: {', '.join(TECLAS_NOMEADAS)})"
                )
            teclas.append(nome)
        return teclas
