"""Carregamento central das variaveis locais do aplicativo."""

from __future__ import annotations

from pathlib import Path
import sys


def environment_file_candidates() -> list[Path]:
    """Retorna locais multiplataforma onde o .env pode acompanhar o app."""
    if getattr(sys, "frozen", False):
        # Não aceite primeiro um .env do diretório corrente: abrir o executável
        # por um atalho/pasta não confiável poderia injetar endpoints e chaves.
        candidates = [Path(sys.executable).resolve().parent / ".env"]
    else:
        project_file = Path(__file__).resolve().parent.parent / ".env"
        candidates = [project_file, Path.cwd() / ".env"]

    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def load_local_environment() -> Path | None:
    """Carrega o primeiro .env encontrado sem substituir o ambiente do SO."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return None

    for candidate in environment_file_candidates():
        if candidate.is_file():
            load_dotenv(dotenv_path=candidate, override=False)
            return candidate
    return None
