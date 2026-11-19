"""Validação dos argumentos de um comando, compartilhada pelos tratadores.

Todo erro é ValueError com o nome do campo, porque a Session o devolve ao controlador
como resposta de erro. Isso importa porque os argumentos vêm da rede: nunca se confia no
tipo nem na faixa do que chegou.
"""

from __future__ import annotations

from typing import Any

_SEM_VALOR = object()


def inteiro(
    args: dict[str, Any],
    nome: str,
    padrao: Any = _SEM_VALOR,
    minimo: int | None = None,
    maximo: int | None = None,
) -> int:
    """Lê o campo `nome` como inteiro dentro de [minimo, maximo].

    Sem `padrao`, o campo é obrigatório. Recusa booleano (que em Python também é int),
    texto e número fracionário.
    """
    if nome not in args:
        if padrao is _SEM_VALOR:
            raise ValueError(f"'{nome}' é obrigatório")
        return padrao
    valor = args[nome]
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise ValueError(f"'{nome}' deve ser um inteiro (recebido: {valor!r})")
    if (minimo is not None and valor < minimo) or (maximo is not None and valor > maximo):
        faixa = (
            f"entre {minimo} e {maximo}" if minimo is not None and maximo is not None
            else f">= {minimo}" if minimo is not None else f"<= {maximo}"
        )
        raise ValueError(f"'{nome}' deve ser um inteiro {faixa} (recebido: {valor})")
    return valor


def booleano(args: dict[str, Any], nome: str, padrao: bool) -> bool:
    """Lê o campo `nome` como booleano estrito (`true`/`false`); 0, 1 e texto são recusados."""
    valor = args.get(nome, padrao)
    if not isinstance(valor, bool):
        raise ValueError(f"'{nome}' deve ser true ou false (recebido: {valor!r})")
    return valor


def escolha(args: dict[str, Any], nome: str, validos: tuple[str, ...], padrao: str) -> str:
    """Lê o campo `nome` como um dos textos de `validos` (sem diferenciar maiúsculas)."""
    valor = args.get(nome, padrao)
    if not isinstance(valor, str) or valor.lower() not in validos:
        raise ValueError(f"'{nome}' deve ser um de: {', '.join(validos)} (recebido: {valor!r})")
    return valor.lower()
