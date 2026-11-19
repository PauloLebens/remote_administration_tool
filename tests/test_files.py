"""LS, GET, PUT e transferência. Rodar: PYTHONPATH=src python -m unittest discover -s tests"""

import base64
import hashlib
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agent.handlers.files_handler import FileHandlers
from agent.server import AgentServer
from controller.connection import Connection
from controller.controller import executar
from shared.config import AgentConfig, ControllerConfig
from controller.transfer import IntegrityError, TransferError, download, upload
from shared.protocol import CHUNK_SIZE, Acao, make_command

POSIX = os.name != "nt"
SECRET = "segredo-de-teste"


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def msg(acao, **args):
    return make_command(acao, args)


class Pasta(unittest.TestCase):
    """Cada teste ganha uma pasta temporária (self.dir) e um FileHandlers."""

    raiz = None  # subclasses podem restringir

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name).resolve()
        self.fh = FileHandlers.de_config(AgentConfig(raiz=str(self.dir) if self.raiz else None))

    def tearDown(self):
        self._tmp.cleanup()

    def arq(self, nome, conteudo=b"x"):
        p = self.dir / nome
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(conteudo)
        return p


class LsTests(Pasta):
    def test_lista_com_atributos_dirs_primeiro(self):
        self.arq("b.txt", b"12345")
        self.arq("A.txt")
        (self.dir / "zdir").mkdir()
        r = self.fh.ls(msg(Acao.LS, caminho=str(self.dir)))
        self.assertEqual([e["nome"] for e in r["entradas"]], ["zdir", "A.txt", "b.txt"])
        b = next(e for e in r["entradas"] if e["nome"] == "b.txt")
        self.assertEqual((b["tipo"], b["tamanho"]), ("arquivo", 5))
        self.assertRegex(b["modificado"], r"^\d{4}-\d\d-\d\dT")
        self.assertTrue(b["permissoes"])
        self.assertTrue(r["e_diretorio"])
        self.assertEqual(r["pai"], str(self.dir.parent))

    def test_ls_de_um_arquivo_mostra_os_atributos_dele(self):
        p = self.arq("um.txt", b"abc")
        r = self.fh.ls(msg(Acao.LS, caminho=str(p)))
        self.assertFalse(r["e_diretorio"])
        self.assertEqual(r["entradas"][0]["tamanho"], 3)

    def test_inexistente_e_erro(self):
        with self.assertRaises(FileNotFoundError):
            self.fh.ls(msg(Acao.LS, caminho=str(self.dir / "nao_existe")))

    def test_listagem_enorme_e_cortada(self):
        for i in range(5):
            self.arq(f"f{i}")
        fh = FileHandlers.de_config(AgentConfig(ls_max_entradas=3))  # limite vem da configuração
        r = fh.ls(msg(Acao.LS, caminho=str(self.dir)))
        self.assertEqual((len(r["entradas"]), r["truncado"]), (3, True))

    @unittest.skipUnless(POSIX, "links simbólicos")
    def test_link_simbolico_e_link_quebrado(self):
        alvo = self.arq("alvo.txt")
        os.symlink(alvo, self.dir / "link")
        os.symlink(self.dir / "sumiu", self.dir / "quebrado")
        r = self.fh.ls(msg(Acao.LS, caminho=str(self.dir)))
        por_nome = {e["nome"]: e for e in r["entradas"]}
        self.assertEqual(por_nome["link"]["tipo"], "link")
        self.assertEqual(por_nome["link"]["link_para"], str(alvo))
        self.assertEqual(por_nome["quebrado"]["tipo"], "link")  # listado sem derrubar o LS


