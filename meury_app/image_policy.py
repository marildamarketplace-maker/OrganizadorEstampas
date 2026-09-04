"""Política de segurança para imagens grandes usadas pelo aplicativo."""

from __future__ import annotations


# O Pillow emite um aviso acima de MAX_IMAGE_PIXELS e lança
# DecompressionBombError acima do dobro desse valor. Artes de impressão com
# cerca de 100 MP são esperadas neste catálogo, então aceitamos até 150 MP sem
# aviso e ainda bloqueamos imagens acima de 300 MP.
MAX_IMAGE_PIXELS = 150_000_000


def configure_pillow_limits() -> None:
    """Aceita artes de alta resolução sem desativar a proteção anti-bomba."""
    try:
        from PIL import Image
    except ImportError:
        return
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
