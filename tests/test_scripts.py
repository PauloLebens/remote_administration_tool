"""Trava dos scripts de inicialização (.bat): eles não podem virar persistência, ocultação
nem guardar o segredo (Seção 3). Rodar: PYTHONPATH=src python -m unittest discover -s tests
"""

import os
import re
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SCRIPTS = {
    "iniciar_agente.bat": "-m agent.agent",
    "iniciar_controlador.bat": "-m controller.controller",
}

# Scripts de duplo clique do macOS (.command).
SCRIPTS_MAC = {
    "iniciar_agente.command": "-m agent.agent",
    "iniciar_controlador.command": "-m controller.controller",
}

# Persistência ou ocultação no macOS/Linux: login item, LaunchAgent, cron, segundo plano...
PROIBIDOS_MAC = (
    "nohup", "disown", "setsid", "launchctl", "launchagent", "launchdaemon", "crontab",
    "osascript", "defaults write", "login item", "tmux", "screen -d", "at now",
)

# Qualquer um destes num script de inicialização indica persistência ou ocultação.
PROIBIDOS = (
    "/min", "-windowstyle", "hidden", "pythonw", "wscript", "cscript", ".vbs", "attrib +h",
    "schtasks", "taskschd", "sc create", "nssm", "reg add", "regedit", "setx", "runonce",
    "startup", "\\run\\",
)


def ler(nome):
    return (RAIZ / nome).read_bytes()


def codigo_sem_comentarios(nome, prefixo):
    """Linhas executáveis do script: os comentários explicam o que NÃO se faz."""
    linhas = ler(nome).decode("ascii").lower().splitlines()
    return "\n".join(l for l in linhas if not l.strip().startswith(prefixo))


class ScriptsTests(unittest.TestCase):
    def test_existem_e_chamam_o_programa_certo_com_o_python_do_projeto(self):
        for nome, modulo in SCRIPTS.items():
            texto = ler(nome).decode("ascii")
            self.assertIn(modulo, texto, nome)
            self.assertIn(r".venv\Scripts\python.exe", texto, nome)

    def test_fim_de_linha_crlf_e_so_ascii(self):
        for nome in SCRIPTS:
            dados = ler(nome)
            dados.decode("ascii")  # cmd sem chcp estraga acentos
            self.assertNotIn(b"\n", dados.replace(b"\r\n", b""), f"{nome} tem LF solto")

    def test_nao_ha_persistencia_nem_ocultacao(self):
        for nome in SCRIPTS:
            texto = ler(nome).decode("ascii").lower()
            # os comentários explicam o que NÃO se faz; só o código executável é conferido
            codigo = "\n".join(l for l in texto.splitlines() if not l.strip().startswith("rem"))
            for termo in PROIBIDOS:
                self.assertNotIn(termo, codigo, f"{nome}: '{termo}'")

    def test_o_segredo_nunca_esta_escrito_no_script(self):
        for nome in SCRIPTS:
            for linha in ler(nome).decode("ascii").splitlines():
                # lê-lo de um arquivo (set /p RAT_SECRET=<...) é permitido; atribuir um valor, não
                self.assertIsNone(re.search(r"RAT_SECRET=(?!<)\S", linha), f"{nome}: {linha}")

    def test_arquivo_do_segredo_e_a_configuracao_ficam_fora_do_git(self):
        ignorados = (RAIZ / ".gitignore").read_text().split()
        for padrao in ("segredo.local", "config.json"):
            self.assertIn(padrao, ignorados)

    def test_git_preserva_crlf_nos_bat(self):
        self.assertRegex((RAIZ / ".gitattributes").read_text(), r"\*\.bat\s+text\s+eol=crlf")


class ScriptsMacTests(unittest.TestCase):
    def test_existem_sao_executaveis_e_chamam_o_programa_certo(self):
        for nome, modulo in SCRIPTS_MAC.items():
            dados = ler(nome)
            texto = dados.decode("ascii")
            self.assertTrue(texto.startswith("#!/bin/bash\n"), f"{nome}: falta o shebang")
            self.assertTrue(os.access(RAIZ / nome, os.X_OK), f"{nome} não é executável (chmod +x)")
            self.assertIn(modulo, texto, nome)
            self.assertIn(".venv/bin/python", texto, nome)
            self.assertNotIn(b"\r", dados, f"{nome} tem CR: o bash recusaria o shebang")

    def test_nao_ha_persistencia_nem_ocultacao(self):
        for nome in SCRIPTS_MAC:
            codigo = codigo_sem_comentarios(nome, "#")
            for termo in PROIBIDOS_MAC:
                self.assertNotIn(termo, codigo, f"{nome}: '{termo}'")
            # nada de rodar em segundo plano: '&' sozinho no fim de uma linha (não '&&')
            self.assertIsNone(re.search(r"(?<!&)&\s*$", codigo, re.M), f"{nome}: comando em segundo plano")

    def test_o_segredo_nunca_esta_escrito_no_script(self):
        for nome in SCRIPTS_MAC:
            for linha in ler(nome).decode("ascii").splitlines():
                # Permitido: ler de arquivo ('read -r RAT_SECRET < ...') e tirar o CR do valor já
                # lido (RAT_SECRET="${RAT_SECRET%...}"). Proibido: atribuir um valor literal.
                self.assertIsNone(re.search(r'RAT_SECRET=(?!"\$\{RAT_SECRET%)\S', linha), f"{nome}: {linha}")

    def test_git_preserva_lf_nos_command(self):
        self.assertRegex((RAIZ / ".gitattributes").read_text(), r"\*\.command\s+text\s+eol=lf")


if __name__ == "__main__":
    unittest.main()
