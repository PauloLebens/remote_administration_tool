"""Decide se o controlador confia na identidade (impressão digital) de um agente.

Sem autoridade certificadora, a identidade do agente é a impressão digital do certificado
dele. Duas formas de fixá-la:

  - VerificadorFixo: o operador informa a impressão digital esperada (config ou linha de
    comando) e qualquer outra é recusada;
  - AgentesConhecidos: como o `known_hosts` do SSH. Na primeira conexão o operador confere a
    impressão digital com a que aparece na janela do agente e confirma; ela fica salva. Se
    mudar depois, a conexão é recusada SEM perguntar: é o sinal clássico de alguém se
    passando pelo agente, e perguntar de novo só ensinaria o operador a clicar "sim".
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Protocol

from shared.fingerprint import normalizar


class IdentidadeRecusada(ConnectionError):
    """O controlador não aceitou a identidade do agente; a conexão não deve seguir."""


class IdentidadeMudou(IdentidadeRecusada):
    """A impressão digital é diferente da que se esperava: possível agente falso."""


class Verificador(Protocol):
    """Quem confere a impressão digital que o agente apresentou no handshake TLS."""

    def verificar(self, host: str, porta: int, fingerprint: str) -> None:
        """Retorna se a identidade é aceita; levanta IdentidadeRecusada se não for."""


def _mensagem_mudou(host: str, porta: int, esperada: str, recebida: str) -> str:
    """Texto do alerta de identidade diferente, com as duas impressões digitais lado a lado."""
    return (
        f"A identidade do agente em {host}:{porta} mudou! Pode haver alguém se passando por ele.\n"
        f"  esperada: {esperada}\n  recebida: {recebida}\n"
        "Se você sabe que o agente gerou um certificado novo, confira a impressão digital na "
        "janela dele e apague a entrada antiga do arquivo de agentes conhecidos."
    )


class VerificadorFixo:
    """Aceita só o agente cuja impressão digital é exatamente a informada."""

    def __init__(self, fingerprint: str) -> None:
        """`fingerprint` pode vir com ou sem o prefixo SHA256: e com ou sem dois-pontos."""
        self.esperada = normalizar(fingerprint)

    def verificar(self, host: str, porta: int, fingerprint: str) -> None:
        """Recusa com IdentidadeMudou se a impressão digital recebida for diferente."""
        if fingerprint != self.esperada:
            raise IdentidadeMudou(_mensagem_mudou(host, porta, self.esperada, fingerprint))


class AgentesConhecidos:
    """Memória de impressões digitais já confirmadas, num arquivo JSON {"host:porta": "SHA256:..."}."""

    def __init__(
        self,
        arquivo: str | Path,
        perguntar: Callable[[str, int, str], bool] | None = None,
    ) -> None:
        """`perguntar(host, porta, fingerprint)` é chamada só para agentes NUNCA vistos e deve
        devolver True se o operador confirma. Sem ela, agente desconhecido é sempre recusado
        (padrão seguro para uso sem terminal).
        """
        self.arquivo = Path(arquivo)
        self._perguntar = perguntar

    def verificar(self, host: str, porta: int, fingerprint: str) -> None:
        """Aceita agente conhecido com a mesma impressão digital; pergunta pelo desconhecido
        e recusa, sem perguntar, o conhecido cuja impressão digital mudou.
        """
        chave = f"{host.lower()}:{porta}"
        conhecidos = self._ler()
        esperada = conhecidos.get(chave)
        if esperada is None:
            if self._perguntar is None or not self._perguntar(host, porta, fingerprint):
                raise IdentidadeRecusada(
                    f"identidade do agente em {host}:{porta} não confirmada ({fingerprint})"
                )
            conhecidos[chave] = fingerprint
            self._salvar(conhecidos)
            return
        if esperada != fingerprint:
            raise IdentidadeMudou(_mensagem_mudou(host, porta, esperada, fingerprint))

    def _ler(self) -> dict[str, str]:
        """Lê o arquivo; ausente = vazio. Arquivo corrompido é erro: reiniciá-lo em silêncio
        apagaria as identidades fixadas e abriria a porta para um agente falso.
        """
        if not self.arquivo.exists():
            return {}
        try:
            dados = json.loads(self.arquivo.read_text(encoding="utf-8"))
            if not isinstance(dados, dict) or not all(isinstance(v, str) for v in dados.values()):
                raise ValueError("formato inesperado")
            return dados
        except (OSError, ValueError) as exc:
            raise IdentidadeRecusada(
                f"o arquivo de agentes conhecidos {self.arquivo} está inválido ({exc}); "
                "corrija ou apague-o conscientemente"
            ) from exc

    def _salvar(self, conhecidos: dict[str, str]) -> None:
        """Grava de forma atômica (arquivo temporário + renomear), para uma queda no meio não
        deixar o arquivo pela metade.
        """
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.arquivo.with_name(self.arquivo.name + ".tmp")
        tmp.write_text(json.dumps(conhecidos, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.arquivo)
