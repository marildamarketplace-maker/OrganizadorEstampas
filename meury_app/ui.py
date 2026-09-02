from __future__ import annotations

from pathlib import Path
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .art_search import ArtSearchEngine, SearchResult, ThumbnailCache, principal_keywords
from .config import (
    APP_DIR, APP_NAME, load_config, save_config,
    select_app_data_dir, validate_original_images_path,
)
from .catalog_diagnostics import load_catalog_statistics, load_category_records
from .image_collector import collect_images, format_size
from .image_analyzer import LocalImageAnalyzer
from .indexer import (
    append_analysis_result, build_index, load_index,
    index_catalog_available, normalize_source_dirs, update_index_incremental,
)
from .processor import process_csv_text, process_excel
from .platform_utils import open_with_default_application
from .preview_generator import generate_pending_previews
from .cloud_preview import upload_pending_previews
from .supabase_sync import sync_pending_records
from .pending_sync import synchronize_pending
from .original_folder import OriginalFolderError, open_original_directory
from .semantic_search import (
    SemanticSearchIndex, merge_hybrid_results, record_identity,
    semantic_index_status,
)
from .visual_search import (
    VisualSearchIndex, visual_index_status, visual_record_identity,
)


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("960x840")
        self.root.minsize(640, 480)

        self.config = load_config()
        self.index = {}
        self.collector_cancel_event = None
        self.operation_pause_event = None
        self.search_engine = None
        self.semantic_engine = None
        self.visual_engine = None
        self.search_lock = threading.Lock()
        self.search_generation = 0
        self.search_after_id = None
        self.search_image_refs = []
        self.thumbnail_cache = ThumbnailCache()

        self.excel_var = tk.StringVar(value=self.config.get("excel_path", ""))
        self.input_mode_var = tk.StringVar(
            value=self.config.get("input_mode", "excel")
        )
        self.source_dirs = [
            str(path) for path in normalize_source_dirs(self.config.get("source_dirs", []))
        ]
        self._validate_source_paths()
        self.output_var = tk.StringVar(value=self.config.get("output_dir", ""))
        self.app_data_dir_var = tk.StringVar(value=str(APP_DIR))
        self.collector_source_dirs = list(
            self.config.get("collector_source_dirs", [])
        )
        self.collector_output_var = tk.StringVar(
            value=self.config.get("collector_output_dir", "")
        )
        selected_collector_extensions = set(
            self.config.get(
                "collector_extensions",
                [".jpg", ".jpeg", ".png"],
            )
        )
        self.collector_extension_vars = {
            extension: tk.BooleanVar(
                value=extension in selected_collector_extensions
            )
            for extension in (".jpg", ".jpeg", ".png", ".pdf")
        }
        self.collector_status_var = tk.StringVar(
            value="Selecione as entradas, os formatos e a pasta de saída."
        )
        self.status_var = tk.StringVar(value="Selecione a planilha e as pastas.")
        self.index_status_var = tk.StringVar(value="Índice ainda não carregado.")
        self.semantic_enabled_var = tk.BooleanVar(
            value=bool(self.config.get("semantic_search_enabled", False))
        )
        self.semantic_status_var = tk.StringVar(value="Verificando índice semântico...")
        self.visual_status_var = tk.StringVar(value="Verificando índice visual...")
        self.catalog_stats_var = tk.StringVar(value="Estatísticas: calculando em segundo plano...")
        self.stats_generation = 0

        self._build_style()
        self._build_ui()
        if self.root_path_error:
            self.index_status_var.set(self.root_path_error)
            message = self.root_path_error
            self.root.after_idle(self._show_root_path_error, message)
        else:
            self._try_load_saved_index()
        self._refresh_semantic_status()
        self._refresh_visual_status()
        if not self.root_path_error:
            self._refresh_catalog_statistics()

    def _build_style(self):
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("Arial", 20, "bold"))
        style.configure("Subtitle.TLabel", font=("Arial", 10))
        style.configure("Primary.TButton", font=("Arial", 11, "bold"), padding=10)
        style.configure("Secondary.TButton", padding=8)

    def _show_root_path_error(self, message):
        """Mostra o erro apenas enquanto a janela principal ainda existe."""
        try:
            if self.root.winfo_exists():
                messagebox.showerror(
                    "Pasta de entrada indisponível", message, parent=self.root
                )
        except tk.TclError:
            # A janela pode ter sido fechada antes da execução do after_idle.
            return

    def _validate_source_paths(self):
        errors = []
        for source in self.source_dirs:
            try:
                validate_original_images_path({"original_images_path": source})
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
        self.root_path_error = "\n\n".join(errors)

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=24)
        main.pack(fill="both", expand=True)

        notebook = ttk.Notebook(main)
        notebook.pack(fill="both", expand=True)
        organizer_tab, organizer_page, organizer_canvas = self._scrollable_page(notebook)
        collector_tab, collector_page, collector_canvas = self._scrollable_page(notebook)
        # A pesquisa avançada pertence ao outro sistema. Mantemos o painel
        # construído, mas fora do Notebook, para preservar compatibilidade com
        # configurações e permitir reativação futura sem migração.
        search_tab = ttk.Frame(notebook, padding=18)
        notebook.add(organizer_tab, text="Mapear estampas")
        notebook.add(collector_tab, text="Copiar imagens")

        tab_canvases = {
            str(organizer_tab): organizer_canvas,
            str(collector_tab): collector_canvas,
        }

        def scroll_current_page(event):
            if isinstance(event.widget, (tk.Text, tk.Listbox)):
                return
            canvas = tab_canvases.get(notebook.select())
            if canvas is None:
                return
            if getattr(event, "num", None) == 4:
                amount = -1
            elif getattr(event, "num", None) == 5:
                amount = 1
            else:
                amount = -1 if event.delta > 0 else 1 if event.delta < 0 else 0
            canvas.yview_scroll(amount, "units")

        self.root.bind_all("<MouseWheel>", scroll_current_page, add="+")
        self.root.bind_all("<Button-4>", scroll_current_page, add="+")
        self.root.bind_all("<Button-5>", scroll_current_page, add="+")

        self._build_organizer_tab(organizer_page)
        self._build_collector_tab(collector_page)
        self._build_search_tab(search_tab)

    def _scrollable_page(self, parent):
        container = ttk.Frame(parent)
        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            container, orient="vertical", command=canvas.yview
        )
        page = ttk.Frame(canvas, padding=18)
        page_window = canvas.create_window((0, 0), window=page, anchor="nw")

        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        page.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(page_window, width=event.width),
        )

        return container, page, canvas

    def _build_organizer_tab(self, main):
        ttk.Label(main, text="Organizador de Estampas", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            main,
            text="Adicione as pastas de estampas, atualize o índice e sincronize os pendentes.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(4, 20))

        form = ttk.LabelFrame(main, text="1. Pastas e configurações", padding=16)
        form.pack(fill="x")

        # Campos do fluxo legado de pedidos ficam construídos para manter os
        # métodos compatíveis, porém não são exibidos na aba de mapeamento.
        legacy_order_fields = ttk.Frame(form)
        mode_frame = ttk.Frame(legacy_order_fields)
        mode_frame.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Label(mode_frame, text="Entrada dos pedidos:").pack(side="left")
        self.excel_mode_button = ttk.Radiobutton(
            mode_frame,
            text="Planilha Excel",
            variable=self.input_mode_var,
            value="excel",
        )
        self.excel_mode_button.pack(side="left", padx=(10, 4))
        self.csv_mode_button = ttk.Radiobutton(
            mode_frame,
            text="Texto CSV",
            variable=self.input_mode_var,
            value="csv",
        )
        self.csv_mode_button.pack(side="left", padx=4)

        self._path_row(
            legacy_order_fields, 1, "Planilha Excel", self.excel_var,
            "Selecionar Excel", self.select_excel
        )
        self._csv_text_row(legacy_order_fields, 2)
        self._source_paths_row(form, 0)
        self._path_row(
            form, 1, "Pasta de dados/configurações", self.app_data_dir_var,
            "Alterar pasta", self.select_app_data_dir
        )
        self._path_row(
            form, 2, "Pasta de saída dos pedidos", self.output_var,
            "Selecionar saída", self.select_output
        )

        index_frame = ttk.LabelFrame(main, text="2. Índice das estampas", padding=16)
        index_frame.pack(fill="x", pady=16)

        ttk.Label(index_frame, textvariable=self.index_status_var).pack(anchor="w")
        ttk.Label(
            index_frame,
            text="Atualização local: verifica as pastas e atualiza o catálogo. "
                 "Não gera previews, não envia arquivos e não usa IA.",
            style="Subtitle.TLabel", wraplength=820,
        ).pack(anchor="w", pady=(5, 0))
        button_line = ttk.Frame(index_frame)
        button_line.pack(fill="x", pady=(10, 0))
        self.index_button = ttk.Button(
            button_line, text="ATUALIZAR ÍNDICE", command=self.start_local_indexing,
            style="Primary.TButton"
        )
        self.index_button.pack(side="left")
        self.pending_sync_button = ttk.Button(
            button_line, text="Sincronizar pendentes",
            command=self.start_pending_sync, style="Primary.TButton",
        )
        self.pending_sync_button.pack(side="left", padx=(8, 0))
        secondary_line = ttk.Frame(index_frame)
        secondary_line.pack(fill="x", pady=(8, 0))
        self.incremental_index_button = ttk.Button(
            secondary_line,
            text="Reconstruir índice local",
            command=self.start_indexing,
            style="Secondary.TButton",
        )
        self.incremental_index_button.pack(side="left", padx=(8, 0))
        self.preview_button = ttk.Button(
            secondary_line, text="Gerar previews pendentes",
            command=self.start_preview_generation, style="Secondary.TButton",
        )
        self.preview_button.pack(side="left", padx=(8, 0))
        self.cloud_button = ttk.Button(
            secondary_line, text="Enviar previews para Cloud",
            command=self.start_cloud_upload, style="Secondary.TButton",
        )
        self.cloud_button.pack(side="left", padx=(8, 0))
        self.supabase_button = ttk.Button(
            secondary_line, text="Sincronizar Supabase",
            command=self.start_supabase_sync, style="Secondary.TButton",
        )
        self.supabase_button.pack(side="left", padx=(8, 0))
        ttk.Button(
            secondary_line, text="Abrir pasta de configurações",
            command=self.open_app_folder, style="Secondary.TButton"
        ).pack(side="left", padx=8)
        batch_controls = ttk.Frame(index_frame)
        batch_controls.pack(fill="x", pady=(8, 0))
        self.operation_pause_button = ttk.Button(
            batch_controls, text="Pausar ação", command=self.pause_current_operation,
            state="disabled",
        )
        self.operation_pause_button.pack(side="left")
        self.operation_continue_button = ttk.Button(
            batch_controls, text="Continuar ação",
            command=self.continue_current_operation, state="disabled",
        )
        self.operation_continue_button.pack(side="left", padx=(8, 0))
        ttk.Label(
            batch_controls,
            text="A pausa acontece com segurança após concluir o item ou lote atual.",
            style="Subtitle.TLabel",
        ).pack(side="left", padx=(12, 0))

        # Etapa legada de geração de pedidos: mantida construída para preservar
        # os métodos existentes, mas fora do layout da aba Mapear estampas.
        process_frame = ttk.LabelFrame(main, text="3. Gerar pastas dos pedidos", padding=16)

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

    def _build_collector_tab(self, main):
        ttk.Label(
            main, text="Copiar imagens por pasta", style="Title.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            main,
            text=(
                "Percorre as entradas e copia cada imagem para "
                "SAÍDA/PASTA_DA_IMAGEM/ARQUIVO."
            ),
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(4, 20))

        form = ttk.LabelFrame(main, text="Configuração", padding=16)
        form.pack(fill="x")
        ttk.Label(form, text="Pastas de entrada").grid(
            row=0, column=0, sticky="nw", pady=7
        )
        list_frame = ttk.Frame(form)
        list_frame.grid(row=0, column=1, sticky="ew", padx=10, pady=7)
        list_frame.columnconfigure(0, weight=1)
        self.collector_source_list = tk.Listbox(
            list_frame, height=5, selectmode="extended", exportselection=False
        )
        self.collector_source_list.grid(row=0, column=0, sticky="ew")
        scrollbar = ttk.Scrollbar(
            list_frame,
            orient="vertical",
            command=self.collector_source_list.yview,
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.collector_source_list.configure(yscrollcommand=scrollbar.set)
        self._refresh_collector_source_list()

        buttons = ttk.Frame(form)
        buttons.grid(row=0, column=2, sticky="n", pady=7)
        self.collector_add_button = ttk.Button(
            buttons,
            text="Adicionar entrada",
            command=self.select_collector_source,
        )
        self.collector_add_button.pack(fill="x")
        self.collector_remove_button = ttk.Button(
            buttons,
            text="Remover selecionada",
            command=self.remove_collector_sources,
        )
        self.collector_remove_button.pack(fill="x", pady=(6, 0))

        ttk.Label(form, text="Formatos").grid(
            row=1, column=0, sticky="w", pady=7
        )
        formats = ttk.Frame(form)
        formats.grid(row=1, column=1, sticky="w", padx=10, pady=7)
        self.collector_format_buttons = []
        for extension, variable in self.collector_extension_vars.items():
            button = ttk.Checkbutton(
                formats,
                text=extension.removeprefix(".").upper(),
                variable=variable,
                command=self._save_paths,
            )
            button.pack(side="left", padx=(0, 12))
            self.collector_format_buttons.append(button)

        self._path_row(
            form,
            2,
            "Pasta de saída",
            self.collector_output_var,
            "Selecionar saída",
            self.select_collector_output,
        )
        form.columnconfigure(1, weight=1)

        process_frame = ttk.LabelFrame(main, text="Copiar imagens", padding=16)
        process_frame.pack(fill="both", expand=True, pady=(16, 0))
        self.collector_progress = ttk.Progressbar(
            process_frame, mode="determinate", maximum=100
        )
        self.collector_progress.pack(fill="x", pady=(0, 10))
        ttk.Label(
            process_frame,
            textvariable=self.collector_status_var,
            wraplength=760,
        ).pack(anchor="w")
        self.collector_log = tk.Text(
            process_frame, height=12, state="disabled", wrap="word"
        )
        self.collector_log.pack(fill="both", expand=True, pady=12)
        actions = ttk.Frame(process_frame)
        actions.pack(fill="x")
        self.collector_process_button = ttk.Button(
            actions,
            text="COPIAR IMAGENS",
            command=self.start_collecting_images,
            style="Primary.TButton",
        )
        self.collector_process_button.pack(side="left")
        self.collector_stop_button = ttk.Button(
            actions,
            text="PARAR CÓPIA",
            command=self.stop_collecting_images,
            state="disabled",
            style="Secondary.TButton",
        )
        self.collector_stop_button.pack(side="left", padx=(10, 0))
        ttk.Button(
            actions,
            text="Abrir pasta de saída",
            command=self.open_collector_output,
            style="Secondary.TButton",
        ).pack(side="left", padx=10)

    def _build_search_tab(self, main):
        ttk.Label(main, text="Pesquisar Artes", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            main,
            text="Pesquise por descrição, palavras-chave, cores, elementos, temas, categoria, nome ou caminho.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(4, 14))
        search_line = ttk.Frame(main)
        search_line.pack(fill="x")
        ttk.Label(search_line, text="Pesquisar artes...").pack(side="left", padx=(0, 8))
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(
            search_line, textvariable=self.search_var, font=("Arial", 16)
        )
        self.search_entry.pack(side="left", fill="x", expand=True, ipady=8)
        self.search_entry.insert(0, "")
        self.search_entry.bind("<Return>", lambda _event: self.start_art_search())
        self.search_entry.bind("<KeyRelease>", self._schedule_art_search)
        ttk.Button(
            search_line, text="PESQUISAR", command=self.start_art_search,
            style="Primary.TButton",
        ).pack(side="left", padx=(10, 0))
        ttk.Button(
            search_line, text="Recarregar catálogo", command=self.reload_art_search,
            style="Secondary.TButton",
        ).pack(side="left", padx=(8, 0))
        advanced_line = ttk.Frame(main)
        advanced_line.pack(fill="x", pady=(10, 0))
        self.advanced_search_button = ttk.Menubutton(
            advanced_line, text="Pesquisa avançada ▾", style="Secondary.TButton"
        )
        advanced_menu = tk.Menu(self.advanced_search_button, tearoff=False)
        advanced_menu.add_checkbutton(
            label="Ativar busca semântica",
            variable=self.semantic_enabled_var,
            command=self._semantic_option_changed,
        )
        advanced_menu.add_separator()
        advanced_menu.add_command(
            label="Encontrar imagens semelhantes...",
            command=self.select_similar_image,
        )
        advanced_menu.add_separator()
        advanced_menu.add_command(
            label="Atualizar índice semântico",
            command=lambda: self.start_semantic_update(False),
        )
        advanced_menu.add_command(
            label="Reconstruir índice semântico",
            command=lambda: self.start_semantic_update(True),
        )
        advanced_menu.add_separator()
        advanced_menu.add_command(
            label="Como funciona a busca semântica?",
            command=self.show_semantic_help,
        )
        advanced_menu.add_command(
            label="Como funcionam as imagens semelhantes?",
            command=self.show_similar_help,
        )
        self.advanced_search_button.configure(menu=advanced_menu)
        self.advanced_search_button.pack(side="left")
        self.semantic_update_button = self.advanced_search_button
        self.semantic_rebuild_button = self.advanced_search_button
        self.visual_find_button = self.advanced_search_button
        self.visual_update_button = self.advanced_search_button
        self.visual_rebuild_button = self.advanced_search_button
        stats = ttk.LabelFrame(main, text="Estatísticas do catálogo", padding=8)
        stats.pack(fill="x", pady=(9, 0))
        ttk.Label(stats, textvariable=self.catalog_stats_var, wraplength=850).pack(anchor="w")
        self.catalog_stats_buttons = ttk.Frame(stats)
        self.catalog_stats_buttons.pack(fill="x", pady=(7, 0))
        self.catalog_category_buttons = {}
        categories = (
            ("total", "Total"), ("new", "Novas"), ("changed", "Alteradas"),
            ("unchanged", "Inalteradas"), ("preview", "Preview pendente"),
            ("cloud", "Cloud pendente"), ("supabase", "Supabase pendente"),
            ("synced", "Sincronizadas"), ("missing", "Ausentes"),
            ("errors", "Com erro"),
        )
        for position, (category, label) in enumerate(categories):
            button = ttk.Button(
                self.catalog_stats_buttons, text=label,
                command=lambda key=category, title=label: self.show_catalog_category(key, title),
            )
            button.grid(row=position // 5, column=position % 5, sticky="ew", padx=3, pady=3)
            self.catalog_stats_buttons.columnconfigure(position % 5, weight=1)
            self.catalog_category_buttons[category] = button
        self.search_status_var = tk.StringVar(
            value="Digite, por exemplo: flores vermelhas, Natal ou xadrez azul."
        )
        ttk.Label(
            main, textvariable=self.search_status_var, wraplength=850
        ).pack(anchor="w", pady=(10, 8))

        results_container = ttk.Frame(main)
        results_container.pack(fill="both", expand=True)
        self.search_canvas = tk.Canvas(results_container, highlightthickness=0)
        search_scrollbar = ttk.Scrollbar(
            results_container, orient="vertical", command=self.search_canvas.yview
        )
        self.search_results_frame = ttk.Frame(self.search_canvas)
        self.search_results_window = self.search_canvas.create_window(
            (0, 0), window=self.search_results_frame, anchor="nw"
        )
        self.search_canvas.configure(yscrollcommand=search_scrollbar.set)
        self.search_canvas.pack(side="left", fill="both", expand=True)
        search_scrollbar.pack(side="right", fill="y")
        self.search_results_frame.bind(
            "<Configure>",
            lambda _event: self.search_canvas.configure(
                scrollregion=self.search_canvas.bbox("all")
            ),
        )
        self.search_canvas.bind(
            "<Configure>",
            lambda event: self.search_canvas.itemconfigure(
                self.search_results_window, width=event.width
            ),
        )
        self.search_canvas.bind_all("<MouseWheel>", self._search_mousewheel, add="+")
        self.search_canvas.bind_all("<Button-4>", self._search_mousewheel, add="+")
        self.search_canvas.bind_all("<Button-5>", self._search_mousewheel, add="+")

    def _search_mousewheel(self, event):
        if not hasattr(self, "search_canvas"):
            return
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if widget is None:
            return
        current = widget
        inside = False
        while current is not None:
            if current == self.search_canvas:
                inside = True
                break
            current = getattr(current, "master", None)
        if not inside:
            return
        if getattr(event, "num", None) == 4:
            amount = -1
        elif getattr(event, "num", None) == 5:
            amount = 1
        else:
            amount = -1 if event.delta > 0 else 1
        self.search_canvas.yview_scroll(amount, "units")
        return "break"

    def _schedule_art_search(self, _event=None):
        if self.search_after_id:
            self.root.after_cancel(self.search_after_id)
        self.search_after_id = self.root.after(450, self.start_art_search)

    def reload_art_search(self):
        self.search_engine = None
        self.semantic_engine = None
        self.start_art_search()

    def _refresh_catalog_statistics(self):
        self.stats_generation += 1
        generation = self.stats_generation
        if not self.source_dirs:
            self.catalog_stats_var.set("Nenhuma pasta de artes configurada.")
            return
        self.catalog_stats_var.set("Estatísticas: calculando em segundo plano...")

        def worker():
            try:
                result = load_catalog_statistics([Path(source) for source in self.source_dirs])
                self.root.after(0, self._show_catalog_statistics, generation, result)
            except Exception as exc:
                self.root.after(0, self.catalog_stats_var.set, f"Estatísticas indisponíveis: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _show_catalog_statistics(self, generation, result):
        if generation != self.stats_generation:
            return
        self.catalog_stats_var.set(
            f"Total de imagens: {result.total:,}   |   Precisam de processamento: "
            f"{max(result.pending_preview, result.pending_cloud, result.pending_supabase):,}   |   "
            f"Ausentes: {result.missing:,}   |   Com erro: {result.errors:,}"
        )
        counts = {
            "total": result.total, "new": result.new, "changed": result.changed,
            "unchanged": result.unchanged, "preview": result.pending_preview,
            "cloud": result.pending_cloud, "supabase": result.pending_supabase,
            "synced": result.synced, "missing": result.missing, "errors": result.errors,
        }
        labels = {
            "total": "Total", "new": "Novas", "changed": "Alteradas",
            "unchanged": "Inalteradas", "preview": "Preview pendente",
            "cloud": "Cloud pendente", "supabase": "Supabase pendente",
            "synced": "Sincronizadas", "missing": "Ausentes", "errors": "Com erro",
        }
        for category, count in counts.items():
            self.catalog_category_buttons[category].configure(
                text=f"{labels[category]}\n{count:,}"
            )

    def show_catalog_category(self, category, title):
        if not self.source_dirs:
            return

        def worker():
            try:
                records = load_category_records(
                    [Path(source) for source in self.source_dirs], category, limit=500
                )
                self.root.after(0, self._show_catalog_category_dialog, title, records)
            except Exception as exc:
                self.root.after(0, messagebox.showerror, "Detalhes do catálogo", str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _show_catalog_category_dialog(self, title, records):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.geometry("820x480")
        body = ttk.Frame(dialog, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text=f"{len(records):,} arquivo(s) exibido(s). Limite da lista: 500.",
        ).pack(anchor="w", pady=(0, 8))
        list_frame = ttk.Frame(body)
        list_frame.pack(fill="both", expand=True)
        items = tk.Listbox(list_frame)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=items.yview)
        items.configure(yscrollcommand=scrollbar.set)
        items.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        for record in records:
            reason = record.get("attention_reason") or record.get("last_error") or ""
            suffix = f"  —  {reason}" if reason else ""
            items.insert("end", f"{record.get('path', '')}{suffix}")

        def open_selected(_event=None):
            selection = items.curselection()
            if not selection:
                return
            path = Path(records[selection[0]].get("path", ""))
            if path.is_file():
                self._open_file(path)
            elif path.parent.is_dir():
                self._open_directory(path.parent)

        items.bind("<Double-Button-1>", open_selected)
        ttk.Button(body, text="Abrir selecionado", command=open_selected).pack(
            anchor="e", pady=(8, 0)
        )

    def _semantic_option_changed(self):
        self._save_paths()
        self._refresh_semantic_status()
        if self.search_var.get().strip():
            self.start_art_search()

    def show_semantic_help(self):
        messagebox.showinfo(
            "Busca semântica",
            "A busca semântica entende o significado da pesquisa, mesmo quando as "
            "palavras digitadas não são exatamente iguais às palavras do catálogo.\n\n"
            "Exemplo: procurar por ‘campanha contra câncer de mama’ também pode "
            "encontrar artes marcadas como Outubro Rosa, laço rosa, saúde feminina "
            "e prevenção.\n\n"
            "Os metadados são gerados pelo sistema de IA após a sincronização. "
            "Atualize o índice semântico depois que esses dados estiverem disponíveis.",
        )

    def show_similar_help(self):
        messagebox.showinfo(
            "Imagens semelhantes",
            "Essa pesquisa usa a descrição, as cores, os elementos, os temas e a "
            "categoria gerados pelo GPT para localizar artes com conteúdo parecido.\n\n"
            "Ela compara o significado visual, não os pixels ou arquivos idênticos. "
            "Para melhores resultados, sincronize as imagens, aguarde o processamento "
            "no outro sistema e depois atualize o índice semântico.",
        )

    def _mark_semantic_stale(self):
        self.semantic_engine = None
        available, _message, count = semantic_index_status()
        if available:
            self.semantic_status_var.set(
                f"Índice semântico existente para {count:,} artes; clique em "
                "Atualizar índice semântico para incorporar as alterações."
            )

    def _refresh_semantic_status(self):
        available, message, _count = semantic_index_status()
        state = "ativada" if self.semantic_enabled_var.get() else "desativada"
        self.semantic_status_var.set(f"Busca semântica {state}. {message}")
        return available

    def start_semantic_update(self, rebuild=False):
        if not self.source_dirs:
            messagebox.showwarning("Índice necessário", "Adicione e indexe as pastas primeiro.")
            return
        if not self._ensure_openai_api_key():
            return
        if rebuild and not messagebox.askyesno(
            "Reconstruir índice semântico",
            "Deseja recalcular todos os embeddings semânticos? Os arquivos de imagens não serão alterados.",
        ):
            return
        self._set_busy(True)
        action = "Reconstruindo" if rebuild else "Atualizando"
        self.semantic_status_var.set(f"{action} o índice semântico OpenAI...")
        threading.Thread(
            target=self._semantic_update_worker, args=(rebuild,), daemon=True
        ).start()

    def _semantic_update_worker(self, rebuild):
        try:
            engine = SemanticSearchIndex([Path(source) for source in self.source_dirs])
            result = engine.update(
                rebuild=rebuild,
                progress_callback=lambda current, total, message: self.root.after(
                    0, self._semantic_update_progress, current, total, message
                ),
            )
            self.root.after(0, self._semantic_update_complete, engine, result)
        except Exception as exc:
            self.root.after(0, self._semantic_update_failed, str(exc))

    def _semantic_update_progress(self, current, total, message):
        percent = current / total * 100 if total else 100
        self.semantic_status_var.set(f"{message} ({percent:.1f}%)")

    def _semantic_update_complete(self, engine, result):
        engine.release_model()
        self.semantic_engine = engine
        text = (
            f"Índice semântico pronto: {result.total_eligible:,} artes; "
            f"{result.added:,} novas; {result.updated:,} alteradas; "
            f"{result.removed:,} removidas; {result.reused:,} reutilizadas."
        )
        self.semantic_status_var.set(text)
        self._refresh_visual_status()
        self._set_busy(False)
        self._refresh_catalog_statistics()
        if self.search_var.get().strip() and self.semantic_enabled_var.get():
            self.start_art_search()
        messagebox.showinfo("Busca semântica", text)

    def _semantic_update_failed(self, message):
        self.semantic_status_var.set(f"Busca semântica indisponível: {message}")
        self._set_busy(False)
        messagebox.showerror("Erro no índice semântico", message)

    def _refresh_visual_status(self):
        available, _message, count = semantic_index_status()
        self.visual_status_var.set(
            f"Similaridade por conteúdo disponível para {count:,} artes."
            if available else
            "Crie o índice semântico OpenAI para encontrar imagens semelhantes."
        )

    def _mark_visual_stale(self):
        self.semantic_engine = None
        available, _message, count = semantic_index_status()
        if available:
            self.visual_status_var.set(
                f"Índice de semelhantes existente para {count:,} artes; clique em "
                "Atualizar índice para semelhantes após gerar novos metadados."
            )

    def start_visual_update(self, rebuild=False):
        if not self.source_dirs:
            messagebox.showwarning("Índice necessário", "Adicione e indexe as pastas primeiro.")
            return
        question = (
            "Deseja recalcular todos os embeddings visuais? Esta operação pode demorar "
            "muitas horas, mas não altera as imagens."
            if rebuild else
            "Deseja atualizar os embeddings visuais? Somente imagens novas ou alteradas serão processadas."
        )
        if not messagebox.askyesno("Índice visual", question):
            return
        self._set_busy(True)
        self.visual_status_var.set(
            "Reconstruindo o índice visual..." if rebuild else "Atualizando o índice visual..."
        )
        threading.Thread(
            target=self._visual_update_worker, args=(rebuild,), daemon=True
        ).start()

    def _visual_update_worker(self, rebuild):
        try:
            engine = VisualSearchIndex([Path(source) for source in self.source_dirs])
            result = engine.update(
                rebuild=rebuild,
                progress_callback=lambda current, total, errors, message: self.root.after(
                    0, self._visual_update_progress, current, total, errors, message
                ),
            )
            self.root.after(0, self._visual_update_complete, engine, result)
        except Exception as exc:
            self.root.after(0, self._visual_update_failed, str(exc))

    def _visual_update_progress(self, current, total, errors, message):
        percent = current / total * 100 if total else 100
        self.visual_status_var.set(f"{message} ({percent:.1f}%) | Erros: {errors:,}")

    def _visual_update_complete(self, engine, result):
        engine.release_model()
        self.visual_engine = engine
        text = (
            f"Índice visual pronto: {result.total_eligible:,} artes; "
            f"{result.added:,} novas; {result.updated:,} alteradas; "
            f"{result.removed:,} removidas; {result.reused:,} reutilizadas; "
            f"{result.errors:,} erros."
        )
        self.visual_status_var.set(text)
        self._set_busy(False)
        messagebox.showinfo("Índice visual", text)

    def _visual_update_failed(self, message):
        self.visual_status_var.set(f"Índice visual indisponível: {message}")
        self._set_busy(False)
        messagebox.showerror("Erro no índice visual", message)

    def select_similar_image(self):
        path = filedialog.askopenfilename(
            title="Selecione uma arte para encontrar semelhantes",
            filetypes=[("Imagens", "*.jpg *.jpeg *.png"), ("Todos os arquivos", "*.*")],
        )
        if path:
            self.find_similar_images(Path(path))

    def find_similar_images(self, image_path, source_record=None):
        if not self.source_dirs:
            messagebox.showwarning("Índice necessário", "Adicione e indexe as pastas primeiro.")
            return
        if not Path(image_path).is_file():
            messagebox.showerror("Imagem indisponível", f"A imagem não foi encontrada:\n{image_path}")
            return
        if Path(image_path).suffix.casefold() not in {".jpg", ".jpeg", ".png"}:
            messagebox.showwarning(
                "Formato não suportado",
                "A busca por semelhantes aceita imagens JPG, JPEG e PNG.",
            )
            return
        if not self._ensure_openai_api_key():
            return
        self.search_generation += 1
        generation = self.search_generation
        self.search_status_var.set("Analisando o conteúdo e procurando artes semelhantes...")
        threading.Thread(
            target=self._similar_images_worker,
            args=(Path(image_path), source_record, generation), daemon=True,
        ).start()

    def _similar_images_worker(self, image_path, source_record, generation):
        try:
            if generation != self.search_generation:
                return
            with self.search_lock:
                if generation != self.search_generation:
                    return
                if self.search_engine is None:
                    self.search_engine = ArtSearchEngine(
                        [Path(source) for source in self.source_dirs]
                    )
                    total_records = self.search_engine.load()
                else:
                    total_records = len(self.search_engine._documents)
                if self.semantic_engine is None:
                    self.semantic_engine = SemanticSearchIndex(
                        [Path(source) for source in self.source_dirs]
                    )
                    self.semantic_engine.load()
                records = {
                    record_identity(record): record
                    for record, _fields in self.search_engine._documents
                }
                if source_record and any(source_record.get(field) for field in (
                    "description", "keywords", "colors", "elements", "themes", "category"
                )):
                    from .semantic_search import semantic_document
                    document = semantic_document(source_record)
                else:
                    analysis = LocalImageAnalyzer().analyze(image_path)
                    document = (
                        f"descrição: {analysis.description}. palavras-chave: "
                        f"{', '.join(analysis.keywords)}. cores: {', '.join(analysis.colors)}. "
                        f"elementos: {', '.join(analysis.elements)}. temas: "
                        f"{', '.join(analysis.themes)}. categoria: {analysis.category}"
                    )
                similar = self.semantic_engine.search_document(
                    document, records, limit=201
                )
                exclude = record_identity(source_record) if source_record else None
                if exclude:
                    similar = [item for item in similar if record_identity(item[0]) != exclude]
                similar = similar[:200]
                results = [
                    SearchResult(record, similarity, similarity)
                    for record, similarity in similar
                ]
            self.root.after(
                0, self._show_search_results,
                f"semelhantes a {Path(image_path).name}", generation,
                total_records, results, " Similaridade por conteúdo gerado pelo GPT.",
            )
        except Exception as exc:
            self.root.after(0, self._search_error, generation, str(exc))

    def show_art_context_menu(self, event, record):
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(
            label="Abrir pasta original",
            command=lambda: self.open_original_folder(record),
        )
        menu.add_separator()
        menu.add_command(
            label="Encontrar semelhantes",
            command=lambda: self.find_similar_images(record.get("path", ""), record),
        )
        menu.tk_popup(event.x_root, event.y_root)

    def start_art_search(self):
        self.search_after_id = None
        query = self.search_var.get().strip()
        if not query:
            self.search_status_var.set("Digite o que deseja localizar no catálogo.")
            return
        if not self.source_dirs:
            messagebox.showwarning("Índice necessário", "Adicione e indexe as pastas primeiro.")
            return
        self.search_generation += 1
        generation = self.search_generation
        self.search_status_var.set("Pesquisando no catálogo...")
        semantic_enabled = self.semantic_enabled_var.get()
        thread = threading.Thread(
            target=self._search_worker,
            args=(query, generation, semantic_enabled), daemon=True,
        )
        thread.start()

    def _search_worker(self, query, generation, semantic_enabled):
        try:
            if generation != self.search_generation:
                return
            with self.search_lock:
                # Digitação rápida pode criar várias solicitações. As antigas
                # saem antes de percorrer os 195 mil documentos.
                if generation != self.search_generation:
                    return
                if self.search_engine is None:
                    self.search_engine = ArtSearchEngine(
                        [Path(source) for source in self.source_dirs]
                    )
                    total_records = self.search_engine.load()
                else:
                    total_records = len(self.search_engine._documents)
                text_results = self.search_engine.search(query, limit=250)
                semantic_message = ""
                if semantic_enabled:
                    try:
                        if self.semantic_engine is None:
                            self.semantic_engine = SemanticSearchIndex(
                                [Path(source) for source in self.source_dirs]
                            )
                            self.semantic_engine.load()
                        records_by_identity = {
                            record_identity(record): record
                            for record, _fields in self.search_engine._documents
                        }
                        semantic_results = self.semantic_engine.search(
                            query, records_by_identity, limit=300
                        )
                        hybrid = merge_hybrid_results(
                            text_results, semantic_results, limit=200
                        )
                        results = [SearchResult(record, score) for record, score in hybrid]
                        semantic_message = " Busca híbrida textual + semântica."
                    except Exception as exc:
                        results = text_results[:200]
                        semantic_message = f" Busca semântica indisponível nesta consulta: {exc}"
                else:
                    results = text_results[:200]
            self.root.after(
                0, self._show_search_results, query, generation, total_records,
                results, semantic_message,
            )
        except Exception as exc:
            self.root.after(0, self._search_error, generation, str(exc))

    def _search_error(self, generation, message):
        if generation != self.search_generation:
            return
        self.search_status_var.set(f"Não foi possível pesquisar: {message}")

    def _show_search_results(
        self, query, generation, total_records, results, semantic_message=""
    ):
        if generation != self.search_generation:
            return
        for child in self.search_results_frame.winfo_children():
            child.destroy()
        self.search_image_refs = []
        self.search_canvas.yview_moveto(0)
        if not results:
            self.search_status_var.set(
                f"Nenhuma arte encontrada para “{query}” em {total_records:,} registros."
            )
            return
        suffix = " Exibindo os 200 mais relevantes." if len(results) == 200 else ""
        self.search_status_var.set(
            f"{len(results):,} resultado(s) para “{query}” em "
            f"{total_records:,} registros.{suffix}{semantic_message}"
        )
        columns = 4
        thumbnail_jobs = []
        for position, result in enumerate(results):
            row, column = divmod(position, columns)
            card = ttk.Frame(self.search_results_frame, padding=7, relief="ridge")
            card.grid(row=row, column=column, padx=5, pady=5, sticky="nsew")
            image_label = tk.Label(
                card, text="Carregando miniatura...", width=25, height=9,
                bg="#eeeeee", cursor="hand2",
            )
            image_label.pack(fill="x")
            filename = str(result.record.get("filename", "Sem nome"))
            ttk.Label(card, text=filename, font=("Arial", 10, "bold"), wraplength=195).pack(
                anchor="w", pady=(6, 2)
            )
            if result.similarity is not None:
                ttk.Label(
                    card,
                    text=f"Similaridade: {result.similarity * 100:.0f}%",
                    font=("Arial", 9, "bold"),
                ).pack(anchor="w", pady=(0, 2))
            keywords = principal_keywords(result.record) or "Sem palavras-chave"
            ttk.Label(card, text=keywords, wraplength=195).pack(anchor="w")
            for widget in (card, image_label):
                widget.bind(
                    "<Button-1>",
                    lambda _event, record=result.record: self.open_art_details(record),
                )
                widget.bind(
                    "<Button-3>",
                    lambda event, record=result.record: self.show_art_context_menu(event, record),
                )
                widget.bind(
                    "<Button-2>",
                    lambda event, record=result.record: self.show_art_context_menu(event, record),
                )
            thumbnail_jobs.append((image_label, result.record))
        for column in range(columns):
            self.search_results_frame.columnconfigure(column, weight=1)
        threading.Thread(
            target=self._thumbnail_worker,
            args=(generation, thumbnail_jobs), daemon=True,
        ).start()

    def _thumbnail_worker(self, generation, jobs):
        for label, record in jobs:
            if generation != self.search_generation:
                return
            try:
                cached = self.thumbnail_cache.get_or_create(
                    record.get("path", ""), size=(200, 150)
                )
                self.root.after(0, self._apply_thumbnail, generation, label, str(cached))
            except Exception:
                self.root.after(0, self._thumbnail_failed, generation, label)

    def _apply_thumbnail(self, generation, label, cached_path):
        if generation != self.search_generation or not label.winfo_exists():
            return
        from PIL import Image, ImageTk
        with Image.open(cached_path) as image:
            photo = ImageTk.PhotoImage(image.copy())
        label.configure(image=photo, text="", width=200, height=150)
        label.image = photo
        self.search_image_refs.append(photo)

    def _thumbnail_failed(self, generation, label):
        if generation == self.search_generation and label.winfo_exists():
            label.configure(text="Miniatura indisponível")

    def open_art_details(self, record):
        dialog = tk.Toplevel(self.root)
        dialog.title(str(record.get("filename", "Detalhes da arte")))
        dialog.geometry("900x820")
        dialog.minsize(680, 600)
        body = ttk.Frame(dialog, padding=18)
        body.pack(fill="both", expand=True)
        preview = tk.Label(
            body, text="Carregando visualização...", bg="#eeeeee", height=22
        )
        preview.pack(fill="both", expand=True)

        path = str(record.get("path", ""))
        ttk.Label(
            body, text=path, wraplength=840, font=("Arial", 9)
        ).pack(anchor="w", pady=(12, 8))
        details = ttk.Frame(body)
        details.pack(fill="x")
        description = str(record.get("description") or "Sem descrição")
        ttk.Label(details, text="Descrição:", font=("Arial", 10, "bold")).grid(
            row=0, column=0, sticky="nw", pady=3
        )
        ttk.Label(details, text=description, wraplength=700).grid(
            row=0, column=1, sticky="w", pady=3
        )
        keywords_var = tk.StringVar(value=self._metadata_text(record.get("keywords")))
        rows = (
            ("Palavras-chave:", keywords_var),
            ("Cores:", self._metadata_text(record.get("colors"))),
            ("Elementos:", self._metadata_text(record.get("elements"))),
            ("Temas:", self._metadata_text(record.get("themes"))),
            ("Categoria:", str(record.get("category") or "—")),
        )
        for row, (label, value) in enumerate(rows, start=1):
            ttk.Label(details, text=label, font=("Arial", 10, "bold")).grid(
                row=row, column=0, sticky="nw", pady=3
            )
            value_options = (
                {"textvariable": value} if isinstance(value, tk.StringVar)
                else {"text": value}
            )
            ttk.Label(details, wraplength=700, **value_options).grid(
                row=row, column=1, sticky="w", pady=3
            )
        details.columnconfigure(1, weight=1)

        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(14, 0))
        ttk.Button(
            actions, text="Abrir imagem", command=lambda: self._open_file(Path(path))
        ).pack(side="left")
        ttk.Button(
            actions, text="Abrir pasta original",
            command=lambda: self.open_original_folder(record),
        ).pack(side="left", padx=6)
        ttk.Button(
            actions, text="Copiar caminho", command=lambda: self._copy_text(path)
        ).pack(side="left", padx=6)
        ttk.Button(
            actions, text="Copiar nome do arquivo",
            command=lambda: self._copy_text(str(record.get("filename", ""))),
        ).pack(side="left", padx=6)
        ttk.Button(
            actions, text="Editar palavras-chave",
            command=lambda: self._edit_art_keywords(dialog, record, keywords_var),
        ).pack(side="left", padx=6)
        ttk.Button(
            actions, text="Encontrar semelhantes",
            command=lambda: self.find_similar_images(path, record),
        ).pack(side="left", padx=6)
        threading.Thread(
            target=self._detail_preview_worker,
            args=(dialog, preview, path), daemon=True,
        ).start()

    @staticmethod
    def _metadata_text(value):
        if isinstance(value, list):
            return ", ".join(str(item) for item in value) or "—"
        return str(value or "—")

    def _detail_preview_worker(self, dialog, label, path):
        try:
            cached = self.thumbnail_cache.get_or_create(path, size=(760, 500))
            self.root.after(0, self._apply_detail_preview, dialog, label, str(cached))
        except Exception:
            self.root.after(
                0, lambda: label.winfo_exists() and label.configure(
                    text="Visualização indisponível"
                )
            )

    def _apply_detail_preview(self, dialog, label, cached_path):
        if not dialog.winfo_exists() or not label.winfo_exists():
            return
        from PIL import Image, ImageTk
        with Image.open(cached_path) as image:
            photo = ImageTk.PhotoImage(image.copy())
        label.configure(image=photo, text="", height=500)
        label.image = photo
        dialog.preview_image = photo

    def _edit_art_keywords(self, parent, record, variable):
        current = self._metadata_text(record.get("keywords"))
        if current == "—":
            current = ""
        value = simpledialog.askstring(
            "Editar palavras-chave",
            "Separe as palavras-chave por vírgulas:",
            initialvalue=current,
            parent=parent,
        )
        if value is None:
            return
        keywords, seen = [], set()
        for item in value.split(","):
            cleaned = item.strip().casefold()
            if cleaned and cleaned not in seen:
                keywords.append(cleaned)
                seen.add(cleaned)
        try:
            append_analysis_result(record, metadata={"keywords": keywords})
            record["keywords"] = keywords
            variable.set(self._metadata_text(keywords))
            self.search_engine = None
            self._mark_semantic_stale()
            messagebox.showinfo(
                "Palavras-chave", "Alteração salva no catálogo local.", parent=parent
            )
        except Exception as exc:
            messagebox.showerror("Erro ao salvar", str(exc), parent=parent)

    def _copy_text(self, value):
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.root.update_idletasks()

    def _open_file(self, path: Path):
        if not path.is_file():
            messagebox.showerror("Arquivo indisponível", f"O arquivo não foi encontrado:\n{path}")
            return
        open_with_default_application(path)

    def _open_directory(self, path: Path):
        if not path.is_dir():
            messagebox.showerror("Pasta indisponível", f"A pasta não foi encontrada:\n{path}")
            return
        open_with_default_application(path)

    def open_original_folder(self, record):
        try:
            open_original_directory(record, self.config)
        except OriginalFolderError as exc:
            messagebox.showerror("Pasta original indisponível", str(exc))

    def _path_row(self, parent, row, label, variable, button_text, command):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=7)
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, sticky="ew", padx=10, pady=7
        )
        ttk.Button(parent, text=button_text, command=command).grid(
            row=row, column=2, pady=7
        )
        parent.columnconfigure(1, weight=1)

    def _source_paths_row(self, parent, row):
        ttk.Label(parent, text="Pastas de entrada das estampas").grid(
            row=row, column=0, sticky="nw", pady=7
        )
        list_frame = ttk.Frame(parent)
        list_frame.grid(row=row, column=1, sticky="ew", padx=10, pady=7)
        list_frame.columnconfigure(0, weight=1)

        self.source_list = tk.Listbox(
            list_frame, height=3, selectmode="extended", exportselection=False
        )
        self.source_list.grid(row=0, column=0, sticky="ew")
        scrollbar = ttk.Scrollbar(
            list_frame, orient="vertical", command=self.source_list.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.source_list.configure(yscrollcommand=scrollbar.set)
        self._refresh_source_list()

        button_frame = ttk.Frame(parent)
        button_frame.grid(row=row, column=2, sticky="n", pady=7)
        self.add_source_button = ttk.Button(
            button_frame, text="Adicionar pasta", command=self.select_source
        )
        self.add_source_button.pack(fill="x")
        self.remove_source_button = ttk.Button(
            button_frame,
            text="Remover selecionadas",
            command=self.remove_selected_sources,
        )
        self.remove_source_button.pack(fill="x", pady=(6, 0))

    def _csv_text_row(self, parent, row):
        ttk.Label(parent, text="Texto CSV dos pedidos").grid(
            row=row, column=0, sticky="nw", pady=7
        )
        csv_frame = ttk.Frame(parent)
        csv_frame.grid(row=row, column=1, columnspan=2, sticky="ew", padx=10, pady=7)
        csv_frame.columnconfigure(0, weight=1)
        self.csv_text = tk.Text(csv_frame, height=4, wrap="none")
        self.csv_text.grid(row=0, column=0, sticky="ew")
        csv_scrollbar = ttk.Scrollbar(
            csv_frame, orient="vertical", command=self.csv_text.yview
        )
        csv_scrollbar.grid(row=0, column=1, sticky="ns")
        self.csv_text.configure(yscrollcommand=csv_scrollbar.set)
        ttk.Label(
            csv_frame,
            text=(
                "Ordem: ID do Pedido; Data do Pedido; ID do Cliente; "
                "BASE; ID da Estampa; Variante"
            ),
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

    def _refresh_source_list(self):
        if not hasattr(self, "source_list"):
            return
        self.source_list.delete(0, "end")
        for source in self.source_dirs:
            self.source_list.insert("end", source)

    def _refresh_collector_source_list(self):
        if not hasattr(self, "collector_source_list"):
            return
        self.collector_source_list.delete(0, "end")
        for source in self.collector_source_dirs:
            self.collector_source_list.insert("end", source)

    def select_excel(self):
        path = filedialog.askopenfilename(
            title="Selecione a planilha",
            filetypes=[("Excel", "*.xlsx *.xlsm")]
        )
        if path:
            self.excel_var.set(path)
            self.input_mode_var.set("excel")
            self._save_paths()

    def select_source(self):
        path = filedialog.askdirectory(title="Adicione uma pasta de entrada das estampas")
        if path:
            sources = [str(source) for source in normalize_source_dirs([*self.source_dirs, path])]
            if sources == self.source_dirs:
                return
            self.source_dirs = sources
            self._validate_source_paths()
            self._refresh_source_list()
            self._save_paths()
            self.index = {}
            self.search_engine = None
            self.semantic_engine = None
            self.visual_engine = None
            self.index_status_var.set(
                self.root_path_error or "Pastas alteradas. Clique em Atualizar índice."
            )

    def remove_selected_sources(self):
        selected = list(self.source_list.curselection())
        if not selected:
            return
        for index in reversed(selected):
            del self.source_dirs[index]
        self._validate_source_paths()
        self._refresh_source_list()
        self._save_paths()
        self.index = {}
        self.search_engine = None
        self.semantic_engine = None
        self.visual_engine = None
        self.index_status_var.set(
            self.root_path_error or "Pastas alteradas. Clique em Atualizar índice."
        )

    def select_collector_source(self):
        path = filedialog.askdirectory(
            title="Adicione uma pasta para procurar imagens"
        )
        if path and path not in self.collector_source_dirs:
            self.collector_source_dirs.append(path)
            self._refresh_collector_source_list()
            self._save_paths()

    def remove_collector_sources(self):
        selected = list(self.collector_source_list.curselection())
        if not selected:
            return
        for index in reversed(selected):
            del self.collector_source_dirs[index]
        self._refresh_collector_source_list()
        self._save_paths()

    def select_output(self):
        path = filedialog.askdirectory(title="Selecione a pasta de saída")
        if path:
            self.output_var.set(path)
            self._save_paths()

    def select_app_data_dir(self):
        path = filedialog.askdirectory(
            title="Selecione uma pasta vazia para os dados do software",
            parent=self.root,
        )
        if not path:
            return
        try:
            destination = select_app_data_dir(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "Não foi possível alterar a pasta", str(exc), parent=self.root
            )
            return
        self.app_data_dir_var.set(str(destination))
        messagebox.showinfo(
            "Pasta de dados alterada",
            "Os dados atuais foram copiados com segurança. Feche e abra novamente "
            "o aplicativo para concluir a troca.",
            parent=self.root,
        )

    def select_collector_output(self):
        path = filedialog.askdirectory(
            title="Selecione a pasta de saída das imagens"
        )
        if path:
            self.collector_output_var.set(path)
            self._save_paths()

    def _save_paths(self):
        updated_config = {
            "excel_path": self.excel_var.get(),
            "input_mode": self.input_mode_var.get(),
            "source_dirs": self.source_dirs,
            "original_images_path": (
                self.source_dirs[0] if self.source_dirs
                else ""
            ),
            "output_dir": self.output_var.get(),
            "collector_source_dirs": self.collector_source_dirs,
            "collector_output_dir": self.collector_output_var.get(),
            "collector_extensions": [
                extension
                for extension, variable in self.collector_extension_vars.items()
                if variable.get()
            ],
            "semantic_search_enabled": self.semantic_enabled_var.get(),
        }
        save_config(updated_config)
        # Todas as ações abertas nesta sessão passam a enxergar imediatamente a
        # mesma lista de pastas escolhida, sem exigir reinicialização.
        self.config.update(updated_config)

    def _try_load_saved_index(self):
        sources = [Path(source) for source in self.source_dirs]
        if not sources:
            return
        # O dicionário completo (centenas de milhares de caminhos) só é carregado
        # quando o organizador de pedidos realmente precisar dele.
        if index_catalog_available(sources):
            self.index_status_var.set(
                f"Catálogo disponível para {len(sources)} pasta(s). "
                "O índice de pedidos será carregado sob demanda."
            )

    def start_indexing(self):
        sources = [Path(source) for source in self.source_dirs]
        if not sources:
            messagebox.showwarning(
                "Atenção",
                "Adicione pelo menos uma pasta de entrada das estampas.",
            )
            return

        self._set_busy(True)
        self.progress.configure(mode="indeterminate")
        self.progress.start(10)
        self.status_var.set("Indexando as imagens. Aguarde a conclusão.")
        self._log("Iniciando indexação...")

        thread = threading.Thread(
            target=self._index_worker,
            args=(sources,),
            daemon=True
        )
        thread.start()

    def start_local_indexing(self):
        """Executa somente o scan local, incremental sempre que possível."""
        sources = [Path(source) for source in self.source_dirs]
        if sources and index_catalog_available(sources):
            self.start_incremental_indexing()
        else:
            self.start_indexing()

    def _choose_operation_limit(self, title, callback):
        """Escolhe um lote pequeno para teste ou todos os itens pendentes."""
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="Quantos itens deseja processar nesta execução?",
            wraplength=420,
        ).pack(anchor="w", pady=(0, 12))
        choice = tk.StringVar(value="10")
        for value, label in (
            ("1", "1 item (teste rápido)"), ("10", "10 itens"),
            ("50", "50 itens"), ("100", "100 itens"),
            ("500", "500 itens"), ("all", "Todos os itens pendentes"),
        ):
            ttk.Radiobutton(
                body, text=label, variable=choice, value=value,
            ).pack(anchor="w", pady=2)
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(16, 0))

        def confirm():
            limit = None if choice.get() == "all" else int(choice.get())
            dialog.destroy()
            callback(limit)

        ttk.Button(actions, text="INICIAR", command=confirm).pack(side="left")
        ttk.Button(actions, text="Cancelar", command=dialog.destroy).pack(
            side="left", padx=8,
        )

    def _start_controllable_operation(self):
        self.operation_pause_event = threading.Event()
        self.operation_pause_event.set()
        self._set_busy(True)
        self.operation_pause_button.configure(state="normal")
        self.operation_continue_button.configure(state="disabled")

    def pause_current_operation(self):
        if self.operation_pause_event is None:
            return
        self.operation_pause_event.clear()
        self.operation_pause_button.configure(state="disabled")
        self.operation_continue_button.configure(state="normal")
        self.status_var.set("Pausa solicitada; concluindo o item ou lote atual com segurança...")

    def continue_current_operation(self):
        if self.operation_pause_event is None:
            return
        self.operation_pause_event.set()
        self.operation_pause_button.configure(state="normal")
        self.operation_continue_button.configure(state="disabled")
        self.status_var.set("Processamento retomado...")

    def _finish_controllable_operation(self):
        if self.operation_pause_event is not None:
            self.operation_pause_event.set()
        self.operation_pause_event = None
        self.operation_pause_button.configure(state="disabled")
        self.operation_continue_button.configure(state="disabled")
        self._set_busy(False)

    def start_preview_generation(self):
        sources = [Path(source) for source in self.source_dirs]
        if not sources:
            messagebox.showwarning("Previews", "Adicione e indexe as pastas primeiro.")
            return
        self._choose_operation_limit(
            "Gerar previews pendentes",
            lambda limit: self._begin_preview_generation(sources, limit),
        )

    def _begin_preview_generation(self, sources, limit):
        self._start_controllable_operation()
        self.progress.configure(mode="determinate", value=0)
        scope = "todos" if limit is None else f"até {limit:,}"
        self.status_var.set(f"Gerando {scope} previews locais pendentes...")
        threading.Thread(
            target=self._preview_generation_worker, args=(sources, limit), daemon=True,
        ).start()

    def _preview_generation_worker(self, sources, limit):
        try:
            result = generate_pending_previews(
                sources,
                progress_callback=lambda current, total, message: self.root.after(
                    0, self._preview_generation_progress, current, total, message
                ),
                limit=limit, pause_event=self.operation_pause_event,
            )
            self.root.after(0, self._preview_generation_complete, result)
        except Exception as exc:
            self.root.after(0, self._show_error, str(exc))

    def _preview_generation_progress(self, current, total, message):
        self.progress.configure(value=(current / total * 100) if total else 0)
        self.status_var.set(message)

    def _preview_generation_complete(self, result):
        self.progress.configure(value=100 if result.pending else 0)
        self.status_var.set("Geração local de previews concluída.")
        self._finish_controllable_operation()
        self._refresh_catalog_statistics()
        messagebox.showinfo(
            "Previews concluídos",
            f"Pendentes encontradas: {result.pending:,}\n"
            f"Concluídas: {result.completed:,}\n"
            f"Falhas: {result.failed:,}",
        )

    def start_cloud_upload(self):
        sources = [Path(source) for source in self.source_dirs]
        if not sources:
            messagebox.showwarning("Cloud", "Adicione e indexe as pastas primeiro.")
            return
        self._choose_operation_limit(
            "Enviar previews para Cloud",
            lambda limit: self._begin_cloud_upload(sources, limit),
        )

    def _begin_cloud_upload(self, sources, limit):
        self._start_controllable_operation()
        self.progress.configure(mode="determinate", value=0)
        scope = "todos" if limit is None else f"até {limit:,}"
        self.status_var.set(f"Enviando {scope} previews concluídos para Cloud...")
        threading.Thread(
            target=self._cloud_upload_worker, args=(sources, limit), daemon=True,
        ).start()

    def _cloud_upload_worker(self, sources, limit):
        try:
            result = upload_pending_previews(
                sources,
                progress_callback=lambda current, total, message: self.root.after(
                    0, self._cloud_upload_progress, current, total, message
                ),
                limit=limit, pause_event=self.operation_pause_event,
            )
            self.root.after(0, self._cloud_upload_complete, result)
        except Exception as exc:
            self.root.after(0, self._show_error, str(exc))

    def _cloud_upload_progress(self, current, total, message):
        self.progress.configure(value=(current / total * 100) if total else 0)
        self.status_var.set(message)

    def _cloud_upload_complete(self, result):
        self.progress.configure(value=100 if result.pending else 0)
        self.status_var.set("Envio de previews para Cloud concluído.")
        self._finish_controllable_operation()
        self._refresh_catalog_statistics()
        messagebox.showinfo(
            "Cloud",
            f"Previews pendentes: {result.pending:,}\n"
            f"Enviados: {result.completed:,}\n"
            f"Falhas: {result.failed:,}",
        )

    def start_supabase_sync(self):
        sources = [Path(source) for source in self.source_dirs]
        if not sources:
            messagebox.showwarning("Supabase", "Adicione e indexe as pastas primeiro.")
            return
        self._choose_operation_limit(
            "Sincronizar Supabase",
            lambda limit: self._begin_supabase_sync(sources, limit),
        )

    def _begin_supabase_sync(self, sources, limit):
        self._start_controllable_operation()
        self.progress.configure(mode="determinate", value=0)
        scope = "todos" if limit is None else f"até {limit:,}"
        self.status_var.set(f"Sincronizando {scope} registros com Supabase...")
        threading.Thread(
            target=self._supabase_sync_worker, args=(sources, limit), daemon=True,
        ).start()

    def _supabase_sync_worker(self, sources, limit):
        try:
            result = sync_pending_records(
                sources,
                progress_callback=lambda current, total, message: self.root.after(
                    0, self._supabase_sync_progress, current, total, message
                ),
                limit=limit, pause_event=self.operation_pause_event,
            )
            self.root.after(0, self._supabase_sync_complete, result)
        except Exception as exc:
            self.root.after(0, self._show_error, str(exc))

    def _supabase_sync_progress(self, current, total, message):
        self.progress.configure(value=(current / total * 100) if total else 0)
        self.status_var.set(message)

    def _supabase_sync_complete(self, result):
        self.progress.configure(value=100 if result.pending else 0)
        self.status_var.set("Sincronização com Supabase concluída.")
        self._finish_controllable_operation()
        self._refresh_catalog_statistics()
        messagebox.showinfo(
            "Supabase",
            f"Registros pendentes: {result.pending:,}\n"
            f"Sincronizados: {result.completed:,}\n"
            f"Falhas: {result.failed:,}",
        )

    def start_pending_sync(self):
        sources = [Path(source) for source in self.source_dirs]
        if not sources:
            messagebox.showwarning(
                "Sincronizar pendentes", "Adicione e indexe as pastas primeiro."
            )
            return
        self._choose_operation_limit(
            "Sincronizar pendentes",
            lambda limit: self._begin_pending_sync(sources, limit),
        )

    def _begin_pending_sync(self, sources, limit):
        self._start_controllable_operation()
        self.progress.configure(mode="determinate", value=0)
        scope = "todos" if limit is None else f"até {limit:,}"
        self.status_var.set(f"Buscando {scope} itens pendentes...")
        threading.Thread(
            target=self._pending_sync_worker, args=(sources, limit), daemon=True,
        ).start()

    def _pending_sync_worker(self, sources, limit):
        try:
            result = synchronize_pending(
                sources,
                progress_callback=lambda current, total, message: self.root.after(
                    0, self._pending_sync_progress, current, total, message
                ),
                limit=limit, pause_event=self.operation_pause_event,
            )
            self.root.after(0, self._pending_sync_complete, result)
        except Exception as exc:
            self.root.after(0, self._show_error, str(exc))

    def _pending_sync_progress(self, current, total, message):
        self.progress.configure(value=(current / total * 100) if total else 0)
        self.status_var.set(message)

    def _pending_sync_complete(self, result):
        self.progress.configure(value=100 if not result.pending else 0)
        self.status_var.set("Sincronização de pendentes concluída.")
        self._finish_controllable_operation()
        self._refresh_catalog_statistics()
        messagebox.showinfo(
            "Sincronizar pendentes",
            f"Concluídos: {result.completed:,}\n"
            f"Pendentes: {result.pending:,}\n"
            f"Erros: {result.errors:,}\n\n"
            f"Previews: {result.previews_completed:,}\n"
            f"Cloud: {result.uploads_completed:,}\n"
            f"Supabase: {result.supabase_completed:,}",
        )

    def _index_worker(self, sources):
        try:
            index, result = build_index(
                sources,
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
        self.search_engine = None
        self._mark_semantic_stale()
        self._mark_visual_stale()
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.index_status_var.set(
            f"Índice pronto em {result.source_dirs} pasta(s): "
            f"{result.total_files:,} arquivos; "
            f"{result.indexed_names:,} nomes; {result.duplicates:,} duplicados."
        )
        self.status_var.set("Indexação concluída.")
        self._log(
            f"Índice concluído em {result.elapsed_seconds:.1f}s. "
            f"Pastas: {result.source_dirs}. Arquivos: {result.total_files:,}. "
            f"Duplicados: {result.duplicates:,}."
        )
        if result.duplicates_log:
            self._log(f"Log de duplicidades: {result.duplicates_log}")
        alert = (
            f"Imagens encontradas: {result.total_files:,}\n"
            f"Duplicidades: {result.duplicates:,}"
        )
        if result.duplicates_log:
            alert += f"\n\nConsulte o log para ajustar manualmente:\n{result.duplicates_log}"
        messagebox.showinfo("Índice concluído", alert)
        self._set_busy(False)
        self._refresh_catalog_statistics()

    def start_incremental_indexing(self):
        sources = [Path(source) for source in self.source_dirs]
        if not sources:
            messagebox.showwarning(
                "Atenção",
                "Adicione pelo menos uma pasta de entrada das estampas.",
            )
            return

        self._set_busy(True)
        self.progress.configure(mode="indeterminate")
        self.progress.start(10)
        self.status_var.set("Verificando o catálogo local. Aguarde a conclusão.")
        self._log("Iniciando scan local incremental...")
        thread = threading.Thread(
            target=self._incremental_index_worker,
            args=(sources,),
            daemon=True,
        )
        thread.start()

    def _incremental_index_worker(self, sources):
        try:
            index, result = update_index_incremental(
                sources,
                progress_callback=lambda count, msg: self.root.after(
                    0, self._index_progress, count, msg
                ),
            )
            self.index = index
            self.root.after(0, self._incremental_index_complete, result)
        except Exception as exc:
            self.root.after(0, self._show_error, str(exc))

    def _incremental_index_complete(self, result):
        self.search_engine = None
        self._mark_semantic_stale()
        self._mark_visual_stale()
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.index_status_var.set(
            f"Índice atualizado: {result.added_files:,} imagens novas; "
            f"{result.indexed_names:,} nomes; {result.duplicates:,} duplicados."
        )
        self.status_var.set("Atualização rápida concluída.")
        self._log(
            f"Atualização rápida concluída em {result.elapsed_seconds:.1f}s. "
            f"Total encontrado: {result.total_found:,}. "
            f"Inalteradas: {result.unchanged_files:,}. "
            f"Novas: {result.added_files:,}. Alteradas: {result.changed_files:,}. "
            f"Movidas/renomeadas: {result.moved_files:,}. "
            f"Revisão necessária: {result.review_files:,}. "
            f"Ausentes: {result.absent_files:,}. Erros: {result.errors:,}. "
            f"Duplicados: {result.duplicates:,}."
        )
        if result.duplicates_log:
            self._log(f"Log de duplicidades: {result.duplicates_log}")
        alert = (
            f"Total encontrado: {result.total_found:,}\n"
            f"Inalteradas: {result.unchanged_files:,}\n"
            f"Imagens novas adicionadas: {result.added_files:,}\n"
            f"Alteradas: {result.changed_files:,}\n"
            f"Movidas ou renomeadas: {result.moved_files:,}\n"
            f"Revisão necessária: {result.review_files:,}\n"
            f"Ausentes: {result.absent_files:,}\n"
            f"Erros: {result.errors:,}\n"
            f"Duplicidades no índice: {result.duplicates:,}\n\n"
            "Os dados já associados às imagens foram preservados."
        )
        if result.duplicates_log:
            alert += f"\n\nLog de duplicidades:\n{result.duplicates_log}"
        messagebox.showinfo("Atualização local concluída", alert)
        self._set_busy(False)
        self._refresh_catalog_statistics()

    def _ensure_openai_api_key(self):
        """Mantém suporte às pesquisas opcionais que ainda usam a API OpenAI."""
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            api_key = simpledialog.askstring(
                "Chave da API OpenAI",
                "Cole sua chave da API OpenAI. Ela será mantida somente enquanto "
                "o aplicativo estiver aberto:",
                parent=self.root,
                show="*",
            )
            if not api_key or not api_key.strip():
                return False
            os.environ["OPENAI_API_KEY"] = api_key.strip()
        return True

    def start_processing(self):
        excel = self.excel_var.get().strip()
        input_mode = self.input_mode_var.get()
        csv_text = self.csv_text.get("1.0", "end").strip()
        sources = [Path(source) for source in self.source_dirs]
        output = self.output_var.get().strip()

        has_orders = excel if input_mode == "excel" else csv_text
        if not has_orders or not sources or not output:
            messagebox.showwarning(
                "Atenção",
                "Informe os pedidos, adicione uma pasta de entrada e "
                "selecione a pasta de saída."
            )
            return

        if not self.index:
            self.index = load_index(sources)
        if not self.index:
            messagebox.showwarning(
                "Índice necessário",
                "Clique primeiro em Atualizar índice."
            )
            return

        self._save_paths()
        self._set_busy(True)
        self.progress.configure(mode="determinate", value=0)
        self.status_var.set("Processando os pedidos...")
        self._log("Iniciando processamento dos pedidos.")

        thread = threading.Thread(
            target=self._process_worker,
            args=(input_mode, excel, csv_text, Path(output)),
            daemon=True
        )
        thread.start()

    def _process_worker(self, input_mode, excel, csv_text, output):
        try:
            progress_callback = lambda current, total, msg: self.root.after(
                0, self._processing_progress, current, total, msg
            )
            if input_mode == "csv":
                results, summary = process_csv_text(
                    csv_text,
                    output,
                    self.index,
                    progress_callback=progress_callback,
                )
            else:
                results, summary = process_excel(
                    Path(excel),
                    output,
                    self.index,
                    progress_callback=progress_callback,
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

    def start_collecting_images(self):
        sources = [Path(source) for source in self.collector_source_dirs]
        output = self.collector_output_var.get().strip()
        extensions = {
            extension
            for extension, variable in self.collector_extension_vars.items()
            if variable.get()
        }
        if not sources or not output or not extensions:
            messagebox.showwarning(
                "Atenção",
                "Adicione uma entrada, selecione ao menos um formato "
                "e informe a pasta de saída.",
            )
            return

        self._save_paths()
        self.collector_cancel_event = threading.Event()
        self._set_busy(True)
        self.collector_stop_button.configure(state="normal")
        self.collector_progress.configure(mode="indeterminate", value=0)
        self.collector_progress.start(10)
        self.collector_status_var.set("Procurando imagens...")
        self._collector_log("Iniciando busca e cópia das imagens.")
        thread = threading.Thread(
            target=self._collector_worker,
            args=(sources, Path(output), extensions),
            daemon=True,
        )
        thread.start()

    def _collector_worker(self, sources, output, extensions):
        try:
            result = collect_images(
                sources,
                output,
                extensions,
                progress_callback=lambda current, total, message: self.root.after(
                    0,
                    self._collector_progress,
                    current,
                    total,
                    message,
                ),
                cancel_callback=self.collector_cancel_event.is_set,
                confirm_callback=self._confirm_collector_copy,
            )
            self.root.after(0, self._collector_complete, result)
        except Exception as exc:
            self.root.after(0, self._collector_error, str(exc))

    def _collector_progress(self, current, total, message):
        if not total:
            self.collector_status_var.set(message)
            self._collector_log(message)
            return

        self.collector_progress.stop()
        self.collector_progress.configure(
            mode="determinate",
            value=current / total * 100,
        )
        self.collector_status_var.set(message)

    def _collector_complete(self, result):
        self.collector_progress.stop()
        self.collector_stop_button.configure(state="disabled")
        self.collector_cancel_event = None
        progress_value = (
            result.processed / result.found * 100 if result.found else 0
        )
        self.collector_progress.configure(
            mode="determinate",
            value=progress_value if result.cancelled else 100,
        )
        if result.declined:
            text = (
                f"Cópia não iniciada: {result.found:,} imagens encontradas "
                f"({format_size(result.found_bytes)}); "
                f"{result.planned_count:,} estavam pendentes "
                f"({format_size(result.planned_bytes)})."
            )
            self.collector_status_var.set(text)
            self._collector_log(text)
            self._set_busy(False)
            return

        if result.cancelled:
            text = (
                f"Interrompido: {result.processed:,} de {result.found:,} "
                f"imagens processadas; {result.copied:,} copiadas; "
                f"{result.skipped:,} já existentes; "
                f"{format_size(result.copied_bytes)} copiados."
            )
            self.collector_status_var.set(text)
            self._collector_log(text)
            self._set_busy(False)
            messagebox.showinfo("Cópia interrompida", text)
            return

        text = (
            f"Concluído: {result.found:,} imagens encontradas; "
            f"{result.copied:,} copiadas; {result.skipped:,} ignoradas."
            f" Tamanho encontrado: {format_size(result.found_bytes)}; "
            f"copiado: {format_size(result.copied_bytes)}."
        )
        self.collector_status_var.set(text)
        self._collector_log(text)
        if result.conflicts:
            self._collector_log(
                "Exemplos de arquivos ignorados porque o destino já existia:"
            )
            for conflict in result.conflicts:
                self._collector_log(f"- {conflict}")
        if result.conflicts_omitted:
            self._collector_log(
                f"... e mais {result.conflicts_omitted:,} arquivo(s) já existente(s)."
            )
        self._set_busy(False)
        messagebox.showinfo("Cópia concluída", text)

    def _collector_error(self, message):
        self.collector_progress.stop()
        self.collector_stop_button.configure(state="disabled")
        self.collector_cancel_event = None
        self.collector_progress.configure(mode="determinate", value=0)
        self.collector_status_var.set("Ocorreu um erro.")
        self._collector_log(f"ERRO: {message}")
        self._set_busy(False)
        messagebox.showerror("Erro", message)

    def _confirm_collector_copy(
        self,
        found,
        found_bytes,
        planned_count,
        planned_bytes,
    ):
        answered = threading.Event()
        response = {"approved": False}

        def ask_permission():
            response["approved"] = messagebox.askyesno(
                "Confirmar cópia",
                (
                    f"Foram encontradas {found:,} imagens "
                    f"({format_size(found_bytes)}).\n\n"
                    f"Serão copiadas {planned_count:,} imagens novas "
                    f"({format_size(planned_bytes)}).\n\n"
                    "Deseja iniciar a cópia?"
                ),
            )
            answered.set()

        self.root.after(0, ask_permission)
        answered.wait()
        return response["approved"]

    def stop_collecting_images(self):
        if self.collector_cancel_event is None:
            return
        self.collector_cancel_event.set()
        self.collector_stop_button.configure(state="disabled")
        self.collector_status_var.set("Parando com segurança...")
        self._collector_log("Cancelamento solicitado. Finalizando o arquivo atual...")

    def _show_error(self, message):
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.status_var.set("Ocorreu um erro.")
        self._log(f"ERRO: {message}")
        if self.operation_pause_event is not None:
            self._finish_controllable_operation()
        else:
            self._set_busy(False)
        messagebox.showerror("Erro", message)

    def _set_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.index_button.configure(state=state)
        self.incremental_index_button.configure(state=state)
        self.preview_button.configure(state=state)
        self.cloud_button.configure(state=state)
        self.supabase_button.configure(state=state)
        self.pending_sync_button.configure(state=state)
        self.semantic_update_button.configure(state=state)
        self.semantic_rebuild_button.configure(state=state)
        self.visual_find_button.configure(state=state)
        self.visual_update_button.configure(state=state)
        self.visual_rebuild_button.configure(state=state)
        self.process_button.configure(state=state)
        self.add_source_button.configure(state=state)
        self.remove_source_button.configure(state=state)
        self.excel_mode_button.configure(state=state)
        self.csv_mode_button.configure(state=state)
        self.collector_process_button.configure(state=state)
        self.collector_add_button.configure(state=state)
        self.collector_remove_button.configure(state=state)
        for button in self.collector_format_buttons:
            button.configure(state=state)

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _collector_log(self, text):
        self.collector_log.configure(state="normal")
        self.collector_log.insert("end", text + "\n")
        self.collector_log.see("end")
        self.collector_log.configure(state="disabled")

    def open_output_folder(self):
        path = self.output_var.get().strip()
        if path:
            self._open_path(Path(path))

    def open_collector_output(self):
        path = self.collector_output_var.get().strip()
        if path:
            self._open_path(Path(path))

    def open_app_folder(self):
        from .config import APP_DIR, ensure_app_dir
        ensure_app_dir()
        self._open_path(APP_DIR)

    def _open_path(self, path: Path):
        resolved = path.expanduser().resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        open_with_default_application(resolved)

    def run(self):
        self.root.mainloop()
