from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import shutil


@dataclass
class CollectionResult:
    found: int
    copied: int
    skipped: int
    conflicts: list[str]


def collect_images(
    source_dirs: list[Path],
    output_dir: Path,
    extensions: set[str],
    progress_callback: Callable[[int, int, str], None] | None = None,
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
    seen_images: set[Path] = set()
    seen_sources: set[Path] = set()
    for source in sources:
        if source in seen_sources:
            continue
        seen_sources.add(source)
        for path in source.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in selected_extensions:
                continue
            resolved = path.resolve()
            if resolved == output or output in resolved.parents:
                continue
            if resolved in seen_images:
                continue
            seen_images.add(resolved)
            images.append(resolved)
            if progress_callback and len(images) % 1000 == 0:
                progress_callback(
                    len(images),
                    0,
                    f"Procurando imagens: {len(images):,} encontradas até agora...",
                )

    output.mkdir(parents=True, exist_ok=True)
    copied = 0
    skipped = 0
    conflicts: list[str] = []

    for position, source_file in enumerate(images, start=1):
        destination = output / source_file.parent.name / source_file.name
        if destination.exists():
            skipped += 1
            conflicts.append(f"{source_file} -> {destination}")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination)
            copied += 1

        if progress_callback:
            progress_callback(
                position,
                len(images),
                f"Copiando imagens: {position:,} de {len(images):,}",
            )

    return CollectionResult(
        found=len(images),
        copied=copied,
        skipped=skipped,
        conflicts=conflicts,
    )
