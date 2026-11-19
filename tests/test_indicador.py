"""Indicador visível e kill switch (Seção 3), com o Qt em modo sem tela (offscreen).

Rodar: PYTHONPATH=src python -m unittest discover -s tests
"""

import os
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # antes de criar o QApplication

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QShortcut  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from agent.ui.despachante import DespachanteThreadPrincipal, EntradaNaThreadPrincipal  # noqa: E402
from agent.ui.indicador import ATALHO_ENCERRAR, TEXTO_CONTROLADO, JanelaIndicador  # noqa: E402

app = QApplication.instance() or QApplication([])


def bombear(ate=lambda: False, timeout=3.0):
    """Processa os eventos do Qt (a thread principal faz o papel do laço de eventos)."""
    fim = time.monotonic() + timeout
    while time.monotonic() < fim:
        app.processEvents()
        if ate():
            return True
        time.sleep(0.005)
    return ate()


def em_thread(fn):
    """Roda `fn` numa thread e devolve (thread, resultado) com o retorno ou a exceção."""
    saida = {}

    def alvo():
        try:
            saida["valor"] = fn()
        except BaseException as exc:
            saida["erro"] = exc

    t = threading.Thread(target=alvo, daemon=True)
    t.start()
    return t, saida


class JanelaCase(unittest.TestCase):
    def setUp(self):
        self.encerrou = []
        self.janela = JanelaIndicador(5055, lambda: self.encerrou.append(1))
        self.addCleanup(self.janela.deleteLater)

    def texto(self):
        return self.janela.rotulo_estado.text()


class TextosTests(JanelaCase):
    def test_texto_do_aviso_e_exatamente_o_da_especificacao(self):
        # Trava o requisito da Seção 3: se alguém mexer no texto, este teste acusa.
        self.assertEqual(TEXTO_CONTROLADO, "Esta máquina está sob controle remoto — Trabalho de SO")

    def test_estado_inicial_e_ocioso_e_nao_afirma_que_ha_controle(self):
        self.assertIn("aguardando conexão na porta 5055", self.texto())
        self.assertNotIn("sob controle remoto", self.texto())
        self.assertIn("Nenhum controlador", self.janela.rotulo_controlador.text())

    def test_sessao_iniciada_mostra_o_aviso_e_o_endereco_do_controlador(self):
        self.janela.sessao_iniciada("10.148.165.161:60178")  # da thread principal: direto
        self.assertEqual(self.texto(), TEXTO_CONTROLADO)
        self.assertIn("10.148.165.161:60178", self.janela.rotulo_controlador.text())

    def test_sessao_encerrada_volta_ao_estado_ocioso(self):
        self.janela.sessao_iniciada("1.2.3.4:5")
        self.janela.sessao_encerrada()
        bombear(lambda: "aguardando" in self.texto())
        self.assertIn("aguardando conexão", self.texto())
        self.assertIn("Nenhum controlador", self.janela.rotulo_controlador.text())

    def test_aviso_fixo_aparece_quando_informado(self):
        self.assertTrue(self.janela.rotulo_aviso.isHidden())
        self.janela.avisar("sem permissão de Acessibilidade")
        self.assertFalse(self.janela.rotulo_aviso.isHidden())
        self.assertIn("Acessibilidade", self.janela.rotulo_aviso.text())


class EntreThreadsTests(JanelaCase):
    def test_sessao_iniciada_de_outra_thread_so_volta_com_o_aviso_na_tela(self):
        t, saida = em_thread(lambda: self.janela.sessao_iniciada("9.9.9.9:1"))
        time.sleep(0.3)  # sem processar eventos da interface...
        self.assertTrue(t.is_alive(), "a Session foi liberada antes do aviso aparecer")
        self.assertNotEqual(self.texto(), TEXTO_CONTROLADO)
        bombear(lambda: not t.is_alive())  # ...agora a interface processa o pedido
        self.assertFalse(t.is_alive())
        self.assertNotIn("erro", saida)
        self.assertEqual(self.texto(), TEXTO_CONTROLADO)  # quando voltou, o aviso já estava lá

    def test_encerramento_pedido_de_outra_thread_aciona_o_kill_switch(self):
        t, _ = em_thread(self.janela.fechar_de_qualquer_thread)
        bombear(lambda: self.encerrou)
        t.join(2)
        self.assertEqual(self.encerrou, [1])


