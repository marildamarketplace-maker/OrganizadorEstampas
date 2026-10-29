"""Ícone compartilhado pelas janelas de login e do aplicativo."""
from pathlib import Path
import sys
import tkinter as tk


def apply_app_icon(root):
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    path = base / 'assets' / 'meury-logo.png'
    if not path.is_file():
        return
    # A referência deve acompanhar a janela para o Tk não liberar a imagem.
    root.meury_icon = tk.PhotoImage(master=root, file=str(path))
    root.iconphoto(True, root.meury_icon)
    if sys.platform == 'darwin':
        _apply_macos_dock_icon(path)


def _apply_macos_dock_icon(path):
    """Altera o ícone do processo atual sem modificar a instalação do Python."""
    import ctypes
    import threading
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError('O ícone do Dock deve ser configurado na thread principal')
    ctypes.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit')
    objc = ctypes.CDLL('/usr/lib/libobjc.A.dylib')
    pointer = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.objc_getClass.restype = pointer
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = pointer
    send = ctypes.CFUNCTYPE(pointer, pointer, pointer)(('objc_msgSend', objc))
    send_pointer = ctypes.CFUNCTYPE(pointer, pointer, pointer, pointer)(('objc_msgSend', objc))
    send_string = ctypes.CFUNCTYPE(pointer, pointer, pointer, ctypes.c_char_p)(('objc_msgSend', objc))
    send_void = ctypes.CFUNCTYPE(None, pointer, pointer, pointer)(('objc_msgSend', objc))
    selector = objc.sel_registerName
    pool = send(objc.objc_getClass(b'NSAutoreleasePool'), selector(b'new'))
    image = None
    try:
        filename = send_string(objc.objc_getClass(b'NSString'), selector(b'stringWithUTF8String:'), str(path).encode('utf-8'))
        allocated = send(objc.objc_getClass(b'NSImage'), selector(b'alloc'))
        image = send_pointer(allocated, selector(b'initWithContentsOfFile:'), filename)
        if not image:
            raise ValueError('Não foi possível carregar o ícone MV')
        application = send(objc.objc_getClass(b'NSApplication'), selector(b'sharedApplication'))
        send_void(application, selector(b'setApplicationIconImage:'), image)
    finally:
        if image:
            ctypes.CFUNCTYPE(None, pointer, pointer)(('objc_msgSend', objc))(image, selector(b'release'))
        ctypes.CFUNCTYPE(None, pointer, pointer)(('objc_msgSend', objc))(pool, selector(b'drain'))
