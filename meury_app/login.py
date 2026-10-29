"""Janela de autenticação anterior à construção das abas."""
from __future__ import annotations

from pathlib import Path
from queue import Empty, Queue
import sqlite3
import threading
import tkinter as tk
from tkinter import ttk

from .user_store import autenticar_usuario
from .config import APP_NAME


def solicitar_login(db_path: Path, *, root=None) -> dict | None:
    owns_root = root is None
    root = root if root is not None else tk.Tk()
    root.title(f'{APP_NAME} — Entrar')
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=28)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Entre para acessar as abas', font=('Arial', 15, 'bold')).grid(
        row=0, column=0, columnspan=2, pady=(0, 20))
    usuario = tk.StringVar(master=root)
    senha = tk.StringVar(master=root)
    status = tk.StringVar(master=root)
    ttk.Label(frame, text='Usuário').grid(row=1, column=0, sticky='w')
    username_entry = ttk.Entry(frame, textvariable=usuario, width=32)
    username_entry.grid(row=2, column=0, columnspan=2, pady=(4, 12))
    ttk.Label(frame, text='Senha').grid(row=3, column=0, sticky='w')
    password_entry = ttk.Entry(frame, textvariable=senha, show='•', width=32)
    password_entry.grid(row=4, column=0, columnspan=2, pady=(4, 12))
    ttk.Label(frame, textvariable=status, wraplength=320).grid(
        row=5, column=0, columnspan=2, pady=(0, 12))
    results = Queue()
    identity = None
    busy = False
    failures = 0
    callbacks = set()

    def schedule(delay, callback):
        callbacks.add(root.after(delay, callback))

    def cancel():
        root.quit()

    def unlock():
        nonlocal busy
        busy = False
        button.configure(state='normal')
        password_entry.focus_set()

    def poll():
        nonlocal identity, failures
        try:
            result, unavailable = results.get_nowait()
        except Empty:
            schedule(50, poll)
            return
        if result is not None:
            identity = result
            root.quit()
            return
        failures += 1
        status.set('Não foi possível acessar o banco de usuários.' if unavailable
                   else 'Usuário ou senha inválidos.')
        # Retentativas limitadas na janela, sem bloquear a interface.
        schedule(min(30, failures) * 1000, unlock)

    def submit(event=None):
        nonlocal busy
        if busy:
            return
        if not usuario.get().strip() or not senha.get():
            status.set('Preencha usuário e senha.')
            return
        busy = True
        button.configure(state='disabled')
        status.set('Verificando...')
        username, password = usuario.get(), senha.get()
        senha.set('')

        def worker():
            try:
                results.put((autenticar_usuario(db_path, username, password), False))
            except (sqlite3.Error, OSError, ValueError):
                results.put((None, True))

        threading.Thread(target=worker, daemon=True).start()
        schedule(50, poll)

    button = ttk.Button(frame, text='Entrar', command=submit)
    button.grid(row=6, column=0, sticky='ew', padx=(0, 6))
    ttk.Button(frame, text='Cancelar', command=cancel).grid(row=6, column=1, sticky='ew')
    root.bind('<Return>', submit)
    root.bind('<Escape>', lambda event: cancel())
    root.protocol('WM_DELETE_WINDOW', cancel)
    username_entry.focus_set()
    root.mainloop()
    for callback_id in callbacks:
        root.after_cancel(callback_id)
    root.unbind('<Return>')
    root.unbind('<Escape>')
    frame.destroy()
    # Release Tcl variables on the main thread, including on cancellation.
    usuario = senha = status = None
    if owns_root or identity is None:
        root.destroy()
    return identity
