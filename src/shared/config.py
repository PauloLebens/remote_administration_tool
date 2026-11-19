"""Configurações do usuário (arquivo JSON) para o agente e o controlador.

O que o arquivo PODE fazer: ajustar *como* o programa roda (porta, limites, caminhos).
O que ele NÃO PODE fazer, de propósito: ligar ou desligar qualquer regra da Seção 3.
Não existe campo para esconder o indicador, desligar o log ou iniciar sozinho, e o
segredo compartilhado nunca fica em arquivo (vem de RAT_SECRET ou de um prompt).
Também não entram aqui as constantes do protocolo (CHUNK_SIZE, MAX_MESSAGE_SIZE): os dois
lados precisam concordar, então configurar cada máquina separadamente quebraria a conversa.

Formato (qualquer das duas seções pode faltar; campos ausentes usam o padrão):

    {"agente": {"porta": 5055, "raiz": "/srv/lab"}, "controlador": {"host": "10.0.0.7"}}

Precedência, da mais forte para a mais fraca: argumento de linha de comando > arquivo
> padrão do código. O arquivo é procurado em `--config`, na variável RAT_CONFIG ou em
`config.json` no diretório atual; só este último pode faltar sem erro.

Todo valor é validado (tipo e faixa) e campo desconhecido é erro, para um nome
digitado errado não ser ignorado em silêncio.
"""

from __future__ import annotations

import difflib
import json
import math
import os
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, ClassVar

from shared.fingerprint import normalizar
from shared.protocol import DEFAULT_PORT

CONFIG_PADRAO = "config.json"
CONFIG_ENV = "RAT_CONFIG"


class ConfigError(ValueError):
    """Configuração inválida; a mensagem diz qual campo e o que se espera."""


def _campo(tipo: str, padrao: Any, minimo: float | None = None, maximo: float | None = None):
    """Declara um campo da configuração. `tipo`: int | num | txt | txt? (texto opcional)."""
    return field(default=padrao, metadata={"tipo": tipo, "min": minimo, "max": maximo})


def _validar(nome: str, meta: dict[str, Any], valor: Any) -> Any:
    """Confere tipo e faixa de um valor e o devolve normalizado (num vira float)."""
    tipo, lo, hi = meta["tipo"], meta["min"], meta["max"]
    if tipo == "txt?" and valor is None:
        return None
    if tipo in ("txt", "txt?"):
        if not isinstance(valor, str) or not valor.strip():
            raise ConfigError(f"'{nome}' deve ser um texto não vazio (recebido: {valor!r})")
        return valor

    faixa = f" entre {lo} e {hi}" if lo is not None and hi is not None else ""
    esperado = f"'{nome}' deve ser {'um inteiro' if tipo == 'int' else 'um número'}{faixa}"
    # bool é subclasse de int em Python: `true` no JSON não vale como número
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ConfigError(f"{esperado} (recebido: {valor!r})")
    if tipo == "int" and not isinstance(valor, int):
        raise ConfigError(f"{esperado} (recebido: {valor!r})")
    if not math.isfinite(valor) or (lo is not None and valor < lo) or (hi is not None and valor > hi):
        raise ConfigError(f"{esperado} (recebido: {valor!r})")
    return float(valor) if tipo == "num" else valor


def _ler_arquivo(caminho: str | None) -> tuple[Path | None, dict[str, Any]]:
    """Localiza e lê o arquivo de configuração.

    Devolve (caminho, conteúdo). Sem arquivo e sem pedido explícito, devolve (None, {}).
    Caminho pedido por --config ou RAT_CONFIG que não existe é erro.
    """
    explicito = caminho or os.environ.get(CONFIG_ENV)
    path = Path(explicito or CONFIG_PADRAO)
    if not path.is_file():
        if explicito:
            raise ConfigError(f"arquivo de configuração não encontrado: {path}")
        return None, {}
    try:
        dados = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: JSON inválido (linha {exc.lineno}, coluna {exc.colno}): {exc.msg}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: não foi possível ler o arquivo: {exc}") from exc
    if not isinstance(dados, dict):
        raise ConfigError(f"{path}: o conteúdo deve ser um objeto JSON")
    desconhecidas = sorted(set(dados) - {AgentConfig.SECAO, ControllerConfig.SECAO})
    if desconhecidas:
        raise ConfigError(
            f"{path}: seção desconhecida: {', '.join(map(repr, desconhecidas))} "
            f"(válidas: '{AgentConfig.SECAO}', '{ControllerConfig.SECAO}')"
        )
    return path, dados


