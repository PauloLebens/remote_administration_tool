"""EXEC e SYSINFO. Rodar: PYTHONPATH=src python3 -m unittest discover -s tests"""

import os
import subprocess
import sys
import threading
import time
import unittest

from agent.handlers.exec_handler import ExecHandler, run_command
from agent.handlers.sysinfo_handler import get_sysinfo
from agent.server import AgentServer
from controller.connection import Connection
from shared.config import AgentConfig
from shared.protocol import Acao, make_command
from tls_helper import CONTEXTO, verificador

POSIX = os.name != "nt"
PY = f'"{sys.executable}"'


EXEC = ExecHandler.de_config(AgentConfig())


def exec_(cmd, **args):
    return EXEC.handle(make_command(Acao.EXEC, {"cmd": cmd, **args}))


class ExecTests(unittest.TestCase):
    def test_stdout_e_codigo_zero(self):
        r = exec_("echo ola")
        self.assertEqual(r["stdout"].strip(), "ola")
        self.assertEqual((r["codigo"], r["expirou"], r["truncado"]), (0, False, False))

    def test_codigo_de_saida_diferente_de_zero(self):
        self.assertEqual(exec_("exit 3")["codigo"], 3)

    def test_stderr_separado_do_stdout(self):
        r = exec_("echo falhou 1>&2")
        self.assertEqual(r["stderr"].strip(), "falhou")
        self.assertEqual(r["stdout"], "")

    def test_comando_inexistente_nao_levanta(self):
        r = exec_("comando_que_nao_existe_xyz")
        self.assertNotEqual(r["codigo"], 0)
        self.assertTrue(r["stderr"])

    @unittest.skipUnless(POSIX, "acentos no terminal do Windows dependem do codepage")
    def test_unicode(self):
        self.assertEqual(exec_("echo 'olá ✓'")["stdout"].strip(), "olá ✓")

    def test_timeout_mata_o_comando(self):
        t0 = time.monotonic()
        r = exec_(f'{PY} -c "import time; time.sleep(30)"', timeout=0.5)
        self.assertTrue(r["expirou"])
        self.assertLess(time.monotonic() - t0, 5)

    @unittest.skipUnless(POSIX, "teste de árvore de processos usa sh")
    def test_timeout_mata_tambem_os_filhos(self):
        marca = f"marca_{os.getpid()}_{time.monotonic_ns()}"
        t0 = time.monotonic()
        # O filho é um Python cuja linha de comando carrega a marca (um `sh -c`
        # simples viraria `sleep` e perderia o comentário).
        filho = f'{PY} -c "import time; time.sleep(30)  # {marca}"'
        r = exec_(f"{filho} & wait", timeout=0.5)
        self.assertTrue(r["expirou"])
        # Com o grupo morto os pipes fecham na hora; sem isso o agente esperaria
        # os 2 s de join de cada stream.
        self.assertLess(time.monotonic() - t0, 2)
        time.sleep(0.3)
        achados = subprocess.run(["pgrep", "-f", marca], capture_output=True, text=True)
        self.assertEqual(achados.stdout.split(), [], "o filho sobreviveu ao timeout")

    def test_stdin_fechado_nao_trava(self):
        r = exec_(f'{PY} -c "import sys; print(len(sys.stdin.read()))"', timeout=5)
        self.assertEqual((r["stdout"].strip(), r["expirou"]), ("0", False))

    def test_saida_grande_e_truncada_sem_travar(self):
        r = run_command(f'{PY} -c "print(\'x\' * 3_000_000)"', timeout=20, max_output=1000)
        self.assertTrue(r["truncado"])
        self.assertEqual(len(r["stdout"]), 1000)
        self.assertEqual(r["codigo"], 0)  # o processo terminou: o excesso foi drenado

    def test_argumentos_invalidos(self):
        for args in ({}, {"cmd": ""}, {"cmd": "  "}, {"cmd": 5}, {"cmd": "echo"} | {"timeout": 0},
                     {"cmd": "echo", "timeout": "10"}, {"cmd": "echo", "timeout": True}):
            with self.assertRaises(ValueError, msg=args):
                EXEC.handle(make_command(Acao.EXEC, args))

    def _dorme(self):
        return f'{PY} -c "import time; time.sleep(30)"'

    def test_timeout_pedido_e_limitado_ao_teto_da_config(self):
        h = ExecHandler.de_config(AgentConfig(exec_timeout_padrao=0.5, exec_timeout_max=1.0))
        t0 = time.monotonic()
        r = h.handle(make_command(Acao.EXEC, {"cmd": self._dorme(), "timeout": 99999}))
        self.assertTrue(r["expirou"])
        self.assertLess(time.monotonic() - t0, 4)  # parou no teto (1 s), não em 99999 s

    def test_timeout_padrao_vem_da_config(self):
        h = ExecHandler.de_config(AgentConfig(exec_timeout_padrao=0.5, exec_timeout_max=1.0))
        t0 = time.monotonic()
        r = h.handle(make_command(Acao.EXEC, {"cmd": self._dorme()}))  # o pedido não diz o prazo
        self.assertTrue(r["expirou"])
        self.assertLess(time.monotonic() - t0, 4)

    def test_limite_de_saida_vem_da_config(self):
        h = ExecHandler.de_config(AgentConfig(exec_saida_max=1024))
        r = h.handle(make_command(Acao.EXEC, {"cmd": f"{PY} -c \"print('x' * 5000)\""}))
        self.assertEqual((len(r["stdout"]), r["truncado"]), (1024, True))


