"""TLS do canal: cifragem, identidade do agente (impressão digital) e política de confiança.

Rodar: PYTHONPATH=src python -m unittest discover -s tests
"""

import inspect
import json
import os
import socket
import ssl
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from cryptography import x509

from agent.server import AgentServer
from controller import controller as cli
from controller.confianca import (
    AgentesConhecidos, IdentidadeMudou, IdentidadeRecusada, VerificadorFixo,
)
from controller.connection import Connection
from shared import fingerprint as fp_mod
from shared.config import AgentConfig, ConfigError, ControllerConfig
from shared.protocol import Acao
from shared.tls import (
    TlsError, carregar_ou_gerar, contexto_cliente, fingerprint_do_arquivo, gerar_certificado,
)
from tls_helper import CONTEXTO, FINGERPRINT, nova_identidade, socket_tls, verificador

SECRET = "segredo-de-teste"
POSIX = os.name != "nt"


def sobe_agente(contexto=CONTEXTO, auth_timeout=2.0):
    """Agente de verdade em thread. Devolve (servidor, thread)."""
    server = AgentServer(
        AgentConfig(host="127.0.0.1", porta=0, auth_timeout=auth_timeout), secret=SECRET, contexto_tls=contexto
    )
    server.open()
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, t


class AgenteCase(unittest.TestCase):
    auth_timeout = 2.0

    def setUp(self):
        self.server, self.thread = sobe_agente(auth_timeout=self.auth_timeout)
        self.porta = self.server.port

    def tearDown(self):
        self.server.stop()
        self.thread.join(5)

    def conecta(self, porta=None, verif=None):
        c = Connection("127.0.0.1", porta or self.porta, secret=SECRET, verificador=verif or verificador())
        c.connect()
        self.addCleanup(c.close)
        return c


class FingerprintTests(unittest.TestCase):
    def test_formato_canonico(self):
        fp = fp_mod.formatar(b"qualquer coisa")
        self.assertRegex(fp, r"^SHA256:([0-9A-F]{2}:){31}[0-9A-F]{2}$")

    def test_normalizar_aceita_as_formas_comuns(self):
        fp = fp_mod.formatar(b"x")
        puro = fp.replace("SHA256:", "").replace(":", "")
        for forma in (fp, fp.lower(), puro, puro.lower(), "sha256:" + puro, "SHA-256 " + puro, f"  {fp}  ",
                      fp.replace(":", " ")):
            self.assertEqual(fp_mod.normalizar(forma), fp, forma)

    def test_normalizar_recusa_o_que_nao_e_um_sha256_inteiro(self):
        puro = fp_mod.formatar(b"x").replace("SHA256:", "").replace(":", "")
        for ruim in ("", "abc", puro[:-2], puro + "00", "Z" * 64, puro[:-1] + "G", None, 123, b"x"):
            with self.assertRaises(ValueError, msg=repr(ruim)):
                fp_mod.normalizar(ruim)