class GetTests(Pasta):
    def test_blocos_cobrem_o_arquivo_e_eof(self):
        dados = os.urandom(250)
        p = self.arq("f.bin", dados)
        juntos, offset = b"", 0
        while True:
            r = self.fh.get(msg(Acao.GET, caminho=str(p), offset=offset, tamanho=100))
            bloco = base64.b64decode(r["dados"])
            self.assertEqual((r["offset"], r["total"], r["tamanho"]), (offset, 250, len(bloco)))
            juntos += bloco
            offset += len(bloco)
            if r["eof"]:
                break
        self.assertEqual(juntos, dados)
        self.assertEqual(offset, 250)

    def test_sha256_so_quando_pedido(self):
        p = self.arq("f.bin", b"conteudo")
        self.assertNotIn("sha256", self.fh.get(msg(Acao.GET, caminho=str(p))))
        self.assertEqual(self.fh.get(msg(Acao.GET, caminho=str(p), sha256=True))["sha256"], sha(b"conteudo"))

    def test_arquivo_vazio(self):
        r = self.fh.get(msg(Acao.GET, caminho=str(self.arq("v", b"")), sha256=True))
        self.assertEqual((r["dados"], r["eof"], r["sha256"]), ("", True, sha(b"")))

    def test_tamanho_pedido_e_limitado_ao_bloco_maximo(self):
        p = self.arq("grande", b"a" * (CHUNK_SIZE * 2))
        r = self.fh.get(msg(Acao.GET, caminho=str(p), tamanho=10**9))
        self.assertEqual(r["tamanho"], CHUNK_SIZE)
        self.assertFalse(r["eof"])

    def test_erros(self):
        p = self.arq("f", b"abc")
        with self.assertRaises(IsADirectoryError):
            self.fh.get(msg(Acao.GET, caminho=str(self.dir)))
        with self.assertRaises(FileNotFoundError):
            self.fh.get(msg(Acao.GET, caminho=str(self.dir / "nada")))
        with self.assertRaises(ValueError):
            self.fh.get(msg(Acao.GET, caminho=str(p), offset=4))  # além do fim
        for ruim in ({"offset": -1}, {"offset": "0"}, {"offset": True}, {"tamanho": 0}):
            with self.assertRaises(ValueError, msg=ruim):
                self.fh.get(msg(Acao.GET, caminho=str(p), **ruim))
        for caminho in ("", "  ", 5, None):
            with self.assertRaises(ValueError, msg=caminho):
                self.fh.get(msg(Acao.GET, caminho=caminho))

    @unittest.skipUnless(POSIX, "FIFO")
    def test_fifo_e_recusado_sem_bloquear(self):
        fifo = self.dir / "fifo"
        os.mkfifo(fifo)
        with self.assertRaises(ValueError):
            self.fh.get(msg(Acao.GET, caminho=str(fifo)))


class PutTests(Pasta):
    def put(self, nome, dados, offset=0, **kw):
        return self.fh.put(msg(Acao.PUT, caminho=str(self.dir / nome), offset=offset, dados=b64(dados), **kw))

    def test_blocos_montam_o_arquivo_so_depois_do_hash(self):
        a, b, c = b"aaaa", b"bbbb", b"cc"
        total = a + b + c
        self.assertEqual(self.put("o.bin", a)["recebido"], 4)
        self.assertFalse((self.dir / "o.bin").exists())  # ainda é só .parte
        self.assertTrue((self.dir / "o.bin.parte").exists())
        self.assertEqual(self.put("o.bin", b, offset=4)["recebido"], 8)
        r = self.put("o.bin", c, offset=8, final=True, sha256=sha(total))
        self.assertEqual((r["tamanho"], r["sha256"]), (10, sha(total)))
        self.assertEqual((self.dir / "o.bin").read_bytes(), total)
        self.assertFalse((self.dir / "o.bin.parte").exists())

    def test_hash_errado_nao_cria_o_destino_e_apaga_a_parte(self):
        with self.assertRaisesRegex(ValueError, "integridade"):
            self.put("o.bin", b"dados", final=True, sha256=sha(b"outra coisa"))
        self.assertFalse((self.dir / "o.bin").exists())
        self.assertFalse((self.dir / "o.bin.parte").exists())

    def test_hash_hexadecimal_maiusculo_e_aceito(self):
        self.put("o.bin", b"x", final=True, sha256=sha(b"x").upper())
        self.assertTrue((self.dir / "o.bin").exists())

    def test_bloco_final_sem_hash_e_recusado(self):
        for kw in ({}, {"sha256": "curto"}, {"sha256": 123}):
            with self.assertRaises(ValueError, msg=kw):
                self.put("o.bin", b"x", final=True, **kw)
            self.assertFalse((self.dir / "o.bin").exists())

    def test_nao_sobrescreve_sem_pedir(self):
        existente = self.arq("o.bin", b"original")
        with self.assertRaises(FileExistsError):
            self.put("o.bin", b"novo", final=True, sha256=sha(b"novo"))
        self.assertEqual(existente.read_bytes(), b"original")
        self.put("o.bin", b"novo", final=True, sha256=sha(b"novo"), sobrescrever=True)
        self.assertEqual(existente.read_bytes(), b"novo")

    def test_offset_precisa_continuar_exatamente_de_onde_parou(self):
        self.put("o.bin", b"aaaa")
        for offset in (3, 5, 100):
            with self.assertRaisesRegex(ValueError, "offset inesperado"):
                self.put("o.bin", b"bb", offset=offset)
        with self.assertRaisesRegex(ValueError, "offset inesperado"):  # sem parte nenhuma
            self.put("outro.bin", b"bb", offset=4)

    def test_recomecar_do_zero_descarta_a_parte_anterior(self):
        self.put("o.bin", b"lixo antigo")
        self.put("o.bin", b"novo", final=True, sha256=sha(b"novo"))
        self.assertEqual((self.dir / "o.bin").read_bytes(), b"novo")

    def test_entradas_invalidas(self):
        with self.assertRaisesRegex(ValueError, "base64"):
            self.fh.put(msg(Acao.PUT, caminho=str(self.dir / "o"), dados="isso não é base64!!"))
        with self.assertRaises(ValueError):
            self.fh.put(msg(Acao.PUT, caminho=str(self.dir / "o"), dados=123))
        with self.assertRaisesRegex(ValueError, "excede"):
            self.put("o", b"a" * (CHUNK_SIZE + 1))
        with self.assertRaises(IsADirectoryError):
            self.fh.put(msg(Acao.PUT, caminho=str(self.dir), dados=""))
        with self.assertRaises(FileNotFoundError):
            self.put("sem_pasta/o.bin", b"x")

    def test_arquivo_vazio(self):
        self.put("v", b"", final=True, sha256=sha(b""))
        self.assertEqual((self.dir / "v").read_bytes(), b"")


