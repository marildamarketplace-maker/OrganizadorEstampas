"""Operações pequenas que variam entre os sistemas operacionais suportados."""

from __future__ import annotations

from pathlib import Path
import os
import platform
import subprocess


def open_with_default_application(path: Path | str) -> None:
    """Abre um arquivo ou diretório no aplicativo padrão do sistema."""
    target = Path(path).expanduser().resolve()
    system = platform.system()
    if system == "Windows":
        os.startfile(str(target))  # type: ignore[attr-defined]
    elif system == "Darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])
