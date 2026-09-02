"""Mede previews em um acervo sintético isolado, sem tocar em originais do usuário."""

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=60)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count precisa ser positivo")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from PIL import Image, ImageDraw

    with tempfile.TemporaryDirectory(prefix="meury-preview-benchmark-") as temporary:
        root = Path(temporary).resolve()
        os.environ["MEURY_APP_DATA_PATH"] = str(root / "dados")
        from meury_app.indexer import build_index
        from meury_app.preview_generator import generate_pending_previews

        source = root / "Estampas com acentuação"
        rng = random.Random(42)
        paths = []
        originals = {}
        for number in range(args.count):
            suffix, mode = [(".jpg", "RGB"), (".png", "RGBA"), (".tiff", "RGB")][number % 3]
            path = source / str(number) / f"{number}-A{suffix}"
            path.parent.mkdir(parents=True)
            with Image.new(mode, (3000, 2000), "#f8e2d2") as image:
                draw = ImageDraw.Draw(image)
                for _ in range(1500):
                    x, y = rng.randrange(3000), rng.randrange(2000)
                    color = tuple(rng.randrange(256) for _ in range(3))
                    if mode == "RGBA":
                        color += (rng.randrange(100, 256),)
                    draw.ellipse((x, y, x + rng.randrange(5, 160), y + rng.randrange(5, 160)), fill=color)
                image.save(path)
            paths.append(path)
            originals[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        build_index(source)
        messages = []
        started = time.monotonic()
        result = generate_pending_previews(
            source, preview_dir=root / "previews",
            progress_callback=lambda current, total, message: messages.append(message),
        )
        wall_seconds = time.monotonic() - started
        assert result.completed == args.count and result.failed == 0
        previews = list((root / "previews").glob("*"))
        assert len(previews) == args.count
        for preview in previews:
            with Image.open(preview) as image:
                image.load()
                assert max(image.size) == 1024 and image.size == (1024, 683)
        for path in paths:
            assert originals[str(path)] == hashlib.sha256(path.read_bytes()).hexdigest()
        repeated = generate_pending_previews(source, preview_dir=root / "previews")
        assert repeated.pending == 0
        report = {
            "platform": platform.platform(), "python": platform.python_version(),
            "fixture": "3000x2000 pixels, JPEG/PNG com alpha/TIFF; desenhos sintéticos, disco local",
            "result": asdict(result), "wall_seconds": wall_seconds,
            "previews_per_second": args.count / wall_seconds,
            "preview_bytes": sum(path.stat().st_size for path in previews),
            "progress_events": len(messages), "originals_unchanged": True,
            "last_message": messages[-1] if messages else "",
            "limitations": "Amostra sintética; não é uma execução de 195 mil previews nem uma previsão para o HD real.",
        }
        try:
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            report["peak_rss_mib"] = peak / (1024 * 1024 if sys.platform == "darwin" else 1024)
        except ImportError:
            pass
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
