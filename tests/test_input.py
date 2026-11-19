"""Mouse e teclado (RF6 a RF8) e o indicador como pré-condição. Nenhum teste toca no mouse
real: a entrada passa por um backend falso. Rodar: PYTHONPATH=src python -m unittest discover -s tests
"""

import tempfile
import threading
import unittest

from agent.handlers.input_handler import MAX_TECLAS_COMBO, MAX_TEXTO, InputHandlers
from agent.server import AgentServer
from agent.so.entrada import BOTOES, TECLAS_NOMEADAS
from controller.connection import Connection
from controller.controller import executar
from controller.transfer import TransferError
from shared.config import AgentConfig
from shared.protocol import Acao, make_command
from tls_helper import CONTEXTO, verificador

SECRET = "segredo-de-teste"


class FalsoBackend:
    """InputBackend que só anota o que lhe pedem (e simula a posição do cursor)."""

    def __init__(self, eventos=None):
        self.chamadas = []
        self._eventos = eventos if eventos is not None else []
        self._pos = (100, 100)

    def _anota(self, *c):
        self.chamadas.append(c)
        self._eventos.append(c)

    def mover_absoluto(self, x, y):
        self._anota("abs", x, y)
        self._pos = (x, y)

    def mover_relativo(self, dx, dy):
        self._anota("rel", dx, dy)
        self._pos = (self._pos[0] + dx, self._pos[1] + dy)

    def posicao(self):
        return self._pos

    def clicar(self, botao, duplo):
        self._anota("clicar", botao, duplo)

    def rolar(self, dx, dy):
        self._anota("rolar", dx, dy)

    def digitar(self, texto):
        self._anota("digitar", texto)

    def combinacao(self, teclas):
        self._anota("combo", tuple(teclas))


class FalsoObservador:
    """Observador (indicador) que registra início e fim, e pode ser feito falhar."""

    def __init__(self, eventos, falhar=False):
        self.eventos, self.falhar = eventos, falhar

    def sessao_iniciada(self, origem):
        if self.falhar:
            raise RuntimeError("janela indisponível")
        self.eventos.append(("iniciada", origem))

    def sessao_encerrada(self):
        self.eventos.append(("encerrada",))


def msg(acao, **args):
    return make_command(acao, args)


class HandlerCase(unittest.TestCase):
    def setUp(self):
        self.be = FalsoBackend()
        self.h = InputHandlers(self.be)

    def recusa(self, fn, trecho=None, **args):
        """O pedido deve ser recusado com ValueError, sem tocar no backend."""
        with self.assertRaisesRegex(ValueError, trecho or ""):
            fn(msg("X", **args))
        self.assertEqual(self.be.chamadas, [], f"o backend foi chamado apesar do erro: {args}")


class MouseMoveTests(HandlerCase):
    def test_absoluto_e_o_padrao(self):
        r = self.h.mouse_move(msg(Acao.MOUSE_MOVE, x=840, y=410))
        self.assertEqual(r, {"x": 840, "y": 410, "abs": True})
        self.assertEqual(self.be.chamadas, [("abs", 840, 410)])

    def test_relativo_soma_a_posicao_atual(self):
        r = self.h.mouse_move(msg(Acao.MOUSE_MOVE, x=-10, y=25, abs=False))
        self.assertEqual((r["x"], r["y"], r["abs"]), (90, 125, False))  # partia de (100, 100)
        self.assertEqual(self.be.chamadas, [("rel", -10, 25)])

    def test_coordenadas_negativas_valem_para_monitor_a_esquerda(self):
        self.h.mouse_move(msg(Acao.MOUSE_MOVE, x=-1920, y=0))

    def test_argumentos_invalidos_nao_chegam_ao_backend(self):
        for args in (
            {}, {"x": 1}, {"y": 1},
            {"x": "10", "y": 5}, {"x": 1.5, "y": 5}, {"x": True, "y": 5}, {"x": None, "y": 5},
            {"x": 40000, "y": 0}, {"x": 0, "y": -40000},          # fora da faixa absoluta
            {"x": 20000, "y": 0, "abs": False},                    # fora da faixa relativa
            {"x": 1, "y": 1, "abs": "true"}, {"x": 1, "y": 1, "abs": 1},
        ):
            self.recusa(self.h.mouse_move, **args)


