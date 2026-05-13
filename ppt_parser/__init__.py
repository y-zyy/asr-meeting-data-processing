"""PPT/PDF → Markdown parser using XML/text analysis, OCR, and VLM."""
from .config import Config
from .models import SlideResult

__all__ = ["Config", "SlideResult", "parse_pptx", "parse_pdf"]


def _run_pipeline(input_path, cfg: Config, *, is_pdf: bool) -> list[SlideResult]:
    """Shared pipeline for both PPTX and PDF inputs."""
    from pathlib import Path
    from .config import default_config
    from . import step0_converter, path_b_ocr, step1_geometry, step2_vlm

    input_path = Path(input_path)
    cfg = cfg or default_config
    cfg.ensure_dirs()

    # Step 0: → JPEG images
    if is_pdf:
        images = step0_converter.convert_pdf(input_path, cfg)
    else:
        images = step0_converter.convert_pptx(input_path, cfg)

    # Path A: structural text extraction
    if is_pdf:
        from . import pdf_text_parser
        raw_structures = pdf_text_parser.parse_all_pages(input_path)
    else:
        from . import path_a_xml_parser
        raw_structures = path_a_xml_parser.parse_all_slides(input_path)
    xml_map = {s.slide_num: step1_geometry.analyze_geometry(s, cfg) for s in raw_structures}

    # Path B: OCR
    ocr_results = path_b_ocr.run_ocr_batch(images, cfg)

    results: list[SlideResult] = []
    for idx, img_path in enumerate(images, start=1):
        xml_enriched = xml_map.get(idx)
        ocr = ocr_results[idx - 1]

        try:
            vlm = step2_vlm.run_vlm(img_path, idx, xml_enriched, ocr, cfg)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("VLM failed for page/slide %d: %s", idx, exc)
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


def parse_pptx(pptx_path, cfg: Config | None = None) -> list[SlideResult]:
    """Full pipeline: PPTX → list of SlideResult."""
    return _run_pipeline(pptx_path, cfg, is_pdf=False)


def parse_pdf(pdf_path, cfg: Config | None = None) -> list[SlideResult]:
    """Full pipeline: PDF → list of SlideResult."""
    return _run_pipeline(pdf_path, cfg, is_pdf=True)
