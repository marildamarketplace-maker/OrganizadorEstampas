"""Cria uma pasta de pedido a partir do JSON extraído do PDF."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from meury_app.config import load_config
from meury_app.indexer import build_index, load_index
from meury_app.processor import clean_order_date, process_order_json, safe_folder_name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cria a pasta e copia as estampas de um pedido em JSON."
    )
    parser.add_argument(
        "json_file",
        nargs="?",
        default="-",
        help="Arquivo JSON do pedido; use '-' ou omita para ler da entrada padrão.",
    )
    parser.add_argument("--saida", help="Pasta de saída (opcional se já salva no app).")
    parser.add_argument(
        "--origem",
        action="append",
        default=[],
        help="Pasta de estampas; pode ser repetida (opcional se já salva no app).",
    )
    parser.add_argument(
        "--atualizar-indice",
        action="store_true",
        help="Reconstrói o índice antes de criar o pedido.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config()
    sources = [Path(value) for value in (args.origem or config["source_dirs"])]
    output = Path(args.saida or config["output_dir"]) if (args.saida or config["output_dir"]) else None

    if not sources:
        raise ValueError("Nenhuma pasta de estampas foi informada ou salva no aplicativo.")
    if output is None:
        raise ValueError("Nenhuma pasta de saída foi informada ou salva no aplicativo.")

    if args.json_file == "-":
        json_text = sys.stdin.read()
    else:
        json_text = Path(args.json_file).read_text(encoding="utf-8")

    index = {} if args.atualizar_indice else load_index(sources)
    if not index:
        index, _ = build_index(sources)

    results, summary = process_order_json(json_text, output, index)
    _, date_folder = clean_order_date(results[0].data) if results else ("", "")
    response = {
        "sucesso": True,
        "pedido": results[0].pedido if results else "",
        "pastaPedido": str(
            output
            / safe_folder_name(results[0].cliente)
            / date_folder
            / safe_folder_name(results[0].pedido)
        ) if results else "",
        "copiados": summary.copiados,
        "arquivosCopiados": [
            item.arquivo_procurado for item in results if item.status == "COPIADO"
        ],
        "naoEncontrados": summary.nao_encontrados,
        "estampasNaoEncontradas": [
            item.arquivo_procurado for item in results if item.status == "NÃO ENCONTRADO"
        ],
        "duplicados": summary.duplicados,
        "estampasDuplicadas": [
            item.arquivo_procurado for item in results if item.status == "DUPLICADO"
        ],
        "jaExistentesOuIgnorados": summary.ignorados,
        "arquivosJaExistentes": [
            item.arquivo_procurado for item in results if item.status == "JÁ EXISTE"
        ],
        "erros": [],
        "relatorio": summary.report_xlsx,
    }
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print(json.dumps({"sucesso": False, "erro": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