class MouseClickTests(HandlerCase):
    def test_padrao_e_clique_esquerdo_simples(self):
        self.assertEqual(self.h.mouse_click(msg(Acao.MOUSE_CLICK)), {"botao": "esq", "duplo": False})
        self.assertEqual(self.be.chamadas, [("clicar", "esq", False)])

    def test_todos_os_botoes_e_duplo(self):
        for b in BOTOES:
            self.h.mouse_click(msg(Acao.MOUSE_CLICK, botao=b, duplo=True))
        self.assertEqual(self.be.chamadas, [("clicar", b, True) for b in BOTOES])

    def test_maiusculas_sao_aceitas(self):
        self.assertEqual(self.h.mouse_click(msg(Acao.MOUSE_CLICK, botao="DIR"))["botao"], "dir")

    def test_invalidos(self):
        for args in ({"botao": "esquerdo"}, {"botao": 1}, {"botao": None},
                     {"duplo": "sim"}, {"duplo": 1}, {"duplo": None}):
            self.recusa(self.h.mouse_click, **args)


class MouseScrollTests(HandlerCase):
    def test_vertical_horizontal_e_ambos(self):
        self.assertEqual(self.h.mouse_scroll(msg(Acao.MOUSE_SCROLL, dy=-3)), {"dx": 0, "dy": -3})
        self.assertEqual(self.h.mouse_scroll(msg(Acao.MOUSE_SCROLL, dx=2)), {"dx": 2, "dy": 0})
        self.assertEqual(self.h.mouse_scroll(msg(Acao.MOUSE_SCROLL, dx=1, dy=1)), {"dx": 1, "dy": 1})
        self.assertEqual(self.be.chamadas, [("rolar", 0, -3), ("rolar", 2, 0), ("rolar", 1, 1)])

    def test_invalidos(self):
        for args in ({}, {"dy": 0}, {"dx": 0, "dy": 0}, {"dy": 5000}, {"dx": -5000},
                     {"dy": "3"}, {"dy": 1.5}, {"dy": True}):
            self.recusa(self.h.mouse_scroll, **args)


class KeyTests(HandlerCase):
    def test_texto(self):
        self.assertEqual(self.h.key(msg(Acao.KEY, texto="olá ✓")), {"digitados": 5})
        self.assertEqual(self.be.chamadas, [("digitar", "olá ✓")])

    def test_texto_aceita_quebra_de_linha_e_tab(self):
        self.h.key(msg(Acao.KEY, texto="linha1\nlinha2\tfim"))

    def test_combo_e_normalizado(self):
        r = self.h.key(msg(Acao.KEY, combo=["CTRL", "Shift", "t"]))
        self.assertEqual(r, {"combo": ["ctrl", "shift", "t"]})
        self.assertEqual(self.be.chamadas, [("combo", ("ctrl", "shift", "t"))])

    def test_caractere_unico_mantem_a_caixa(self):
        self.assertEqual(self.h.key(msg(Acao.KEY, combo=["ctrl", "C"]))["combo"], ["ctrl", "C"])

    def test_todos_os_nomes_de_tecla_conhecidos_sao_aceitos(self):
        for nome in TECLAS_NOMEADAS:
            self.h.key(msg(Acao.KEY, combo=[nome]))
        self.assertEqual(len(self.be.chamadas), len(TECLAS_NOMEADAS))

    def test_exatamente_um_entre_texto_e_combo(self):
        self.recusa(self.h.key, "exatamente um")
        self.recusa(self.h.key, "exatamente um", texto="a", combo=["ctrl"])

    def test_texto_invalido(self):
        for texto in ("", 5, None, ["a"], "a" * (MAX_TEXTO + 1),
                      "esc\x1b", "nulo\x00", "bell\x07", "del\x7f"):
            self.recusa(self.h.key, texto=texto)
        self.h.key(msg(Acao.KEY, texto="a" * MAX_TEXTO))  # no limite é aceito

    def test_combo_invalido(self):
        for combo in ([], "ctrl", None, 5, ["ctrl"] * (MAX_TECLAS_COMBO + 1),
                      ["ctrll"], ["ctrl", 5], ["ctrl", ""], ["ctrl", None], ["enterr", "a"]):
            self.recusa(self.h.key, combo=combo)

    def test_mensagem_de_tecla_desconhecida_lista_as_validas(self):
        with self.assertRaisesRegex(ValueError, "ctrll.*ctrl"):
            self.h.key(msg(Acao.KEY, combo=["ctrll"]))


class RegistroTests(unittest.TestCase):
    def test_registrar_adiciona_as_quatro_acoes(self):
        handlers = {}
        InputHandlers(FalsoBackend()).registrar(handlers)
        self.assertEqual(set(handlers), {Acao.MOUSE_MOVE, Acao.MOUSE_CLICK, Acao.MOUSE_SCROLL, Acao.KEY})


class FalsoTeclado:
    """Controlador de teclado falso do pynput: anota press/release e pode falhar numa tecla."""

    def __init__(self, falha_em=None):
        self.eventos, self.falha_em = [], falha_em

    def press(self, tecla):
        if tecla == self.falha_em:
            raise RuntimeError("falha ao pressionar")
        self.eventos.append(("press", tecla))

    def release(self, tecla):
        self.eventos.append(("release", tecla))


