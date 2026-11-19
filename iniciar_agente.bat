@echo off
rem =====================================================================
rem  Inicia o AGENTE (a maquina que sera controlada). Duplo clique.
rem
rem  Inicio MANUAL, por quem esta diante da maquina (Secao 3 do enunciado):
rem  este arquivo NAO se instala para rodar sozinho, NAO esconde a janela,
rem  e NAO cria tarefa agendada, servico nem entrada de inicializacao.
rem  A janela vermelha de aviso e o botao Encerrar aparecem na tela.
rem =====================================================================
setlocal
cd /d "%~dp0"

rem --- Ajustes opcionais. Vazio = usa o config.json ou os padroes. ---
set "PORTA="
set "RAIZ="

if not exist ".venv\Scripts\python.exe" (
    echo [!] Ambiente nao encontrado em .venv
    where uv >nul 2>nul
    if errorlevel 1 (
        echo     Instale o uv ^(https://docs.astral.sh/uv^) e rode: uv sync
        goto :fim
    )
    echo     Criando o ambiente com: uv sync
    uv sync || goto :fim
)

rem --- Segredo compartilhado: 1a linha do arquivo segredo.local (fora do Git).
rem --- Sem o arquivo, o programa pergunta na tela, sem mostrar o que voce digita.
if exist "segredo.local" set /p RAT_SECRET=<"segredo.local"

set "PYTHONPATH=%~dp0src"
set "ARGS="
if defined PORTA set "ARGS=%ARGS% --port %PORTA%"
if defined RAIZ set "ARGS=%ARGS% --raiz "%RAIZ%""

echo Iniciando o agente. Para encerrar: botao da janela, Ctrl+Shift+K ou Ctrl+C aqui.
".venv\Scripts\python.exe" -m agent.agent %ARGS%

:fim
echo.
echo Agente encerrado.
pause
endlocal
