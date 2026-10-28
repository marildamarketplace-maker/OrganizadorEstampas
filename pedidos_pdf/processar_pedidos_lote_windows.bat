@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title Processamento de pedidos em lote
set "RESULTADO=1"
set "MOSTRAR_ULTIMO_RELATORIO=0"

rem Versao para Windows do processador de pedidos em lote.
for %%I in ("%~dp0..") do set "RAIZ_PROJETO=%%~fI"
cd /d "%RAIZ_PROJETO%"
if errorlevel 1 (
    echo ERRO: nao foi possivel acessar a pasta do projeto.
    goto FINALIZAR
)
set "CAIXA_ENTRADA=%RAIZ_PROJETO%\pedidos_pdf\entrada"
set "RELATORIOS=%RAIZ_PROJETO%\pedidos_pdf\relatorios"
set "CONTROLE=%RAIZ_PROJETO%\pedidos_pdf\.controle"
set "TRAVA=%CONTROLE%\em_execucao"
set "PROCESSADOR=%RAIZ_PROJETO%\meury_app\batch_order_processor.py"
set "INICIALIZADOR=%RAIZ_PROJETO%\scripts\run_order_batch_windows.ps1"
set "ESQUEMA=%RAIZ_PROJETO%\meury_app\batch_order_report.schema.json"

if exist "%RAIZ_PROJETO%\.venv\Scripts\python.exe" (
    set "PYTHON_BIN=%RAIZ_PROJETO%\.venv\Scripts\python.exe"
) else (
    for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PYTHON_BIN set "PYTHON_BIN=%%I"
)

for /f "delims=" %%I in ('where codex.exe 2^>nul') do if not defined CODEX_BIN set "CODEX_BIN=%%I"
if not defined CODEX_BIN (
    for /d %%D in ("%LOCALAPPDATA%\OpenAI\Codex\bin\*") do (
        if exist "%%~fD\codex.exe" set "CODEX_BIN=%%~fD\codex.exe"
    )
)

if not defined PYTHON_BIN (
    echo ERRO: Python nao encontrado. Crie a pasta .venv ou instale o Python.
    goto FINALIZAR
)
if not defined CODEX_BIN echo AVISO: Codex nao encontrado. A API OpenAI sera usada se OPENAI_API_KEY estiver configurada.
if not exist "%PROCESSADOR%" (
    echo ERRO: Processador nao encontrado: "%PROCESSADOR%"
    goto FINALIZAR
)
if not exist "%INICIALIZADOR%" (
    echo ERRO: Inicializador nao encontrado: "%INICIALIZADOR%"
    goto FINALIZAR
)
if not exist "%ESQUEMA%" (
    echo ERRO: Esquema de relatorio nao encontrado: "%ESQUEMA%"
    goto FINALIZAR
)

if /i "%~1"=="--verificar" (
    echo Configuracao valida.
    echo Projeto: %RAIZ_PROJETO%
    echo Python: %PYTHON_BIN%
    if defined CODEX_BIN (echo Codex: %CODEX_BIN%) else (echo Codex: nao encontrado; fallback via API)
    echo Entrada: %CAIXA_ENTRADA%
    set "RESULTADO=0"
    goto FINALIZAR
)

if not exist "%CAIXA_ENTRADA%" mkdir "%CAIXA_ENTRADA%"
if not exist "%RELATORIOS%" mkdir "%RELATORIOS%"
if not exist "%CONTROLE%" mkdir "%CONTROLE%"

set "REINICIAR="
if /i "%~1"=="--reiniciar" set "REINICIAR=-Reiniciar"
rem O inicializador verifica a TRAVA e somente processos de --projeto deste lote.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%INICIALIZADOR%" -ProjectRoot "%RAIZ_PROJETO%" -PythonBin "%PYTHON_BIN%" -ProcessorPath "%PROCESSADOR%" -CodexBin "%CODEX_BIN%" %REINICIAR%
set "RESULTADO=%ERRORLEVEL%"

if "%RESULTADO%"=="2" goto FINALIZAR
set "MOSTRAR_ULTIMO_RELATORIO=1"

:FINALIZAR
echo.
if "%MOSTRAR_ULTIMO_RELATORIO%"=="1" (
    set "ULTIMO_RELATORIO="
    for /f "delims=" %%D in ('dir /b /ad /o-d "%RELATORIOS%" 2^>nul') do if not defined ULTIMO_RELATORIO set "ULTIMO_RELATORIO=%RELATORIOS%\%%D"
    if defined ULTIMO_RELATORIO (
        echo Último relatório: !ULTIMO_RELATORIO!
    )
)
echo.
echo ============================================================
echo   Pressione qualquer tecla para fechar esta janela.
echo ============================================================
pause >nul
exit /b %RESULTADO%
