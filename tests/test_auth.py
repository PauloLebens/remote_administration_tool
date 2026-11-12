import unittest

from shared.auth import check_secret, compute_proof, make_nonce, verify_proof


class AuthTests(unittest.TestCase):
    def test_prova_correta_e_incorreta(self):
        n = make_nonce()
        self.assertTrue(verify_proof("segredo-ok!", n, compute_proof("segredo-ok!", n)))
        self.assertFalse(verify_proof("segredo-ok!", n, compute_proof("outro-segredo", n)))
        self.assertFalse(verify_proof("segredo-ok!", n, compute_proof("segredo-ok!", make_nonce())))

    def test_prova_de_tipo_invalido_nao_levanta(self):
        for ruim in (None, 123, [], {}, "çãõ"):
            self.assertFalse(verify_proof("segredo-ok!", "n", ruim))

    def test_nonces_sao_unicos(self):
        self.assertEqual(len({make_nonce() for _ in range(100)}), 100)

    def test_segredo_curto_e_recusado(self):
        with self.assertRaises(ValueError):
            check_secret("curto")
        self.assertEqual(check_secret("longo-o-bastante"), "longo-o-bastante")


if __name__ == "__main__":
    unittest.main()
