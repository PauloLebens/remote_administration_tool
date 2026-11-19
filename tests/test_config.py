"""Configuração do usuário. Rodar: PYTHONPATH=src python -m unittest discover -s tests"""

import dataclasses
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.agent import _config_da_linha_de_comando as config_agente
from agent.agent import mensagem_ao_escutar
from controller.controller import _config_da_linha_de_comando as config_controlador
from shared.config import CONFIG_ENV, AgentConfig, ConfigError, ControllerConfig

EXEMPLO = Path(__file__).resolve().parent.parent / "config.example.json"
CLASSES = (AgentConfig, ControllerConfig)


class Base(unittest.TestCase):
    """Cada teste roda numa pasta vazia, sem RAT_CONFIG no ambiente."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self._cwd = os.getcwd()
        os.chdir(self.dir)
        env = mock.patch.dict(os.environ)
        env.start()
        os.environ.pop(CONFIG_ENV, None)
        self.addCleanup(env.stop)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def arquivo(self, conteudo, nome="config.json"):
        p = self.dir / nome
        p.write_text(conteudo if isinstance(conteudo, str) else json.dumps(conteudo), encoding="utf-8")
        return str(p)


class ArquivoTests(Base):
    def test_sem_arquivo_valem_os_padroes(self):
        self.assertEqual(AgentConfig.carregar(), AgentConfig())
        self.assertEqual(ControllerConfig.carregar(), ControllerConfig())

    def test_arquivo_parcial_sobrescreve_so_o_que_informa(self):
        self.arquivo({"agente": {"porta": 5055, "raiz": "/srv/lab"}})
        a = AgentConfig.carregar()  # acha ./config.json sozinho
        self.assertEqual((a.porta, a.raiz, a.host), (5055, "/srv/lab", AgentConfig().host))
        self.assertEqual(ControllerConfig.carregar(), ControllerConfig())  # seção ausente

    def test_exemplo_versionado_esta_em_dia_com_os_padroes(self):
        for cls in CLASSES:
            self.assertEqual(cls.carregar(str(EXEMPLO)), cls(), cls.__name__)

    def test_exemplo_versionado_lista_todos_os_campos(self):
        # Campo ausente cai no padrão e passaria no teste acima: aqui se exige que o exemplo
        # MOSTRE cada campo, senão o usuário nunca descobre que ele existe.
        exemplo = json.loads(EXEMPLO.read_text(encoding="utf-8"))
        for cls in CLASSES:
            self.assertEqual(set(exemplo[cls.SECAO]), {f.name for f in dataclasses.fields(cls)}, cls.__name__)

    def test_arquivo_pedido_e_inexistente_e_erro(self):
        with self.assertRaisesRegex(ConfigError, "não encontrado"):
            AgentConfig.carregar(str(self.dir / "nada.json"))
        os.environ[CONFIG_ENV] = str(self.dir / "nada.json")
        with self.assertRaisesRegex(ConfigError, "não encontrado"):
            AgentConfig.carregar()

    def test_variavel_de_ambiente_e_argumento_explicito(self):
        env = self.arquivo({"agente": {"porta": 6001}}, "env.json")
        arg = self.arquivo({"agente": {"porta": 6002}}, "arg.json")
        os.environ[CONFIG_ENV] = env
        self.assertEqual(AgentConfig.carregar().porta, 6001)
        self.assertEqual(AgentConfig.carregar(arg).porta, 6002)  # o argumento vence

    def test_json_invalido_aponta_arquivo_e_linha(self):
        caminho = self.arquivo('{\n  "agente": {"porta": }\n}')
        with self.assertRaises(ConfigError) as cm:
            AgentConfig.carregar(caminho)
        self.assertIn("config.json", str(cm.exception))
        self.assertIn("linha 2", str(cm.exception))

    def test_estruturas_invalidas(self):
        for conteudo, trecho in (
            ("[1, 2]", "objeto JSON"),
            ({"agentes": {}}, "seção desconhecida"),  # nome da seção digitado errado
            ({"agente": [1]}, "objeto JSON"),
        ):
            with self.assertRaisesRegex(ConfigError, trecho, msg=conteudo):
                AgentConfig.carregar(self.arquivo(conteudo))

    def test_erro_de_campo_diz_qual_arquivo(self):
        caminho = self.arquivo({"agente": {"porta": "abc"}})
        with self.assertRaisesRegex(ConfigError, r"config\.json.*'porta'"):
            AgentConfig.carregar(caminho)


class ValidacaoTests(Base):
    def test_campo_desconhecido_e_erro_com_sugestao(self):
        with self.assertRaises(ConfigError) as cm:
            AgentConfig.de_dict({"porat": 5000})
        self.assertIn("'porat'", str(cm.exception))
        self.assertIn("você quis dizer 'porta'", str(cm.exception))
        with self.assertRaisesRegex(ConfigError, "campo desconhecido"):
            ControllerConfig.de_dict({"zzz_qualquer": 1})

    def test_tipos_invalidos(self):
        for campo, valor in (
            ("porta", True),  # bool não vale como número
            ("porta", "5000"), ("porta", 5000.5), ("porta", None),
            ("host", ""), ("host", "   "), ("host", 5), ("host", None),
            ("raiz", ""), ("raiz", 5),
            ("auth_timeout", "10"), ("auth_timeout", True), ("auth_timeout", None),
            ("auth_timeout", float("nan")), ("auth_timeout", float("inf")),
        ):
            with self.assertRaises(ConfigError, msg=(campo, valor)):
                AgentConfig(**{campo: valor})

    def test_faixas(self):
        for campo, valor in (
            ("porta", -1), ("porta", 65536),
            ("auth_timeout", 0), ("auth_timeout", 121),
            ("exec_saida_max", 10), ("ls_max_entradas", 0), ("ls_max_entradas", 10**9),
        ):
            with self.assertRaises(ConfigError, msg=(campo, valor)):
                AgentConfig(**{campo: valor})
        AgentConfig(porta=0)  # 0 = o SO escolhe
        AgentConfig(porta=65535)

    def test_mensagem_diz_o_campo_a_faixa_e_o_valor(self):
        with self.assertRaises(ConfigError) as cm:
            AgentConfig(porta=70000)
        for trecho in ("'porta'", "inteiro", "0 e 65535", "70000"):
            self.assertIn(trecho, str(cm.exception))

    def test_inteiro_vira_float_em_campo_numerico(self):
        a = AgentConfig(auth_timeout=5)
        self.assertEqual(a.auth_timeout, 5.0)
        self.assertIsInstance(a.auth_timeout, float)

    def test_timeout_padrao_nao_pode_passar_do_teto(self):
        with self.assertRaisesRegex(ConfigError, "exec_timeout_padrao.*exec_timeout_max"):
            AgentConfig(exec_timeout_padrao=100, exec_timeout_max=50)
        AgentConfig(exec_timeout_padrao=50, exec_timeout_max=50)

    def test_valor_do_arquivo_passa_pela_mesma_validacao(self):
        with self.assertRaises(ConfigError):
            AgentConfig.carregar(self.arquivo({"agente": {"auth_timeout": 9999}}))


class SobrescritaTests(Base):
    def test_com_sobrescreve_ignora_none_e_nao_altera_o_original(self):
        a = AgentConfig(porta=5055)
        b = a.com(porta=6000, host=None, raiz=None)
        self.assertEqual((b.porta, b.host), (6000, a.host))
        self.assertEqual(a.porta, 5055)  # imutável

    def test_com_valida_de_novo(self):
        with self.assertRaises(ConfigError):
            AgentConfig().com(porta=99999)

    def test_instancia_e_imutavel(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            AgentConfig().porta = 1

    def test_linha_de_comando_vence_o_arquivo(self):
        caminho = self.arquivo({"agente": {"porta": 6001, "host": "10.0.0.5"}, "controlador": {"porta": 6002}})
        a = config_agente(["--config", caminho, "--port", "7000"])
        self.assertEqual((a.porta, a.host), (7000, "10.0.0.5"))  # só a porta foi sobrescrita
        c = config_controlador(["--config", caminho])
        self.assertEqual(c.porta, 6002)
        self.assertEqual(config_controlador(["--config", caminho, "--host", "9.9.9.9"]).host, "9.9.9.9")

    def test_config_invalida_sai_com_mensagem_sem_traceback(self):
        ruim = self.arquivo({"agente": {"porta": "x"}}, "ruim.json")
        with self.assertRaises(SystemExit) as cm:
            config_agente(["--config", ruim])
        self.assertIn("configuração inválida", str(cm.exception.code))
        with self.assertRaises(SystemExit):
            config_controlador(["--config", str(self.dir / "nao_existe.json")])

    def test_raiz_inexistente_e_recusada_na_partida(self):
        with self.assertRaises(SystemExit) as cm:
            config_agente(["--raiz", str(self.dir / "nao_existe")])
        self.assertIn("raiz", str(cm.exception.code))
        self.assertEqual(config_agente(["--raiz", str(self.dir)]).raiz, str(self.dir))


class MensagemPortaTests(unittest.TestCase):
    def test_porta_ocupada_explica_a_causa_e_a_saida(self):
        import errno
        msg = mensagem_ao_escutar(OSError(errno.EADDRINUSE, "Address already in use"), AgentConfig(porta=5055))
        for trecho in ("0.0.0.0:5055", "já está em uso", "AirPlay", "--port", "config.json"):
            self.assertIn(trecho, msg)

    def test_outros_erros_nao_ganham_a_dica_de_porta(self):
        msg = mensagem_ao_escutar(PermissionError(13, "Permission denied"), AgentConfig(porta=80))
        self.assertIn("Permission denied", msg)
        self.assertNotIn("AirPlay", msg)


class PortaPadraoTests(unittest.TestCase):
    def test_o_padrao_nao_e_uma_porta_que_o_macos_ocupa(self):
        # O receptor AirPlay do macOS ocupa a 5000 (e a 7000): um padrão nelas faria o agente
        # falhar ao subir justamente nos Macs.
        self.assertNotIn(AgentConfig().porta, (5000, 7000))
        self.assertEqual(AgentConfig().porta, ControllerConfig().porta)  # agente e controlador combinam


class RegrasDaSecao3Tests(unittest.TestCase):
    """Trava: a configuração nunca pode virar uma porta para os itens proibidos."""

    # Se um destes termos aparecer num nome de campo, alguém está criando uma chave
    # para desligar uma regra da Seção 3 (ou para guardar o segredo em arquivo).
    PROIBIDOS = (
        "indicador", "aviso", "kill", "ocult", "hidden", "stealth", "silenc", "persist",
        "autostart", "startup", "inicializ", "disfarc", "log_desativ", "sem_log",
        "segredo", "secret", "senha", "password", "token",
    )

    def test_nenhum_campo_liga_ou_desliga_regra_da_secao_3_nem_guarda_o_segredo(self):
        for cls in CLASSES:
            for f in dataclasses.fields(cls):
                for termo in self.PROIBIDOS:
                    self.assertNotIn(termo, f.name.lower(), f"{cls.__name__}.{f.name}")

    def test_o_arquivo_de_exemplo_tambem_nao_tem_esses_campos(self):
        texto = EXEMPLO.read_text(encoding="utf-8").lower()
        for termo in self.PROIBIDOS:
            self.assertNotIn(f'"{termo}', texto)


if __name__ == "__main__":
    unittest.main()
