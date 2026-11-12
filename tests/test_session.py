"""Integração agente <-> controlador. Rodar: PYTHONPATH=src python3 -m unittest discover -s tests"""

import queue
import socket
import struct
import threading
import unittest

from agent.server import AgentServer
from controller.connection import EVENTO_DESCONECTADO, AuthError, Connection
from shared.auth import compute_proof
from shared.protocol import Acao, make_command
from shared.receiver import recv_msg
from shared.sender import send_msg


SECRET = "segredo-de-teste"


def autentica(sock, secret=SECRET):
    """Faz o handshake à mão num socket cru e devolve a resposta do AUTH."""
    desafio = recv_msg(sock)
    assert desafio["acao"] == Acao.DESAFIO
    prova = compute_proof(secret, desafio["args"]["nonce"])
    send_msg(sock, make_command(Acao.AUTH, {"prova": prova}))
    return recv_msg(sock)


class ServerCase(unittest.TestCase):
    auth_timeout = 10.0

    def setUp(self):
        self.server = AgentServer("127.0.0.1", 0, secret=SECRET, auth_timeout=self.auth_timeout)
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

    def conecta(self, **kw):
        return Connection("127.0.0.1", self.port, secret=kw.pop("secret", SECRET), **kw)


class AgentServerTests(ServerCase):
    def test_sobrevive_a_lixo_e_responde_depois(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as s:
            self.assertEqual(autentica(s)["status"], "ok")
            s.sendall(struct.pack("!I", 5) + b"lixo!")
            self.assertIsNone(recv_msg(s))  # só esta sessão caiu
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as s:
            autentica(s)
            cmd = make_command(Acao.ECHO, {"t": "oi"})
            send_msg(s, cmd)
            resp = recv_msg(s)
            self.assertEqual(resp["id"], cmd["id"])
            self.assertEqual(resp["resultado"], {"eco": {"t": "oi"}})

    def test_acao_desconhecida_vira_erro_e_sessao_continua(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as s:
            autentica(s)
            send_msg(s, make_command("NAO_EXISTE"))
            self.assertEqual(recv_msg(s)["status"], "erro")
            send_msg(s, make_command(Acao.ECHO))
            self.assertEqual(recv_msg(s)["status"], "ok")

    def test_tratador_com_bug_nao_derruba_sessao(self):
        self.server.handlers["BOOM"] = lambda msg: 1 / 0
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as s:
            autentica(s)
            send_msg(s, make_command("BOOM"))
            resp = recv_msg(s)
            self.assertEqual(resp["status"], "erro")
            self.assertIn("ZeroDivisionError", resp["erro"])
            send_msg(s, make_command(Acao.ECHO))
            self.assertEqual(recv_msg(s)["status"], "ok")

    def test_stop_derruba_sessao_ativa(self):  # caminho do kill switch
        with self.conecta() as conn:
            conn.connect()
            conn.send(Acao.ECHO)
            self.assertEqual(self.espera(conn)["acao"], Acao.ECHO)
            self.server.stop()
            self.assertEqual(self.espera(conn)["acao"], EVENTO_DESCONECTADO)


class ConnectionTests(ServerCase):
    def test_sessao_persistente_com_varios_comandos(self):
        with self.conecta() as conn:
            conn.connect()
            ids = [conn.send(Acao.ECHO, {"n": n}) for n in range(20)]
            recebidos = [self.espera(conn) for _ in ids]
            self.assertEqual([m["id"] for m in recebidos], ids)  # casados pelo id
            self.assertEqual(recebidos[7]["resultado"], {"eco": {"n": 7}})

    def test_close_gera_um_unico_evento_de_desconexao(self):
        conn = self.conecta()
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
            Connection("127.0.0.1", livre, secret=SECRET).connect(timeout=1)


class AuthTests(ServerCase):
    auth_timeout = 0.4

    def test_segredo_errado_e_recusado_e_agente_segue_atendendo(self):
        with self.assertRaises(AuthError):
            self.conecta(secret="segredo-errado!").connect()
        with self.conecta() as conn:  # o segredo certo ainda entra
            conn.connect()
            conn.send(Acao.ECHO)
            self.assertEqual(self.espera(conn)["status"], "ok")

    def test_comando_antes_de_autenticar_e_recusado(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as s:
            self.assertEqual(recv_msg(s)["acao"], Acao.DESAFIO)
            send_msg(s, make_command(Acao.ECHO, {"x": 1}))  # pula o AUTH
            resp = recv_msg(s)
            self.assertEqual(resp["status"], "erro")
            self.assertNotIn("resultado", resp)  # o ECHO não foi executado
            self.assertIsNone(recv_msg(s))  # e a conexão foi derrubada

    def test_prova_ausente_ou_de_tipo_errado(self):
        for args in ({}, {"prova": 123}, {"prova": None}, {"prova": "zzz"}):
            with socket.create_connection(("127.0.0.1", self.port), timeout=3) as s:
                recv_msg(s)
                send_msg(s, make_command(Acao.AUTH, args))
                self.assertEqual(recv_msg(s)["status"], "erro")
                self.assertIsNone(recv_msg(s))

    def test_prova_de_outra_conexao_nao_serve(self):  # replay
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as s1:
            nonce1 = recv_msg(s1)["args"]["nonce"]
            send_msg(s1, make_command(Acao.AUTH, {"prova": compute_proof(SECRET, nonce1)}))
            self.assertEqual(recv_msg(s1)["status"], "ok")
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as s2:
            nonce2 = recv_msg(s2)["args"]["nonce"]
            self.assertNotEqual(nonce1, nonce2)
            send_msg(s2, make_command(Acao.AUTH, {"prova": compute_proof(SECRET, nonce1)}))
            self.assertEqual(recv_msg(s2)["status"], "erro")

    def test_conexao_muda_e_derrubada_por_timeout(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as s:
            recv_msg(s)  # recebe o desafio e não responde
            self.assertIsNone(recv_msg(s))  # o agente desistiu (auth_timeout=0.4 s)
        with self.conecta() as conn:  # e o lugar ficou livre
            conn.connect()

    def test_agente_exige_segredo(self):
        with self.assertRaises(ValueError):
            AgentServer("127.0.0.1", 0, secret="")


if __name__ == "__main__":
    unittest.main()
