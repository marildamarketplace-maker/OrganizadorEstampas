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
import statistics
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=195000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--temp-parent", type=Path,
                        help="Pasta para fixtures descartáveis; nunca usa originais existentes.")
    parser.add_argument("--fixture", choices=["jpeg", "pdf-header"], default="jpeg")
    args = parser.parse_args()
    if args.repeats < 1 or args.count < 20 + args.repeats * 10:
        parser.error("--repeats deve ser positivo; --count >= 20 + 10 * repeats")

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from PIL import Image

    with tempfile.TemporaryDirectory(prefix="meury-index-benchmark-", dir=args.temp_parent) as temporary:
        root = Path(temporary).resolve()
        os.environ["MEURY_APP_DATA_PATH"] = str(root / "dados")
        from meury_app.indexer import build_index, update_index_incremental

        source = root / "Estampas com espaços e acentuação"
        buffer = io.BytesIO()
        Image.new("RGB", (32, 32), (33, 99, 150)).save(buffer, format="JPEG")
        image_bytes = buffer.getvalue()
        if args.fixture == "pdf-header":
            image_bytes = b"%PDF-1.4\n"

        def image_path(number):
            design = str(10000 + number // 100)
            suffix = ".jpg" if args.fixture == "jpeg" else ".pdf"
            return source / f"{design} COLEÇÃO" / f"{design}-A{number % 100}{suffix}"

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
        print(f"Completo: {full.elapsed_seconds:.3f}s", flush=True)
        assert full.total_files == args.count and len(index) == args.count
        del index
        gc.collect()
        catalog = root / "dados" / "indice_estampas.jsonl"
        catalog_mtime = catalog.stat().st_mtime_ns
        unchanged_runs = []
        for iteration in range(args.repeats):
            index, unchanged = update_index_incremental(source, progress)
            assert unchanged.unchanged_files == args.count
            assert unchanged.hashed_files == 0 and unchanged.errors == 0
            assert catalog.stat().st_mtime_ns == catalog_mtime
            assert unchanged.performance["counters"]["catalog_bytes_written"] == 0
            assert unchanged.performance["counters"]["journal_bytes_written"] == 0
            unchanged_runs.append(asdict(unchanged))
            print(f"Sem alterações {iteration + 1}: {unchanged.elapsed_seconds:.3f}s", flush=True)
            del index
            gc.collect()

        # Alteração, movimento e remoção reais, mantendo arquivos válidos.
        changed_runs = []
        moved_paths = {number: image_path(number) for number in range(10, 20)}
        for iteration in range(args.repeats):
            for number in range(10):
                with image_path(number).open("ab") as stream:
                    stream.write(b"changed")
            for number in range(10, 20):
                old = moved_paths[number]
                new = old.with_name(old.stem + "-renamed" + old.suffix)
                old.rename(new)
                moved_paths[number] = new
            for number in range(20 + iteration * 10, 30 + iteration * 10):
                image_path(number).unlink()
            for number in range(args.count + iteration * 10, args.count + (iteration + 1) * 10):
                path = image_path(number)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(image_bytes + str(number).encode("ascii"))
            index, changed = update_index_incremental(source, progress)
            assert changed.changed_files == 10 and changed.moved_files == 10
            assert changed.added_files == 10 and changed.removed_files == 10
            assert changed.hashed_files == 30 and changed.unchanged_files == args.count - 30
            assert len(index) == args.count and changed.errors == 0
            assert changed.performance["counters"]["catalog_writes"] == 1
            assert changed.performance["counters"]["journal_records_written"] == 30
            changed_runs.append(asdict(changed))
            print(f"Poucas alterações {iteration + 1}: {changed.elapsed_seconds:.3f}s", flush=True)
            del index
            gc.collect()

        def timing_summary(runs):
            values = [run["elapsed_seconds"] for run in runs]
            return {"median_seconds": statistics.median(values), "worst_seconds": max(values)}

        report = {
            "platform": platform.platform(), "python": platform.python_version(),
            "fixture": "JPEG 32x32" if args.fixture == "jpeg" else "cabeçalhos PDF sintéticos; não são PDFs completos",
            "temporary_root": str(root), "cache": "quente/não controlado; sem limpeza de cache do SO",
            "repeats": args.repeats,
            "count": args.count, "setup_seconds": setup_seconds,
            "catalog_bytes": catalog.stat().st_size,
            "full": asdict(full), "unchanged": asdict(unchanged), "changed": asdict(changed),
            "unchanged_runs": unchanged_runs, "changed_runs": changed_runs,
            "timing_summary": {"unchanged": timing_summary(unchanged_runs),
                               "changed": timing_summary(changed_runs)},
            "progress_events": len(events),
            "limitations": "Fixtures pequenas descartáveis. Não comprova desempenho do acervo real, HD externo ou rede; nenhuma medição de cache frio.",
        }
        try:
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            report["peak_rss_mib"] = peak / (1024 * 1024 if sys.platform == "darwin" else 1024)
        except ImportError:
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes
                class MemoryCounters(ctypes.Structure):
                    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                        (name, ctypes.c_size_t) for name in (
                            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                            "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                            "PagefileUsage", "PeakPagefileUsage")]
                counters = MemoryCounters()
                counters.cb = ctypes.sizeof(counters)
                get_current_process = ctypes.windll.kernel32.GetCurrentProcess
                get_current_process.restype = wintypes.HANDLE
                get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
                get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
                if get_memory(get_current_process(), ctypes.byref(counters), counters.cb):
                    report["peak_rss_mib"] = counters.PeakWorkingSetSize / 1024 ** 2
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
