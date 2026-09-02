"""Progresso limitado por tempo, com o contrato legado (quantidade, mensagem)."""

from collections.abc import Callable
import time


class IndexProgress:
    def __init__(self, callback: Callable[[int, str], None] | None = None):
        self.callback = callback
        self.phase = ""
        self.last_report = 0.0

    def report(self, phase: str, current: int = 0, total: int | None = None,
               *, detail: str = "", force: bool = False) -> None:
        now = time.monotonic()
        if not force and phase == self.phase and now - self.last_report < 1.0:
            return
        self.phase, self.last_report = phase, now
        amount = f"{current:,}"
        if total is not None:
            percent = current / total * 100 if total else 100.0
            amount += f"/{total:,} ({percent:.1f}% da etapa)"
        message = f"{phase}: {amount} registros"
        if detail:
            message += f" | {detail}"
        if self.callback is not None:
            self.callback(current, message)