class SysinfoTests(unittest.TestCase):
    def test_campos_obrigatorios(self):
        info = get_sysinfo()
        for campo in ("so", "usuario", "hostname", "cpu", "memoria", "discos"):
            self.assertIn(campo, info)
        self.assertTrue(info["so"]["sistema"])
        self.assertGreater(info["memoria"]["total"], 0)
        self.assertGreaterEqual(info["cpu"]["nucleos_logicos"], 1)
        self.assertTrue(info["discos"], "nenhum disco listado")
        d = info["discos"][0]
        self.assertLessEqual(d["usado"], d["total"])

    def test_resposta_cabe_em_json(self):
        import json
        json.dumps(get_sysinfo())


class ViaRedeTests(unittest.TestCase):
    """Do controlador ao agente, passando por handshake, framing e log."""

    def setUp(self):
        self.server = AgentServer(AgentConfig(host="127.0.0.1", porta=0), secret="segredo-de-teste", contexto_tls=CONTEXTO)
        self.server.open()
        self.t = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.t.start()
        self.conn = Connection("127.0.0.1", self.server.port, secret="segredo-de-teste", verificador=verificador())
        self.conn.connect()

    def tearDown(self):
        self.conn.close()
        self.server.stop()
        self.t.join(3)

    def test_exec_e_sysinfo_pela_rede_e_comando_vai_pro_log(self):
        with self.assertLogs("agente", "INFO") as logs:
            i1 = self.conn.send(Acao.EXEC, {"cmd": "echo via-rede"})
            r1 = self.conn.incoming.get(timeout=5)
            i2 = self.conn.send(Acao.SYSINFO)
            r2 = self.conn.incoming.get(timeout=5)
        self.assertEqual((r1["id"], r1["status"]), (i1, "ok"))
        self.assertEqual(r1["resultado"]["stdout"].strip(), "via-rede")
        self.assertEqual((r2["id"], r2["status"]), (i2, "ok"))
        self.assertIn("hostname", r2["resultado"])
        self.assertTrue(any("echo via-rede" in linha for linha in logs.output),
                        "o comando executado deve aparecer no log do agente")

    def test_exec_invalido_vira_resposta_de_erro(self):
        self.conn.send(Acao.EXEC, {"cmd": ""})
        r = self.conn.incoming.get(timeout=5)
        self.assertEqual(r["status"], "erro")
        self.conn.send(Acao.ECHO)  # a sessão continua viva
        self.assertEqual(self.conn.incoming.get(timeout=5)["status"], "ok")


if __name__ == "__main__":
    unittest.main()
