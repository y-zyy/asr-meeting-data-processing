from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def encode_image_base64(image_path: str | Path) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def content_sha256(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode())
    return h.hexdigest()


def load_cache(cache_dir: Path, cache_key: str) -> Optional[Dict[str, Any]]:
    cache_file = cache_dir / f"{cache_key}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def save_cache(cache_dir: Path, cache_key: str, data: Dict[str, Any]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{cache_key}.json"
    cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def retry_with_backoff(func, max_retries: int = 3, base_delay: float = 2.0):
    """Call func(), retrying up to max_retries times with exponential backoff."""
    last_exc: Exception = RuntimeError("No attempts made")
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning("Attempt %d failed (%s). Retrying in %.0fs…", attempt + 1, exc, delay)
                time.sleep(delay)
    raise last_exc


def emu_to_pt(emu: float) -> float:
    """1 pt = 12700 EMU."""
    return emu / 12700


def emu_to_cm(emu: float) -> float:
    """1 cm = 360000 EMU."""
    return emu / 360000


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
