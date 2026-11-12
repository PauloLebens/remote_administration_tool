"""Integração agente <-> controlador. Rodar: PYTHONPATH=src python3 -m unittest discover -s tests"""

import queue
import socket
import struct
import threading
import unittest

from agent.server import AgentServer
from controller.connection import EVENTO_DESCONECTADO, Connection
from shared.protocol import Acao, make_command, make_response
from shared.receiver import recv_msg
from shared.sender import send_msg


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.server = AgentServer("127.0.0.1", 0)
        self.server.open()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.port

    def tearDown(self):
        self.server.stop()
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive(), "serve_forever não parou")

    def espera(self, conn, timeout=3):
        return conn.incoming.get(timeout=timeout)


class AgentServerTests(ServerCase):
    def test_sobrevive_a_lixo_e_responde_depois(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as s:
            s.sendall(struct.pack("!I", 5) + b"lixo!")
            self.assertIsNone(recv_msg(s))  # só esta sessão caiu
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as s:
            cmd = make_command(Acao.ECHO, {"t": "oi"})
            send_msg(s, cmd)
            resp = recv_msg(s)
            self.assertEqual(resp["id"], cmd["id"])
            self.assertEqual(resp["resultado"], {"eco": {"t": "oi"}})

    def test_acao_desconhecida_vira_erro_e_sessao_continua(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as s:
            send_msg(s, make_command("NAO_EXISTE"))
            self.assertEqual(recv_msg(s)["status"], "erro")
            send_msg(s, make_command(Acao.ECHO))
            self.assertEqual(recv_msg(s)["status"], "ok")

    def test_tratador_com_bug_nao_derruba_sessao(self):
        self.server.handlers["BOOM"] = lambda msg: 1 / 0
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as s:
            send_msg(s, make_command("BOOM"))
            resp = recv_msg(s)
            self.assertEqual(resp["status"], "erro")
            self.assertIn("ZeroDivisionError", resp["erro"])
            send_msg(s, make_command(Acao.ECHO))
            self.assertEqual(recv_msg(s)["status"], "ok")

    def test_stop_derruba_sessao_ativa(self):  # caminho do kill switch
        with Connection("127.0.0.1", self.port) as conn:
            conn.connect()
            conn.send(Acao.ECHO)
            self.assertEqual(self.espera(conn)["acao"], Acao.ECHO)
            self.server.stop()
            self.assertEqual(self.espera(conn)["acao"], EVENTO_DESCONECTADO)


class ConnectionTests(ServerCase):
    def test_sessao_persistente_com_varios_comandos(self):
        with Connection("127.0.0.1", self.port) as conn:
            conn.connect()
            ids = [conn.send(Acao.ECHO, {"n": n}) for n in range(20)]
            recebidos = [self.espera(conn) for _ in ids]
            self.assertEqual([m["id"] for m in recebidos], ids)  # casados pelo id
            self.assertEqual(recebidos[7]["resultado"], {"eco": {"n": 7}})

    def test_close_gera_um_unico_evento_de_desconexao(self):
        conn = Connection("127.0.0.1", self.port)
        conn.connect()
        conn.close()
        conn.close()
        self.assertEqual(self.espera(conn)["acao"], EVENTO_DESCONECTADO)
        with self.assertRaises(queue.Empty):
            conn.incoming.get(timeout=0.3)
        with self.assertRaises(ConnectionError):
            conn.send(Acao.ECHO)

    def test_conexao_recusada(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            livre = s.getsockname()[1]
        with self.assertRaises(OSError):
            Connection("127.0.0.1", livre).connect(timeout=1)


if __name__ == "__main__":
    unittest.main()
