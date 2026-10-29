"""Acesso visual aos PDFs de entrada e concluídos, sem mover arquivos."""
from pathlib import Path
import subprocess
from tkinter import messagebox, ttk

from .platform_utils import open_existing_location, open_with_default_application


PDF_FOLDERS = {'entrada': 'Entrada', 'entrada/concluido': 'Concluídos'}


def list_pdfs(project: Path, folder: str) -> list[Path]:
    if folder not in PDF_FOLDERS:
        raise ValueError('Pasta de pedidos inválida')
    directory = Path(project).expanduser().resolve() / 'pedidos_pdf' / folder
    if not directory.exists():
        return []
    return sorted((path for path in directory.iterdir()
                   if path.is_file() and not path.is_symlink() and path.suffix.casefold() == '.pdf'),
                  key=lambda path: path.name.casefold())


class OrderPdfPanel:
    def __init__(self, parent, app, project):
        self.app = app
        self.project = project
        self.tables = {}
        self.frames = {}
        self.paths = {}
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill='x', pady=(6, 4))
        ttk.Label(toolbar, text='PDFs dos pedidos').pack(side='left')
        ttk.Button(toolbar, text='Atualizar PDFs', command=self.refresh).pack(side='right')
        panels = ttk.Frame(parent)
        panels.pack(fill='x')
        panels.columnconfigure(0, weight=1)
        panels.columnconfigure(1, weight=1)
        for column, (folder, title) in enumerate(PDF_FOLDERS.items()):
            frame = ttk.LabelFrame(panels, text=title, padding=6)
            frame.grid(row=0, column=column, sticky='nsew', padx=(0, 6) if column == 0 else (6, 0))
            self.frames[folder] = frame
            listing = ttk.Frame(frame)
            listing.pack(fill='both', expand=True)
            table = ttk.Treeview(listing, columns=('pdf',), show='headings', height=5, selectmode='browse')
            table.heading('pdf', text='Arquivo PDF — duplo clique para abrir')
            table.column('pdf', width=250)
            scroll = ttk.Scrollbar(listing, command=table.yview)
            table.configure(yscrollcommand=scroll.set)
            table.pack(side='left', fill='both', expand=True)
            scroll.pack(side='right', fill='y')
            self.tables[folder] = table
            table.bind('<Double-1>', lambda event, key=folder: self.open_pdf(key))
            table.bind('<Return>', lambda event, key=folder: self.open_pdf(key))
            buttons = ttk.Frame(frame)
            buttons.pack(fill='x', pady=(4, 0))
            ttk.Button(buttons, text='Abrir PDF', command=lambda key=folder: self.open_pdf(key)).pack(side='left')
            ttk.Button(buttons, text='Abrir pasta', command=lambda key=folder: self.open_folder(key)).pack(side='left', padx=6)
        self.refresh()

    def refresh(self):
        for folder, title in PDF_FOLDERS.items():
            table = self.tables[folder]
            selection = table.selection()
            previous = self.paths.get(folder, {}).get(selection[0]) if selection else None
            table.delete(*table.get_children())
            self.paths[folder] = {}
            try:
                files = list_pdfs(Path(self.project.get()), folder) if self.project.get().strip() else []
            except OSError:
                self.frames[folder].configure(text=f'{title} — indisponível')
                continue
            self.frames[folder].configure(text=f'{title} ({len(files)})')
            for i, path in enumerate(files):
                key = str(i)
                self.paths[folder][key] = path
                table.insert('', 'end', iid=key, values=(path.name,))
                if path == previous:
                    table.selection_set(key)

    def open_pdf(self, folder):
        table = self.tables[folder]
        selection = table.selection()
        path = self.paths.get(folder, {}).get(selection[0]) if selection else None
        if path is None:
            return
        try:
            expected = (Path(self.project.get()).expanduser().resolve() / 'pedidos_pdf' / folder).resolve()
            if path.resolve().parent != expected or path.is_symlink() or not path.is_file():
                raise ValueError('O PDF não está mais disponível nessa pasta. Atualize a lista.')
            open_with_default_application(path)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            messagebox.showerror('PDF indisponível', str(exc), parent=self.app.root)

    def open_folder(self, folder):
        try:
            path = Path(self.project.get()).expanduser() / 'pedidos_pdf' / folder
            path.mkdir(parents=True, exist_ok=True)
            open_existing_location(path)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            messagebox.showerror('Pasta indisponível', str(exc), parent=self.app.root)
