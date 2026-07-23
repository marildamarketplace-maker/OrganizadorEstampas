from pathlib import Path
import json

APP_NAME = "Organizador de Estampas - Meury Shop"
APP_DIR = Path.home() / ".meury_organizador_estampas"
CONFIG_FILE = APP_DIR / "config.json"
INDEX_FILE = APP_DIR / "indice_estampas.json"

SUPPORTED_EXTENSIONS = {
    ".jpg", ".png", ".pdf"
}

DEFAULT_CONFIG = {
    "excel_path": "",
    "input_mode": "excel",
    "source_dirs": [],
    "output_dir": "",
}

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
        return {**DEFAULT_CONFIG, **data}
    except Exception:
        return DEFAULT_CONFIG.copy()

def save_config(data):
    ensure_app_dir()
    CONFIG_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
