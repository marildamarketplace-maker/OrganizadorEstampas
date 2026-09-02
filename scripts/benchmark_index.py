"""Carga reproduzível com arquivos reais temporários; não lê o catálogo do usuário.

Execute: python scripts/benchmark_index.py --count 195000 --output benchmark-index.json
"""

import argparse
from dataclasses import asdict
import gc
import io
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=195000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.count < 30:
        parser.error("--count deve ser pelo menos 30")

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from PIL import Image

    with tempfile.TemporaryDirectory(prefix="meury-index-benchmark-") as temporary:
        root = Path(temporary).resolve()
        os.environ["MEURY_APP_DATA_PATH"] = str(root / "dados")
        from meury_app.indexer import build_index, update_index_incremental

        source = root / "Estampas com espaços e acentuação"
        buffer = io.BytesIO()
        Image.new("RGB", (32, 32), (33, 99, 150)).save(buffer, format="JPEG")
        image_bytes = buffer.getvalue()

        def image_path(number):
            design = str(10000 + number // 100)
            return source / f"{design} COLEÇÃO" / f"{design}-A{number % 100}.jpg"

        started = time.monotonic()
        for number in range(args.count):
            path = image_path(number)
            if number % 100 == 0:
                path.parent.mkdir(parents=True)
            path.write_bytes(image_bytes + str(number).encode("ascii"))
            if (number + 1) % 25000 == 0:
                print(f"Preparando arquivos: {number + 1:,}/{args.count:,}", flush=True)
        setup_seconds = time.monotonic() - started
        events = []

        def progress(count, message):
            events.append((time.monotonic(), count, message))

        index, full = build_index(source, progress)
        assert full.total_files == args.count and len(index) == args.count
        del index
        gc.collect()
        catalog = root / "dados" / "indice_estampas.jsonl"
        catalog_mtime = catalog.stat().st_mtime_ns
        index, unchanged = update_index_incremental(source, progress)
        assert unchanged.unchanged_files == args.count
        assert unchanged.hashed_files == 0 and unchanged.errors == 0
        assert catalog.stat().st_mtime_ns == catalog_mtime
        del index
        gc.collect()

        # Alteração, movimento e remoção reais, mantendo arquivos válidos.
        for number in range(10):
            with image_path(number).open("ab") as stream:
                stream.write(b"changed")
        for number in range(10, 20):
            old = image_path(number)
            old.rename(old.with_name(old.stem + "-renamed.jpg"))
        for number in range(20, 30):
            image_path(number).unlink()
        index, changed = update_index_incremental(source, progress)
        assert changed.changed_files == 10 and changed.moved_files == 10
        assert changed.removed_files == 10 and changed.hashed_files == 20
        assert changed.unchanged_files == args.count - 30
        assert len(index) == args.count - 10 and changed.errors == 0

        report = {
            "platform": platform.platform(), "python": platform.python_version(),
            "fixture": "arquivos JPEG reais de 32x32 pixels, em disco temporário local",
            "count": args.count, "setup_seconds": setup_seconds,
            "catalog_bytes": catalog.stat().st_size,
            "full": asdict(full), "unchanged": asdict(unchanged), "changed": asdict(changed),
            "progress_events": len(events),
            "limitations": "Não representa leitura de originais grandes, HD externo ou compartilhamento de rede.",
        }
        try:
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            report["peak_rss_mib"] = peak / (1024 * 1024 if sys.platform == "darwin" else 1024)
        except ImportError:
            pass
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
