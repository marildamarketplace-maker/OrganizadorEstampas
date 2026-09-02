"""Contadores e estimativa do lote local, sem incluir o tempo em pausa."""

from datetime import datetime, timedelta
import time


def _duration(seconds: float) -> str:
    hours, remainder = divmod(max(0, int(seconds)), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


class PreviewProgress:
    def __init__(self, total: int, already_ready: int, callback=None):
        self.total = total
        self.already_ready = already_ready
        self.callback = callback
        self.started = time.monotonic()
        self.last_report = float("-inf")
        self.paused_seconds = 0.0
        self.paused_at: float | None = None

    def pause(self):
        self.paused_at = time.monotonic()

    def resume(self):
        if self.paused_at is not None:
            self.paused_seconds += time.monotonic() - self.paused_at
            self.paused_at = None

    def report(self, completed: int, failed: int, *, force=False, finishing=False):
        now = time.monotonic()
        if not force and now - self.last_report < 1:
            return
        self.last_report = now
        processed = completed + failed
        percent = processed / self.total * 100 if self.total else 100.0
        until = self.paused_at if self.paused_at is not None else now
        active_seconds = max(0, until - self.started - self.paused_seconds)
        message = (
            f"Previews: {processed:,}/{self.total:,} ({percent:.1f}% do lote) | "
            f"Concluídos: {completed:,}; falhas: {failed:,} | "
            f"Prontos no catálogo: {self.already_ready + completed:,}"
        )
        if self.paused_at is not None:
            message += " | Pausado; resultados salvos"
        elif finishing:
            message += " | Lote finalizado; resultados salvos"
        elif processed >= 3 and active_seconds >= 1:
            rate = processed / active_seconds
            remaining = max(0, self.total - processed) / rate
            finish_at = datetime.now() + timedelta(seconds=remaining)
            message += (
                f" | {rate:.2f} itens/s | Restante estimado: {_duration(remaining)}"
                f" | Término estimado: {finish_at:%d/%m %H:%M:%S}"
            )
        else:
            message += " | Estimativa: calculando com as primeiras imagens"
        if self.callback:
            self.callback(processed, self.total, message)
