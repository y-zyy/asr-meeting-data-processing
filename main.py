#!/usr/bin/env python3
"""
PPT → Markdown pipeline CLI.

Usage examples:
  python main.py presentation.pptx
  python main.py presentation.pptx --output ./results --dpi 200
  python main.py presentation.pptx --no-vlm          # XML + OCR only
  python main.py presentation.pptx --no-ocr --no-vlm  # XML only (offline test)

Environment variables for API endpoints:
  OCR_API_URL    default: http://localhost:8000/v1/chat/completions
  OCR_API_KEY    default: (empty)
  OCR_MODEL      default: lightonai/LightOnOCR-2-1B
  VLM_API_URL    default: http://localhost:8001/v1/chat/completions
  VLM_API_KEY    default: (empty)
  VLM_MODEL      default: gemma4
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert a PPTX file to Markdown using XML analysis, OCR, and VLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("pptx", help="Path to the input .pptx file")
    parser.add_argument("-o", "--output", default="output", help="Output directory (default: ./output)")
    parser.add_argument("--dpi", type=int, default=150, help="JPEG render DPI (default: 150)")
    parser.add_argument("--quality", type=int, default=85, help="JPEG quality (default: 85)")
    parser.add_argument("--no-ocr", action="store_true", help="Skip OCR step")
    parser.add_argument("--no-vlm", action="store_true", help="Skip VLM step (use XML+OCR fallback)")
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

    pptx_path = Path(args.pptx)
    if not pptx_path.exists():
        logger.error("File not found: %s", pptx_path)
        return 1
    if pptx_path.suffix.lower() not in (".pptx", ".ppt"):
        logger.warning("File does not have a .pptx extension: %s", pptx_path)

    cfg = build_config(args)
    cfg.ensure_dirs()

    logger.info("=== PPT → Markdown pipeline ===")
    logger.info("Input:  %s", pptx_path.resolve())
    logger.info("Output: %s", cfg.output_dir.resolve())

    # --- Step 0: Convert ---
    from ppt_parser import step0_converter
    images = step0_converter.convert_pptx(pptx_path, cfg)
    logger.info("Converted %d slides", len(images))

    # --- Path A: XML ---
    from ppt_parser import path_a_xml_parser, step1_geometry
    xml_structures = path_a_xml_parser.parse_all_slides(pptx_path)
    xml_map = {s.slide_num: step1_geometry.analyze_geometry(s, cfg) for s in xml_structures}

    # --- Path B: OCR ---
    from ppt_parser import path_b_ocr
    ocr_map: dict = {}
    if not args.no_ocr:
        ocr_list = path_b_ocr.run_ocr_batch(images, cfg)
        ocr_map = {i + 1: r for i, r in enumerate(ocr_list)}
    else:
        logger.info("OCR skipped (--no-ocr)")

    # --- Step 2: VLM ---
    from ppt_parser import step2_vlm
    from ppt_parser.models import SlideResult

    results = []
    for idx, img_path in enumerate(images, start=1):
        xml_s = xml_map.get(idx)
        ocr = ocr_map.get(idx)
        vlm_result = None

        if not args.no_vlm:
            try:
                vlm_result = step2_vlm.run_vlm(img_path, idx, xml_s, ocr, cfg)
            except Exception as exc:
                logger.error("VLM failed for slide %d: %s", idx, exc)

        results.append(
            SlideResult(
                slide_num=idx,
                image_path=str(img_path),
                xml_structure=xml_s,
                ocr_result=ocr,
                vlm_result=vlm_result,
            )
        )

    # --- Step 3: Integrate & save ---
    from ppt_parser import step3_integrator
    step3_integrator.integrate_results(results, cfg.output_dir, pptx_path.name)

    logger.info("=== Done ===")
    logger.info("Markdown: %s/output.md", cfg.output_dir)
    logger.info("Report:   %s/analysis_report.json", cfg.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
