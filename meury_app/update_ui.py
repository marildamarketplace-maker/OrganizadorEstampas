"""Tk update controller; workers communicate exclusively through a queue."""
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from . import __version__
from .updates import check, download
from .update_install import target_path, executable, state_dir


class UpdateController:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.events = queue.Queue()
        self.running = False
        self.window = None
        self.status = tk.StringVar(master=self.root)
        menu = tk.Menu(self.root)
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label=f'Versão {__version__}', state='disabled')
        help_menu.add_command(label='Buscar atualização', command=lambda: self.check(True))
        menu.add_cascade(label='Ajuda', menu=help_menu)
        self.root.configure(menu=menu)
        self.root.after(100, self.poll)

    def check(self, manual=False):
        if not manual:
            return
        if sys.platform != 'win32':
            if manual:
                messagebox.showinfo('Atualizações',
                    'A busca de atualizações está disponível somente para Windows por enquanto.',
                    parent=self.root)
            return
        if self.running:
            return
        if manual and self.window is not None and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            return
        self.running = True
        def worker():
            try:
                raw, item = check(__version__)
                self.events.put(('release', (raw, item, manual)))
            except Exception:
                self.events.put(('idle', None))
                if manual:
                    self.events.put(('error', 'Não foi possível consultar atualizações. Verifique a conexão e a configuração de distribuição.'))
            except BaseException:
                self.events.put(('idle', None))
                raise
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == 'idle':
                    self.running = False
                elif event == 'error':
                    self.status.set(value)
                    messagebox.showerror('Atualizações', value, parent=self.root)
                elif event == 'download_failed':
                    self.download_button.configure(state='normal')
                elif event == 'progress':
                    self.status.set(f'Baixando atualização: {value:.0%}')
                elif event == 'ready':
                    self.work = value
                    self.status.set('Download validado. Pronto para instalar.')
                    self.install_button.configure(state='normal')
                elif event == 'release':
                    self.running = False
                    raw, item, manual = value
                    if item:
                        self.offer(raw, item)
                    elif manual:
                        messagebox.showinfo('Atualizações', 'Você já está usando a versão mais recente.', parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def offer(self, raw, item):
        if self.window is not None and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            return
        self.window = tk.Toplevel(self.root)
        self.window.title('Atualização do MV Image')
        self.window.protocol('WM_DELETE_WINDOW', self.window.withdraw)
        ttk.Label(self.window, text=f"Nova versão: {item['version']} (atual: {__version__})").pack(padx=20, pady=10)
        notes = tk.Text(self.window, width=65, height=12, wrap='word')
        notes.insert('1.0', item['notes'])
        notes.configure(state='disabled')
        notes.pack(padx=20)
        ttk.Label(self.window, textvariable=self.status, wraplength=500).pack(padx=20, pady=10)
        self.install_button = ttk.Button(self.window, text='Atualizar e reiniciar', state='disabled', command=self.install)
        self.install_button.pack(pady=5)
        ttk.Button(self.window, text='Mais tarde', command=self.window.withdraw).pack(pady=10)
        if not getattr(sys, 'frozen', False) or Path(sys.executable).resolve() != executable(target_path()).resolve():
            self.status.set(f'Instale primeiro o aplicativo em {target_path()} para habilitar a atualização.')
            return
        if not os.access(target_path().parent, os.W_OK):
            self.status.set('A pasta de instalação não permite escrita. Reinstale para este usuário.')
            return
        self.download_button = ttk.Button(self.window, text='Baixar atualização',
                                          command=lambda: self.start_download(raw, item))
        self.download_button.pack(pady=5)
        self.status.set('Clique em Baixar atualização para continuar.')

    def start_download(self, raw, item):
        if self.running:
            return
        self.running = True
        self.download_button.configure(state='disabled')
        def worker():
            work = None
            try:
                work = Path(tempfile.mkdtemp(prefix='job-', dir=state_dir()))
                (work / 'manifest.json').write_bytes(raw)
                download(item, work / 'package.zip', lambda progress: self.events.put(('progress', progress)))
                self.events.put(('ready', work))
            except Exception:
                if work is not None:
                    shutil.rmtree(work, ignore_errors=True)
                self.events.put(('download_failed', None))
                self.events.put(('error', 'Falha ao baixar ou validar a atualização. Tente novamente.'))
            finally:
                self.events.put(('idle', None))
        threading.Thread(target=worker, daemon=True).start()

    def install(self):
        # Conservative gate includes tab-owned threads and unregistered background tasks.
        if any(t.is_alive() and t is not threading.main_thread() for t in threading.enumerate()):
            messagebox.showinfo('Tarefa em andamento', 'Aguarde a conclusão das tarefas antes de atualizar.', parent=self.window)
            return
        from .config import APP_DIR
        from .shared_sessions import active_sessions
        try:
            others = active_sessions(APP_DIR)
        except Exception:
            messagebox.showerror('Atualizações',
                                 'Não foi possível verificar as sessões no compartilhamento. Tente novamente.',
                                 parent=self.window)
            return
        if others:
            messagebox.showinfo('Sistema em uso',
                                'Encerre as sessões dos usuários: ' + ', '.join(others)
                                + '. O pacote baixado será mantido.', parent=self.window)
            return
        target = target_path().resolve()
        if target == APP_DIR.resolve() or target in APP_DIR.resolve().parents:
            messagebox.showerror('Atualizações', 'Mova a pasta de dados para fora da instalação antes de atualizar.', parent=self.window)
            return
        name = 'MV Image Updater.exe' if sys.platform == 'win32' else 'MV Image Updater'
        bundled = Path(getattr(sys, '_MEIPASS')) / name
        helper = self.work / name
        try:
            from .update_install import write_state
            write_state(self.work / 'context.json', {'data_dir': str(APP_DIR.resolve())})
            shutil.copy2(bundled, helper)
            subprocess.Popen([str(helper), str(self.work)], cwd=self.work)
        except OSError:
            messagebox.showerror('Atualizações', 'Não foi possível iniciar o instalador.', parent=self.window)
            return
        self.root.destroy()
