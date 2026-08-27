@echo off
setlocal
cd /d "%~dp0"
set "VENV_PY=.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo Execute primeiro o arquivo executar_windows.bat.
    pause
    exit /b 1
)
echo Instalando IA local, busca semantica e similaridade visual...
echo O download pode ser grande e demorar varios minutos.
"%VENV_PY%" -m meury_app.dependency_setup ai
if errorlevel 1 (
    echo ERRO: a instalacao nao foi concluida.
    pause
    exit /b 1
)
echo Recursos de IA instalados com sucesso.
pause