class RaizTests(Pasta):
    raiz = True

    def test_dentro_da_raiz_funciona_e_relativo_e_relativo_a_raiz(self):
        self.arq("sub/f.txt", b"ok")
        r = self.fh.get(msg(Acao.GET, caminho="sub/f.txt"))
        self.assertEqual(base64.b64decode(r["dados"]), b"ok")
        self.assertEqual(self.fh.ls(msg(Acao.LS))["caminho"], str(self.dir))  # padrão = a raiz

    def test_nao_escapa_nem_por_ponto_ponto_nem_absoluto(self):
        fora = self.dir.parent / "fora_da_raiz.txt"
        for caminho in ("../fora_da_raiz.txt", "sub/../../fora_da_raiz.txt", str(fora), "/etc/hosts"):
            for acao, fn in ((Acao.GET, self.fh.get), (Acao.LS, self.fh.ls)):
                with self.assertRaises(PermissionError, msg=(acao, caminho)):
                    fn(msg(acao, caminho=caminho))
        with self.assertRaises(PermissionError):
            self.fh.put(msg(Acao.PUT, caminho="../x.bin", dados=""))

    @unittest.skipUnless(POSIX, "links simbólicos")
    def test_link_para_fora_nao_serve_de_atalho(self):
        fora = tempfile.mkdtemp()
        try:
            Path(fora, "segredo.txt").write_text("segredo")
            os.symlink(fora, self.dir / "atalho")
            with self.assertRaises(PermissionError):
                self.fh.get(msg(Acao.GET, caminho="atalho/segredo.txt"))
            with self.assertRaises(PermissionError):
                self.fh.ls(msg(Acao.LS, caminho="atalho"))
        finally:
            import shutil
            shutil.rmtree(fora)

    def test_nao_navega_acima_da_raiz(self):
        self.assertIsNone(self.fh.ls(msg(Acao.LS))["pai"])
        (self.dir / "sub").mkdir()
        self.assertEqual(self.fh.ls(msg(Acao.LS, caminho="sub"))["pai"], str(self.dir))

    def test_prefixo_parecido_nao_conta_como_dentro(self):
        irmao = Path(str(self.dir) + "_irmao")
        irmao.mkdir()
        try:
            with self.assertRaises(PermissionError):
                self.fh.ls(msg(Acao.LS, caminho=str(irmao)))
        finally:
            irmao.rmdir()