class CertificadoTests(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = Path(self._t.name)
        self.addCleanup(self._t.cleanup)

    def test_gera_reutiliza_e_nao_muda_a_identidade(self):
        _, fp1, novo1 = carregar_ou_gerar(self.d / "a.crt", self.d / "a.key")
        _, fp2, novo2 = carregar_ou_gerar(self.d / "a.crt", self.d / "a.key")
        self.assertEqual((novo1, novo2, fp1 == fp2), (True, False, True))
        self.assertEqual(fingerprint_do_arquivo(self.d / "a.crt"), fp1)

    def test_dois_certificados_gerados_tem_identidades_diferentes(self):
        a = gerar_certificado(self.d / "a.crt", self.d / "a.key")
        b = gerar_certificado(self.d / "b.crt", self.d / "b.key")
        self.assertNotEqual(a, b)

    @unittest.skipUnless(POSIX, "permissões POSIX")
    def test_chave_privada_so_o_dono_le(self):
        carregar_ou_gerar(self.d / "a.crt", self.d / "a.key")
        self.assertEqual((self.d / "a.key").stat().st_mode & 0o777, 0o600)

    def test_certificado_tem_os_nomes_pedidos_e_nao_e_uma_ca(self):
        gerar_certificado(self.d / "a.crt", self.d / "a.key", ["10.0.0.9", "meu-agente.lab"])
        cert = x509.load_pem_x509_certificate((self.d / "a.crt").read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        self.assertIn("10.0.0.9", [str(i) for i in san.get_values_for_type(x509.IPAddress)])
        self.assertIn("meu-agente.lab", san.get_values_for_type(x509.DNSName))
        self.assertFalse(cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)

    def test_so_um_dos_arquivos_existe_e_erro_e_nada_e_sobrescrito(self):
        (self.d / "a.crt").write_text("conteudo antigo")
        with self.assertRaisesRegex(TlsError, "só um dos arquivos"):
            carregar_ou_gerar(self.d / "a.crt", self.d / "a.key")
        self.assertEqual((self.d / "a.crt").read_text(), "conteudo antigo")
        self.assertFalse((self.d / "a.key").exists())

    def test_arquivos_invalidos_sao_erro_claro(self):
        (self.d / "a.crt").write_text("isto não é um certificado")
        (self.d / "a.key").write_text("nem uma chave")
        with self.assertRaises(TlsError):
            carregar_ou_gerar(self.d / "a.crt", self.d / "a.key")

    def test_contexto_do_servidor_exige_tls_1_3(self):
        self.assertEqual(CONTEXTO.minimum_version, ssl.TLSVersion.TLSv1_3)
        self.assertEqual(contexto_cliente().minimum_version, ssl.TLSVersion.TLSv1_3)


class CanalTests(AgenteCase):
    def test_a_sessao_roda_em_tls_1_3_com_cifra_autenticada(self):
        c = self.conecta()
        self.assertEqual(c._sock.version(), "TLSv1.3")
        self.assertRegex(c._sock.cipher()[0], r"^TLS_(AES_(128|256)_GCM|CHACHA20_POLY1305)")
        self.assertEqual(c.request(Acao.ECHO, {"n": 1})["resultado"]["eco"], {"n": 1})

    def test_o_que_passa_pela_rede_nao_contem_nada_legivel(self):
        relay = Relay(self.porta)
        self.addCleanup(relay.parar)
        marcador = "PAYLOAD-INCONFUNDIVEL-98765"
        c = Connection("127.0.0.1", relay.porta, secret=SECRET, verificador=verificador())
        c.connect()
        self.addCleanup(c.close)
        c.request(Acao.ECHO, {"texto": marcador})
        c.request(Acao.EXEC, {"cmd": "echo comando-secreto"})
        c.close()
        time.sleep(0.2)
        visto = relay.capturado()
        self.assertGreater(len(visto), 500)  # trafegou bastante coisa...
        for legivel in (marcador, "comando-secreto", SECRET, "ECHO", "EXEC", "AUTH", "acao", "prova", "nonce",
                        "DESAFIO", "resultado"):
            self.assertNotIn(legivel.encode(), visto, f"'{legivel}' passou em claro pela rede")
        self.assertEqual(visto[:2], b"\x16\x03")  # ...e começa como um ClientHello TLS

    def test_cliente_em_texto_claro_e_recusado_e_o_agente_segue(self):
        with self.assertLogs("agente", "WARNING") as logs:
            with socket.create_connection(("127.0.0.1", self.porta), timeout=3) as s:
                s.sendall(b"\x00\x00\x00\x05hello")
                s.settimeout(3)
                try:
                    resposta = s.recv(100)
                except OSError:
                    resposta = b""
                self.assertNotIn(b"DESAFIO", resposta)  # o agente nem chegou a falar o protocolo
            time.sleep(0.2)
        self.assertTrue(any("handshake TLS falhou" in l for l in logs.output))
        self.assertEqual(self.conecta().request(Acao.ECHO)["status"], "ok")  # e continua atendendo

    def test_cliente_tls_1_2_e_recusado(self):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        cru = socket.create_connection(("127.0.0.1", self.porta), timeout=3)
        with self.assertRaises(ssl.SSLError):
            ctx.wrap_socket(cru, server_hostname="127.0.0.1")
        cru.close()
        self.assertEqual(self.conecta().request(Acao.ECHO)["status"], "ok")


class ClienteMudoTests(AgenteCase):
    auth_timeout = 0.5

    def test_cliente_que_conecta_e_cala_nao_trava_o_agente(self):
        mudo = socket.create_connection(("127.0.0.1", self.porta), timeout=3)
        self.addCleanup(mudo.close)
        time.sleep(0.9)  # passou do prazo do handshake
        mudo.settimeout(2)
        self.assertEqual(mudo.recv(10), b"")  # o agente o derrubou
        inicio = time.monotonic()
        self.assertEqual(self.conecta().request(Acao.ECHO)["status"], "ok")
        self.assertLess(time.monotonic() - inicio, 2)


class AgenteFalsoTests(AgenteCase):
    def test_agente_com_outro_certificado_e_recusado_sem_enviar_nada(self):
        """Quem se passa pelo agente (certificado diferente) é pego, e nenhum dado vai até ele."""
        ctx_falso, fp_falso = nova_identidade("impostor")
        self.assertNotEqual(fp_falso, FINGERPRINT)
        recebido = []

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        self.addCleanup(sock.close)

        def impostor():
            try:
                c, _ = sock.accept()
                tls = ctx_falso.wrap_socket(c, server_side=True)
                tls.settimeout(1.5)
                try:
                    recebido.append(tls.recv(4096))  # o que o cliente mandar depois do handshake
                except (OSError, ssl.SSLError):
                    recebido.append(b"")
                tls.close()
            except OSError:
                recebido.append(b"<falhou>")

        t = threading.Thread(target=impostor, daemon=True)
        t.start()
        c = Connection("127.0.0.1", sock.getsockname()[1], secret=SECRET, verificador=verificador())
        with self.assertRaises(IdentidadeMudou) as cm:
            c.connect()
        t.join(3)
        self.assertIn(fp_falso, str(cm.exception))
        self.assertIn(FINGERPRINT, str(cm.exception))
        self.assertEqual(recebido, [b""], "o controlador enviou dados a um agente não verificado")
        self.assertFalse(c.connected)

    def test_identidade_correta_passa(self):
        self.assertTrue(self.conecta().connected)


class VerificadorFixoTests(unittest.TestCase):
    def test_aceita_a_mesma_em_qualquer_formato_e_recusa_outra(self):
        fp = fp_mod.formatar(b"agente")
        for forma in (fp, fp.lower(), fp.replace("SHA256:", "").replace(":", "")):
            VerificadorFixo(forma).verificar("h", 1, fp)
        with self.assertRaises(IdentidadeMudou):
            VerificadorFixo(fp).verificar("h", 1, fp_mod.formatar(b"outro"))

    def test_fingerprint_malformado_na_criacao_e_erro(self):
        with self.assertRaises(ValueError):
            VerificadorFixo("abc")


class AgentesConhecidosTests(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.arq = Path(self._t.name) / "conhecidos.json"
        self.addCleanup(self._t.cleanup)
        self.A, self.B = fp_mod.formatar(b"A"), fp_mod.formatar(b"B")
        self.perguntas = []

    def verif(self, resposta=True):
        def perguntar(host, porta, fp):
            self.perguntas.append((host, porta, fp))
            return resposta
        return AgentesConhecidos(self.arq, perguntar)

    def test_desconhecido_confirmado_e_salvo_e_nao_pergunta_de_novo(self):
        v = self.verif(True)
        v.verificar("10.0.0.5", 5055, self.A)
        v.verificar("10.0.0.5", 5055, self.A)
        self.assertEqual(self.perguntas, [("10.0.0.5", 5055, self.A)])  # só a 1ª vez
        self.assertEqual(json.loads(self.arq.read_text()), {"10.0.0.5:5055": self.A})
        self.assertFalse(list(self.arq.parent.glob("*.tmp")))

    def test_desconhecido_recusado_nao_grava_nada(self):
        with self.assertRaises(IdentidadeRecusada):
            self.verif(False).verificar("10.0.0.5", 5055, self.A)
        self.assertFalse(self.arq.exists())

    def test_sem_como_perguntar_o_desconhecido_e_sempre_recusado(self):
        with self.assertRaises(IdentidadeRecusada):
            AgentesConhecidos(self.arq).verificar("10.0.0.5", 5055, self.A)
        self.assertFalse(self.arq.exists())

    def test_identidade_que_mudou_e_recusada_SEM_perguntar(self):
        self.verif(True).verificar("10.0.0.5", 5055, self.A)
        self.perguntas.clear()
        with self.assertRaises(IdentidadeMudou):
            self.verif(True).verificar("10.0.0.5", 5055, self.B)  # mesmo que o operador "aceitasse"
        self.assertEqual(self.perguntas, [])
        self.assertEqual(json.loads(self.arq.read_text())["10.0.0.5:5055"], self.A)  # não foi trocada

    def test_cada_host_e_porta_e_uma_entrada_e_o_host_ignora_caixa(self):
        v = self.verif(True)
        v.verificar("Agente.Lab", 5055, self.A)
        v.verificar("agente.lab", 5055, self.A)  # mesma entrada
        v.verificar("agente.lab", 6000, self.B)  # outra porta, outra entrada
        self.assertEqual(len(self.perguntas), 2)

    def test_arquivo_corrompido_e_erro_e_nao_e_reiniciado_em_silencio(self):
        for conteudo in ("{ isto não é json", "[1, 2]", '{"a": 5}'):
            self.arq.write_text(conteudo)
            with self.assertRaisesRegex(IdentidadeRecusada, "inválido"):
                self.verif(True).verificar("h", 1, self.A)
            self.assertEqual(self.arq.read_text(), conteudo)  # intacto: nada foi apagado


class ConhecidosViaConexaoTests(AgenteCase):
    def test_primeira_conexao_pergunta_e_a_segunda_nao(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        perguntas = []
        verif = AgentesConhecidos(Path(t.name) / "k.json", lambda h, p, fp: perguntas.append(fp) or True)
        self.conecta(verif=verif).close()
        time.sleep(0.2)
        self.conecta(verif=verif).close()
        self.assertEqual(perguntas, [FINGERPRINT])

    def test_agente_conhecido_com_outra_identidade_e_recusado(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        arq = Path(t.name) / "k.json"
        arq.write_text(json.dumps({f"127.0.0.1:{self.porta}": fp_mod.formatar(b"o de antes")}))
        with self.assertRaises(IdentidadeMudou):
            Connection("127.0.0.1", self.porta, secret=SECRET, verificador=AgentesConhecidos(arq)).connect()


class Relay:
    """Retransmissor TCP que guarda tudo que passa (para provar que o tráfego está cifrado)."""

    def __init__(self, destino):
        self._destino, self._bytes, self._lock = destino, bytearray(), threading.Lock()
        self._srv = socket.socket()
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.porta = self._srv.getsockname()[1]
        self._socks = [self._srv]
        threading.Thread(target=self._aceitar, daemon=True).start()

    def _aceitar(self):
        try:
            cliente, _ = self._srv.accept()
            servidor = socket.create_connection(("127.0.0.1", self._destino))
        except OSError:
            return
        self._socks += [cliente, servidor]
        for a, b in ((cliente, servidor), (servidor, cliente)):
            threading.Thread(target=self._copiar, args=(a, b), daemon=True).start()

    def _copiar(self, origem, destino):
        try:
            while dados := origem.recv(65536):
                with self._lock:
                    self._bytes += dados
                destino.sendall(dados)
        except OSError:
            pass

    def capturado(self):
        with self._lock:
            return bytes(self._bytes)

    def parar(self):
        for s in self._socks:
            try:
                s.close()
            except OSError:
                pass


class ConfiguracaoTests(unittest.TestCase):
    def test_campos_novos_e_validacao(self):
        self.assertTrue(AgentConfig().certificado.endswith(".crt") and AgentConfig().chave.endswith(".key"))
        self.assertEqual(ControllerConfig().fingerprint_agente, None)
        self.assertEqual(ControllerConfig(fingerprint_agente=FINGERPRINT).fingerprint_agente, FINGERPRINT)
        for ruim in ("abc", "SHA256:AB:CD", "Z" * 64):
            with self.assertRaisesRegex(ConfigError, "fingerprint_agente"):
                ControllerConfig(fingerprint_agente=ruim)

    def test_a_chave_privada_nunca_vai_para_o_exemplo_versionado(self):
        exemplo = (Path(__file__).resolve().parent.parent / "config.example.json").read_text()
        self.assertIn('"certificado"', exemplo)  # o caminho sim; o conteúdo da chave jamais
        self.assertNotIn("BEGIN", exemplo)

    def test_git_ignora_chave_certificado_e_agentes_conhecidos(self):
        ign = (Path(__file__).resolve().parent.parent / ".gitignore").read_text().split()
        for item in ("certs/", "agentes_conhecidos.json"):
            self.assertIn(item, ign)


class LinhaDeComandoTests(unittest.TestCase):
    def test_flag_fingerprint_e_validada_antes_de_conectar(self):
        c = cli._config_da_linha_de_comando(["--fingerprint", FINGERPRINT])
        self.assertEqual(c.fingerprint_agente, FINGERPRINT)
        with self.assertRaises(SystemExit) as cm:
            cli._config_da_linha_de_comando(["--fingerprint", "abc"])
        self.assertIn("configuração inválida", str(cm.exception.code))

    def test_com_fingerprint_so_aquele_agente_e_aceito_sem_perguntar(self):
        self.assertIsInstance(cli.criar_verificador(ControllerConfig(fingerprint_agente=FINGERPRINT)), VerificadorFixo)
        self.assertIsInstance(cli.criar_verificador(ControllerConfig()), AgentesConhecidos)

    def test_pergunta_so_confirma_com_s_ou_sim(self):
        def responde(texto):
            with mock.patch("builtins.input", return_value=texto), mock.patch("builtins.print"):
                return cli._perguntar_no_terminal("h", 1, FINGERPRINT)
        for sim in ("s", "S", "sim", " Sim "):
            self.assertTrue(responde(sim), sim)
        for nao in ("", "n", "nao", "não", "talvez", "y", "yes", "ss"):
            self.assertFalse(responde(nao), nao)
        with mock.patch("builtins.input", side_effect=EOFError), mock.patch("builtins.print"):
            self.assertFalse(cli._perguntar_no_terminal("h", 1, FINGERPRINT))  # sem terminal = não

    def test_a_pergunta_mostra_a_impressao_digital_e_manda_conferir_na_janela_do_agente(self):
        saida = []
        with mock.patch("builtins.input", return_value="n"), mock.patch("builtins.print", side_effect=lambda *a, **k: saida.append(" ".join(map(str, a)))):
            cli._perguntar_no_terminal("10.0.0.5", 5055, FINGERPRINT)
        texto = "\n".join(saida)
        for trecho in ("10.0.0.5:5055", FINGERPRINT, "janela do agente"):
            self.assertIn(trecho, texto)


class SemModoInseguroTests(unittest.TestCase):
    """Garantias estruturais: não há como rodar o canal sem cifragem nem sem verificar o agente."""

    def test_servidor_exige_contexto_tls_e_conexao_exige_verificador(self):
        p = inspect.signature(AgentServer.__init__).parameters["contexto_tls"]
        self.assertIs(p.default, inspect.Parameter.empty)
        v = inspect.signature(Connection.__init__).parameters["verificador"]
        self.assertIs(v.default, inspect.Parameter.empty)
        self.assertEqual(v.kind, inspect.Parameter.KEYWORD_ONLY)

    def test_verificacao_desligada_do_cliente_fica_confinada_em_tls_py(self):
        # CERT_NONE é aceitável SÓ porque a impressão digital é conferida logo depois. Por isso
        # não pode aparecer em nenhum outro lugar, onde ninguém lembraria de conferir.
        raiz = Path(__file__).resolve().parent.parent / "src"
        for arq in raiz.rglob("*.py"):
            if arq.name == "tls.py":
                continue
            self.assertNotIn("CERT_NONE", arq.read_text(), str(arq))
            self.assertNotIn("check_hostname", arq.read_text(), str(arq))


if __name__ == "__main__":
    unittest.main()
