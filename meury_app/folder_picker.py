"""Portable Tk folder picker with Ctrl/Shift multi-selection."""
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


def choose_directories(parent, initial=None):
    dialog = FolderPicker(parent, initial)
    parent.wait_window(dialog)
    return dialog.result


class FolderPicker(tk.Toplevel):
    def __init__(self, parent, initial=None):
        super().__init__(parent)
        self.title("Adicionar pastas de PSDs")
        self.geometry("760x480")
        self.transient(parent)
        self.result = []
        self.current = Path(initial or Path.home()).expanduser()
        self.location = tk.StringVar(value=str(self.current))
        row = ttk.Frame(self, padding=10)
        row.pack(fill="x")
        entry = ttk.Entry(row, textvariable=self.location)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda event: self.navigate(self.location.get()))
        ttk.Button(row, text="Ir", command=lambda: self.navigate(self.location.get())).pack(side="left", padx=4)
        ttk.Button(row, text="Procurar…", command=self.browse).pack(side="left")
        ttk.Button(row, text="Subir", command=lambda: self.navigate(self.current.parent)).pack(side="left", padx=4)
        ttk.Label(self, text="Selecione várias pastas com Ctrl (⌘ no Mac) ou Shift. Dê dois cliques para entrar em uma pasta.", wraplength=720).pack(anchor="w", padx=10)
        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)
        self.folders = tk.Listbox(frame, selectmode="extended", exportselection=False)
        self.folders.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.folders.yview)
        scroll.pack(side="right", fill="y")
        self.folders.configure(yscrollcommand=scroll.set)
        self.folders.bind("<Double-1>", self.enter_folder)
        self.bind("<Escape>", lambda event: self.destroy())
        row = ttk.Frame(self, padding=10)
        row.pack(fill="x")
        ttk.Button(row, text="Adicionar selecionadas", command=self.accept_selected).pack(side="left")
        ttk.Button(row, text="Adicionar pasta atual", command=lambda: self.accept([str(self.current)])).pack(side="left", padx=6)
        ttk.Button(row, text="Cancelar", command=self.destroy).pack(side="right")
        self.paths = []
        self.navigate(self.current)
        self.grab_set()
        entry.focus_set()

    def navigate(self, path):
        try:
            target = Path(path).expanduser()
            if not target.is_absolute():
                target = self.current / target
            paths = sorted((item for item in target.iterdir() if item.is_dir()), key=lambda item: item.name.casefold())
        except OSError as exc:
            messagebox.showerror("Não foi possível abrir a pasta", str(exc), parent=self)
            self.location.set(str(self.current))
            return
        self.current = target
        self.location.set(str(target))
        self.paths = paths
        self.folders.delete(0, "end")
        for item in paths:
            self.folders.insert("end", item.name)

    def browse(self):
        path = filedialog.askdirectory(parent=self, initialdir=str(self.current), title="Escolha a pasta que contém as pastas desejadas")
        if path:
            self.navigate(path)

    def enter_folder(self, event=None):
        selected = self.folders.curselection()
        if len(selected) == 1:
            self.navigate(self.paths[selected[0]])

    def accept_selected(self):
        paths = [str(self.paths[index]) for index in self.folders.curselection()]
        if paths:
            self.accept(paths)
        else:
            messagebox.showinfo("Selecione as pastas", "Marque as pastas que deseja adicionar ou use Adicionar pasta atual.", parent=self)

    def accept(self, paths):
        self.result = paths
        self.destroy()
