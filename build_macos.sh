#!/bin/bash
set -e
cd "$(dirname "$0")"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python3 -m meury_app.dependency_setup core
python3 -m PyInstaller --noconfirm --clean --windowed \
  --hidden-import faiss \
  --hidden-import openai \
  --collect-all pypdfium2 \
  --collect-all pypdfium2_raw \
  --hidden-import google.cloud.storage \
  --hidden-import google.oauth2.service_account \
  --exclude-module torch \
  --exclude-module torchvision \
  --exclude-module transformers \
  --exclude-module scipy \
  --exclude-module sklearn \
  --exclude-module av \
  --name "OrganizadorEstampasMeury" app.py
echo "Aplicativo criado em dist/OrganizadorEstampasMeury.app"
