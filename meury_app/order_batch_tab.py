"""Separação de pedidos PDF com o mesmo processador usado pelos iniciadores."""
from pathlib import Path
import os
import queue
import shutil
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import APP_DIR, OPERATIONAL_DB_FILE, load_config, save_config
from .batch_reports_view import BatchReportsView
from .order_pdf_panel import OrderPdfPanel
from .order_batch import executar_lote
from .platform_utils import open_existing_location


class OrderBatchTab:
    def __init__(self, parent, app):
        self.app = app
        permissions = (getattr(app, 'usuario_atual', None) or {}).get('permissoes', [])
        self.can_edit_paths = isinstance(permissions, list) and 'admin' in permissions
        self.worker = None
        self.events = queue.Queue()
        self.report_reader = None
        default = APP_DIR / 'lotes' if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent
        self.project = tk.StringVar(master=parent, value=load_config().get('order_batch_folder', str(default)))
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
        self.temporary_folder = tk.StringVar(master=parent, value=load_config().get(
            'order_temporary_folder', str(Path(tempfile.gettempdir()) / 'MV Image')))
        ttk.Label(parent, text='Pasta temporária local para extração dos PDFs').pack(anchor='w')
        temporary_row = ttk.Frame(parent)
        temporary_row.pack(fill='x', pady=8)
        self.temporary_entry = ttk.Entry(temporary_row, textvariable=self.temporary_folder)
        self.temporary_entry.pack(side='left', fill='x', expand=True)
        self.temporary_button = ttk.Button(temporary_row, text='Escolher pasta', command=self.choose_temporary)
        self.temporary_button.pack(side='left')
        ttk.Button(row, text='Abrir pasta', command=lambda: self.open_path(self.project)).pack(side='left', padx=8)
        ttk.Button(temporary_row, text='Abrir pasta', command=lambda: self.open_path(self.temporary_folder)).pack(side='left', padx=8)
        self.output_folder = tk.StringVar(master=parent, value=load_config().get('batch_output_dir', ''))
        ttk.Label(parent, text='Pasta de saída dos pedidos PDF').pack(anchor='w')
        output_row = ttk.Frame(parent)
        output_row.pack(fill='x', pady=8)
        self.output_entry = ttk.Entry(output_row, textvariable=self.output_folder)
        self.output_entry.pack(side='left', fill='x', expand=True)
        self.output_entry.bind('<FocusOut>', lambda event: self.save_output())
        self.output_button = ttk.Button(output_row, text='Escolher pasta', command=self.choose_output)
        self.output_button.pack(side='left')
        ttk.Button(output_row, text='Abrir pasta', command=lambda: self.open_path(self.output_folder)).pack(side='left', padx=8)
        self.set_path_states()
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
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill='x', pady=(4, 6))
        ttk.Label(toolbar, text='Logs do processamento', font=('Arial', 11, 'bold')).pack(side='left')
        self.follow_logs = tk.BooleanVar(master=parent, value=True)
        ttk.Checkbutton(toolbar, text='Acompanhar novos logs', variable=self.follow_logs).pack(side='left', padx=16)
        ttk.Button(toolbar, text='Copiar logs', command=self.copy_logs).pack(side='right')
        ttk.Button(toolbar, text='Ir ao final', command=self.show_log_end).pack(side='right', padx=8)
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.log = tk.Text(frame, height=14, wrap='none', state='disabled',
                           font=('Courier New', 11), background='#18212f', foreground='#e5edf7',
                           selectbackground='#385a80', relief='flat', padx=12, pady=10,
                           spacing1=2, spacing3=2)
        self.log.tag_configure('error', foreground='#ff9999')
        scroll = ttk.Scrollbar(frame, command=self.log.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=self.log.xview)
        self.log.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.log.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')

    def copy_logs(self):
        self.log.clipboard_clear()
        self.log.clipboard_append(self.log.get('1.0', 'end-1c'))

    def show_log_end(self):
        self.log.see('end')

    def append_log(self, text, tag=()):
        self.log.configure(state='normal')
        self.log.insert('end', text, tag)
        if self.follow_logs.get():
            self.log.see('end')
        self.log.configure(state='disabled')

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
        if not self.can_edit_paths:
            return
        path = filedialog.askdirectory(parent=self.app.root, title='Pasta do lote')
        if path:
            self.project.set(path)
            config = load_config()
            config['order_batch_folder'] = path
            save_config(config)
            self.pdf_panel.refresh()

    def open_folder(self, name):
        try:
            path = Path(self.project.get()).expanduser() / 'pedidos_pdf' / name
            path.mkdir(parents=True, exist_ok=True)
            open_existing_location(path)
        except (OSError, RuntimeError) as exc:
            messagebox.showerror('Pasta indisponível', str(exc), parent=self.app.root)

    def choose_temporary(self):
        if not self.can_edit_paths:
            return
        path = filedialog.askdirectory(parent=self.app.root, title='Pasta temporária local')
        if path:
            self.temporary_folder.set(path)
            config = load_config()
            config['order_temporary_folder'] = path
            save_config(config)

    def open_path(self, variable):
        try:
            open_existing_location(Path(variable.get()).expanduser())
        except (OSError, RuntimeError) as exc:
            messagebox.showerror('Pasta indisponível', str(exc), parent=self.app.root)

    def choose_output(self):
        if not self.can_edit_paths:
            return
        path = filedialog.askdirectory(parent=self.app.root, title='Saída dos pedidos PDF')
        if path:
            self.output_folder.set(path)
            self.save_output()

    def save_output(self):
        if self.can_edit_paths:
            config = load_config()
            config['batch_output_dir'] = self.output_folder.get().strip()
            save_config(config)

    def set_path_states(self, busy=False):
        entry_state = 'disabled' if busy else ('normal' if self.can_edit_paths else 'readonly')
        button_state = 'normal' if self.can_edit_paths and not busy else 'disabled'
        for entry in (self.path_entry, self.temporary_entry, self.output_entry):
            entry.configure(state=entry_state)
        for button in (self.choose_button, self.temporary_button, self.output_button):
            button.configure(state=button_state)

    def set_busy(self, busy):
        self.start_button.configure(state='disabled' if busy else 'normal')

    def start(self):
        if self.worker is not None and self.worker.is_alive():
            return
        if not self.project.get().strip():
            messagebox.showwarning('Pasta do lote', 'Escolha a pasta do lote.', parent=self.app.root)
            return
        self.save_output()
        try:
            if not self.temporary_folder.get().strip():
                raise ValueError('Escolha uma pasta temporária local.')
            temporary_folder = Path(self.temporary_folder.get()).expanduser().resolve()
            if str(temporary_folder).startswith(('\\\\', '//')):
                raise ValueError('Escolha uma pasta no computador, fora do compartilhamento de rede.')
            temporary_folder.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=temporary_folder):
                pass
            if self.can_edit_paths:
                config = load_config()
                config['order_temporary_folder'] = str(temporary_folder)
                config['order_batch_folder'] = self.project.get().strip()
                save_config(config)
        except (OSError, ValueError) as exc:
            messagebox.showerror('Pasta temporária', str(exc), parent=self.app.root)
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
        self.temporary_entry.configure(state='disabled')
        self.temporary_button.configure(state='disabled')
        self.set_path_states(busy=True)
        self.pdf_panel.refresh()
        self.status.set('Processando pedidos...')

        def worker():
            try:
                result = executar_lote(project, OPERATIONAL_DB_FILE, user_id, update_index=update,
                    log=lambda text: self.events.put(('log', text)), codex_path=codex,
                    temporary_folder=temporary_folder)
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
                self.append_log(value)
            else:
                if kind == 'error':
                    self.append_log(f'\nERRO: {value}\n', 'error')
                self.pdf_panel.refresh()
                reader = self.report_reader
                if reader is not None and reader.window.winfo_exists():
                    reader.refresh()
                self.app._set_busy(False)
                self.set_path_states()
                self.status.set(('Concluído.' if value == 0 else 'Concluído com pendências. Confira os logs e relatórios.')
                                if kind == 'done' else f'Falha: {value}')
                return
        self.app.root.after(100, self.poll)
