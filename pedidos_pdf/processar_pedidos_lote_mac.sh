#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RAIZ_PROJETO="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$RAIZ_PROJETO"
CAIXA_ENTRADA="$RAIZ_PROJETO/pedidos_pdf/entrada"
RELATORIOS="$RAIZ_PROJETO/pedidos_pdf/relatorios"
CONTROLE="$RAIZ_PROJETO/pedidos_pdf/.controle"
TRAVA="$CONTROLE/em_execucao"

mkdir -p "$CAIXA_ENTRADA" "$RELATORIOS" "$CONTROLE"

if ! mkdir "$TRAVA" 2>/dev/null; then
  echo "Ja existe um processamento em lote em andamento."
  exit 1
fi
trap 'rmdir "$TRAVA" 2>/dev/null || true' EXIT INT TERM

if command -v codex >/dev/null 2>&1; then
  CODEX_BIN="$(command -v codex)"
else
  CODEX_BIN=""
  echo "AVISO: Codex nao encontrado. A API OpenAI sera usada se OPENAI_API_KEY estiver configurada."
fi

if [[ -x "$RAIZ_PROJETO/.venv/bin/python" ]]; then
  PYTHON_BIN="$RAIZ_PROJETO/.venv/bin/python"
else
  PYTHON_BIN="$(command -v python3)"
fi

echo "Caixa de entrada: $CAIXA_ENTRADA"
echo "Iniciando atualizacao diaria do indice e processamento dos PDFs novos..."

"$PYTHON_BIN" "$RAIZ_PROJETO/meury_app/batch_order_processor.py" \
  --projeto "$RAIZ_PROJETO" \
  --codex "$CODEX_BIN"
