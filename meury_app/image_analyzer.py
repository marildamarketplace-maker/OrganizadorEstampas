"""Análise de uma arte por visão computacional usando a API OpenAI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import base64
import io
import json
import os
import re
import time


def _terminal_log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] Análise de IA: {message}", flush=True)


DEFAULT_MODEL_ID = "gpt-4o-mini"
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
API_IMAGE_EDGE = 768
API_TIMEOUT_SECONDS = 300

ANALYSIS_PROMPT = """Analise somente a arte ou estampa visível na imagem, ignorando
produto, tecido, manequim e fundo. Gere metadados úteis para pesquisar essa arte em um
catálogo. Escreva tudo em português brasileiro. Forneça de 10 a 20 palavras-chave
curtas, cores predominantes, elementos visuais concretos, temas e uma categoria.
Não invente personagens, marcas ou nomes quando não houver certeza visual."""

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "colors": {"type": "array", "items": {"type": "string"}},
        "elements": {"type": "array", "items": {"type": "string"}},
        "themes": {"type": "array", "items": {"type": "string"}},
        "category": {"type": "string"},
    },
    "required": ["description", "keywords", "colors", "elements", "themes", "category"],
    "additionalProperties": False,
}


@dataclass
class ImageAnalysis:
    description: str
    keywords: list[str]
    colors: list[str]
    elements: list[str]
    themes: list[str]
    category: str
    processed: bool = True
    model: str = DEFAULT_MODEL_ID
    device: str = "cpu"
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def detect_device(torch_module=None) -> tuple[str, Any]:
    """Retorna CUDA/float16 quando disponível; caso contrário CPU/float32."""
    if torch_module is None:
        try:
            import torch as torch_module
        except ImportError as exc:
            raise RuntimeError(
                "As dependências de IA não estão instaladas. Execute: "
                "instalar_recursos_ia_windows.bat (Windows) ou "
                "instalar_recursos_ia_macos.command (macOS)"
            ) from exc
    if torch_module.cuda.is_available():
        return "cuda", torch_module.float16
    return "cpu", torch_module.float32


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("O modelo não retornou um objeto JSON.")
        try:
            value = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"O modelo retornou JSON inválido: {exc.msg}.") from exc
    if not isinstance(value, dict):
        raise ValueError("A resposta do modelo precisa ser um objeto JSON.")
    return value


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clean_list(value: Any, limit: int) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,;\n]", value)
    if not isinstance(value, list):
        return []
    result, seen = [], set()
    for item in value:
        cleaned = _clean_text(item).strip(" .,-").casefold()
        if cleaned and cleaned not in seen:
            result.append(cleaned)
            seen.add(cleaned)
        if len(result) >= limit:
            break
    return result


def parse_analysis_response(
    response: str,
    *,
    model_id: str = DEFAULT_MODEL_ID,
    device: str = "cpu",
    elapsed_seconds: float = 0.0,
) -> ImageAnalysis:
    """Converte e normaliza a resposta livre do modelo em dados do catálogo."""
    value = _extract_json(response)
    description = _clean_text(value.get("description") or value.get("descricao"))
    category = _clean_text(value.get("category") or value.get("categoria")).casefold()
    analysis = ImageAnalysis(
        description=description,
        keywords=_clean_list(value.get("keywords") or value.get("palavras_chave"), 30),
        colors=_clean_list(value.get("colors") or value.get("cores"), 12),
        elements=_clean_list(value.get("elements") or value.get("elementos"), 20),
        themes=_clean_list(value.get("themes") or value.get("temas"), 12),
        category=category,
        model=model_id,
        device=device,
        elapsed_seconds=elapsed_seconds,
    )
    if not analysis.description:
        raise ValueError("O modelo não forneceu uma descrição.")
    if not analysis.keywords:
        raise ValueError("O modelo não forneceu palavras-chave.")
    return analysis


class LocalImageAnalyzer:
    """Analisa imagens pela API OpenAI, mantendo o cliente entre as chamadas."""

    def __init__(self, model_id: str = DEFAULT_MODEL_ID, local_files_only: bool = False):
        self.model_id = model_id
        self.local_files_only = local_files_only  # Compatibilidade com chamadas antigas.
        self.client = None
        self.device = "api"

    def load(self) -> None:
        if self.client is not None:
            return
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("Informe a chave da API OpenAI antes de iniciar a análise.")
        _terminal_log(f"preparando cliente da API para {self.model_id}...")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "O cliente da OpenAI não está instalado. Feche e abra o aplicativo "
                "para atualizar as dependências."
            ) from exc
        self.client = OpenAI(api_key=api_key, timeout=API_TIMEOUT_SECONDS, max_retries=2)
        _terminal_log("cliente da API pronto.")

    def analyze(self, image_path: Path | str) -> ImageAnalysis:
        path = validate_image_path(image_path)
        self.load()
        _terminal_log(f"iniciando imagem: {path.name}")
        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("A biblioteca de imagens não está instalada.") from exc

        started = time.monotonic()
        with Image.open(path) as opened:
            _terminal_log("abrindo, redimensionando e compactando a imagem...")
            image = opened.convert("RGB")
            image.thumbnail((API_IMAGE_EDGE, API_IMAGE_EDGE))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=85, optimize=True)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        data_url = f"data:image/jpeg;base64,{encoded}"
        _terminal_log(
            f"enviando {len(buffer.getvalue()) / 1024:.0f} KB para a API OpenAI..."
        )
        try:
            api_response = self.client.responses.create(
                model=self.model_id,
                input=[{
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": ANALYSIS_PROMPT},
                        {"type": "input_image", "image_url": data_url, "detail": "low"},
                    ],
                }],
                max_output_tokens=500,
                text={
                    "verbosity": "medium",
                    "format": {
                        "type": "json_schema",
                        "name": "analise_estampa",
                        "strict": True,
                        "schema": ANALYSIS_SCHEMA,
                    },
                },
                store=False,
            )
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            name = type(exc).__name__
            if status == 401 or name == "AuthenticationError":
                raise RuntimeError("A chave da API OpenAI é inválida ou foi revogada.") from exc
            if status == 429 or name == "RateLimitError":
                raise RuntimeError(
                    "A API recusou a solicitação por limite de uso ou falta de créditos."
                ) from exc
            if name in {"APITimeoutError", "TimeoutError"}:
                raise RuntimeError("A API OpenAI excedeu o limite de 5 minutos.") from exc
            if name == "APIConnectionError":
                raise RuntimeError(
                    "Não foi possível conectar à API OpenAI. Verifique a internet."
                ) from exc
            raise RuntimeError(f"Falha na API OpenAI: {exc}") from exc
        response = api_response.output_text
        usage = getattr(api_response, "usage", None)
        if usage:
            _terminal_log(
                f"resposta recebida; entrada={usage.input_tokens} tokens; "
                f"saída={usage.output_tokens} tokens."
            )
        else:
            _terminal_log("resposta recebida da API.")
        _terminal_log(f"imagem concluída em {time.monotonic() - started:.1f}s.")
        return parse_analysis_response(
            response, model_id=self.model_id, device="openai-api",
            elapsed_seconds=time.monotonic() - started,
        )

    def release(self) -> None:
        """Mantém somente o cliente leve durante o lote."""


def validate_image_path(image_path: Path | str) -> Path:
    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"A imagem não existe: {path}")
    if path.suffix.casefold() not in SUPPORTED_IMAGE_EXTENSIONS:
        raise ValueError("A análise aceita somente arquivos JPG, JPEG e PNG.")
    return path


def analyze_image(
    image_path: Path | str,
    *,
    analyzer: LocalImageAnalyzer | None = None,
    model_id: str = DEFAULT_MODEL_ID,
    local_files_only: bool = False,
) -> ImageAnalysis:
    """Função independente para analisar uma imagem, sem alterar o catálogo."""
    engine = analyzer or LocalImageAnalyzer(model_id, local_files_only)
    return engine.analyze(image_path)
