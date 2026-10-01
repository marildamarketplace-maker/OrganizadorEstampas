"""Persistência do último scan local concluído com sucesso."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Iterable


STATE_FILENAME = "ultima_atualizacao_indice.json"


def _normalized_sources(source_dirs: Iterable[Path | str]) -> list[str]:
    return [
        str(Path(source).expanduser().resolve(strict=False))
        for source in source_dirs
    ]


def state_file_for(index_file: Path) -> Path:
    return index_file.with_name(STATE_FILENAME)


def index_updated_today(
    source_dirs: Iterable[Path | str],
    *,
    state_file: Path,
    now: datetime | None = None,
) -> bool:
    """Confirma que as mesmas origens tiveram um scan bem-sucedido hoje."""
    current = now or datetime.now().astimezone()
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return (
        payload.get("completed_on") == current.date().isoformat()
        and payload.get("source_dirs") == _normalized_sources(source_dirs)
    )


def record_index_update(
    source_dirs: Iterable[Path | str],
    *,
    state_file: Path,
    now: datetime | None = None,
) -> None:
    """Grava atomicamente o momento do último scan concluído."""
    current = now or datetime.now().astimezone()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_file.with_name(f"{state_file.name}.tmp.{os.getpid()}")
    try:
        temporary.write_text(
            json.dumps(
                {
                    "completed_on": current.date().isoformat(),
                    "completed_at": current.isoformat(timespec="seconds"),
                    "source_dirs": _normalized_sources(source_dirs),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(state_file)
    finally:
        temporary.unlink(missing_ok=True)
