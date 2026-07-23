@echo off
setlocal
if not exist .venv (
    py -m venv .venv
)
call .venv\Scripts\activate
py -m pip install --upgrade pip
pip install -r requirements.txt
pyinstaller --noconfirm --clean --windowed --name "OrganizadorEstampasMeury" app.py
echo.
echo Aplicativo criado em dist\OrganizadorEstampasMeury
pause
