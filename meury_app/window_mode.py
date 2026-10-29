"""Modo de tela compartilhado pelo login e pela aplicação."""
import tkinter as tk


def configure_maximized(root):
    """Preenche a área de trabalho preservando os controles da janela."""
    root.resizable(True, True)
    root.attributes('-fullscreen', False)
    try:
        root.state('zoomed')
    except tk.TclError:
        # Gerenciadores de janelas X11 usam este atributo em vez do estado.
        root.attributes('-zoomed', True)
