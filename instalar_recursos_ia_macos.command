#!/bin/bash
cd "$(dirname "$0")" || exit 1
VENV_PY=".venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "Execute primeiro o arquivo executar_macos.command."
  read -r -p "Pressione Enter para fechar..."
  exit 1
fi
echo "Instalando IA local, busca semântica e similaridade visual..."
echo "O download pode ser grande e demorar vários minutos."
"$VENV_PY" -m meury_app.dependency_setup ai
STATUS=$?
if [ $STATUS -eq 0 ]; then
  echo "Recursos de IA instalados com sucesso."
else
  echo "ERRO: a instalação não foi concluída."
fi
read -r -p "Pressione Enter para fechar..."
exit $STATUS
