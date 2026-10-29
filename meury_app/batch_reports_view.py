"""Leitor de relatórios com seleção do lote e visualização dentro do aplicativo."""
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from .batch_reports import list_reports, read_report, report_path
from .platform_utils import open_with_default_application


class BatchReportsView:
    def __init__(self, parent, project):
        self.project = Path(project)
        self.paths = {}
        self.window = tk.Toplevel(parent)
        self.window.title('MV Image — Relatórios de pedidos')
        self.window.geometry('1000x700')
        self.window.minsize(650, 450)
        self.window.transient(parent)
        main = ttk.Frame(self.window, padding=12)
        main.pack(fill='both', expand=True)
        self.status = tk.StringVar(master=self.window)
        actions = ttk.Frame(main)
        actions.pack(fill='x', pady=(0, 8))
        ttk.Button(actions, text='Atualizar lista', command=self.refresh).pack(side='left')
        ttk.Button(actions, text='Abrir arquivo completo', command=self.open_file).pack(side='left', padx=8)
        ttk.Label(main, text='Lotes mais recentes primeiro. Selecione um arquivo para ler.').pack(anchor='w')
        frame = ttk.Frame(main)
        frame.pack(fill='x', pady=8)
        self.table = ttk.Treeview(frame, columns=('lote', 'arquivo'), show='headings', height=7, selectmode='browse')
        self.table.heading('lote', text='Lote')
        self.table.heading('arquivo', text='Relatório')
        self.table.column('lote', width=170, stretch=False)
        self.table.column('arquivo', width=550)
        scroll = ttk.Scrollbar(frame, command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side='left', fill='x', expand=True)
        scroll.pack(side='right', fill='y')
        self.table.bind('<<TreeviewSelect>>', self.select)
        self.table.bind('<Double-1>', lambda event: self.open_file())
        preview = ttk.Frame(main)
        preview.pack(fill='both', expand=True)
        self.text = tk.Text(preview, wrap='word', state='disabled')
        scrollbar = ttk.Scrollbar(preview, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        ttk.Label(main, textvariable=self.status, wraplength=900).pack(anchor='w', pady=(8, 0))
        self.refresh()

    def show_text(self, text):
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('end', text)
        self.text.configure(state='disabled')

    def refresh(self):
        previous = self.selected_path()
        self.table.delete(*self.table.get_children())
        self.paths = {}
        self.show_text('')
        try:
            paths = list_reports(self.project)
        except OSError:
            self.status.set('Não foi possível acessar a pasta de relatórios.')
            return
        for i, path in enumerate(paths):
            self.paths[str(i)] = path
            self.table.insert('', 'end', iid=str(i), values=(path.parent.name, path.name))
        self.status.set(f'{len(paths)} arquivos — até 100 lotes recentes e 1.000 arquivos.')
        if paths:
            selected = next((key for key, value in self.paths.items() if value == previous), '0')
            self.table.selection_set(selected)
            self.table.see(selected)
            self.select()
        else:
            self.show_text('Nenhum relatório encontrado. Execute um lote para gerar os relatórios.')

    def selected_path(self):
        selection = self.table.selection()
        return self.paths.get(selection[0]) if selection else None

    def select(self, event=None):
        path = self.selected_path()
        if path is None:
            return
        try:
            self.show_text(read_report(self.project, path))
        except (OSError, ValueError) as exc:
            self.show_text(f'Não foi possível ler o relatório: {exc}')

    def open_file(self):
        path = self.selected_path()
        if path is None:
            return
        try:
            open_with_default_application(report_path(self.project, path))
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror('Relatório indisponível', str(exc), parent=self.window)