@dataclass(frozen=True, kw_only=True)
class _Config:
    """Base das configurações: validação de campos, leitura do arquivo e sobrescrita.

    Imutável: para mudar um valor crie outra com `com()`, que valida de novo.
    """

    SECAO: ClassVar[str]

    def __post_init__(self) -> None:
        """Valida (e normaliza) todos os campos, por mais que a instância tenha sido criada."""
        for f in fields(self):
            valor = getattr(self, f.name)
            novo = _validar(f.name, f.metadata, valor)
            if novo is not valor:
                object.__setattr__(self, f.name, novo)
        self._validar_cruzado()

    def _validar_cruzado(self) -> None:
        """Regras que envolvem mais de um campo. As subclasses sobrescrevem."""

    @classmethod
    def de_dict(cls, dados: Any) -> _Config:
        """Cria a configuração a partir de um dicionário, recusando campos desconhecidos."""
        if not isinstance(dados, dict):
            raise ConfigError(f"a seção '{cls.SECAO}' deve ser um objeto JSON")
        validos = [f.name for f in fields(cls)]
        desconhecidos = [k for k in dados if k not in validos]
        if desconhecidos:
            dicas = []
            for k in desconhecidos:
                parecido = difflib.get_close_matches(str(k), validos, n=1)
                dicas.append(f"{k!r}" + (f" (você quis dizer {parecido[0]!r}?)" if parecido else ""))
            raise ConfigError(
                f"campo desconhecido em '{cls.SECAO}': {', '.join(dicas)}. "
                f"Campos válidos: {', '.join(validos)}"
            )
        return cls(**dados)

    @classmethod
    def carregar(cls, caminho: str | None = None) -> _Config:
        """Lê o arquivo (ver a docstring do módulo) e devolve a configuração desta seção."""
        path, dados = _ler_arquivo(caminho)
        try:
            return cls.de_dict(dados.get(cls.SECAO, {}))
        except ConfigError as exc:
            raise ConfigError(f"{path}: {exc}" if path else str(exc)) from exc

    def com(self, **mudancas: Any) -> _Config:
        """Cópia com valores sobrescritos (ex.: vindos da linha de comando).

        Valores None são ignorados, o que permite passar direto os argumentos do argparse
        que o usuário não informou. A cópia é validada de novo.
        """
        return replace(self, **{k: v for k, v in mudancas.items() if v is not None})


@dataclass(frozen=True, kw_only=True)
class AgentConfig(_Config):
    """Configuração do agente (a máquina controlada). Tempos em segundos, tamanhos em bytes."""

    SECAO: ClassVar[str] = "agente"

    host: str = _campo("txt", "0.0.0.0")  # interface onde escutar
    porta: int = _campo("int", DEFAULT_PORT, 0, 65535)  # 0 = o SO escolhe (útil em testes)
    raiz: str | None = _campo("txt?", None)  # restringe LS/GET/PUT (não o EXEC)
    arquivo_log: str = _campo("txt", "logs/agente.log")
    certificado: str = _campo("txt", "certs/agente.crt")  # gerado na 1ª execução se não existir
    chave: str = _campo("txt", "certs/agente.key")  # chave PRIVADA do certificado: nunca compartilhe
    auth_timeout: float = _campo("num", 10.0, 0.1, 120.0)  # prazo para concluir o handshake
    exec_timeout_padrao: float = _campo("num", 30.0, 0.1, 3600.0)  # se o pedido não disser
    exec_timeout_max: float = _campo("num", 300.0, 0.1, 3600.0)  # teto, mesmo se o pedido pedir mais
    exec_saida_max: int = _campo("int", 1024 * 1024, 1024, 64 * 1024 * 1024)  # por stream
    ls_max_entradas: int = _campo("int", 5000, 1, 100_000)

    def _validar_cruzado(self) -> None:
        """O timeout padrão do EXEC não pode passar do teto."""
        if self.exec_timeout_padrao > self.exec_timeout_max:
            raise ConfigError(
                f"'exec_timeout_padrao' ({self.exec_timeout_padrao:g}) não pode ser maior "
                f"que 'exec_timeout_max' ({self.exec_timeout_max:g})"
            )


@dataclass(frozen=True, kw_only=True)
class ControllerConfig(_Config):
    """Configuração do controlador (a máquina do operador). Tempos em segundos."""

    SECAO: ClassVar[str] = "controlador"

    host: str = _campo("txt", "127.0.0.1")  # endereço do agente
    porta: int = _campo("int", DEFAULT_PORT, 0, 65535)
    arquivo_log: str = _campo("txt", "logs/controlador.log")
    timeout_conexao: float = _campo("num", 5.0, 0.1, 120.0)
    timeout_requisicao: float = _campo("num", 30.0, 0.1, 3600.0)  # um comando comum ou um bloco
    timeout_exec: float = _campo("num", 330.0, 0.1, 7200.0)  # deve passar de exec_timeout_max do agente
    pasta_downloads: str = _campo("txt", ".")  # destino padrão do `get` sem caminho local
    arquivo_agentes_conhecidos: str = _campo("txt", "agentes_conhecidos.json")  # identidades já confirmadas
    fingerprint_agente: str | None = _campo("txt?", None)  # se informada, só este agente é aceito

    def _validar_cruzado(self) -> None:
        """A impressão digital informada precisa ser um SHA-256 completo e bem formado."""
        if self.fingerprint_agente is not None:
            try:
                normalizar(self.fingerprint_agente)
            except ValueError as exc:
                raise ConfigError(f"'fingerprint_agente': {exc}") from exc
