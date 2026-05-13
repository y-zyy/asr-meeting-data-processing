from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class APIConfig:
    """Configuration for a single API endpoint."""
    url: str
    api_key: str = ""
    timeout: int = 120
    max_retries: int = 3
    # Optional headers beyond Authorization
    extra_headers: dict = field(default_factory=dict)


@dataclass
class Config:
    # --- API endpoints (user-provided at runtime) ---
    # Both OCR and VLM use OpenAI-compatible /v1/chat/completions endpoints.
    ocr_api: APIConfig = field(default_factory=lambda: APIConfig(
        url=os.getenv("OCR_API_URL", "http://localhost:8000/v1/chat/completions"),
        api_key=os.getenv("OCR_API_KEY", ""),
    ))
    vlm_api: APIConfig = field(default_factory=lambda: APIConfig(
        url=os.getenv("VLM_API_URL", "http://localhost:8001/v1/chat/completions"),
        api_key=os.getenv("VLM_API_KEY", ""),
    ))

    # --- Image conversion ---
    jpeg_dpi: int = 150          # pdftoppm resolution
    jpeg_quality: int = 85       # JPEG compression quality

    # --- Output paths ---
    output_dir: Path = Path("output")

    @property
    def slides_dir(self) -> Path:
        return self.output_dir / "slides"

    @property
    def cache_dir(self) -> Path:
        return self.output_dir / "cache"

    # --- Geometry thresholds (in EMU; 1 pt ≈ 12700 EMU) ---
    # Shapes whose centers differ by less than this on one axis are "aligned"
    alignment_threshold_emu: float = 254000   # ~20 pt
    # Shapes within this edge-to-edge distance are "proximate"
    proximity_threshold_emu: float = 635000   # ~50 pt

    # --- OCR (LightOnOCR via vllm) ---
    ocr_model: str = os.getenv("OCR_MODEL", "lightonai/LightOnOCR-2-1B")
    ocr_max_tokens: int = 4096
    ocr_temperature: float = 0.2
    ocr_top_p: float = 0.9

    # --- VLM (Gemma4) ---
    vlm_model: str = os.getenv("VLM_MODEL", "gemma4")
    vlm_max_tokens: int = 4096
    vlm_temperature: float = 0.1


    def ensure_dirs(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.slides_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)


# Module-level default config (can be replaced by callers)
default_config = Config()
