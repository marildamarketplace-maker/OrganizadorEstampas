from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import os
import shutil


PROGRESS_INTERVAL = 1000
COPY_PROGRESS_INTERVAL = 100
CONFLICT_DETAILS_LIMIT = 100


@dataclass
class CollectionResult:
    found: int
    copied: int
    skipped: int
    conflicts: list[str]
    conflicts_omitted: int = 0
    processed: int = 0
    cancelled: bool = False
    found_bytes: int = 0
    planned_count: int = 0
    planned_bytes: int = 0
    copied_bytes: int = 0
    declined: bool = False


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            decimals = 0 if unit == "B" else 1
            return f"{value:.{decimals}f} {unit}"
        value /= 1024
    return f"{size} B"


def iter_files(source: Path, cancel_callback: Callable[[], bool] | None = None):
    """Percorre uma árvore usando scandir, mais eficiente no Windows."""
    pending = [source]
    while pending:
        if cancel_callback and cancel_callback():
            return
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if cancel_callback and cancel_callback():
                    return
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    yield Path(entry.path)


def collect_images(
    source_dirs: list[Path],
    output_dir: Path,
    extensions: set[str],
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
    confirm_callback: Callable[[int, int, int, int], bool] | None = None,
) -> CollectionResult:
    if not source_dirs:
        raise ValueError("Adicione pelo menos uma pasta de entrada.")
    if not extensions:
        raise ValueError("Selecione pelo menos um formato de imagem.")

    sources = [Path(source).expanduser().resolve() for source in source_dirs]
    invalid = [str(source) for source in sources if not source.is_dir()]
    if invalid:
        raise ValueError(
            "Estas pastas de entrada não são válidas: " + " | ".join(invalid)
        )

    output = Path(output_dir).expanduser().resolve()
    selected_extensions = {
        extension.casefold()
        if extension.startswith(".")
        else f".{extension.casefold()}"
        for extension in extensions
    }

    images: list[Path] = []
    image_sizes: dict[Path, int] = {}
    seen_images: set[Path] = set()
    seen_sources: set[Path] = set()
    for source in sources:
        if source in seen_sources:
            continue
        seen_sources.add(source)
        for path in iter_files(source, cancel_callback):
            if path.suffix.casefold() not in selected_extensions:
                continue
            resolved = path.resolve()
            if resolved == output or output in resolved.parents:
                continue
            if resolved in seen_images:
                continue
            seen_images.add(resolved)
            images.append(resolved)
            image_sizes[resolved] = resolved.stat().st_size
            if progress_callback and len(images) % PROGRESS_INTERVAL == 0:
                progress_callback(
                    len(images),
                    0,
                    f"Procurando imagens: {len(images):,} encontradas até agora...",
                )
        if cancel_callback and cancel_callback():
            return CollectionResult(
                found=len(images),
                copied=0,
                skipped=0,
                conflicts=[],
                processed=0,
                cancelled=True,
                found_bytes=sum(image_sizes.values()),
            )

    output.mkdir(parents=True, exist_ok=True)
    planned_destinations: set[str] = set()
    planned_count = 0
    planned_bytes = 0
    for source_file in images:
        if cancel_callback and cancel_callback():
            return CollectionResult(
                found=len(images),
                copied=0,
                skipped=0,
                conflicts=[],
                processed=0,
                cancelled=True,
                found_bytes=sum(image_sizes.values()),
                planned_count=planned_count,
                planned_bytes=planned_bytes,
            )
        destination = output / source_file.parent.name / source_file.name
        destination_key = os.path.normcase(str(destination))
        if not destination.exists() and destination_key not in planned_destinations:
            planned_destinations.add(destination_key)
            planned_count += 1
            planned_bytes += image_sizes[source_file]

    found_bytes = sum(image_sizes.values())
    if progress_callback:
        progress_callback(
            len(images),
            0,
            f"Encontradas {len(images):,} imagens ({format_size(found_bytes)}); "
            f"pendente para copiar: {format_size(planned_bytes)}.",
        )

    if planned_count and confirm_callback and not confirm_callback(
        len(images),
        found_bytes,
        planned_count,
        planned_bytes,
    ):
        return CollectionResult(
            found=len(images),
            copied=0,
            skipped=0,
            conflicts=[],
            processed=0,
            cancelled=True,
            found_bytes=found_bytes,
            planned_count=planned_count,
            planned_bytes=planned_bytes,
            declined=True,
        )

    copied = 0
    copied_bytes = 0
    skipped = 0
    conflicts: list[str] = []

    for position, source_file in enumerate(images, start=1):
        if cancel_callback and cancel_callback():
            processed = position - 1
            return CollectionResult(
                found=len(images),
                copied=copied,
                skipped=skipped,
                conflicts=conflicts,
                conflicts_omitted=max(0, skipped - len(conflicts)),
                processed=processed,
                cancelled=True,
                found_bytes=found_bytes,
                planned_count=planned_count,
                planned_bytes=planned_bytes,
                copied_bytes=copied_bytes,
            )
        destination = output / source_file.parent.name / source_file.name
        if destination.exists():
            skipped += 1
            if len(conflicts) < CONFLICT_DETAILS_LIMIT:
                conflicts.append(f"{source_file} -> {destination}")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination)
            copied += 1
            copied_bytes += image_sizes[source_file]

        if progress_callback and (
            position % COPY_PROGRESS_INTERVAL == 0 or position == len(images)
        ):
            progress_callback(
                position,
                len(images),
                f"Copiando imagens: {position:,} de {len(images):,}; "
                f"{format_size(copied_bytes)} de {format_size(planned_bytes)}.",
            )

    return CollectionResult(
        found=len(images),
        copied=copied,
        skipped=skipped,
        conflicts=conflicts,
        conflicts_omitted=max(0, skipped - len(conflicts)),
        processed=len(images),
        found_bytes=found_bytes,
        planned_count=planned_count,
        planned_bytes=planned_bytes,
        copied_bytes=copied_bytes,
    )
