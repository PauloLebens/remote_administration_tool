"""Leva chamadas da thread do servidor para a thread principal do Qt.

O agente atende a rede numa thread, mas a janela só pode ser tocada pela thread principal
(a que roda o laço de eventos do Qt). A entrada de teclado também precisa dela no macOS.
Em vez de cada parte cuidar disso, tudo passa por aqui: a thread do servidor entrega a
tarefa e ESPERA ela terminar, então quando a chamada volta o efeito já aconteceu.
"""

from __future__ import annotations

import concurrent.futures
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot

from agent.so.entrada import InputBackend


class DespachanteThreadPrincipal(QObject):
    """Executa funções na thread principal a pedido de outras threads.

    Precisa ser criado na thread principal, com o laço de eventos do Qt rodando.
    """

    _pedido = Signal(object)

    def __init__(self) -> None:
        """Liga o sinal interno ao executor, em modo enfileirado (cruza threads com segurança)."""
        super().__init__()
        self._pedido.connect(self._executar, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _executar(self, tarefa: tuple[Callable[[], Any], concurrent.futures.Future]) -> None:
        """Roda a tarefa na thread principal e entrega o resultado (ou a exceção) a quem espera."""
        fn, futuro = tarefa
        try:
            futuro.set_result(fn())
        except BaseException as exc:  # a exceção é de quem pediu, não da interface
            futuro.set_exception(exc)

    def executar(self, fn: Callable[[], Any], timeout: float = 10.0) -> Any:
        """Executa `fn` na thread principal e devolve o que ela devolver.

        Chamada da própria thread principal, roda direto. De outra thread, espera até
        `timeout` segundos (TimeoutError se o laço de eventos não responder) e repassa a
        exceção que `fn` tiver levantado.
        """
        if QThread.currentThread() is self.thread():
            return fn()
        futuro: concurrent.futures.Future = concurrent.futures.Future()
        self._pedido.emit((fn, futuro))
        return futuro.result(timeout)


class EntradaNaThreadPrincipal:
    """InputBackend que repassa cada chamada a outro backend, pela thread principal."""

    def __init__(self, backend: InputBackend, despachante: DespachanteThreadPrincipal) -> None:
        """`backend` faz o trabalho de verdade; `despachante` o leva para a thread principal."""
        self._backend = backend
        self._despachante = despachante

    def mover_absoluto(self, x: int, y: int) -> None:
        """Ver InputBackend.mover_absoluto."""
        self._despachante.executar(lambda: self._backend.mover_absoluto(x, y))

    def mover_relativo(self, dx: int, dy: int) -> None:
        """Ver InputBackend.mover_relativo."""
        self._despachante.executar(lambda: self._backend.mover_relativo(dx, dy))

    def posicao(self) -> tuple[int, int]:
        """Ver InputBackend.posicao."""
        return self._despachante.executar(self._backend.posicao)

    def clicar(self, botao: str, duplo: bool) -> None:
        """Ver InputBackend.clicar."""
        self._despachante.executar(lambda: self._backend.clicar(botao, duplo))

    def rolar(self, dx: int, dy: int) -> None:
        """Ver InputBackend.rolar."""
        self._despachante.executar(lambda: self._backend.rolar(dx, dy))

    def digitar(self, texto: str) -> None:
        """Ver InputBackend.digitar."""
        self._despachante.executar(lambda: self._backend.digitar(texto))

    def combinacao(self, teclas: list[str]) -> None:
        """Ver InputBackend.combinacao."""
        self._despachante.executar(lambda: self._backend.combinacao(teclas))
