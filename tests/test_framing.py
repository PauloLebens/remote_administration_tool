"""Testes do framing e do eco. Rodar: PYTHONPATH=src python3 -m unittest discover -s tests"""

import struct
import unittest

from shared.protocol import (
    MAX_MESSAGE_SIZE, Acao, ProtocolError, Status, make_command, make_response, validate,
)
from shared.receiver import recv_msg
from shared.sender import encode_frame


class FakeSock:
    """Entrega os bytes em pedaços de `step`, simulando fragmentação do TCP."""

    def __init__(self, data: bytes, step: int = 1):
        self.data, self.step = data, step

    def recv(self, n):
        out, self.data = self.data[: min(n, self.step)], self.data[min(n, self.step):]
        return out


class FramingTests(unittest.TestCase):
    def test_roundtrip_fragmentado_byte_a_byte(self):
        cmd = make_command(Acao.EXEC, {"cmd": "echo olá ✓"})
        self.assertEqual(recv_msg(FakeSock(encode_frame(cmd), step=1)), cmd)

    def test_duas_mensagens_grudadas(self):
        a, b = make_command(Acao.ECHO), make_command(Acao.SYSINFO)
        sock = FakeSock(encode_frame(a) + encode_frame(b), step=10_000)
        self.assertEqual(recv_msg(sock), a)
        self.assertEqual(recv_msg(sock), b)
        self.assertIsNone(recv_msg(sock))  # EOF limpo entre mensagens

    def test_conexao_cai_no_meio_da_mensagem(self):
        frame = encode_frame(make_command(Acao.ECHO))
        with self.assertRaises(ConnectionError):
            recv_msg(FakeSock(frame[:-3]))

    def test_tamanho_acima_do_limite(self):
        with self.assertRaises(ProtocolError):
            recv_msg(FakeSock(struct.pack("!I", MAX_MESSAGE_SIZE + 1)))

    def test_tamanho_zero(self):
        with self.assertRaises(ProtocolError):
            recv_msg(FakeSock(struct.pack("!I", 0)))

    def test_json_invalido_e_utf8_invalido(self):
        for corpo in (b"{nao e json", b"\xff\xfe\xfd"):
            with self.assertRaises(ProtocolError):
                recv_msg(FakeSock(struct.pack("!I", len(corpo)) + corpo))

    def test_encode_recusa_mensagem_gigante(self):
        with self.assertRaises(ProtocolError):
            encode_frame({"x": "a" * (MAX_MESSAGE_SIZE + 1)})

    def test_validate(self):
        validate(make_command(Acao.ECHO))
        for ruim in ([], {}, {"id": "1", "tipo": "x", "acao": "A"},
                     {"id": "1", "tipo": "comando", "acao": "A", "args": []}):
            with self.assertRaises(ProtocolError):
                validate(ruim)

    def test_resposta_reaproveita_id(self):
        cmd = make_command(Acao.ECHO)
        self.assertEqual(make_response(cmd, "x")["id"], cmd["id"])
        self.assertEqual(make_response(cmd, erro="falhou")["status"], Status.ERRO)


if __name__ == "__main__":
    unittest.main()
