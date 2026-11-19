@echo off
rem =====================================================================
rem  Inicia o CONTROLADOR (console do operador) e conecta ao agente.
rem  Duplo clique. Digite "ajuda" no prompt para ver os comandos.
rem =====================================================================
setlocal
cd /d "%~dp0"

rem --- Ajustes opcionais. Vazio = usa o config.json ou os padroes. ---
set "AGENTE="
set "PORTA="

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

rem --- Sem IP definido aqui nem no config.json, pergunta (Enter = esta maquina). ---
if not defined AGENTE if not exist "config.json" set /p AGENTE=IP do agente [127.0.0.1]: 

rem --- Segredo compartilhado: 1a linha do arquivo segredo.local (fora do Git).
rem --- Sem o arquivo, o programa pergunta na tela, sem mostrar o que voce digita.
if exist "segredo.local" set /p RAT_SECRET=<"segredo.local"

set "PYTHONPATH=%~dp0src"
set "ARGS="
if defined AGENTE set "ARGS=%ARGS% --host %AGENTE%"
if defined PORTA set "ARGS=%ARGS% --port %PORTA%"

".venv\Scripts\python.exe" -m controller.controller %ARGS%

:fim
echo.
echo Controlador encerrado.
pause
endlocal
