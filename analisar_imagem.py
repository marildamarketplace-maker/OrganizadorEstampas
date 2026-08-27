"""Testa a análise de uma única imagem pela API sem modificar o índice."""

import argparse
import json
import sys

from meury_app.image_analyzer import analyze_image


def main() -> int:
    parser = argparse.ArgumentParser(description="Analisa uma arte com a API OpenAI.")
    parser.add_argument("imagem", help="Caminho de uma imagem JPG, JPEG ou PNG.")
    args = parser.parse_args()
    result = analyze_image(args.imagem)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        raise SystemExit(1)
