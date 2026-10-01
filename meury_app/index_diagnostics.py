"""Métricas locais de uma execução; não compartilha estado entre threads."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import time


ACTIVE_METRICS = ContextVar("index_metrics", default=None)


def count(name, amount=1):
    metrics = ACTIVE_METRICS.get()
    if metrics is not None:
        counters = metrics["counters"]
        counters[name] = counters.get(name, 0) + amount


@contextmanager
def stage(name):
    started = time.perf_counter()
    try:
        yield
    finally:
        metrics = ACTIVE_METRICS.get()
        if metrics is not None:
            durations = metrics["seconds"]
            durations[name] = durations.get(name, 0.0) + time.perf_counter() - started


def measured(name):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with stage(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def new_metrics():
    return {
        "seconds": {},
        "counters": dict.fromkeys((
            "catalog_records_read", "catalog_bytes_read", "analysis_events_read",
            "directories_read", "entries_read", "entry_stat_calls", "candidate_stat_calls",
            "validation_files", "hash_files", "hash_bytes_read",
            "checkpoints", "journal_records_written", "journal_bytes_written",
            "journal_bytes_read", "recovered_candidates", "catalog_writes",
            "catalog_records_written", "catalog_bytes_written",
            "commit_intent_bytes_written", "recovered_commits",
        ), 0),
        "timing_note": "Tempos inclusivos: load_total e scan_total incluem suas subetapas; não somar todos os campos.",
    }
