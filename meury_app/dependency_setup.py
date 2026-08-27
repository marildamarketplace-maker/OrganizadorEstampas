"""Instala dependências somente quando a configuração do ambiente mudou."""

from __future__ import annotations

from pathlib import Path
import hashlib
import importlib.util
import subprocess
import sys


PROJECT_DIR = Path(__file__).resolve().parent.parent
BUNDLES = {
    "core": (["requirements.txt"], ["openpyxl", "PIL", "openai", "faiss", "numpy"]),
    # Mantido como alias para os atalhos de instalação já distribuídos.
    "ai": (["requirements.txt"], ["PIL", "openai", "faiss", "numpy"]),
}


def _fingerprint(requirement_files: list[str]) -> str:
    digest = hashlib.sha256()
    for filename in requirement_files:
        path = PROJECT_DIR / filename
        digest.update(filename.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def ensure_dependencies(bundle: str) -> bool:
    """Retorna True quando foi necessário executar o pip."""
    requirement_files, modules = BUNDLES[bundle]
    fingerprint = _fingerprint(requirement_files)
    marker = Path(sys.prefix) / f".meury-{bundle}-requirements.sha256"
    missing = [module for module in modules if importlib.util.find_spec(module) is None]
    try:
        marker_matches = marker.read_text(encoding="ascii").strip() == fingerprint
    except OSError:
        marker_matches = False
    if marker_matches and not missing:
        print(f"Dependências {bundle} já estão atualizadas.")
        return False

    print(f"Preparando dependências {bundle}. Isso será feito somente quando necessário...")
    for filename in requirement_files:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(PROJECT_DIR / filename)],
            cwd=PROJECT_DIR,
            check=True,
        )
    marker.write_text(fingerprint + "\n", encoding="ascii")
    return True


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in BUNDLES:
        print("Uso: python -m meury_app.dependency_setup core|ai")
        return 2
    try:
        ensure_dependencies(sys.argv[1])
        return 0
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"ERRO: não foi possível instalar as dependências: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