class PynputBackendTests(unittest.TestCase):
    """O backend real, sem nunca mover o mouse nem digitar: só o teclado é trocado por um falso."""

    def setUp(self):
        from agent.so.entrada import EntradaIndisponivel
        from agent.so.entrada_pynput import PynputBackend

        try:
            self.backend = PynputBackend()  # criar os controladores não gera nenhum evento
        except EntradaIndisponivel as exc:  # só pula se NÃO há pynput/ambiente gráfico (CI sem DISPLAY)
            self.skipTest(f"pynput indisponível: {exc}")
        # Qualquer outro erro aqui é um defeito e precisa falhar, não ser pulado.

    def test_todo_nome_de_tecla_aceito_pelo_handler_tem_mapeamento(self):
        for nome in TECLAS_NOMEADAS:
            self.assertIn(nome, self.backend._teclas, nome)

    def test_todo_botao_aceito_pelo_handler_tem_mapeamento(self):
        self.assertEqual(set(self.backend._botoes), set(BOTOES))

    def test_combinacao_pressiona_em_ordem_e_solta_em_ordem_inversa(self):
        self.backend._teclado = FalsoTeclado()
        self.backend.combinacao(["ctrl", "shift", "t"])
        ctrl, shift = self.backend._teclas["ctrl"], self.backend._teclas["shift"]
        self.assertEqual(
            self.backend._teclado.eventos,
            [("press", ctrl), ("press", shift), ("press", "t"),
             ("release", "t"), ("release", shift), ("release", ctrl)],
        )

    def test_tecla_sem_equivalente_no_sistema_e_recusada_antes_de_apertar_qualquer_coisa(self):
        self.backend._teclado = FalsoTeclado()
        del self.backend._teclas["f5"]  # simula uma tecla que o sistema não tem
        with self.assertRaisesRegex(ValueError, "'f5' não existe neste sistema"):
            self.backend.combinacao(["ctrl", "f5"])
        self.assertEqual(self.backend._teclado.eventos, [])  # nem o ctrl chegou a ser pressionado

    def test_modificador_nunca_fica_preso_se_uma_tecla_falha(self):
        self.backend._teclado = FalsoTeclado(falha_em="t")
        with self.assertRaises(RuntimeError):
            self.backend.combinacao(["ctrl", "shift", "t"])
        ctrl, shift = self.backend._teclas["ctrl"], self.backend._teclas["shift"]
        # ctrl e shift chegaram a ser pressionados e TÊM de ter sido soltos
        self.assertEqual(
            self.backend._teclado.eventos,
            [("press", ctrl), ("press", shift), ("release", shift), ("release", ctrl)],
        )


class ServidorCase(unittest.TestCase):
    """Agente de verdade na rede, com backend e observador falsos."""

    observador_falha = False
    com_observador = True
    com_entrada = True

    def setUp(self):
        self.eventos = []
        self.be = FalsoBackend(self.eventos)
        self.obs = FalsoObservador(self.eventos, falhar=self.observador_falha)
        self.server = AgentServer(
            AgentConfig(host="127.0.0.1", porta=0),
            secret=SECRET,
            contexto_tls=CONTEXTO,
            observador=self.obs if self.com_observador else None,
            entrada=self.be if self.com_entrada else None,
        )
        self.server.open()
        self.t = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.t.start()

    def tearDown(self):
        self.server.stop()
        self.t.join(5)

    def conecta(self):
        c = Connection("127.0.0.1", self.server.port, secret=SECRET, verificador=verificador())
        c.connect()
        self.addCleanup(c.close)
        return c


class EntradaSoComIndicadorTests(ServidorCase):
    com_observador = False

    def test_sem_indicador_mouse_e_teclado_nem_existem(self):  # Seção 3 / RF8
        for acao in (Acao.MOUSE_MOVE, Acao.MOUSE_CLICK, Acao.MOUSE_SCROLL, Acao.KEY):
            self.assertNotIn(acao, self.server.handlers)
        c = self.conecta()
        r = c.request(Acao.MOUSE_MOVE, {"x": 1, "y": 1})
        self.assertEqual(r["status"], "erro")
        self.assertIn("ação desconhecida", r["erro"])
        self.assertEqual(self.be.chamadas, [])


class SemBackendTests(ServidorCase):
    com_entrada = False

    def test_com_indicador_mas_sem_backend_tambem_nao_registra(self):
        self.assertNotIn(Acao.KEY, self.server.handlers)


