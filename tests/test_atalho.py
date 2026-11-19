"""Atalho global do kill switch. O Quartz é falso: nenhum teste observa o teclado de verdade.

Rodar: PYTHONPATH=src python -m unittest discover -s tests
"""

import sys
import threading
import unittest
from pathlib import Path

from agent.so import atalho as modulo
from agent.so.atalho import KEYCODE_K_MACOS, AtalhoPynput, AtalhoQuartz, criar_atalho, e_o_atalho

CTRL, ALT, SHIFT, CMD = 0x40000, 0x80000, 0x20000, 0x100000
CAPS, FN = 0x10000, 0x800000  # bits que NÃO devem influenciar
DOWN, DISABLED_TIMEOUT, DISABLED_USER = 10, 0xFFFFFFFE, 0xFFFFFFFF


class QuartzFalso:
    """Substitui o módulo Quartz. Guarda o callback e deixa o teste 'pressionar teclas'."""

    kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionListenOnly = "sessao", "inicio", "somente-leitura"
    kCGEventKeyDown = DOWN
    kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput = DISABLED_TIMEOUT, DISABLED_USER
    kCGKeyboardEventKeycode, kCFRunLoopCommonModes = "campo-keycode", "modos"
    kCGEventFlagMaskControl, kCGEventFlagMaskAlternate = CTRL, ALT
    kCGEventFlagMaskShift, kCGEventFlagMaskCommand = SHIFT, CMD

    def __init__(self, negar=False):
        self.negar, self.callback, self.opcoes = negar, None, None
        self.habilitado, self.invalidado, self.parado = [], False, threading.Event()

    def CGEventMaskBit(self, tipo):
        return 1 << tipo

    def CGEventTapCreate(self, tap, posicao, opcao, mascara, callback, refcon):
        self.opcoes = (tap, posicao, opcao, mascara)
        if self.negar:  # o macOS devolve None quando falta a permissão
            return None
        self.callback = callback
        return "TAP"

    def CFMachPortCreateRunLoopSource(self, alocador, tap, ordem): return "FONTE"
    def CFRunLoopGetCurrent(self): return "LACO"
    def CFRunLoopAddSource(self, laco, fonte, modos): pass
    def CFRunLoopRun(self): self.parado.wait(5)
    def CFRunLoopStop(self, laco): self.parado.set()
    def CGEventTapEnable(self, tap, ligar): self.habilitado.append(ligar)
    def CFMachPortInvalidate(self, tap): self.invalidado = True
    def CGEventGetIntegerValueField(self, evento, campo): return evento["keycode"]
    def CGEventGetFlags(self, evento): return evento["flags"]

    def tecla(self, keycode, flags, tipo=DOWN):
        """Simula o sistema entregando uma tecla ao callback; devolve o que o callback devolveu."""
        evento = {"keycode": keycode, "flags": flags}
        return evento, self.callback("proxy", tipo, evento, None)


class FiltroTests(unittest.TestCase):
    def acerta(self, keycode, flags):
        return e_o_atalho(keycode, flags, CTRL, ALT, SHIFT, CMD)

    def test_a_combinacao_certa(self):
        self.assertTrue(self.acerta(KEYCODE_K_MACOS, CTRL | ALT | SHIFT))

    def test_bits_alheios_nao_atrapalham(self):
        self.assertTrue(self.acerta(KEYCODE_K_MACOS, CTRL | ALT | SHIFT | CAPS | FN))

    def test_falta_de_qualquer_modificador_nao_dispara(self):
        for flags in (ALT | SHIFT, CTRL | SHIFT, CTRL | ALT, CTRL, 0):
            self.assertFalse(self.acerta(KEYCODE_K_MACOS, flags), hex(flags))

    def test_command_junto_nao_dispara(self):
        self.assertFalse(self.acerta(KEYCODE_K_MACOS, CTRL | ALT | SHIFT | CMD))

    def test_outra_tecla_nao_dispara(self):
        for keycode in (0, 1, 39, 41, 53, 255):
            self.assertFalse(self.acerta(keycode, CTRL | ALT | SHIFT), keycode)

    def test_mascaras_batem_com_as_do_quartz_real(self):
        if sys.platform != "darwin":
            self.skipTest("só no macOS")
        import Quartz
        self.assertEqual((CTRL, ALT, SHIFT, CMD), (Quartz.kCGEventFlagMaskControl, Quartz.kCGEventFlagMaskAlternate,
                                                   Quartz.kCGEventFlagMaskShift, Quartz.kCGEventFlagMaskCommand))
        self.assertEqual((DISABLED_TIMEOUT, DISABLED_USER), (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput))


