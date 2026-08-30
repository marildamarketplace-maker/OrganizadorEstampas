from __future__ import annotations

from pathlib import Path
from contextlib import closing
import json
import os
import shutil
import sqlite3

APP_NAME = "Organizador de Estampas - Meury Shop"
DEFAULT_APP_DIR = Path.home() / ".meury_organizador_estampas"
APP_DIR_LOCATION_FILE = DEFAULT_APP_DIR / "data_location.json"


def configured_app_dir() -> Path:
    """Resolve a pasta operacional pelo ambiente ou pelo seletor persistente."""
    environment_value = os.environ.get("MEURY_APP_DATA_PATH", "").strip()
    if environment_value:
        return Path(environment_value).expanduser().resolve(strict=False)
    try:
        payload = json.loads(APP_DIR_LOCATION_FILE.read_text(encoding="utf-8"))
        selected = str(payload.get("path", "")).strip()
        if selected:
            return Path(selected).expanduser().resolve(strict=False)
    except (OSError, ValueError, TypeError):
        pass
    return DEFAULT_APP_DIR


APP_DIR = configured_app_dir()
CONFIG_FILE = APP_DIR / "config.json"
INDEX_FILE = APP_DIR / "indice_estampas.jsonl"
OPERATIONAL_DB_FILE = APP_DIR / "estado_indexador.sqlite3"
PREVIEW_DIR = APP_DIR / "previews"
LEGACY_INDEX_FILE = APP_DIR / "indice_estampas.json"
DUPLICATES_LOG_FILE = APP_DIR / "duplicidades_indice.txt"
ANALYSIS_RESULTS_FILE = APP_DIR / "resultados_analise_ia.jsonl"
ANALYSIS_LOG_FILE = APP_DIR / "analise_ia.log"
SEMANTIC_INDEX_FILE = APP_DIR / "indice_semantico.faiss"
SEMANTIC_METADATA_FILE = APP_DIR / "indice_semantico.jsonl"
SEMANTIC_LOG_FILE = APP_DIR / "indice_semantico.log"
VISUAL_INDEX_FILE = APP_DIR / "indice_visual.faiss"
VISUAL_METADATA_FILE = APP_DIR / "indice_visual.jsonl"
VISUAL_LOG_FILE = APP_DIR / "indice_visual.log"

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".pdf"}
IMAGE_LIKE_EXTENSIONS = SUPPORTED_EXTENSIONS | {
    ".bmp", ".gif", ".webp", ".svg", ".heic", ".heif", ".raw", ".psd", ".ai",
}

DEFAULT_CONFIG = {
    "excel_path": "",
    "input_mode": "excel",
    "source_dirs": [],
    "output_dir": "",
    "collector_source_dirs": [],
    "collector_output_dir": "",
    "collector_extensions": [".jpg", ".jpeg", ".png"],
    "semantic_search_enabled": False,
    "original_images_path": "",
}


def select_app_data_dir(target: str | Path) -> Path:
    """Migra os dados para uma pasta vazia e persiste a escolha para o reinício."""
    if os.environ.get("MEURY_APP_DATA_PATH", "").strip():
        raise ValueError(
            "MEURY_APP_DATA_PATH está definido no ambiente. Remova ou altere essa "
            "variável no .env para escolher a pasta pela interface."
        )
    destination = Path(target).expanduser().resolve(strict=False)
    current = APP_DIR.resolve(strict=False)
    if destination == current:
        return destination
    if current in destination.parents:
        raise ValueError("A nova pasta não pode ficar dentro da pasta de dados atual.")
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError(
            "Escolha uma pasta vazia para evitar misturar bancos e índices diferentes."
        )

    if current.is_dir():
        for item in current.iterdir():
            if item == APP_DIR_LOCATION_FILE:
                continue
            destination_item = destination / item.name
            if item.name in {OPERATIONAL_DB_FILE.name + "-wal", OPERATIONAL_DB_FILE.name + "-shm"}:
                continue
            if item == OPERATIONAL_DB_FILE:
                # O backup nativo produz uma cópia consistente mesmo quando o
                # SQLite está em WAL; copiar apenas o arquivo principal poderia
                # perder transações recentes, especialmente no Windows.
                with closing(sqlite3.connect(item)) as source_db, \
                     closing(sqlite3.connect(destination_item)) as target_db:
                    with target_db:
                        source_db.backup(target_db)
            elif item.is_dir():
                shutil.copytree(item, destination_item)
            else:
                shutil.copy2(item, destination_item)

    DEFAULT_APP_DIR.mkdir(parents=True, exist_ok=True)
    temporary = APP_DIR_LOCATION_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"path": str(destination)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(APP_DIR_LOCATION_FILE)
    return destination


