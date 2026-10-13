#!/bin/bash

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -z "$BASE_DIR" ] || ! cd "$BASE_DIR"; then
  echo "ERRO: não foi possível acessar a pasta do aplicativo."
  if [ "${ORGANIZADOR_SEM_TERMINAL:-0}" != "1" ]; then
    read -r -p "Pressione Enter para fechar..."
  fi
  exit 1
fi

# Execute no Terminal para que o macOS possa solicitar acesso a Documents.
# Um applet em segundo plano pode receber Operation not permitted sem diálogo.
pause_on_error() {
  local status=$?
  if [ "$status" -ne 0 ] && [ -t 0 ]; then
    read -r -p "Pressione Enter para fechar..."
  fi
}
trap pause_on_error EXIT

VENV_PY=".venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "Criando o ambiente virtual local..."
  python3 -m venv .venv
  if [ $? -ne 0 ]; then
    echo "ERRO: não foi possível criar o ambiente virtual. Instale o Python 3."
    exit 1
  fi
fi

"$VENV_PY" -m meury_app.dependency_setup core
if [ $? -ne 0 ]; then
  echo "ERRO: não foi possível preparar as dependências básicas."
  exit 1
fi

"$VENV_PY" app.py
STATUS=$?
if [ $STATUS -eq 130 ]; then
  echo "Aplicativo interrompido manualmente com Control + C."
elif [ $STATUS -eq 139 ]; then
  echo "O mecanismo de IA foi encerrado à força (segmentation fault)."
  echo "Isso pode acontecer ao usar Control + C durante o processamento do modelo."
elif [ $STATUS -ne 0 ]; then
  echo "ERRO: o aplicativo foi encerrado devido a uma falha."
fi
exit $STATUS
