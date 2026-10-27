__version__ = "1.0.0"

from .image_policy import configure_pillow_limits


# Aplica a política antes que qualquer módulo do aplicativo abra uma imagem.
configure_pillow_limits()
