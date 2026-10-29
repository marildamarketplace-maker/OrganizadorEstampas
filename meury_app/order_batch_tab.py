"""Separação de pedidos PDF com o mesmo processador usado pelos iniciadores."""
from pathlib import Path
import os
import queue
import shutil
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import APP_DIR, OPERATIONAL_DB_FILE
from .batch_reports_view import BatchReportsView
from .order_pdf_panel import OrderPdfPanel
from .order_batch import executar_lote
from .platform_utils import open_existing_location


class OrderBatchTab:
    def __init__(self, parent, app):
        self.app = app
        self.worker = None
        self.events = queue.Queue()
        self.report_reader = None
        default = APP_DIR / 'lotes' if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent
        self.project = tk.StringVar(master=parent, value=str(default))
        self.status = tk.StringVar(master=parent, value='Coloque os PDFs em pedidos_pdf/entrada e inicie o lote.')
        ttk.Label(parent, text='Separar pedidos', style='Title.TLabel').pack(anchor='w')
        ttk.Label(parent, text='Mesmo processamento do lote: extração, separação, histórico e relatórios.').pack(anchor='w', pady=8)
        ttk.Label(parent, text='Pasta do lote (contém pedidos_pdf/entrada e pedidos_pdf/relatorios)').pack(anchor='w')
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=8)
        self.path_entry = ttk.Entry(row, textvariable=self.project)
        self.path_entry.pack(side='left', fill='x', expand=True)
        self.choose_button = ttk.Button(row, text='Escolher pasta', command=self.choose)
        self.choose_button.pack(side='left')
        actions = ttk.Frame(parent)
        actions.pack(fill='x', pady=8)
        ttk.Button(actions, text='Abrir entrada de PDFs', command=lambda: self.open_folder('entrada')).pack(side='left')
        ttk.Button(actions, text='Abrir relatórios', command=lambda: self.open_folder('relatorios')).pack(side='left', padx=8)
        ttk.Button(actions, text='Ler relatórios', command=self.read_reports).pack(side='left')
        self.pdf_panel = OrderPdfPanel(parent, app, self.project)
        self.path_entry.bind('<FocusOut>', lambda event: self.pdf_panel.refresh())
        self.start_button = ttk.Button(parent, text='Iniciar separação dos pedidos', command=self.start, style='Primary.TButton')
        self.start_button.pack(anchor='w', pady=8)
        ttk.Label(parent, textvariable=self.status, wraplength=700).pack(anchor='w', pady=8)
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        self.log = tk.Text(frame, height=10, wrap='word', state='disabled')
        scroll = ttk.Scrollbar(frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')

    def read_reports(self):
        if not self.project.get().strip():
            return
        project = Path(self.project.get()).expanduser().resolve()
        reader = self.report_reader
        if reader is not None and reader.window.winfo_exists():
            reader.project = project
            reader.refresh()
            reader.window.lift()
        else:
            self.report_reader = BatchReportsView(self.app.root, project)

    def choose(self):
        path = filedialog.askdirectory(parent=self.app.root, title='Pasta do lote')
        if path:
            self.project.set(path)
            self.pdf_panel.refresh()

    def open_folder(self, name):
        try:
            path = Path(self.project.get()).expanduser() / 'pedidos_pdf' / name
            path.mkdir(parents=True, exist_ok=True)
            open_existing_location(path)
        except (OSError, RuntimeError) as exc:
            messagebox.showerror('Pasta indisponível', str(exc), parent=self.app.root)

    def set_busy(self, busy):
        self.start_button.configure(state='disabled' if busy else 'normal')

    def start(self):
        if self.worker is not None and self.worker.is_alive():
            return
        if not self.project.get().strip():
            messagebox.showwarning('Pasta do lote', 'Escolha a pasta do lote.', parent=self.app.root)
            return
        update = messagebox.askyesnocancel('Atualizar índice',
            'Atualizar o índice antes de separar as imagens?\n\n'
            'Sim: verificar a atualização diária.\nNão: usar somente o índice salvo.', parent=self.app.root)
        if update is None:
            return
        project = Path(self.project.get()).expanduser().resolve()
        user_id = (self.app.usuario_atual or {}).get('id')
        codex = shutil.which('codex') or ''
        if not codex and sys.platform == 'win32':
            local_app_data = os.environ.get('LOCALAPPDATA')
            if local_app_data:
                candidates = sorted((Path(local_app_data) / 'OpenAI' / 'Codex' / 'bin').glob('*/codex.exe'))
                codex = str(candidates[-1]) if candidates else ''
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')
        self.app._set_busy(True)
        self.path_entry.configure(state='disabled')
        self.choose_button.configure(state='disabled')
        self.pdf_panel.refresh()
        self.status.set('Processando pedidos...')

        def worker():
            try:
                result = executar_lote(project, OPERATIONAL_DB_FILE, user_id, update_index=update,
                    log=lambda text: self.events.put(('log', text)), codex_path=codex)
                self.events.put(('done', result))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()
        self.app.root.after(100, self.poll)

    def poll(self):
        # Limite por ciclo para os logs não bloquearem a interface.
        for _ in range(250):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'log':
                self.log.configure(state='normal')
                self.log.insert('end', value)
                self.log.see('end')
                self.log.configure(state='disabled')
            else:
                if kind == 'error':
                    self.log.configure(state='normal')
                    self.log.insert('end', f'\nERRO: {value}\n')
                    self.log.see('end')
                    self.log.configure(state='disabled')
                self.pdf_panel.refresh()
                reader = self.report_reader
                if reader is not None and reader.window.winfo_exists():
                    reader.refresh()
                self.app._set_busy(False)
                self.path_entry.configure(state='normal')
                self.choose_button.configure(state='normal')
                self.status.set(('Concluído.' if value == 0 else 'Concluído com pendências. Confira os logs e relatórios.')
                                if kind == 'done' else f'Falha: {value}')
                return
        self.app.root.after(100, self.poll)
