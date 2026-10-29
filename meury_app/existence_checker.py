"""Verifica arquivos diretamente no disco, sem depender do índice."""
import os
import re
from pathlib import Path

from .config import SUPPORTED_EXTENSIONS
from .processor import image_search_names


def parse_existence_designs(text):
    """Aceita código com variante opcional, somente neste verificador."""
    designs = []
    for part in text.split(';'):
        part = part.strip().upper()
        if not part:
            continue
        if not re.fullmatch(r'[A-Z0-9]+(?:-[A-Z0-9]+)*', part):
            raise ValueError(f'Código inválido: {part}. Use 6162; 6162-A; MV23069-B.')
        code, separator, variant = part.rpartition('-')
        design = (code, variant) if separator else (part, '')
        if design not in designs:
            designs.append(design)
    if not designs:
        raise ValueError('Informe pelo menos um código, com ou sem variante.')
    return designs


def check_existence(folder, text, progress=None, cancel=None):
    designs = parse_existence_designs(text)
    if not str(folder).strip():
        raise ValueError('Escolha uma pasta para verificar.')
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise ValueError('A pasta informada não existe ou está indisponível.')
    root = root.resolve()
    matches = {f'{code}-{variant}' if variant else code: [] for code, variant in designs}
    targets = {}
    for code, variant in designs:
        label = f'{code}-{variant}' if variant else code
        for name in image_search_names(code, variant or 'A'):
            targets.setdefault(name.casefold(), []).append(label)
    errors = []
    scanned = 0
    cancelled = False
    for directory, _, filenames in os.walk(root, onerror=lambda exc: errors.append(str(exc)), followlinks=False):
        if cancel is not None and cancel.is_set():
            cancelled = True
            break
        if progress:
            progress(f'Verificando pasta: {directory}')
        for filename in filenames:
            if cancel is not None and cancel.is_set():
                cancelled = True
                break
            path = Path(directory) / filename
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            scanned += 1
            stem = path.stem.strip().casefold()
            # Aceita o nome exato e descrições após espaço ou ponto,
            # preservando os limites de código e variante.
            candidates = {stem}
            candidates.update(stem[:i] for i, char in enumerate(stem) if char in ' .')
            for candidate in candidates:
                for design in targets.get(candidate, ()):
                    if str(path) not in matches[design]:
                        matches[design].append(str(path))
        if cancelled:
            break
    return matches, errors, scanned, cancelled