class AtalhoQuartzTests(unittest.TestCase):
    def setUp(self):
        self.q = QuartzFalso()
        self.acionou = []
        self.atalho = AtalhoQuartz(lambda: self.acionou.append(1), quartz=self.q)
        self.addCleanup(self.atalho.parar)

    def test_so_a_combinacao_certa_aciona_o_kill_switch(self):
        self.assertIsNone(self.atalho.iniciar())
        for keycode, flags in ((KEYCODE_K_MACOS, CTRL | ALT), (5, CTRL | ALT | SHIFT), (KEYCODE_K_MACOS, 0),
                               (KEYCODE_K_MACOS, CTRL | ALT | SHIFT | CMD)):
            self.q.tecla(keycode, flags)
        self.assertEqual(self.acionou, [])
        self.q.tecla(KEYCODE_K_MACOS, CTRL | ALT | SHIFT)
        self.assertEqual(self.acionou, [1])

    def test_a_escuta_e_somente_leitura_e_so_de_teclas_pressionadas(self):
        self.atalho.iniciar()
        self.assertEqual(self.q.opcoes[2], "somente-leitura")  # não pode bloquear nem alterar teclas
        self.assertEqual(self.q.opcoes[3], 1 << DOWN)  # só 'tecla pressionada'; nada de soltar/mouse

    def test_o_evento_volta_intacto_para_nao_atrapalhar_ninguem(self):
        self.atalho.iniciar()
        for keycode, flags in ((KEYCODE_K_MACOS, CTRL | ALT | SHIFT), (7, 0)):
            evento, devolvido = self.q.tecla(keycode, flags)
            self.assertIs(devolvido, evento)

    def test_tap_desligado_pelo_sistema_e_religado(self):
        self.atalho.iniciar()
        self.q.habilitado.clear()
        self.q.tecla(0, 0, tipo=DISABLED_TIMEOUT)
        self.q.tecla(0, 0, tipo=DISABLED_USER)
        self.assertEqual(self.q.habilitado, [True, True])
        self.assertEqual(self.acionou, [])

    def test_erro_na_acao_nao_vaza_para_o_sistema(self):
        def explode():
            raise RuntimeError("falhou")
        atalho = AtalhoQuartz(explode, quartz=self.q)
        atalho.iniciar()
        self.addCleanup(atalho.parar)
        evento, devolvido = self.q.tecla(KEYCODE_K_MACOS, CTRL | ALT | SHIFT)  # não pode levantar
        self.assertIs(devolvido, evento)

    def test_sem_permissao_explica_e_nao_levanta(self):
        atalho = AtalhoQuartz(lambda: None, quartz=QuartzFalso(negar=True))
        motivo = atalho.iniciar()
        self.assertIn("negou", motivo)
        self.assertIn("Monitoramento de Entrada", motivo)
        atalho.parar()  # seguro mesmo sem ter iniciado

    def test_parar_desliga_libera_e_encerra_a_thread(self):
        self.atalho.iniciar()
        self.assertTrue(self.atalho._thread.is_alive())
        self.atalho.parar()
        self.atalho._thread.join(3)
        self.assertFalse(self.atalho._thread.is_alive())
        self.assertEqual((self.q.habilitado[-1], self.q.invalidado), (False, True))
        self.atalho.parar()  # idempotente


class FabricaTests(unittest.TestCase):
    def test_escolhe_a_implementacao_do_sistema(self):
        esperado = AtalhoQuartz if sys.platform == "darwin" else AtalhoPynput
        self.assertIsInstance(criar_atalho(lambda: None), esperado)

    def test_pynput_sem_ambiente_nao_levanta_e_explica(self):
        class Quebrado(AtalhoPynput):
            def iniciar(self):
                try:
                    raise RuntimeError("sem display")
                except Exception as exc:
                    return f"atalho global indisponível: {exc}"
        self.assertIn("sem display", Quebrado(lambda: None).iniciar())


class NaoVirouKeyloggerTests(unittest.TestCase):
    """Trava estrutural: este módulo não pode passar a registrar ou entregar teclas."""

    def setUp(self):
        self.fonte = Path(modulo.__file__).read_text()
        # só o código: a docstring do módulo e os comentários explicam o que NÃO existe aqui
        linhas = [l for l in self.fonte.split("\n") if not l.strip().startswith("#")]
        corpo = "\n".join(linhas)
        self.codigo = corpo[corpo.index('from __future__'):]

    def test_nao_ha_callback_por_tecla_nem_listener_geral(self):
        for proibido in ("on_press", "on_release", "keyboard.Listener", "Listener("):
            self.assertNotIn(proibido, self.codigo, proibido)

    def test_nao_ha_log_nem_gravacao_de_teclas(self):
        for proibido in ("logging", "getLogger", "print(", ".write(", "open(", "append(", "socket", "send"):
            self.assertNotIn(proibido, self.codigo, proibido)

    def test_o_callback_nao_traduz_teclas(self):
        # sem tradução: nada de TIS/TSM nem de caracteres (o que derrubaria o processo no macOS 14+)
        for proibido in ("TISCopy", "TISGet", "CGEventKeyboardGetUnicodeString", "chr(", "NSEvent"):
            self.assertNotIn(proibido, self.codigo, proibido)


if __name__ == "__main__":
    unittest.main()
