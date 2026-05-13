"""FastAPI web service for PPTX/PDF → Markdown conversion."""
from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

# Ensure the project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from ppt_parser.config import APIConfig, Config
from ppt_parser.utils import setup_logging

setup_logging("INFO")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Job store
# ---------------------------------------------------------------------------

JOBS_DIR = Path(os.getenv("JOBS_DIR", "/tmp/ppt_jobs"))
JOBS_DIR.mkdir(parents=True, exist_ok=True)

PROCESSING_MODE_FLAGS = {
    "opendataloader_only": {"no_ocr": True,  "no_vlm": True,  "no_opendataloader": False},
    "ocr_only":            {"no_ocr": False, "no_vlm": True,  "no_opendataloader": True},
    "vlm_only":            {"no_ocr": True,  "no_vlm": False, "no_opendataloader": True},
    "full":                {"no_ocr": False, "no_vlm": False, "no_opendataloader": False},
}


@dataclass
class Job:
    id: str
    status: str = "pending"   # pending | processing | done | error
    progress: int = 0         # 0–100
    message: str = "Waiting…"
    output_file: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    filename: str = ""


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


def _get_job(job_id: str) -> Job:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------

def _run_pipeline(job: Job, input_path: Path, mode: str, vlm_system_prompt: str) -> None:
    flags = PROCESSING_MODE_FLAGS.get(mode, PROCESSING_MODE_FLAGS["full"])
    output_dir = JOBS_DIR / job.id / "output"

    try:
        def _progress(pct: int, msg: str) -> None:
            with _jobs_lock:
                job.progress = pct
                job.message = msg

        _progress(5, "파일 분석 중…")

        ext = input_path.suffix.lower()
        is_pdf = ext == ".pdf"

        cfg = Config()
        cfg.output_dir = output_dir
        cfg.ensure_dirs()
        if vlm_system_prompt.strip():
            cfg.vlm_system_prompt = vlm_system_prompt.strip()

        # Honour environment variable overrides (set via docker-compose)
        # Config already reads them in its default_factory — nothing extra needed.

        _progress(10, "이미지 변환 중…")
        from ppt_parser import step0_converter
        if is_pdf:
            images = step0_converter.convert_pdf(input_path, cfg)
        else:
            images = step0_converter.convert_pptx(input_path, cfg)
        total = len(images)
        logger.info("Converted %d slide(s) for job %s", total, job.id)

        _progress(20, f"{total}페이지 구조 분석 중…")
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

        _progress(30, "OCR 처리 중…" if not flags["no_ocr"] else "OCR 건너뜀")
        from ppt_parser import path_b_ocr
        ocr_map: dict = {}
        if not flags["no_ocr"]:
            ocr_list = path_b_ocr.run_ocr_batch(images, cfg)
            ocr_map = {i + 1: r for i, r in enumerate(ocr_list)}

        _progress(50, "OpenDataLoader 처리 중…" if (is_pdf and not flags["no_opendataloader"]) else "OpenDataLoader 건너뜀")
        from ppt_parser import path_c_opendataloader
        odl_map: dict = {}
        if is_pdf and not flags["no_opendataloader"]:
            odl_list = path_c_opendataloader.run_opendataloader_batch(input_path, total, cfg)
            odl_map = {i + 1: r for i, r in enumerate(odl_list)}

        _progress(60, "VLM 처리 중…" if not flags["no_vlm"] else "VLM 건너뜀")
        from ppt_parser import step2_vlm
        from ppt_parser.models import SlideResult
        results = []
        for idx, img_path in enumerate(images, start=1):
            xml_s = xml_map.get(idx)
            ocr = ocr_map.get(idx)
            odl = odl_map.get(idx)
            vlm_result = None
            if not flags["no_vlm"]:
                try:
                    vlm_result = step2_vlm.run_vlm(img_path, idx, xml_s, ocr, cfg, opendataloader_result=odl)
                except Exception as exc:
                    logger.error("VLM failed slide %d: %s", idx, exc)
            results.append(SlideResult(
                slide_num=idx,
                image_path=str(img_path),
                xml_structure=xml_s,
                ocr_result=ocr,
                opendataloader_result=odl,
                vlm_result=vlm_result,
            ))
            pct = 60 + int(35 * idx / total)
            _progress(pct, f"슬라이드 {idx}/{total} 처리 완료")

        _progress(97, "결과 통합 중…")
        from ppt_parser import step3_integrator
        step3_integrator.integrate_results(results, output_dir, input_path.name)

        output_md = output_dir / "output.md"
        if not output_md.exists():
            raise FileNotFoundError("output.md not generated")

        with _jobs_lock:
            job.status = "done"
            job.progress = 100
            job.message = "완료"
            job.output_file = str(output_md)

    except Exception as exc:
        logger.exception("Pipeline failed for job %s", job.id)
        with _jobs_lock:
            job.status = "error"
            job.progress = 0
            job.message = "오류 발생"
            job.error = str(exc)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="PPT/PDF → Markdown 변환 서비스")

_static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = _static_dir / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.post("/api/jobs")
async def create_job(
    file: UploadFile = File(...),
    mode: str = Form("full"),
    vlm_system_prompt: str = Form(""),
):
    if mode not in PROCESSING_MODE_FLAGS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 모드: {mode}")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".pdf", ".pptx", ".ppt"}:
        raise HTTPException(status_code=400, detail="PDF 또는 PPTX 파일만 지원합니다.")

    job_id = str(uuid.uuid4())
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Save uploaded file
    safe_name = f"input{ext}"
    input_path = job_dir / safe_name
    content = await file.read()
    input_path.write_bytes(content)

    job = Job(id=job_id, filename=file.filename or safe_name)
    with _jobs_lock:
        _jobs[job_id] = job

    # Kick off background thread
    t = threading.Thread(
        target=_run_pipeline,
        args=(job, input_path, mode, vlm_system_prompt),
        daemon=True,
    )
    t.start()

    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    job = _get_job(job_id)
    return {
        "id": job.id,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "filename": job.filename,
        "error": job.error,
    }


@app.get("/api/jobs/{job_id}/download")
async def download_result(job_id: str):
    job = _get_job(job_id)
    if job.status != "done" or not job.output_file:
        raise HTTPException(status_code=400, detail="아직 처리가 완료되지 않았습니다.")
    output_path = Path(job.output_file)
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="결과 파일을 찾을 수 없습니다.")
    stem = Path(job.filename).stem
    return FileResponse(
        path=str(output_path),
        media_type="text/markdown",
        filename=f"{stem}.md",
    )


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    job = _get_job(job_id)
    job_dir = JOBS_DIR / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir)
    with _jobs_lock:
        _jobs.pop(job_id, None)
    return {"detail": "삭제 완료"}
