"""Formulário da separação manual; operações de disco fora da thread do Tk."""
from pathlib import Path
import threading
import queue
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .indexer import load_index
from .manual_separation import folder_component, parse_designs, separate_designs
from .platform_utils import open_existing_location
from .config import load_config, save_config
from .user_store import pode_acessar_aba


class ManualSeparationTab:
    def __init__(self, parent, app):
        self.app = app
        self.destination = None
        self.worker = None
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.client = tk.StringVar()
        self.category = tk.StringVar()
        self.can_edit_paths = pode_acessar_aba(app.usuario_atual, 'usuarios')
        self.output = tk.StringVar(value=load_config().get('manual_output_dir', ''))
        self.status = tk.StringVar(value='Use o índice das pastas de entrada definidas em Configurações.')
        ttk.Label(parent, text='Separar estampas', style='Title.TLabel').pack(anchor='w', pady=(0, 12))
        for label, variable in [('Cliente', self.client), ('Categoria', self.category)]:
            ttk.Label(parent, text=label).pack(anchor='w')
            ttk.Entry(parent, textvariable=variable).pack(fill='x', pady=(0, 10))
        ttk.Label(parent, text='Estampas (código-variante separados por ponto e vírgula)').pack(anchor='w')
        self.designs = tk.Text(parent, height=5, wrap='word')
        self.designs.pack(fill='x', pady=(0, 4))
        ttk.Label(parent, text='Exemplo: 6162-A; MV23069-B; 6858-V').pack(anchor='w', pady=(0, 12))
        ttk.Label(parent, text='Pasta de saída → cliente / categoria / imagens').pack(anchor='w')
        row = ttk.Frame(parent)
        row.pack(fill='x')
        entry = ttk.Entry(row, textvariable=self.output, state='normal' if self.can_edit_paths else 'readonly')
        entry.pack(side='left', fill='x', expand=True)
        entry.bind('<FocusOut>', lambda event: self.save_output())
        ttk.Button(row, text='Escolher pasta', command=self.choose_output,
                   state='normal' if self.can_edit_paths else 'disabled').pack(side='left')
        ttk.Button(row, text='Abrir pasta', command=self.open_output).pack(side='left', padx=8)
        self.start_button = ttk.Button(parent, text='Separar estampas', command=self.start, style='Primary.TButton')
        self.start_button.pack(anchor='w', pady=12)
        ttk.Button(parent, text='Abrir pasta criada', command=self.open_destination).pack(anchor='w')
        ttk.Label(parent, textvariable=self.status, wraplength=650).pack(anchor='w', pady=10)
        self.log = tk.Text(parent, height=12, wrap='word', state='disabled')
        self.log.pack(fill='both', expand=True)

    def choose_output(self):
        if not self.can_edit_paths:
            return
        path = filedialog.askdirectory(parent=self.app.root, title='Pasta de saída')
        if path:
            self.output.set(path)
            self.save_output()

    def save_output(self):
        if self.can_edit_paths:
            config = load_config()
            config['manual_output_dir'] = self.output.get().strip()
            save_config(config)

    def open_output(self):
        try:
            open_existing_location(Path(self.output.get()).expanduser())
        except (OSError, RuntimeError) as exc:
            messagebox.showerror('Pasta indisponível', str(exc), parent=self.app.root)

    def open_destination(self):
        if self.destination:
            try:
                open_existing_location(self.destination)
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                messagebox.showerror("Falha ao abrir pasta", str(exc), parent=self.app.root)

    def set_busy(self, busy):
        self.start_button.configure(state='disabled' if busy else 'normal')

    def start(self):
        client, category = self.client.get(), self.category.get()
        text, output = self.designs.get('1.0', 'end'), self.output.get().strip()
        try:
            folder_component(client)
            folder_component(category)
            parse_designs(text)
            if not output or not self.app.source_dirs:
                raise ValueError('Escolha a saída e configure as pastas de origem em Configurações.')
        except ValueError as exc:
            messagebox.showwarning('Confira os campos', str(exc), parent=self.app.root)
            return
        self.cancel.clear()
        self.app._set_busy(True)
        self.status.set('Carregando índice e buscando estampas...')
        sources = list(self.app.source_dirs)
        index = self.app.index

        def worker():
            try:
                saved_index = index or load_index([Path(source) for source in sources])
                if not saved_index:
                    raise ValueError('Atualize o índice em Mapear estampas antes de separar.')
                destination, results = separate_designs(
                    client, category, text, output, saved_index,
                    lambda message: self.events.put(("progress", message)),
                    cancel=self.cancel,
                )
                self.events.put(("done", destination, results, None))
            except Exception as exc:
                self.events.put(("done", None, [], str(exc)))
        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()
        self.app.root.after(100, self.poll_events)

    def poll_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "done":
                    self.finish(*event[1:])
                    return
                self.status.set(event[1])
        except queue.Empty:
            self.app.root.after(100, self.poll_events)

    def finish(self, destination, results, error):
        self.app._set_busy(False)
        self.destination = destination
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.insert('end', '\n'.join(results))
        self.log.configure(state='disabled')
        self.status.set(f'Concluído. Pasta: {destination}' if not error else f'Erro: {error}')
        if error:
            messagebox.showerror('Falha ao separar estampas', error, parent=self.app.root)
