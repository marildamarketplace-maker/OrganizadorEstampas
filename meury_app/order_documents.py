"""Discovery, validation and staging of order documents."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import shutil
import warnings

from PIL import Image, ImageOps

SUPPORTED_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png'}


@dataclass(frozen=True)
class OrderDocument:
    identifier: str
    pages: tuple[Path, ...]
    error: str = ''

    @property
    def is_pdf(self):
        return len(self.pages) == 1 and self.pages[0].suffix.casefold() == '.pdf'

    def digest(self):
        digest = hashlib.sha256()
        if self.is_pdf:
            with self.pages[0].open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
        else:
            digest.update(b'order-images-v1')
            for path in self.pages:
                content = hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        content.update(block)
                digest.update(content.digest())
        return digest.hexdigest()


def discover_documents(directory: Path) -> list[OrderDocument]:
    groups = {}
    if not directory.exists():
        return []
    for path in sorted(directory.iterdir(), key=lambda p: p.name.casefold()):
        if path.is_symlink() or not path.is_file() or path.suffix.casefold() not in SUPPORTED_EXTENSIONS:
            continue
        match = re.fullmatch(r'(.+)_([1-9][0-9]*)', path.stem) if path.suffix.casefold() != '.pdf' else None
        identifier = match[1] if match else path.stem
        groups.setdefault(identifier.casefold(), []).append((identifier, int(match[2]) if match else None, path))
    documents = []
    for items in groups.values():
        identifier = items[0][0]
        numbered = [number for _, number, _ in items if number is not None]
        error = ''
        if len(items) > 1 and any(path.suffix.casefold() == '.pdf' for _, _, path in items):
            error = 'PDF e outros arquivos com o mesmo identificador.'
        elif numbered:
            if len(numbered) != len(items):
                error = 'Imagem sem sufixo junto de páginas numeradas.'
            elif len(set(numbered)) != len(numbered):
                error = 'Número de página repetido.'
            elif sorted(numbered) != list(range(1, len(numbered) + 1)):
                error = 'Sequência incompleta: use páginas contínuas começando em _1.'
        elif len(items) > 1:
            error = 'Mais de uma imagem sem número de página.'
        pages = tuple(item[2] for item in sorted(items, key=lambda item: (item[1] or 0, item[2].name.casefold())))
        documents.append(OrderDocument(identifier, pages, error))
    return documents


def prepare_document(document: OrderDocument, destination: Path):
    """Normalize images into one multipage PDF for both extractors."""
    if document.error:
        raise ValueError(document.error)
    images = []
    try:
        for path in document.pages:
            if path.is_symlink() or not path.is_file():
                raise ValueError(f'Arquivo indisponível: {path.name}')
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(path) as source:
                    if source.format not in {'JPEG', 'PNG'}:
                        raise ValueError(f'Formato de imagem incompatível: {path.name}')
                    if getattr(source, 'n_frames', 1) != 1:
                        raise ValueError(f'Imagem animada não suportada: {path.name}')
                    source.load()
                    with ImageOps.exif_transpose(source) as oriented:
                        rgba = oriented.convert('RGBA')
                        try:
                            rgb = Image.new('RGB', rgba.size, 'white')
                            rgb.paste(rgba, mask=rgba.getchannel('A'))
                            images.append(rgb)
                        finally:
                            rgba.close()
        images[0].save(destination, 'PDF', save_all=True, append_images=images[1:], resolution=72,
                       quality=100, subsampling=0)
    except (Image.DecompressionBombWarning, Image.DecompressionBombError, OSError) as exc:
        raise ValueError(f'Não foi possível validar as imagens do pedido: {exc}') from exc
    finally:
        for image in images:
            image.close()


def move_document(document: OrderDocument, completed: Path) -> list[Path]:
    completed.mkdir(parents=True, exist_ok=True)
    counter = 1
    while True:
        prefix = '' if counter == 1 else f'concluido{counter}_'
        destinations = [completed / (prefix + path.name) for path in document.pages]
        existing = {path.name.casefold() for path in completed.iterdir()}
        if all(path.name.casefold() not in existing for path in destinations):
            break
        counter += 1
    moved = []
    try:
        for source, target in zip(document.pages, destinations):
            # Exclusive reservation prevents overwriting an independently created file.
            with target.open('xb'):
                pass
            try:
                shutil.move(str(source), str(target))
            except OSError:
                target.unlink(missing_ok=True)
                raise
            moved.append((source, target))
    except OSError as exc:
        rollback_errors = []
        for source, target in reversed(moved):
            try:
                if source.exists():
                    raise OSError(f'Origem ocupada: {source}')
                shutil.move(str(target), str(source))
            except OSError as rollback:
                rollback_errors.append(f'{target}: {rollback}')
        raise OSError(f'{exc}; restauração: {rollback_errors or "concluída"}') from exc
    return destinations
