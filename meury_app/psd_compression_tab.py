"""Tk/ttk presentation for PSD compression; workers communicate through a queue."""
from datetime import datetime
from pathlib import Path
import json
import os
import queue
from copy import deepcopy
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .compression_store import CompressionStore
from .config import load_config, save_config
from .image_collector import format_size
from .index_journal import catalog_lock
from .folder_picker import choose_directories
from .platform_utils import open_existing_location, open_with_default_application
from .psd_compression import compress, lock_path
from .psd_search import Cancelled, SearchSettings, analyze, parse_names


class CompressionTab:
    def __init__(self, parent, app):
        self.app = app
        self.root = app.root
        self.store = CompressionStore()
        self.events = queue.Queue(maxsize=100)
        self.cancel = threading.Event()
        self.worker = None
        self.aux_workers = []
        self.history_generation = 0
        self.render_generation = 0
        self.closing = False
        self.analysis = None
        self.settings_used = None
        self.external_busy = False
        self.last_progress = 0
        self.sources = list(app.config.get("compression_sources", []))
        self.mode = tk.StringVar(value=app.config.get("compression_mode", "individual"))
        self.output = tk.StringVar(value=app.config.get("compression_output", ""))
        self.start = tk.StringVar(value=app.config.get("compression_start", ""))
        self.end = tk.StringVar(value=app.config.get("compression_end", ""))
        self.status = tk.StringVar(value="Pronto. Analise os arquivos antes de compactar.")
        self.scope = tk.StringVar()
        self.controls = []
        ttk.Label(parent, text="Compactação de PSDs", style="Title.TLabel").pack(anchor="w")
        form = ttk.LabelFrame(parent, text="Origens e filtros", padding=12)
        form.pack(fill="x", pady=10)
        modes = ttk.Frame(form)
        modes.pack(fill="x", pady=5)
        for label, value in (("Por pasta — reúne os PSDs de cada pasta (padrão)", "individual"),
                             ("Em grupo — um .7z na pasta de saída", "group")):
            control = ttk.Radiobutton(modes, text=label, variable=self.mode, value=value)
            control.pack(anchor="w")
            self.controls.append(control)
        ttk.Label(form, text="Por pasta: salva nome-da-pasta.7z na própria pasta; saída ignorada; sem sobrescrever.").pack(anchor="w")
        self.source_list = tk.Listbox(form, height=4, selectmode="extended", exportselection=False)
        self.source_list.pack(fill="x")
        self.controls.append(self.source_list)
        for source in self.sources:
            self.source_list.insert("end", source)
        row = ttk.Frame(form)
        row.pack(fill="x", pady=5)
        self.button(row, "Adicionar pastas", self.add_source)
        self.button(row, "Remover selecionadas", self.remove_sources)
        ttk.Label(form, text="Nomes exatos, com ou sem .psd (linhas, vírgulas ou ponto e vírgula)").pack(anchor="w")
        self.names = tk.Text(form, height=3, wrap="word")
        self.names.insert("1.0", app.config.get("compression_names", ""))
        self.names.pack(fill="x")
        self.controls.append(self.names)
        self.names.bind("<<Modified>>", self.names_changed)
        ttk.Label(form, textvariable=self.scope).pack(anchor="w", pady=4)
        row = ttk.Frame(form)
        row.pack(fill="x")
        for label, variable in (("De (AAAA-MM-DD)", self.start), ("Até (AAAA-MM-DD)", self.end)):
            ttk.Label(row, text=label).pack(side="left")
            entry = ttk.Entry(row, textvariable=variable, width=13)
            entry.pack(side="left", padx=5)
            self.controls.append(entry)
        row = ttk.Frame(form)
        row.pack(fill="x", pady=5)
        ttk.Label(row, text="Saída").pack(side="left")
        entry = ttk.Entry(row, textvariable=self.output)
        entry.pack(side="left", fill="x", expand=True, padx=5)
        self.controls.append(entry)
        self.button(row, "Selecionar saída", self.select_output)
        row = ttk.Frame(parent)
        row.pack(fill="x")
        self.button(row, "Analisar arquivos", self.start_analysis)
        self.compress_button = self.button(row, "Compactar selecionados", self.start_compression)
        self.stop_button = ttk.Button(row, text="Cancelar", command=self.cancel.set, state="disabled")
        self.stop_button.pack(side="left", padx=4)
        self.bar = ttk.Progressbar(parent, maximum=100)
        self.bar.pack(fill="x", pady=8)
        ttk.Label(parent, textvariable=self.status, wraplength=760).pack(anchor="w")
        self.table = self.tree(parent, ("seleção", "nome", "caminho completo", "tamanho", "modificação", "situação"))
        self.table.bind("<Double-1>", lambda event: self.toggle())
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=5)
        self.button(row, "Alternar marcados", self.toggle)
        self.button(row, "Selecionar todos", lambda: self.select_all(True))
        self.button(row, "Desmarcar todos", lambda: self.select_all(False))
        row = ttk.Frame(parent)
        row.pack(fill="x")
        self.button(row, "Incluir grupo homônimo", lambda: self.select_group(True))
        self.button(row, "Ignorar grupo homônimo", lambda: self.select_group(False))
        self.button(row, "Abrir origem do PSD", self.open_candidate)
        self.messages = tk.Text(parent, height=5, wrap="word", state="disabled")
        self.messages.pack(fill="x", pady=8)
        ttk.Label(parent, text="Histórico persistente (200 execuções mais recentes)").pack(anchor="w")
        self.history_query = tk.StringVar()
        search_row = ttk.Frame(parent)
        search_row.pack(fill="x")
        ttk.Label(search_row, text="Pesquisar PSD incluído (nome ou caminho)").pack(side="left")
        search_entry = ttk.Entry(search_row, textvariable=self.history_query)
        search_entry.pack(side="left", fill="x", expand=True, padx=5)
        search_entry.bind("<Return>", lambda event: self.refresh_history())
        self.button(search_row, "Pesquisar", self.refresh_history, controlled=False)
        self.history = self.tree(parent, ("início", "estado", "execução"), height=4)
        row = ttk.Frame(parent)
        row.pack(fill="x")
        self.button(row, "Atualizar / recuperar pendentes", lambda: self.refresh_history(recover=True), controlled=False)
        self.button(row, "Conteúdo final e análise", self.history_details, controlled=False)
        self.button(row, "Abrir saída", self.open_history, controlled=False)
        self.button(row, "Abrir relatório", self.open_report, controlled=False)
        ttk.Label(parent, text="O histórico registra a localização na compactação. A disponibilidade atual só é conferida ao abrir.", wraplength=760).pack(anchor="w")
        ttk.Label(parent, text="Originais são preservados. O 7z ocupa espaço adicional; não libera espaço automaticamente.", wraplength=760).pack(anchor="w", pady=8)
        for variable in (self.start, self.end, self.output, self.mode):
            variable.trace_add("write", lambda *args: self.invalidate())
        self.names.edit_modified(False)
        self.invalidate()
        self.root.after(100, self.drain)
        self.refresh_history(recover=True)

    def button(self, parent, text, command, controlled=True):
        button = ttk.Button(parent, text=text, command=command)
        button.pack(side="left", padx=(0, 5), pady=2)
        if controlled:
            self.controls.append(button)
        return button

    def tree(self, parent, columns, height=7):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=5)
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=height, selectmode="extended")
        for column in columns:
            tree.heading(column, text=column.capitalize())
            tree.column(column, width=130, minwidth=80)
        vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        return tree

    def settings(self):
        return SearchSettings(tuple(self.sources), self.output.get().strip(), self.names.get("1.0", "end").strip(), self.start.get().strip(), self.end.get().strip(), self.mode.get())

    def names_changed(self, event=None):
        if self.names.edit_modified():
            self.names.edit_modified(False)
            self.invalidate()

    def invalidate(self):
        self.analysis = None
        self.scope.set("Busca ampla: todos os PSDs que atendam ao período." if not parse_names(self.names.get("1.0", "end")) else "Busca por nomes exatos, ignorando apenas caixa e extensão .psd.")
        self.compress_button.configure(state="disabled")
        self.status.set("Configuração alterada. Execute Analisar arquivos.")

    def preferences(self):
        value = self.settings()
        return {"compression_mode": value.mode, "compression_sources": self.sources, "compression_output": value.output,
                "compression_names": value.names, "compression_start": value.start, "compression_end": value.end}

    def save(self):
        config = load_config()
        config.update(self.preferences())
        save_config(config)
        self.app.config.update(config)

    def add_source(self):
        initial = Path(self.sources[-1]).parent if self.sources else None
        values = choose_directories(self.root, initial)
        existing = {os.path.normcase(os.path.abspath(os.path.expanduser(value))) for value in self.sources}
        changed = False
        for value in values:
            key = os.path.normcase(os.path.abspath(os.path.expanduser(value)))
            if key in existing:
                continue
            existing.add(key)
            self.sources.append(value)
            self.source_list.insert("end", value)
            changed = True
        if changed:
            self.invalidate()

    def remove_sources(self):
        for index in reversed(self.source_list.curselection()):
            del self.sources[index]
            self.source_list.delete(index)
        self.invalidate()

    def select_output(self):
        value = filedialog.askdirectory(parent=self.root)
        if value:
            self.output.set(value)

    def set_busy(self, busy):
        self.external_busy = busy
        for control in self.controls:
            control.configure(state="disabled" if busy else "normal")
        self.compress_button.configure(state="normal" if not busy and self.analysis else "disabled")

    def start_job(self, kind, work):
        if self.closing or (self.worker and self.worker.is_alive()) or self.external_busy:
            return
        self.cancel.clear()
        self.app._set_busy(True)
        self.stop_button.configure(state="normal")
        self.bar.configure(value=0)
        self.status.set("Iniciando operação em segundo plano...")
        def worker():
            try:
                result = work()
                self.events.put((kind, result))
            except Cancelled:
                self.events.put(("error", "Análise cancelada; nenhum compactado criado."))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        self.worker = threading.Thread(target=worker, daemon=False)
        self.worker.start()

    def progress(self, phase, current, total):
        moment = time.monotonic()
        if moment - self.last_progress < .15 and current != total:
            return
        self.last_progress = moment
        try:
            self.events.put_nowait(("progress", (phase, current, total)))
        except queue.Full:
            pass

    def start_analysis(self):
        try:
            self.save()
            settings = self.settings()
            settings.bounds()
        except Exception as exc:
            messagebox.showerror("Compactação de PSDs", str(exc), parent=self.root)
            return
        self.analysis = None
        self.settings_used = settings
        self.table.delete(*self.table.get_children())
        self.start_job("analysis", lambda: analyze(settings, self.cancel.is_set, self.progress))

    def start_compression(self):
        if not self.analysis or self.settings() != self.settings_used:
            self.invalidate()
            return
        chosen = [item for item in self.analysis.files if item.selected]
        if not chosen:
            messagebox.showwarning("Compactação de PSDs", "Selecione pelo menos um PSD.", parent=self.root)
            return
        warning = "\nAnálise INCOMPLETA: há erros de acesso; somente os resultados visíveis serão incluídos." if self.analysis.incomplete else ""
        destination = "Um .7z por pasta, reunindo seus PSDs selecionados (subpastas separadas)" if self.mode.get() == "individual" else self.output.get()
        summary = f"Compactar {len(chosen)} PSDs ({format_size(sum(item.size for item in chosen))})?\nSaída: {destination}\nLZMA2, normal, sólido desativado.\nArquivos existentes não serão sobrescritos.{warning}\nOs originais serão preservados."
        if messagebox.askyesno("Confirmar seleção", summary, parent=self.root):
            approved = deepcopy(self.analysis)
            self.start_job("compressed", lambda: compress(approved, self.cancel.is_set, self.progress, self.store))

    def drain(self):
        try:
            for _ in range(100):
                kind, value = self.events.get_nowait()
                if self.closing and kind in {"history", "details", "open_history", "opened", "aux_error"}:
                    continue
                if kind == "history":
                    generation, rows = value
                    if generation == self.history_generation:
                        self.history.delete(*self.history.get_children())
                        for row in rows:
                            self.history.insert("", "end", iid=row["id"], values=(row["started"], row["state"], row["id"]))
                    continue
                if kind == "details":
                    self.show_history_details(value)
                    continue
                if kind == "open_history":
                    self.open_location(value["payload"]["archive"])
                    continue
                if kind == "aux_error":
                    self.show_messages(value)
                    continue
                if kind == "opened":
                    continue
                if kind == "progress":
                    phase, current, total = value
                    self.status.set(f"{phase}: {current}/{total}" if total else f"{phase}: {current} (total ainda não determinado)")
                    self.bar.configure(value=current / total * 100 if total else 0)
                    continue
                self.app._set_busy(False)
                self.stop_button.configure(state="disabled")
                if kind == "analysis":
                    if value.settings != self.settings():
                        self.invalidate()
                        continue
                    self.analysis = value
                    self.render()
                    self.compress_button.configure(state="normal" if value.files else "disabled")
                    self.status.set(f"{'Análise INCOMPLETA' if value.incomplete else 'Análise concluída'}: {len(value.files)} PSDs; {len(value.issues)} avisos; {len(value.missing)} nomes não encontrados.")
                    self.show_messages("\n".join(value.issues + [f"Nome não encontrado no período: {name}" for name in value.missing]))
                elif kind == "compressed":
                    self.status.set(f"Execução {value['state']}. Log: {value['log']}")
                    details = [*value["errors"], value["validation"]]
                    if "difference_bytes" in value:
                        delta = value["difference_bytes"]
                        details.append(f"{'Redução' if delta >= 0 else 'Aumento'}: {format_size(abs(delta))}; original {format_size(value['original_bytes'])}; 7z {format_size(value['archive_bytes'])}.")
                        details.append(value["storage_note"])
                    self.show_messages("\n".join(details))
                    self.refresh_history()
                else:
                    self.status.set("Operação não concluída.")
                    self.show_messages(value)
        except queue.Empty:
            pass
        self.root.after(100, self.drain)

    def show_messages(self, text):
        self.messages.configure(state="normal")
        self.messages.delete("1.0", "end")
        self.messages.insert("end", text)
        self.messages.configure(state="disabled")

    def render(self):
        self.render_generation += 1
        generation = self.render_generation
        analysis = self.analysis
        selected_rows = self.table.selection()
        self.table.delete(*self.table.get_children())
        def batch(start=0):
            if self.closing or generation != self.render_generation or self.analysis is not analysis:
                return
            for index in range(start, min(start + 100, len(analysis.files))):
                item = analysis.files[index]
                self.table.insert("", "end", iid=str(index), values=("✓" if item.selected else "", Path(item.path).name, item.path, format_size(item.size), datetime.fromtimestamp(item.mtime_ns / 1e9).strftime("%Y-%m-%d %H:%M:%S"), item.situation))
            if start + 100 < len(analysis.files):
                self.root.after(1, batch, start + 100)
            else:
                self.table.selection_set([row for row in selected_rows if self.table.exists(row)])
        batch()

    def toggle(self):
        if not self.analysis or self.external_busy:
            return
        for index in self.table.selection():
            item = self.analysis.files[int(index)]
            item.selected = not item.selected
            item.decision = "incluir ocorrência" if item.selected else "ignorar ocorrência"
        self.render()

    def select_all(self, selected):
        if not self.analysis or self.external_busy:
            return
        for item in self.analysis.files:
            item.selected, item.decision = selected, "incluir todos" if selected else "ignorar todos"
        self.render()

    def select_group(self, selected):
        if not self.analysis or self.external_busy:
            return
        names = {Path(self.analysis.files[int(index)].path).name.casefold() for index in self.table.selection()}
        for item in self.analysis.files:
            if Path(item.path).name.casefold() in names:
                item.selected, item.decision = selected, "incluir grupo" if selected else "ignorar grupo"
        self.render()

    def open_location(self, path):
        self.background("opened", lambda: open_existing_location(path))

    def open_candidate(self):
        if self.analysis and self.table.selection():
            self.open_location(self.analysis.files[int(self.table.selection()[0])].path)

    def background(self, kind, work):
        if self.closing:
            return
        self.aux_workers = [worker for worker in self.aux_workers if worker.is_alive()]
        def worker():
            try:
                self.events.put((kind, work()))
            except Exception as exc:
                self.events.put(("aux_error", f"Operação indisponível: {exc}"))
        thread = threading.Thread(target=worker, daemon=False)
        self.aux_workers.append(thread)
        thread.start()

    def refresh_history(self, recover=False):
        self.history_generation += 1
        generation = self.history_generation
        query = self.history_query.get()
        def read():
            if recover:
                try:
                    with catalog_lock(lock_path(self.store)):
                        self.store.recover(locked=True)
                except RuntimeError:
                    pass  # Another instance owns a live run; it is not abandoned.
            return generation, self.store.list(query=query)
        self.background("history", read)

    def history_record(self, kind):
        selected = self.history.selection()
        if selected:
            self.background(kind, lambda: self.store.get(selected[0]))

    def open_history(self):
        self.history_record("open_history")

    def history_details(self):
        self.history_record("details")

    def open_report(self):
        selected = self.history.selection()
        if selected:
            def work():
                row = self.store.get(selected[0])
                path = row["payload"].get("report")
                if not path:
                    raise ValueError("Esta execução antiga não possui relatório de conteúdo final.")
                Path(path).stat()  # Preserve access errors; opening does not prove availability.
                open_with_default_application(path)
            self.background("opened", work)

    def show_history_details(self, row):
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Compactação {row['id']} — {row['state']}")
        dialog.geometry("900x650")
        notebook = ttk.Notebook(dialog)
        notebook.pack(fill="both", expand=True)
        final_page = ttk.Frame(notebook)
        analysis_page = ttk.Frame(notebook)
        notebook.add(final_page, text="PSDs efetivamente incluídos")
        notebook.add(analysis_page, text="Análise e decisões")
        ttk.Label(final_page, text=f"Compactado: {row['payload'].get('archive', 'não registrado')}", wraplength=850).pack(anchor="w")
        ttk.Label(final_page, text=f"{len(row['files'])} PSDs registrados. Localizações históricas; disponibilidade atual não verificada.", wraplength=850).pack(anchor="w")
        final_table = self.tree(final_page, ("nome", "origem", "interno", "tamanho"))
        for item in row["files"]:
            final_table.insert("", "end", values=(item["name"], item["original_path"], item["internal_path"], format_size(item["size"])))
        if not row["files"]:
            ttk.Label(final_page, text="Sem inclusão registrada por PSD. Execuções antigas preservam a análise, que não comprova inclusão.").pack(anchor="w")
        files = row["payload"].get("analysis", {}).get("files", [])
        table = self.tree(analysis_page, ("nome", "origem", "interno", "decisão"))
        for index, item in enumerate(files):
            table.insert("", "end", iid=str(index), values=(Path(item["path"]).name, item["path"], item["internal"], item["decision"]))
        ttk.Button(analysis_page, text="Abrir localização original", command=lambda: self.open_location(files[int(table.selection()[0])]["path"]) if table.selection() else None).pack(anchor="w")
        text = tk.Text(dialog, wrap="word", height=15)
        text.pack(fill="both", expand=True)
        text.insert("end", json.dumps(row, ensure_ascii=False, indent=2))
        text.configure(state="disabled")
