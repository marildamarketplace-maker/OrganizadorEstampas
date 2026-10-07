#!/bin/bash

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -z "$BASE_DIR" ] || ! cd "$BASE_DIR"; then
  echo "ERRO: não foi possível acessar a pasta do aplicativo."
  if [ "${ORGANIZADOR_SEM_TERMINAL:-0}" != "1" ]; then
    read -r -p "Pressione Enter para fechar..."
  fi
  exit 1
fi

# Ao abrir o .command pelo Finder, cria/atualiza um aplicativo e transfere a
# inicialização para ele. O .app chama este script novamente em modo oculto.
if [ "${ORGANIZADOR_SEM_TERMINAL:-0}" != "1" ]; then
  /bin/bash "$BASE_DIR/criar_app_macos.command"
  STATUS=$?
  if [ $STATUS -ne 0 ]; then
    echo "ERRO: não foi possível criar o aplicativo do macOS."
    read -r -p "Pressione Enter para fechar..."
    exit $STATUS
  fi
  /usr/bin/open "$BASE_DIR/Organizador de Estampas.app"
  exit $?
fi

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
