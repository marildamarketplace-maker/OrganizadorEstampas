"""Execução controlável de pequenos ou grandes lotes de análise local."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import os
import threading
import time
import traceback

from .config import ANALYSIS_LOG_FILE, ensure_app_dir
from .image_analyzer import LocalImageAnalyzer
from .indexer import append_analysis_result


@dataclass
class AnalysisBatchResult:
    selected: int
    completed: int
    succeeded: int
    errors: int
    cancelled: bool
    elapsed_seconds: float


def append_analysis_log(message: str) -> None:
    ensure_app_dir()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with ANALYSIS_LOG_FILE.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"[{timestamp}] {message}\n")
        stream.flush()
        os.fsync(stream.fileno())


def terminal_log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] Lote de IA: {message}", flush=True)


def run_analysis_batch(
    records: list[dict],
    *,
    analyzer: LocalImageAnalyzer | None = None,
    run_event: threading.Event | None = None,
    cancel_event: threading.Event | None = None,
    current_callback: Callable[[str], None] | None = None,
    progress_callback: Callable[[int, int, int, str, float], None] | None = None,
) -> AnalysisBatchResult:
    """Analisa e salva cada item antes de avançar para o próximo."""
    engine = analyzer or LocalImageAnalyzer()
    running = run_event or threading.Event()
    if run_event is None:
        running.set()
    cancellation = cancel_event or threading.Event()
    started = time.monotonic()
    succeeded = errors = completed = 0
    append_analysis_log(f"Lote iniciado com {len(records)} imagem(ns).")
    terminal_log(f"lote iniciado com {len(records)} imagem(ns).")
    # Falhas globais (dependência ausente/modelo indisponível) devem parar antes
    # do lote; somente falhas específicas de uma imagem são registradas e puladas.
    load = getattr(engine, "load", None)
    if callable(load):
        try:
            load()
        except Exception as exc:
            append_analysis_log(f"FALHA AO CARREGAR MODELO | {type(exc).__name__}: {exc}")
            append_analysis_log("DIAGNÓSTICO COMPLETO\n" + traceback.format_exc())
            terminal_log(f"falha ao carregar o modelo: {type(exc).__name__}: {exc}")
            raise

    for record in records:
        while not running.wait(timeout=0.2):
            if cancellation.is_set():
                break
        if cancellation.is_set():
            break

        path = str(record.get("path", ""))
        terminal_log(f"processando {completed + 1} de {len(records)}: {path}")
        if current_callback:
            current_callback(path)
        try:
            result = engine.analyze(Path(path))
            metadata = result.to_dict()
            metadata["analysis_updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            append_analysis_result(record, metadata=metadata)
            succeeded += 1
            append_analysis_log(f"SUCESSO | {path}")
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            fatal_api_error = isinstance(exc, RuntimeError) and any(
                marker in str(exc) for marker in (
                    "API OpenAI", "chave da API", "falta de créditos",
                    "conectar à API",
                )
            )
            if fatal_api_error:
                append_analysis_log(f"FALHA DA API | {path} | {message}")
                append_analysis_log("DIAGNÓSTICO COMPLETO\n" + traceback.format_exc())
                terminal_log(f"falha da API: {message}")
                raise
            append_analysis_result(record, error=message)
            errors += 1
            append_analysis_log(f"ERRO | {path} | {message}")
            append_analysis_log("DIAGNÓSTICO COMPLETO\n" + traceback.format_exc())
            terminal_log(f"erro em {path}: {message}")
        completed += 1
        if progress_callback:
            progress_callback(
                completed, len(records), errors, path, time.monotonic() - started
            )

    elapsed = time.monotonic() - started
    release = getattr(engine, "release", None)
    if callable(release):
        release()
    cancelled = cancellation.is_set()
    append_analysis_log(
        f"Lote {'cancelado' if cancelled else 'concluído'} | "
        f"processadas={completed} sucessos={succeeded} erros={errors} "
        f"tempo={elapsed:.1f}s"
    )
    terminal_log(
        f"lote {'cancelado' if cancelled else 'concluído'}; "
        f"processadas={completed}; sucessos={succeeded}; erros={errors}; "
        f"tempo={elapsed:.1f}s."
    )
    return AnalysisBatchResult(
        selected=len(records), completed=completed, succeeded=succeeded,
        errors=errors, cancelled=cancelled, elapsed_seconds=elapsed,
    )
