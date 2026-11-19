"""Indicador visível de controle remoto e kill switch local (Seção 3 do enunciado).

A janela fica sempre na tela, por cima das outras, e não pode ser minimizada. Quando há um
controlador conectado e autenticado, ela mostra, em vermelho, o aviso exigido pela
especificação com o endereço dele. Fechá-la (botão, atalho ou botão de fechar da janela)
encerra a sessão e o programa: não existe forma de esconder o aviso mantendo o controle.

Os textos do aviso são fixos de propósito e não fazem parte da configuração.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from agent.ui.despachante import DespachanteThreadPrincipal

TEXTO_CONTROLADO = "Esta máquina está sob controle remoto — Trabalho de SO"
ATALHO_ENCERRAR = "Ctrl+Shift+K"  # K de "kill"; só vale com a janela em foco

_ESTILO_BASE = "QPushButton { background: white; color: #111; padding: 8px; font-size: 14px; }"
_ESTILO_OCIOSO = "QWidget { background: #37474f; color: white; }" + _ESTILO_BASE
_ESTILO_CONTROLADO = "QWidget { background: #b00020; color: white; }" + _ESTILO_BASE


def _em_duas_linhas(fingerprint: str) -> str:
    """Quebra a impressão digital ao meio, para ela caber inteira na janela.

    Importa porque o operador a CONFERE: o Qt não quebra linha nos dois-pontos, e uma
    impressão digital cortada na borda da janela levaria a confirmar só o começo dela. Sem
    espaços nem outro caractere novo: tirando as quebras de linha, o texto é o original.
    """
    partes = fingerprint.split(":")  # ["SHA256", "AB", "CD", ...]: prefixo + 32 octetos
    if len(partes) != 33:
        return fingerprint
    return ":".join(partes[:17]) + ":\n" + ":".join(partes[17:])


class JanelaIndicador(QWidget):
    """Janela do agente: mostra o estado e oferece o kill switch.

    Implementa o Observador da Session (sessao_iniciada / sessao_encerrada), que podem ser
    chamados de qualquer thread.
    """

    _sessao_encerrada = Signal()
    _pedido_encerrar = Signal()

    def __init__(self, porta: int, ao_encerrar: Callable[[], None]) -> None:
        """`porta` aparece no estado ocioso. `ao_encerrar` é chamada uma única vez quando a
        janela é fechada por qualquer caminho, e deve parar o servidor e a sessão.
        """
        super().__init__()
        self._porta = porta
        self._ao_encerrar = ao_encerrar
        self._encerrado = False
        self.despachante = DespachanteThreadPrincipal()

        self.setWindowTitle("Agente de administração remota — Trabalho de SO")
        # Sempre por cima, com barra de título e botão de fechar, sem minimizar: o aviso não
        # pode ser escondido sem encerrar o controle.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.CustomizeWindowHint
        )
        self.setMinimumWidth(560)

        self.rotulo_estado = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self.rotulo_controlador = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.rotulo_aviso = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self.rotulo_aviso.hide()
        # Endereços e impressão digital: o operador do controlador confere esta tela.
        self.rotulo_identidade = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self.rotulo_identidade.setStyleSheet("font-size: 11px; font-family: Menlo, Consolas, monospace;")
        self.rotulo_identidade.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.rotulo_identidade.hide()
        self.rotulo_atalho = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self.rotulo_atalho.setStyleSheet("font-size: 11px;")
        self.rotulo_atalho.hide()
        self.botao_encerrar = QPushButton(f"Encerrar sessão e sair  ({ATALHO_ENCERRAR})")
        self.botao_encerrar.clicked.connect(self.encerrar)
        QShortcut(QKeySequence(ATALHO_ENCERRAR), self, activated=self.encerrar)

        layout = QVBoxLayout(self)
        for w in (self.rotulo_estado, self.rotulo_controlador, self.rotulo_identidade,
                  self.rotulo_atalho, self.rotulo_aviso, self.botao_encerrar):
            layout.addWidget(w)

        self._sessao_encerrada.connect(self._mostrar_ocioso, Qt.ConnectionType.QueuedConnection)
        self._pedido_encerrar.connect(self.encerrar, Qt.ConnectionType.QueuedConnection)
        self._mostrar_ocioso()

    # --- Observador da Session (qualquer thread) -------------------------

    def sessao_iniciada(self, origem: str) -> None:
        """Mostra o aviso de controle e SÓ VOLTA quando ele já está na tela.

        Esperar é de propósito: a Session só aceita comandos depois desta chamada voltar,
        então nenhum comando roda antes de o aviso estar visível.
        """
        self.despachante.executar(lambda: self._mostrar_controlado(origem))

    def sessao_encerrada(self) -> None:
        """Volta ao estado ocioso. Não espera: no encerramento o laço de eventos pode já
        ter parado.
        """
        self._sessao_encerrada.emit()

    def fechar_de_qualquer_thread(self) -> None:
        """Pede o encerramento do programa a partir de outra thread (ex.: o servidor caiu)."""
        self._pedido_encerrar.emit()

    # --- thread principal ------------------------------------------------

    def avisar(self, texto: str) -> None:
        """Mostra uma nota fixa na janela (ex.: falta de permissão de Acessibilidade)."""
        self.rotulo_aviso.setText(f"⚠ {texto}")
        self.rotulo_aviso.show()

    def mostrar_identidade(self, enderecos: list[str], porta: int, fingerprint: str) -> None:
        """Mostra por onde conectar e a impressão digital (SHA-256) do certificado do agente.

        É a informação que o operador do controlador confere na primeira conexão (como a
        chave de host do SSH). Fica visível nos dois estados, porque é consultada antes de
        existir qualquer controlador conectado.
        """
        onde = ", ".join(f"{ip}:{porta}" for ip in enderecos) or f"porta {porta}"
        self.rotulo_identidade.setText(
            f"Conecte em: {onde}\nImpressão digital (SHA-256) deste agente:\n{_em_duas_linhas(fingerprint)}"
        )
        self.rotulo_identidade.show()

    def mostrar_atalho_global(self, descricao: str) -> None:
        """Informa o atalho de emergência que funciona mesmo com esta janela fora de foco."""
        self.rotulo_atalho.setText(f"Atalho de emergência (global): {descricao}")
        self.rotulo_atalho.show()

    def _mostrar_controlado(self, origem: str) -> None:
        """Estado 'sob controle remoto': aviso exigido pela Seção 3 e endereço do controlador."""
        self.setStyleSheet(_ESTILO_CONTROLADO)
        self.rotulo_estado.setText(TEXTO_CONTROLADO)
        self.rotulo_estado.setStyleSheet("font-size: 20px; font-weight: bold;")
        self.rotulo_controlador.setText(f"Controlador conectado: {origem}")
        self.show()
        self.raise_()

    def _mostrar_ocioso(self) -> None:
        """Estado 'aguardando': ninguém controla esta máquina."""
        self.setStyleSheet(_ESTILO_OCIOSO)
        self.rotulo_estado.setText(f"Agente aguardando conexão na porta {self._porta}")
        self.rotulo_estado.setStyleSheet("font-size: 16px;")
        self.rotulo_controlador.setText("Nenhum controlador conectado")

    def encerrar(self) -> None:
        """Kill switch local: fecha a janela, o que encerra a sessão e o programa."""
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Qualquer forma de fechar a janela passa por aqui e aciona o encerramento, uma vez."""
        if not self._encerrado:
            self._encerrado = True
            self._ao_encerrar()
        event.accept()
