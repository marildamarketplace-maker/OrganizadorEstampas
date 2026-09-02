"""Rasterização limitada da primeira página, sem alterar o PDF original."""

from contextlib import closing
import math
from pathlib import Path
import threading


# PDFium não permite chamadas simultâneas, mesmo em documentos diferentes.
# O bitmap é copiado e fechado sob o lock; a compressão continua em paralelo.
_PDFIUM_LOCK = threading.Lock()


def render_first_page(source: Path, max_edge: int):
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError(
            "O suporte a PDF requer pypdfium2. Reinicie pelo inicializador "
            "do aplicativo para instalar as dependências atualizadas."
        ) from exc
    if max_edge < 1:
        raise ValueError("A dimensão do preview precisa ser positiva.")
    with _PDFIUM_LOCK:
        try:
            with closing(pdfium.PdfDocument(source)) as document:
                if not len(document):
                    raise ValueError("O PDF não possui páginas.")
                with closing(document[0]) as page:
                    width, height = page.get_size()
                    if not all(math.isfinite(edge) and edge > 0 for edge in (width, height)):
                        raise ValueError("A primeira página do PDF possui dimensões inválidas.")
                    with closing(page.render(
                        scale=max_edge / max(width, height),
                        fill_color=(255, 255, 255, 255), rev_byteorder=True,
                    )) as bitmap:
                        with bitmap.to_pil() as image:
                            # to_pil pode compartilhar o buffer nativo do PDFium.
                            return image.copy()
        except pdfium.PdfiumError as exc:
            raise ValueError(f"Não foi possível abrir ou renderizar o PDF: {exc}") from exc
