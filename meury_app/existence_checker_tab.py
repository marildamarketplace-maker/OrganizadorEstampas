"""Interface de consulta recursiva com trabalho de disco em segundo plano."""
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .existence_checker import check_existence, parse_existence_designs


class ExistenceCheckerTab:
    def __init__(self, parent, app):
        self.app = app
        self.worker = None
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.folder = tk.StringVar()
        self.status = tk.StringVar(value='Selecione uma pasta. Todas as subpastas serão verificadas.')
        ttk.Label(parent, text='Verificador de existência', style='Title.TLabel').pack(anchor='w', pady=(0, 12))
        ttk.Label(parent, text='Pasta para pesquisar (inclui todas as subpastas)').pack(anchor='w')
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=(0, 12))
        ttk.Entry(row, textvariable=self.folder).pack(side='left', fill='x', expand=True)
        ttk.Button(row, text='Escolher pasta', command=self.choose_folder).pack(side='left')
        ttk.Label(parent, text='Códigos separados por ponto e vírgula (variante opcional)').pack(anchor='w')
        self.designs = tk.Text(parent, height=5, wrap='word')
        self.designs.pack(fill='x')
        ttk.Label(parent, text='Exemplo: 6162; MV23069-B; 6858-V\nSem variante ou com -A: procura o arquivo sem sufixo ou com -A.\nO resultado lista apenas os códigos não encontrados.').pack(anchor='w', pady=8)
        actions = ttk.Frame(parent)
        actions.pack(fill='x')
        self.start_button = ttk.Button(actions, text='Verificar existência', command=self.start, style='Primary.TButton')
        self.start_button.pack(side='left')
        self.cancel_button = ttk.Button(actions, text='Cancelar', command=self.cancel.set, state='disabled')
        self.cancel_button.pack(side='left', padx=8)
        ttk.Label(parent, textvariable=self.status, wraplength=700).pack(anchor='w', pady=10)
        results = ttk.Frame(parent)
        results.pack(fill='both', expand=True)
        self.log = tk.Text(results, height=16, wrap='word', state='disabled')
        scroll = ttk.Scrollbar(results, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')

    def choose_folder(self):
        path = filedialog.askdirectory(parent=self.app.root, title='Pasta para verificar')
        if path:
            self.folder.set(path)

    def start(self):
        folder, text = self.folder.get().strip(), self.designs.get('1.0', 'end')
        try:
            parse_existence_designs(text)
            if not folder:
                raise ValueError('Escolha uma pasta para verificar.')
        except ValueError as exc:
            messagebox.showwarning('Confira os campos', str(exc), parent=self.app.root)
            return
        self.cancel.clear()
        self.start_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.status.set('Verificando arquivos e subpastas...')
        self.write_log('Busca em andamento...')

        def worker():
            try:
                result = check_existence(folder, text, cancel=self.cancel)
                self.events.put(('done', result))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()
        self.app.root.after(100, self.poll_events)

    def write_log(self, text):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.insert('end', text)
        self.log.configure(state='disabled')

    def poll_events(self):
        try:
            kind, result = self.events.get_nowait()
        except queue.Empty:
            self.app.root.after(100, self.poll_events)
            return
        self.start_button.configure(state='normal')
        self.cancel_button.configure(state='disabled')
        if kind == 'error':
            self.status.set('Falha na verificação.')
            self.write_log(result)
            return
        matches, errors, scanned, cancelled = result
        incomplete = cancelled or bool(errors)
        lines = []
        for design, paths in matches.items():
            if paths:
                continue
            label = 'INCONCLUSIVO — busca incompleta' if incomplete else 'NÃO ENCONTRADO'
            lines.append(f'{design}: {label}')
        if not lines and not incomplete:
            lines.append('Todos os códigos foram encontrados.')
        if errors:
            lines.append('Pastas ou arquivos que não puderam ser consultados:')
            lines.extend(errors)
        self.write_log('\n'.join(lines))
        found = sum(bool(paths) for paths in matches.values())
        state = 'Cancelado' if cancelled else ('Verificação incompleta' if errors else 'Concluído')
        self.status.set(f'{state}: {found} de {len(matches)} códigos encontrados; {scanned} arquivos verificados.')
