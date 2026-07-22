@echo off
setlocal
if not exist .venv (
    python -m venv .venv
)
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pyinstaller --noconfirm --clean --windowed --name "OrganizadorEstampasMeury" app.py
echo.
echo Aplicativo criado em dist\OrganizadorEstampasMeury
pause