class ObservadorTests(ServidorCase):
    def test_indicador_e_avisado_antes_do_primeiro_comando_e_depois_do_ultimo(self):
        c = self.conecta()
        c.request(Acao.MOUSE_MOVE, {"x": 5, "y": 6})
        c.close()
        for _ in range(100):  # o agente encerra a sessão ao ver a queda
            if ("encerrada",) in self.eventos:
                break
            threading.Event().wait(0.02)
        self.assertEqual(self.eventos[0][0], "iniciada")
        self.assertRegex(self.eventos[0][1], r"^127\.0\.0\.1:\d+$")  # mostra quem controla
        self.assertEqual(self.eventos[1], ("abs", 5, 6))
        self.assertEqual(self.eventos[-1], ("encerrada",))
        self.assertEqual(self.eventos.count(("encerrada",)), 1)

    def test_falha_de_autenticacao_nao_acende_o_indicador(self):
        c = Connection("127.0.0.1", self.server.port, secret="segredo-errado-9", verificador=verificador())
        with self.assertRaises(ConnectionError):
            c.connect()
        self.assertEqual(self.eventos, [])  # ninguém foi autenticado, nada a mostrar


class IndicadorQuebradoTests(ServidorCase):
    observador_falha = True

    def test_se_o_indicador_falha_a_sessao_e_recusada_e_nada_executa(self):
        with self.assertLogs("agente", "ERROR") as logs:
            c = self.conecta()
            with self.assertRaises(ConnectionError):
                c.request(Acao.MOUSE_MOVE, {"x": 1, "y": 1}, timeout=3)
        self.assertEqual(self.be.chamadas, [])
        self.assertTrue(any("indicador visível falhou" in l for l in logs.output))
        self.assertNotIn(("encerrada",), self.eventos)  # nunca chegou a começar


class LogSemTextoDigitadoTests(ServidorCase):
    def test_texto_digitado_nao_vai_para_o_log_mas_o_comando_sim(self):
        c = self.conecta()
        with self.assertLogs("agente", "INFO") as logs:
            c.request(Acao.KEY, {"texto": "senha-secreta-123"})
            c.request(Acao.KEY, {"combo": ["ctrl", "c"]})
        todo = "\n".join(logs.output)
        self.assertNotIn("senha-secreta", todo)             # o conteúdo não é registrado
        self.assertIn("<17 caracteres>", todo)               # mas o comando e o tamanho sim
        self.assertIn("KEY", todo)
        self.assertIn('"ctrl"', todo)                        # combinações de teclas são registradas
        self.assertIn(("digitar", "senha-secreta-123"), self.be.chamadas)  # e foi digitado de verdade


class CliTests(ServidorCase):
    def test_comandos_de_entrada(self):
        c = self.conecta()
        self.assertEqual(executar(c, "mouse 10 20"), "cursor em (10, 20)")
        self.assertEqual(executar(c, "mouse rel 5 -5"), "cursor em (15, 15)")
        self.assertEqual(executar(c, "click"), "clique esq")
        self.assertEqual(executar(c, "click dir duplo"), "clique duplo dir")
        self.assertEqual(executar(c, "click duplo meio"), "clique duplo meio")
        self.assertEqual(executar(c, "scroll -3"), "rolou dy=-3 dx=0")
        self.assertEqual(executar(c, "scroll 2 1"), "rolou dy=2 dx=1")
        self.assertEqual(executar(c, "digitar olá  mundo"), "digitados: 10 caracteres")  # espaços duplos preservados
        self.assertEqual(executar(c, "combo ctrl c"), "combo: ctrl+c")
        self.assertEqual(
            self.be.chamadas,
            [("abs", 10, 20), ("rel", 5, -5), ("clicar", "esq", False), ("clicar", "dir", True),
             ("clicar", "meio", True), ("rolar", 0, -3), ("rolar", 1, 2),
             ("digitar", "olá  mundo"), ("combo", ("ctrl", "c"))],
        )

    def test_uso_incorreto_nao_envia_nada(self):
        c = self.conecta()
        for linha in ("mouse", "mouse 1", "mouse abc 2", "mouse rel 1", "scroll", "scroll a",
                      "scroll 1 2 3", "digitar", "combo"):
            self.assertIn("uso:", executar(c, linha), linha)
        self.assertEqual(self.be.chamadas, [])

    def test_erros_do_agente_viram_erro_para_o_operador(self):
        c = self.conecta()
        with self.assertRaisesRegex(TransferError, "tecla desconhecida"):
            executar(c, "combo ctrll")
        with self.assertRaisesRegex(TransferError, "botao"):
            executar(c, "click esquerdo")
        with self.assertRaisesRegex(TransferError, "'x'"):
            executar(c, "mouse 99999 0")


if __name__ == "__main__":
    unittest.main()
