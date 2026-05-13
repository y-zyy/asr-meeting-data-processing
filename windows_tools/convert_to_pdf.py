"""
PPTX → PDF 변환 스크립트 (Windows / Microsoft Office)

사용법:
    python convert_to_pdf.py presentation.pptx
    python convert_to_pdf.py C:\\slides\\             # 폴더 내 전체 변환
    python convert_to_pdf.py presentation.pptx -o C:\\output

요구사항:
    pip install pywin32
    Microsoft PowerPoint 설치 필요
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def convert_one(pptx_path: Path, pdf_path: Path) -> None:
    try:
        import win32com.client  # type: ignore
    except ImportError:
        print("ERROR: pywin32 패키지가 필요합니다. 'pip install pywin32'를 실행하세요.")
        sys.exit(1)

    print(f"변환 중: {pptx_path.name} → {pdf_path.name}")
    ppt_app = None
    presentation = None
    try:
        ppt_app = win32com.client.Dispatch("PowerPoint.Application")
        ppt_app.Visible = False
        presentation = ppt_app.Presentations.Open(
            str(pptx_path.resolve()),
            ReadOnly=True,
            Untitled=False,
            WithWindow=False,
        )
        # ppSaveAsPDF = 32
        presentation.SaveAs(str(pdf_path.resolve()), 32)
        print(f"  완료: {pdf_path}")
    finally:
        if presentation:
            presentation.Close()
        if ppt_app:
            ppt_app.Quit()


def main() -> None:
    parser = argparse.ArgumentParser(description="PPTX를 Microsoft Office로 PDF 변환")
    parser.add_argument("input", help="PPTX 파일 또는 폴더 경로")
    parser.add_argument("-o", "--output-dir", default=None,
                        help="출력 폴더 (기본값: 입력 파일과 동일한 위치)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: 경로를 찾을 수 없습니다: {input_path}")
        sys.exit(1)

    if input_path.is_dir():
        files = list(input_path.glob("**/*.pptx")) + list(input_path.glob("**/*.ppt"))
    else:
        files = [input_path]

    if not files:
        print("변환할 PPTX/PPT 파일이 없습니다.")
        sys.exit(1)

    success, fail = 0, 0
    for f in files:
        out_dir = Path(args.output_dir) if args.output_dir else f.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = out_dir / (f.stem + ".pdf")
        try:
            convert_one(f, pdf_path)
            success += 1
        except Exception as exc:
            print(f"  실패: {f.name} — {exc}")
            fail += 1

    print(f"\n── 완료: 성공 {success}개" + (f", 실패 {fail}개" if fail else "") + " ──")


if __name__ == "__main__":
    main()
