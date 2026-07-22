from __future__ import annotations

from pathlib import Path
import os
import platform
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import APP_NAME, load_config, save_config
from .indexer import build_index, load_index
from .processor import process_excel


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("860x650")
        self.root.minsize(760, 580)

        self.config = load_config()
        self.index = {}

        self.excel_var = tk.StringVar(value=self.config.get("excel_path", ""))
        self.source_var = tk.StringVar(value=self.config.get("source_dir", ""))
        self.output_var = tk.StringVar(value=self.config.get("output_dir", ""))
        self.status_var = tk.StringVar(value="Selecione a planilha e as pastas.")
        self.index_status_var = tk.StringVar(value="Índice ainda não carregado.")

        self._build_style()
        self._build_ui()
        self._try_load_saved_index()

    def _build_style(self):
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("Arial", 20, "bold"))
        style.configure("Subtitle.TLabel", font=("Arial", 10))
        style.configure("Primary.TButton", font=("Arial", 11, "bold"), padding=10)
        style.configure("Secondary.TButton", padding=8)

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=24)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text="Organizador de Estampas", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            main,
            text=(
                "Busca em CLIENTE/ESTAMPA/ESTAMPA-VARIANTE e copia para "
                "CLIENTE/DATA/PEDIDO/BASE."
            ),
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(4, 20))

        form = ttk.LabelFrame(main, text="1. Selecione os arquivos e pastas", padding=16)
        form.pack(fill="x")

        self._path_row(
            form, 0, "Planilha Excel", self.excel_var,
            "Selecionar Excel", self.select_excel
        )
        self._path_row(
            form, 1, "Pasta de entrada das estampas", self.source_var,
            "Selecionar entrada", self.select_source
        )
        self._path_row(
            form, 2, "Pasta de saída dos pedidos", self.output_var,
            "Selecionar saída", self.select_output
        )

        index_frame = ttk.LabelFrame(main, text="2. Índice das estampas", padding=16)
        index_frame.pack(fill="x", pady=16)

        ttk.Label(index_frame, textvariable=self.index_status_var).pack(anchor="w")
        button_line = ttk.Frame(index_frame)
        button_line.pack(fill="x", pady=(10, 0))
        self.index_button = ttk.Button(
            button_line, text="Atualizar índice", command=self.start_indexing,
            style="Secondary.TButton"
        )
        self.index_button.pack(side="left")
        ttk.Button(
            button_line, text="Abrir pasta de configurações",
            command=self.open_app_folder, style="Secondary.TButton"
        ).pack(side="left", padx=8)

        process_frame = ttk.LabelFrame(main, text="3. Gerar pastas dos pedidos", padding=16)
        process_frame.pack(fill="both", expand=True)

        self.progress = ttk.Progressbar(process_frame, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 10))

        ttk.Label(
            process_frame, textvariable=self.status_var, wraplength=760
        ).pack(anchor="w")

        self.log = tk.Text(process_frame, height=10, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, pady=12)

        action_line = ttk.Frame(process_frame)
        action_line.pack(fill="x")
        self.process_button = ttk.Button(
            action_line, text="GERAR PASTAS DOS PEDIDOS",
            command=self.start_processing, style="Primary.TButton"
        )
        self.process_button.pack(side="left")
        ttk.Button(
            action_line, text="Abrir pasta de saída",
            command=self.open_output_folder, style="Secondary.TButton"
        ).pack(side="left", padx=10)

    def _path_row(self, parent, row, label, variable, button_text, command):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=7)
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, sticky="ew", padx=10, pady=7
        )
        ttk.Button(parent, text=button_text, command=command).grid(
            row=row, column=2, pady=7
        )
        parent.columnconfigure(1, weight=1)

    def select_excel(self):
        path = filedialog.askopenfilename(
            title="Selecione a planilha",
            filetypes=[("Excel", "*.xlsx *.xlsm")]
        )
        if path:
            self.excel_var.set(path)
            self._save_paths()

    def select_source(self):
        path = filedialog.askdirectory(title="Selecione a pasta das estampas")
        if path:
            self.source_var.set(path)
            self._save_paths()
            self.index = {}
            self.index_status_var.set("Pasta alterada. Clique em Atualizar índice.")

    def select_output(self):
        path = filedialog.askdirectory(title="Selecione a pasta de saída")
        if path:
            self.output_var.set(path)
            self._save_paths()

    def _save_paths(self):
        save_config({
            "excel_path": self.excel_var.get(),
            "source_dir": self.source_var.get(),
            "output_dir": self.output_var.get(),
        })

    def _try_load_saved_index(self):
        source_text = self.source_var.get().strip()
        if not source_text:
            return
        source = Path(source_text)
        self.index = load_index(source)
        if self.index:
            self.index_status_var.set(
                f"Índice carregado: {len(self.index):,} nomes de imagens."
            )

    def start_indexing(self):
        source_text = self.source_var.get().strip()
        if not source_text:
            messagebox.showwarning("Atenção", "Selecione a pasta de entrada das estampas.")
            return

        self._set_busy(True)
        self.progress.configure(mode="indeterminate")
        self.progress.start(10)
        self.status_var.set("Indexando as imagens. Aguarde a conclusão.")
        self._log("Iniciando indexação...")

        thread = threading.Thread(
            target=self._index_worker,
            args=(Path(source_text),),
            daemon=True
        )
        thread.start()

    def _index_worker(self, source):
        try:
            index, result = build_index(
                source,
                progress_callback=lambda count, msg: self.root.after(
                    0, self._index_progress, count, msg
                )
            )
            self.index = index
            self.root.after(0, self._index_complete, result)
        except Exception as exc:
            self.root.after(0, self._show_error, str(exc))

    def _index_progress(self, count, message):
        self.status_var.set(message)
        self._log(message)

    def _index_complete(self, result):
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.index_status_var.set(
            f"Índice pronto: {result.total_files:,} arquivos; "
            f"{result.indexed_names:,} nomes; {result.duplicates:,} duplicados."
        )
        self.status_var.set("Indexação concluída.")
        self._log(
            f"Índice concluído em {result.elapsed_seconds:.1f}s. "
            f"Arquivos: {result.total_files:,}. Duplicados: {result.duplicates:,}."
        )
        self._set_busy(False)

    def start_processing(self):
        excel = self.excel_var.get().strip()
        source = self.source_var.get().strip()
        output = self.output_var.get().strip()

        if not excel or not source or not output:
            messagebox.showwarning(
                "Atenção",
                "Selecione o Excel, a pasta de entrada e a pasta de saída."
            )
            return

        if not self.index:
            self.index = load_index(Path(source))
        if not self.index:
            messagebox.showwarning(
                "Índice necessário",
                "Clique primeiro em Atualizar índice."
            )
            return

        self._save_paths()
        self._set_busy(True)
        self.progress.configure(mode="determinate", value=0)
        self.status_var.set("Processando a planilha...")
        self._log("Iniciando processamento dos pedidos.")

        thread = threading.Thread(
            target=self._process_worker,
            args=(Path(excel), Path(output)),
            daemon=True
        )
        thread.start()

    def _process_worker(self, excel, output):
        try:
            results, summary = process_excel(
                excel,
                output,
                self.index,
                progress_callback=lambda current, total, msg: self.root.after(
                    0, self._processing_progress, current, total, msg
                )
            )
            self.root.after(0, self._processing_complete, results, summary)
        except Exception as exc:
            self.root.after(0, self._show_error, str(exc))

    def _processing_progress(self, current, total, message):
        percent = (current / total * 100) if total else 0
        self.progress.configure(value=percent)
        self.status_var.set(message)

    def _processing_complete(self, results, summary):
        self.progress.configure(value=100)
        text = (
            f"Concluído: {summary.copiados} copiados; "
            f"{summary.nao_encontrados} não encontrados; "
            f"{summary.duplicados} duplicados; "
            f"{summary.ignorados} ignorados; "
            f"{summary.pedidos_criados} pastas de pedidos."
        )
        self.status_var.set(text)
        self._log(text)
        missing_items = [item for item in results if item.status == "NÃO ENCONTRADO"]
        if missing_items:
            self._log("Não encontrados:")
            for item in missing_items:
                self._log(
                    f"- Linha {item.linha} | Pedido: {item.pedido} | "
                    f"Cliente: {item.cliente} | Base: {item.base} | {item.observacao}"
                )
        self._log(f"Relatório: {summary.report_xlsx}")
        self._set_busy(False)
        messagebox.showinfo("Processamento concluído", text)

    def _show_error(self, message):
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.status_var.set("Ocorreu um erro.")
        self._log(f"ERRO: {message}")
        self._set_busy(False)
        messagebox.showerror("Erro", message)

    def _set_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.index_button.configure(state=state)
        self.process_button.configure(state=state)

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def open_output_folder(self):
        path = self.output_var.get().strip()
        if path:
            self._open_path(Path(path))

    def open_app_folder(self):
        from .config import APP_DIR, ensure_app_dir
        ensure_app_dir()
        self._open_path(APP_DIR)

    def _open_path(self, path: Path):
        path.mkdir(parents=True, exist_ok=True)
        system = platform.system()
        if system == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)

    def run(self):
        self.root.mainloop()
