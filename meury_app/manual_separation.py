"""Separação manual usando as mesmas regras de busca dos pedidos em lote."""
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata

from .indexer import image_key
from .processor import (
    image_search_names, exclusive_image_matches, validated_copy_source,

)

from .platform_utils import publish_without_overwrite


def copy_without_overwrite(source: Path, target: Path) -> None:
    """Copia para temporário e publica sem substituir destino concorrente."""
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".separar-", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        shutil.copy2(source, temporary)
        publish_without_overwrite(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def folder_component(value: str) -> str:
    value = unicodedata.normalize("NFC", value.strip())
    if (not value or value in {'.', '..'} or value.endswith(('.', ' '))
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
            or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?', value)):
        raise ValueError('Cliente e categoria devem ser nomes válidos de pasta, sem barras.')
    return value


def parse_designs(text: str) -> list[tuple[str, str]]:
    designs = []
    for part in text.split(';'):
        part = part.strip().upper()
        if not part:
            continue
        match = re.fullmatch(r'([A-Z0-9]+(?:-[A-Z0-9]+)*)-([A-Z0-9]+)', part)
        if not match:
            raise ValueError(f'Estampa inválida: {part}. Use código-variante; exemplo: 6162-A; MV23069-B.')
        if match.groups() not in designs:
            designs.append(match.groups())
    if not designs:
        raise ValueError('Informe pelo menos uma estampa no formato código-variante.')
    return designs


def separate_designs(client, category, text, output, index, progress=None, cancel=None):
    client, category = folder_component(client), folder_component(category)
    designs = parse_designs(text)
    if not str(output).strip():
        raise ValueError("Escolha uma pasta de saída.")
    root = Path(output).expanduser().resolve()
    destination = root / client / category
    # Não permite que uma pasta preexistente redirecione a escrita para fora da saída.
    for folder in (root / client, destination):
        if folder.is_symlink() or not folder.resolve().is_relative_to(root):
            raise ValueError('A pasta de destino não pode redirecionar para outro local.')
    destination.mkdir(parents=True, exist_ok=True)
    results = []
    for number, (code, variant) in enumerate(designs, 1):
        if cancel is not None and cancel.is_set():
            results.append("CANCELADO — arquivos já copiados foram preservados.")
            break
        name = f'{code}-{variant}'
        if progress:
            progress(f'Buscando {name} ({number}/{len(designs)})...')
        names = image_search_names(code, variant)
        matches = list(dict.fromkeys(path for candidate in names
                                   for path in index.get(image_key(code, candidate), [])))
        fallback = not matches
        if fallback:
            matches = exclusive_image_matches(index, names)
        if len(matches) > 1 and not fallback:
            results.append(f'{name}: DUPLICADO — nenhuma cópia; confira as origens: ' + ' | '.join(matches))
            continue
        if not matches:
            results.append(f'{name}: NÃO ENCONTRADO')
        for match in matches:
            if cancel is not None and cancel.is_set():
                break
            try:
                source = validated_copy_source(Path(match), index)
                target = destination / source.name.upper()
                if target.is_symlink():
                    raise ValueError(f'Destino é um link simbólico: {target}')
                if target.exists():
                    results.append(f'{name}: JÁ EXISTE — {target.name}')
                else:
                    try:
                        copy_without_overwrite(source, target)
                    except FileExistsError:
                        results.append(f'{name}: JÁ EXISTE — {target.name}')
                        continue
                    results.append(f'{name}: COPIADO — {target.name}')
            except OSError as exc:
                results.append(f'{name}: FALHA — {exc}')
    return destination, results
