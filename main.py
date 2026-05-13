#!/usr/bin/env python3
"""
PPT/PDF → Markdown pipeline CLI.

Usage examples:
  python main.py presentation.pptx
  python main.py document.pdf
  python main.py presentation.pptx --output ./results --dpi 200
  python main.py presentation.pptx --no-vlm               # XML + OCR only
  python main.py presentation.pptx --no-ocr --no-vlm      # XML only (offline test)
  python main.py document.pdf --no-opendataloader          # skip OpenDataLoader

Environment variables for API endpoints:
  OCR_API_URL                default: http://localhost:8000/v1/chat/completions
  OCR_API_KEY                default: (empty)
  OCR_MODEL                  default: lightonai/LightOnOCR-2-1B
  VLM_API_URL                default: http://localhost:8001/v1/chat/completions
  VLM_API_KEY                default: (empty)
  VLM_MODEL                  default: gemma4
  (OpenDataLoader uses the opendataloader_pdf Python package — no API endpoint needed)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ppt_parser.config import APIConfig, Config
from ppt_parser.utils import setup_logging


def build_config(args: argparse.Namespace) -> Config:
    cfg = Config()
    cfg.output_dir = Path(args.output)
    cfg.jpeg_dpi = args.dpi
    cfg.jpeg_quality = args.quality

    if args.ocr_url:
        cfg.ocr_api.url = args.ocr_url
    if args.ocr_key:
        cfg.ocr_api.api_key = args.ocr_key
    if args.ocr_model:
        cfg.ocr_model = args.ocr_model

    if args.vlm_url:
        cfg.vlm_api.url = args.vlm_url
    if args.vlm_key:
        cfg.vlm_api.api_key = args.vlm_key
    if args.vlm_model:
        cfg.vlm_model = args.vlm_model

    return cfg


_PPTX_EXTS = {".pptx", ".ppt"}
_PDF_EXTS = {".pdf"}
_SUPPORTED_EXTS = _PPTX_EXTS | _PDF_EXTS


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert a PPTX or PDF file to Markdown using XML/text analysis, OCR, and VLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input", help="Path to the input .pptx or .pdf file")
    parser.add_argument("-o", "--output", default="output", help="Output directory (default: ./output)")
    parser.add_argument("--dpi", type=int, default=150, help="JPEG render DPI (default: 150)")
    parser.add_argument("--quality", type=int, default=85, help="JPEG quality (default: 85)")
    parser.add_argument("--no-ocr", action="store_true", help="Skip OCR step")
    parser.add_argument("--no-vlm", action="store_true", help="Skip VLM step (use XML+OCR fallback)")
    parser.add_argument("--no-opendataloader", action="store_true",
                        help="Skip OpenDataLoader step (PDF only)")
    parser.add_argument("--ocr-url", default=None, help="Override OCR API URL")
    parser.add_argument("--ocr-key", default=None, help="Override OCR API key")
    parser.add_argument("--ocr-model", default=None, help="Override OCR model name (default: lightonai/LightOnOCR-2-1B)")
    parser.add_argument("--vlm-url", default=None, help="Override VLM API URL")
    parser.add_argument("--vlm-key", default=None, help="Override VLM API key")
    parser.add_argument("--vlm-model", default=None, help="Override VLM model name (default: gemma4)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)

    input_path = Path(args.input)
    if not input_path.exists():
        logger.error("File not found: %s", input_path)
        return 1

    ext = input_path.suffix.lower()
    if ext not in _SUPPORTED_EXTS:
        logger.warning("Unrecognised file extension '%s'. Supported: %s", ext, ", ".join(sorted(_SUPPORTED_EXTS)))

    is_pdf = ext in _PDF_EXTS

    cfg = build_config(args)
    cfg.ensure_dirs()

    logger.info("=== PPT/PDF → Markdown pipeline ===")
    logger.info("Input:  %s", input_path.resolve())
    logger.info("Output: %s", cfg.output_dir.resolve())
    logger.info("Mode:   %s", "PDF" if is_pdf else "PPTX")

    # --- Step 0: Convert to JPEG slides ---
    from ppt_parser import step0_converter
    if is_pdf:
        images = step0_converter.convert_pdf(input_path, cfg)
    else:
        images = step0_converter.convert_pptx(input_path, cfg)
    logger.info("Converted %d page(s)/slide(s)", len(images))

    # --- Path A: Structural text extraction ---
    from ppt_parser import step1_geometry
    xml_map: dict = {}
    if is_pdf:
        from ppt_parser import pdf_text_parser
        page_structures = pdf_text_parser.parse_all_pages(input_path)
        xml_map = {s.slide_num: step1_geometry.analyze_geometry(s, cfg) for s in page_structures}
    else:
        from ppt_parser import path_a_xml_parser
        xml_structures = path_a_xml_parser.parse_all_slides(input_path)
        xml_map = {s.slide_num: step1_geometry.analyze_geometry(s, cfg) for s in xml_structures}

    # --- Path B: OCR ---
    from ppt_parser import path_b_ocr
    ocr_map: dict = {}
    if not args.no_ocr:
        ocr_list = path_b_ocr.run_ocr_batch(images, cfg)
        ocr_map = {i + 1: r for i, r in enumerate(ocr_list)}
    else:
        logger.info("OCR skipped (--no-ocr)")

    # --- Path C: OpenDataLoader (PDF only) ---
    from ppt_parser import path_c_opendataloader
    odl_map: dict = {}
    if is_pdf and not args.no_opendataloader:
        odl_list = path_c_opendataloader.run_opendataloader_batch(
            input_path, len(images), cfg
        )
        odl_map = {i + 1: r for i, r in enumerate(odl_list)}
    elif not is_pdf:
        logger.info("OpenDataLoader skipped (PPTX input — PDF only feature)")
    else:
        logger.info("OpenDataLoader skipped (--no-opendataloader)")

    # --- Step 2: VLM ---
    from ppt_parser import step2_vlm
    from ppt_parser.models import SlideResult

    results = []
    for idx, img_path in enumerate(images, start=1):
        xml_s = xml_map.get(idx)
        ocr = ocr_map.get(idx)
        odl = odl_map.get(idx)
        vlm_result = None

        if not args.no_vlm:
            try:
                vlm_result = step2_vlm.run_vlm(
                    img_path, idx, xml_s, ocr, cfg, opendataloader_result=odl
                )
            except Exception as exc:
                logger.error("VLM failed for slide/page %d: %s", idx, exc)

        results.append(
            SlideResult(
                slide_num=idx,
                image_path=str(img_path),
                xml_structure=xml_s,
                ocr_result=ocr,
                opendataloader_result=odl,
                vlm_result=vlm_result,
            )
        )

    # --- Step 3: Integrate & save ---
    from ppt_parser import step3_integrator
    step3_integrator.integrate_results(results, cfg.output_dir, input_path.name)

    logger.info("=== Done ===")
    logger.info("Markdown: %s/output.md", cfg.output_dir)
    logger.info("Report:   %s/analysis_report.json", cfg.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