def original_images_path(config: dict | None = None) -> Path:
    """Obtém a raiz das originais sem assumir unidade ou volume do sistema."""
    # Uma escolha feita na interface deve prevalecer sobre o valor inicial do
    # .env; caso contrário, reiniciar o app volta silenciosamente à pasta antiga.
    configured = str(config.get("original_images_path", "")).strip() if config else ""
    if not configured:
        configured = os.environ.get("ORIGINAL_IMAGES_PATH", "").strip()
    if not configured:
        raise ValueError(
            "ORIGINAL_IMAGES_PATH não está configurado. Informe a pasta raiz das imagens originais."
        )
    return Path(configured).expanduser()


def validate_original_images_path(config: dict | None = None) -> Path:
    root = original_images_path(config).resolve(strict=False)
    if not root.is_dir():
        raise ValueError(
            "O diretório raiz das estampas não está disponível. Verifique se o HD "
            f"está conectado ou se o volume está montado:\n{root}"
        )
    if not os.access(root, os.R_OK | os.X_OK):
        raise PermissionError(f"Sem permissão para acessar o diretório raiz:\n{root}")
    return root


def resolve_relative_image_path(
    relative_path: str | Path, *, config: dict | None = None, root: Path | None = None,
) -> Path:
    """Único ponto para combinar raiz configurada e caminho persistido."""
    base = (root or validate_original_images_path(config)).resolve(strict=False)
    relative = Path(str(relative_path).replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("O caminho da estampa deve ser relativo ao diretório raiz.")
    resolved = (base / relative).resolve(strict=False)
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError("O caminho da estampa está fora do diretório raiz.") from exc
    return resolved


def resolve_record_path(record: dict, source_dirs=None, config: dict | None = None) -> Path:
    """Resolve um registro; `source_dirs` mantém índices antigos com múltiplas raízes."""
    relative = str(record.get("relative_path", ""))
    if source_dirs:
        source_number = int(record.get("source", 0))
        if source_number >= len(source_dirs):
            raise ValueError("A origem registrada não existe na configuração atual.")
        return resolve_relative_image_path(relative, root=Path(source_dirs[source_number]))
    return resolve_relative_image_path(relative, config=config)

COLUMN_ALIASES = {
    "pedido": [
        "id do pedido", "pedido", "numero do pedido", "número do pedido",
        "id pedido", "order id", "order_id"
    ],
    "data": [
        "data", "data do pedido", "order date", "order_date"
    ],
    "cliente": [
        "id do cliente", "cliente", "cliente id", "customer id", "customer_id"
    ],
    "base": [
        "base", "nome da base", "base name", "base_name"
    ],
    "estampa": [
        "id da estampa", "estampa", "codigo da estampa", "código da estampa",
        "id estampa", "design", "design id"
    ],
    "variante": [
        "variante", "codigo da variante", "código da variante",
        "variant", "variant code"
    ],
}

def ensure_app_dir():
    APP_DIR.mkdir(parents=True, exist_ok=True)

def load_config():
    ensure_app_dir()
    if not CONFIG_FILE.exists():
        return DEFAULT_CONFIG.copy()
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        # Migra configurações antigas que aceitavam apenas uma pasta.
        if "source_dirs" not in data:
            old_source = str(data.get("source_dir", "")).strip()
            data["source_dirs"] = [old_source] if old_source else []
        merged = {**DEFAULT_CONFIG, **data}
        if not merged.get("original_images_path") and len(merged.get("source_dirs", [])) == 1:
            merged["original_images_path"] = merged["source_dirs"][0]
        return merged
    except Exception:
        return DEFAULT_CONFIG.copy()

def save_config(data):
    ensure_app_dir()
    CONFIG_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
