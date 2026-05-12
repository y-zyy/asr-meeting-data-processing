"""Step 0: PPT → PDF (LibreOffice) → JPEG images (pdftoppm)."""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import List

from .config import Config

logger = logging.getLogger(__name__)


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(
            f"'{name}' not found in PATH. "
            f"Install it: {'libreoffice' if name == 'soffice' else 'poppler-utils'}"
        )
    return path


def pptx_to_pdf(pptx_path: Path, output_dir: Path) -> Path:
    """Convert PPTX → PDF using LibreOffice headless."""
    soffice = _require_tool("soffice")
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        soffice,
        "--headless",
        "--convert-to", "pdf",
        "--outdir", str(output_dir),
        str(pptx_path),
    ]
    logger.info("Converting %s → PDF …", pptx_path.name)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"LibreOffice conversion failed:\n{result.stderr}")

    pdf_path = output_dir / (pptx_path.stem + ".pdf")
    if not pdf_path.exists():
        raise RuntimeError(f"Expected PDF not found at {pdf_path}")
    logger.info("PDF saved: %s", pdf_path)
    return pdf_path


def pdf_to_jpegs(pdf_path: Path, slides_dir: Path, dpi: int = 150, quality: int = 85) -> List[Path]:
    """Convert PDF pages → JPEG images using pdftoppm."""
    _require_tool("pdftoppm")
    slides_dir.mkdir(parents=True, exist_ok=True)

    prefix = slides_dir / "slide"
    cmd = [
        "pdftoppm",
        "-jpeg",
        f"-r", str(dpi),
        f"-jpegopt", f"quality={quality}",
        str(pdf_path),
        str(prefix),
    ]
    logger.info("Rendering PDF pages at %d DPI …", dpi)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"pdftoppm failed:\n{result.stderr}")

    # pdftoppm names files slide-1.jpg, slide-2.jpg, … (zero-padded depending on count)
    images = sorted(slides_dir.glob("slide-*.jpg"), key=_slide_sort_key)
    if not images:
        # Some versions use .jpeg extension
        images = sorted(slides_dir.glob("slide-*.jpeg"), key=_slide_sort_key)
    if not images:
        raise RuntimeError(f"No JPEG images produced in {slides_dir}")

    logger.info("Produced %d slide image(s)", len(images))
    return images


def _slide_sort_key(p: Path) -> int:
    """Extract the page number from filenames like 'slide-01.jpg'."""
    stem = p.stem  # e.g. "slide-01"
    try:
        return int(stem.rsplit("-", 1)[-1])
    except ValueError:
        return 0


def convert_pptx(pptx_path: Path, cfg: Config) -> List[Path]:
    """Full pipeline: PPTX → PDF → JPEG list."""
    cfg.ensure_dirs()
    pdf_path = pptx_to_pdf(pptx_path, cfg.output_dir)
    images = pdf_to_jpegs(pdf_path, cfg.slides_dir, dpi=cfg.jpeg_dpi, quality=cfg.jpeg_quality)
    return images
