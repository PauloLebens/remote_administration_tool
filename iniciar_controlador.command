#!/bin/bash
# =====================================================================
#  Inicia o CONTROLADOR (console do operador) e conecta ao agente.
#  Duplo clique no Finder. Digite "ajuda" no prompt para ver os comandos.
# =====================================================================
cd "$(dirname "$0")" || exit 1

# --- Ajustes opcionais. Vazio = usa o config.json ou os padroes. ---
AGENTE=""
PORTA=""

if [ ! -x ".venv/bin/python" ]; then
    echo "[!] Ambiente nao encontrado em .venv"
    if ! command -v uv >/dev/null 2>&1; then
        echo "    Instale o uv (https://docs.astral.sh/uv) e rode: uv sync"
        read -r -n 1 -s -p "Pressione uma tecla para fechar..."; echo
        exit 1
    fi
    echo "    Criando o ambiente com: uv sync"
    uv sync || { read -r -n 1 -s -p "Pressione uma tecla para fechar..."; echo; exit 1; }
fi

# --- Sem IP definido aqui nem no config.json, pergunta (Enter = esta maquina). ---
if [ -z "$AGENTE" ] && [ ! -f "config.json" ]; then
    read -r -p "IP do agente [127.0.0.1]: " AGENTE
fi

# --- Segredo compartilhado: 1a linha do arquivo segredo.local (fora do Git).
# --- Sem o arquivo, o programa pergunta na tela, sem mostrar o que voce digita.
if [ -f "segredo.local" ]; then
    read -r RAT_SECRET < "segredo.local"
    RAT_SECRET="${RAT_SECRET%$'\r'}"
    export RAT_SECRET
fi

export PYTHONPATH="$PWD/src"
ARGS=()
[ -n "$AGENTE" ] && ARGS+=(--host "$AGENTE")
[ -n "$PORTA" ] && ARGS+=(--port "$PORTA")

.venv/bin/python -m controller.controller "${ARGS[@]}"

echo
echo "Controlador encerrado."
read -r -n 1 -s -p "Pressione uma tecla para fechar..."; echo
