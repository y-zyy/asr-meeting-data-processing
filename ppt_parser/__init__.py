"""PPT → Markdown parser using XML analysis, OCR, and VLM."""
from .config import Config
from .models import SlideResult

__all__ = ["Config", "SlideResult", "parse_pptx"]


def parse_pptx(pptx_path, cfg: Config | None = None) -> list[SlideResult]:
    """
    Full pipeline: PPTX → Markdown.

    Returns a list of SlideResult objects.
    Call step3_integrator.integrate_results() to write output files.
    """
    from pathlib import Path
    from .config import default_config
    from . import (
        step0_converter,
        path_a_xml_parser,
        path_b_ocr,
        step1_geometry,
        step2_vlm,
    )

    pptx_path = Path(pptx_path)
    cfg = cfg or default_config
    cfg.ensure_dirs()

    # Step 0: PPT → JPEG
    images = step0_converter.convert_pptx(pptx_path, cfg)

    # Path A: XML parsing
    xml_structures = path_a_xml_parser.parse_all_slides(pptx_path)
    xml_map = {s.slide_num: s for s in xml_structures}

    # Path B: OCR (concurrent if desired; here sequential for simplicity)
    ocr_results = path_b_ocr.run_ocr_batch(images, cfg)

    results: list[SlideResult] = []
    for idx, img_path in enumerate(images, start=1):
        xml_raw = xml_map.get(idx)
        ocr = ocr_results[idx - 1]

        # Step 1: Geometric analysis
        xml_enriched = step1_geometry.analyze_geometry(xml_raw, cfg) if xml_raw else None

        # Step 2: VLM
        try:
            vlm = step2_vlm.run_vlm(img_path, idx, xml_enriched, ocr, cfg)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("VLM failed for slide %d: %s", idx, exc)
            vlm = None

        results.append(
            SlideResult(
                slide_num=idx,
                image_path=str(img_path),
                xml_structure=xml_enriched,
                ocr_result=ocr,
                vlm_result=vlm,
            )
        )

    return results
