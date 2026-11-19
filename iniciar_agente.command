#!/bin/bash
# =====================================================================
#  Inicia o AGENTE (a maquina que sera controlada). Duplo clique no Finder.
#
#  Inicio MANUAL, por quem esta diante da maquina (Secao 3 do enunciado):
#  este arquivo NAO se instala para rodar sozinho, NAO esconde a janela e
#  NAO cria login item, LaunchAgent nem tarefa agendada. A janela vermelha
#  de aviso e o botao Encerrar aparecem na tela.
#
#  Mouse e teclado remotos exigem a permissao de Acessibilidade para o
#  Terminal: Ajustes do Sistema > Privacidade e Seguranca > Acessibilidade.
# =====================================================================
cd "$(dirname "$0")" || exit 1

# --- Ajustes opcionais. Vazio = usa o config.json ou os padroes. ---
PORTA=""
RAIZ=""

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

# --- Segredo compartilhado: 1a linha do arquivo segredo.local (fora do Git).
# --- Sem o arquivo, o programa pergunta na tela, sem mostrar o que voce digita.
if [ -f "segredo.local" ]; then
    read -r RAT_SECRET < "segredo.local"
    RAT_SECRET="${RAT_SECRET%$'\r'}"   # tolera arquivo salvo com fim de linha do Windows
    export RAT_SECRET
fi

export PYTHONPATH="$PWD/src"
ARGS=()
[ -n "$PORTA" ] && ARGS+=(--port "$PORTA")
[ -n "$RAIZ" ] && ARGS+=(--raiz "$RAIZ")

echo "Iniciando o agente. Para encerrar: botao da janela, Cmd+Shift+K ou Ctrl+C aqui."
.venv/bin/python -m agent.agent "${ARGS[@]}"

echo
echo "Agente encerrado."
read -r -n 1 -s -p "Pressione uma tecla para fechar..."; echo
