@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 goto :directory_error

set "VENV_PY=.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo Criando o ambiente virtual local...
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv .venv
    ) else (
        python -m venv .venv
    )
    if errorlevel 1 goto :venv_error
)

"%VENV_PY%" -m meury_app.dependency_setup core
if errorlevel 1 goto :dependency_error

"%VENV_PY%" app.py
if errorlevel 1 goto :application_error
exit /b 0

:directory_error
echo ERRO: nao foi possivel acessar a pasta do aplicativo.
goto :failure
:venv_error
echo ERRO: nao foi possivel criar o ambiente virtual. Instale o Python 3.
goto :failure
:dependency_error
echo ERRO: nao foi possivel preparar as dependencias basicas.
goto :failure
:application_error
echo ERRO: o aplicativo foi encerrado devido a uma falha.
:failure
echo.
pause
exit /b 1
