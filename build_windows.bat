@echo off
setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"
if not exist .venv (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv .venv
    ) else (
        python -m venv .venv
    )
)
if not exist "%VENV_PY%" (
    echo ERRO: nao foi possivel criar o ambiente Python.
    pause
    exit /b 1
)

"%VENV_PY%" -m meury_app.dependency_setup core
if errorlevel 1 goto :erro
"%VENV_PY%" -m PyInstaller --noconfirm --clean --windowed ^
    --hidden-import faiss ^
    --hidden-import openai ^
    --collect-all pypdfium2 ^
    --collect-all pypdfium2_raw ^
    --hidden-import google.cloud.storage ^
    --hidden-import google.oauth2.service_account ^
    --exclude-module torch ^
    --exclude-module torchvision ^
    --exclude-module transformers ^
    --exclude-module scipy ^
    --exclude-module sklearn ^
    --exclude-module av ^
    --name "OrganizadorEstampasMeury" app.py
if errorlevel 1 goto :erro
echo.
echo Aplicativo criado em dist\OrganizadorEstampasMeury
pause
exit /b 0

:erro
echo.
echo ERRO: nao foi possivel criar o aplicativo.
pause
exit /b 1
