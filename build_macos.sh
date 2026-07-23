#!/bin/bash
set -e
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python3 -m pip install --upgrade pip
pip install -r requirements.txt
pyinstaller --noconfirm --clean --windowed --name "OrganizadorEstampasMeury" app.py
echo "Aplicativo criado em dist/OrganizadorEstampasMeury.app"