class ViaRede(unittest.TestCase):
    """Controlador -> agente de verdade: handshake, framing, blocos e hash."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name).resolve()
        self.agente = self.dir / "agente"
        self.local = self.dir / "controlador"
        self.agente.mkdir()
        self.local.mkdir()
        self.server = AgentServer(AgentConfig(host="127.0.0.1", porta=0, raiz=str(self.agente)), secret=SECRET)
        self.server.open()
        self.t = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.t.start()
        self.conn = Connection("127.0.0.1", self.server.port, secret=SECRET)
        self.conn.connect()

    def tearDown(self):
        self.conn.close()
        self.server.stop()
        self.t.join(3)
        self._tmp.cleanup()

    def test_ida_e_volta_de_arquivo_com_varios_blocos(self):
        dados = os.urandom(CHUNK_SIZE * 3 + 123)  # 4 blocos, o último quebrado
        origem = self.local / "origem.bin"
        origem.write_bytes(dados)
        progresso = []
        r = upload(self.conn, origem, "enviado.bin", progresso=lambda f, t: progresso.append((f, t)))
        self.assertEqual((self.agente / "enviado.bin").read_bytes(), dados)
        self.assertEqual((r["tamanho"], r["sha256"]), (len(dados), sha(dados)))
        self.assertEqual(progresso[-1], (len(dados), len(dados)))
        self.assertEqual(len(progresso), 4)

        r = download(self.conn, "enviado.bin", self.local / "baixado.bin")
        self.assertEqual((self.local / "baixado.bin").read_bytes(), dados)
        self.assertEqual(r["sha256"], sha(dados))
        self.assertFalse(list(self.local.glob("*.parte")))

    def test_arquivo_vazio_e_arquivo_de_um_bloco_exato(self):
        for nome, dados in (("vazio", b""), ("exato", os.urandom(CHUNK_SIZE))):
            origem = self.local / nome
            origem.write_bytes(dados)
            upload(self.conn, origem, nome)
            download(self.conn, nome, self.local / (nome + ".volta"))
            self.assertEqual((self.local / (nome + ".volta")).read_bytes(), dados, nome)

    def test_download_corrompido_na_rede_e_detectado_e_nao_deixa_arquivo(self):
        (self.agente / "f.bin").write_bytes(os.urandom(CHUNK_SIZE + 10))
        real = self.server.handlers[Acao.GET]

        def adultera(m):  # simula corrupção no caminho: troca um byte do 2º bloco
            r = real(m)
            if r["offset"] > 0:
                d = bytearray(base64.b64decode(r["dados"]))
                d[0] ^= 0xFF
                r["dados"] = b64(bytes(d))
            return r

        self.server.handlers[Acao.GET] = adultera
        with self.assertRaises(IntegrityError):
            download(self.conn, "f.bin", self.local / "f.bin")
        self.assertFalse((self.local / "f.bin").exists())
        self.assertFalse((self.local / "f.bin.parte").exists())

    def test_upload_corrompido_na_rede_e_recusado_pelo_agente(self):
        origem = self.local / "o.bin"
        origem.write_bytes(os.urandom(CHUNK_SIZE + 10))
        real = self.server.handlers[Acao.PUT]

        def adultera(m):
            a = m["args"]
            if a["offset"] == 0:
                d = bytearray(base64.b64decode(a["dados"]))
                d[0] ^= 0xFF
                a["dados"] = b64(bytes(d))
            return real(m)

        self.server.handlers[Acao.PUT] = adultera
        with self.assertRaisesRegex(TransferError, "integridade"):
            upload(self.conn, origem, "o.bin")
        self.assertFalse((self.agente / "o.bin").exists())

    def test_arquivo_que_muda_no_meio_do_download_e_detectado(self):
        (self.agente / "f.bin").write_bytes(b"a" * (CHUNK_SIZE * 2))
        real = self.server.handlers[Acao.GET]

        def muda(m):
            r = real(m)
            if r["offset"] == 0:  # depois do 1º bloco, o arquivo é substituído
                (self.agente / "f.bin").write_bytes(b"b" * (CHUNK_SIZE * 2))
            return r

        self.server.handlers[Acao.GET] = muda
        with self.assertRaises(IntegrityError):
            download(self.conn, "f.bin", self.local / "f.bin")
        self.assertFalse((self.local / "f.bin").exists())

    def test_erro_do_agente_vira_transfererror_e_nao_deixa_parte(self):
        with self.assertRaisesRegex(TransferError, "não encontrado"):
            download(self.conn, "nao_existe.bin", self.local / "x.bin")
        self.assertFalse(list(self.local.glob("x.bin*")))
        with self.assertRaisesRegex(TransferError, "fora da raiz"):
            download(self.conn, "../../etc/hosts", self.local / "hosts")

    def test_download_nao_sobrescreve_sem_pedir(self):
        (self.agente / "f").write_bytes(b"remoto")
        (self.local / "f").write_bytes(b"local")
        with self.assertRaises(FileExistsError):
            download(self.conn, "f", self.local / "f")
        self.assertEqual((self.local / "f").read_bytes(), b"local")
        download(self.conn, "f", self.local / "f", sobrescrever=True)
        self.assertEqual((self.local / "f").read_bytes(), b"remoto")

    def test_comandos_da_cli(self):
        (self.agente / "oi.txt").write_bytes(b"ola")
        self.assertIn("oi.txt", executar(self.conn, "ls"))
        self.assertIn("3 B", executar(self.conn, "ls oi.txt"))
        self.assertIn("via-cli", executar(self.conn, "exec echo via-cli"))
        self.assertIn("núcleos", executar(self.conn, "info"))
        self.assertIn("comando desconhecido", executar(self.conn, "xyz"))
        self.assertIn("uso:", executar(self.conn, "get"))
        os.chdir(self.local)
        try:
            self.assertIn("conferido", executar(self.conn, "get oi.txt"))
            self.assertEqual((self.local / "oi.txt").read_bytes(), b"ola")
            (self.local / "novo.txt").write_bytes(b"novo")
            self.assertIn("conferido", executar(self.conn, "put novo.txt"))
            self.assertEqual((self.agente / "novo.txt").read_bytes(), b"novo")
        finally:
            os.chdir("/")


    def test_get_sem_destino_usa_a_pasta_de_downloads_da_config(self):
        (self.agente / "oi.txt").write_bytes(b"ola")
        pasta = self.local / "baixados" / "fundo"  # ainda não existe: deve ser criada
        cfg = ControllerConfig(pasta_downloads=str(pasta))
        self.assertIn("conferido", executar(self.conn, "get oi.txt", config=cfg))
        self.assertEqual((pasta / "oi.txt").read_bytes(), b"ola")


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.server = AgentServer(AgentConfig(host="127.0.0.1", porta=0), secret=SECRET)
        self.server.handlers["LENTO"] = lambda m: time.sleep(m["args"]["s"]) or "pronto"
        self.server.open()
        self.t = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.t.start()
        self.conn = Connection("127.0.0.1", self.server.port, secret=SECRET)
        self.conn.connect()

    def tearDown(self):
        self.conn.close()
        self.server.stop()
        self.t.join(5)

    def test_request_nao_rouba_nem_perde_mensagens_da_fila(self):
        i = self.conn.send(Acao.ECHO, {"n": 1})  # assíncrono: vai para incoming
        r = self.conn.request(Acao.ECHO, {"n": 2})  # síncrono: volta direto
        self.assertEqual(r["resultado"]["eco"], {"n": 2})
        self.assertEqual(self.conn.incoming.get(timeout=3)["id"], i)
        self.assertTrue(self.conn.incoming.empty())

    def test_requests_de_varias_threads_recebem_a_resposta_certa(self):
        resultados, erros = {}, []

        def trabalha(n):
            try:
                resultados[n] = self.conn.request(Acao.ECHO, {"n": n})["resultado"]["eco"]["n"]
            except Exception as exc:  # pragma: no cover
                erros.append(exc)

        ts = [threading.Thread(target=trabalha, args=(n,)) for n in range(30)]
        [t.start() for t in ts]
        [t.join(10) for t in ts]
        self.assertEqual(erros, [])
        self.assertEqual(resultados, {n: n for n in range(30)})

    def test_timeout(self):
        with self.assertRaises(TimeoutError):
            self.conn.request("LENTO", {"s": 0.6}, timeout=0.2)
        time.sleep(0.8)  # a resposta tardia cai na fila e não quebra nada
        self.assertEqual(self.conn.request(Acao.ECHO)["status"], "ok")

    def test_timeout_padrao_vem_da_config(self):
        self.conn.close()  # o agente atende um controlador por vez: libera o lugar
        with Connection("127.0.0.1", self.server.port, secret=SECRET,
                        config=ControllerConfig(timeout_requisicao=0.2)) as c:
            c.connect()
            with self.assertRaises(TimeoutError):
                c.request("LENTO", {"s": 0.6})  # sem timeout: vale o da configuração
            time.sleep(0.6)  # deixa a resposta tardia chegar antes do fechamento

    def test_conexao_cai_com_request_pendente(self):
        resultado = []

        def espera():
            try:
                self.conn.request("LENTO", {"s": 0.5}, timeout=10)
            except ConnectionError as exc:
                resultado.append(exc)

        t = threading.Thread(target=espera)
        t.start()
        time.sleep(0.1)
        self.server.stop()  # derruba a sessão com o pedido em andamento
        t.join(5)
        self.assertEqual(len(resultado), 1)
        with self.assertRaises(ConnectionError):
            self.conn.request(Acao.ECHO)


if __name__ == "__main__":
    unittest.main()