class JanelaNaoPodeSerEscondidaTests(JanelaCase):
    # Dicas que tornam uma janela invisível, sem foco, atravessável pelo mouse ou escondida.
    DICAS_PROIBIDAS = ("FramelessWindowHint", "WindowDoesNotAcceptFocus", "WindowTransparentForInput",
                       "BypassWindowManagerHint", "WindowStaysOnBottomHint")

    def test_fica_por_cima_e_nao_tem_botao_de_minimizar(self):
        flags = self.janela.windowFlags()
        self.assertTrue(flags & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(flags & Qt.WindowType.WindowTitleHint)
        self.assertFalse(flags & Qt.WindowType.WindowMinimizeButtonHint)

    def test_e_uma_janela_comum_e_nao_uma_ferramenta_ou_popup(self):
        # Tool, Popup, SplashScreen e ToolTip são tipos de janela (ocupam os mesmos bits de
        # Window): o tipo se compara pela máscara, não com um & simples.
        tipo = self.janela.windowFlags() & Qt.WindowType.WindowType_Mask
        self.assertEqual(tipo, Qt.WindowType.Window)

    def test_nenhuma_dica_que_esconda_a_janela(self):
        flags = self.janela.windowFlags()
        for nome in self.DICAS_PROIBIDAS:
            self.assertFalse(flags & getattr(Qt.WindowType, nome), nome)

    def test_aparece_na_tela_quando_mostrada(self):
        self.janela.show()
        bombear()
        self.assertTrue(self.janela.isVisible())
        self.assertFalse(self.janela.isMinimized())
        self.assertTrue(self.janela.size().width() > 100 and self.janela.size().height() > 50)


class KillSwitchTests(JanelaCase):
    def test_fechar_a_janela_encerra_uma_unica_vez(self):
        self.janela.close()
        self.janela.close()
        self.janela.encerrar()
        self.assertEqual(self.encerrou, [1])

    def test_botao_encerrar(self):
        self.janela.botao_encerrar.click()
        self.assertEqual(self.encerrou, [1])

    def test_atalho_encerrar(self):
        atalhos = self.janela.findChildren(QShortcut)
        self.assertEqual([a.key().toString() for a in atalhos], [ATALHO_ENCERRAR])
        atalhos[0].activated.emit()
        self.assertEqual(self.encerrou, [1])

    def test_botao_mostra_o_atalho(self):
        self.assertIn(ATALHO_ENCERRAR, self.janela.botao_encerrar.text())


class DespachanteTests(unittest.TestCase):
    def setUp(self):
        self.d = DespachanteThreadPrincipal()
        self.addCleanup(self.d.deleteLater)

    def test_da_thread_principal_roda_direto(self):
        self.assertEqual(self.d.executar(lambda: 42), 42)

    def test_de_outra_thread_roda_na_thread_principal_e_devolve_o_resultado(self):
        onde = []
        t, saida = em_thread(lambda: self.d.executar(lambda: onde.append(threading.get_ident()) or "ok"))
        bombear(lambda: not t.is_alive())
        self.assertEqual(saida.get("valor"), "ok")
        self.assertEqual(onde, [threading.get_ident()])  # rodou na principal, não na worker

    def test_excecao_da_funcao_chega_a_quem_pediu(self):
        t, saida = em_thread(lambda: self.d.executar(lambda: 1 / 0))
        bombear(lambda: not t.is_alive())
        self.assertIsInstance(saida.get("erro"), ZeroDivisionError)

    def test_sem_laco_de_eventos_estoura_o_prazo_em_vez_de_travar(self):
        t, saida = em_thread(lambda: self.d.executar(lambda: 1, timeout=0.3))
        t.join(3)  # não processamos eventos de propósito
        self.assertIsInstance(saida.get("erro"), TimeoutError)


class FalsoBackend:
    """Anota em que thread cada chamada aconteceu."""

    def __init__(self):
        self.threads = []

    def _anota(self):
        self.threads.append(threading.get_ident())

    def mover_absoluto(self, x, y): self._anota()
    def mover_relativo(self, dx, dy): self._anota()
    def clicar(self, botao, duplo): self._anota()
    def rolar(self, dx, dy): self._anota()
    def digitar(self, texto): self._anota()
    def combinacao(self, teclas): self._anota()

    def posicao(self):
        self._anota()
        return (7, 8)


class EntradaNaThreadPrincipalTests(unittest.TestCase):
    def test_toda_chamada_de_entrada_acontece_na_thread_principal(self):
        d = DespachanteThreadPrincipal()
        self.addCleanup(d.deleteLater)
        be = FalsoBackend()
        entrada = EntradaNaThreadPrincipal(be, d)

        def tudo():
            entrada.mover_absoluto(1, 2)
            entrada.mover_relativo(1, 2)
            entrada.clicar("esq", False)
            entrada.rolar(0, 1)
            entrada.digitar("a")
            entrada.combinacao(["ctrl", "c"])
            return entrada.posicao()

        t, saida = em_thread(tudo)
        bombear(lambda: not t.is_alive())
        self.assertEqual(saida.get("valor"), (7, 8))
        self.assertEqual(len(be.threads), 7)
        self.assertEqual(set(be.threads), {threading.get_ident()})


if __name__ == "__main__":
    unittest.main()
