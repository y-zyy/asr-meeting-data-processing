# PPT / PDF → Markdown Parser

PPTX 및 PDF 파일을 Markdown 문서로 변환하는 파이프라인.  
XML 구조 분석, **OpenDataLoader**(직접 PDF 텍스트 추출), OCR, VLM(Gemma4)을 조합하여  
도형·화살표·표 등 시각적 관계를 포함한 Markdown을 생성합니다.  
완전 폐쇄망(오프라인) 환경을 전제로 설계되었습니다.

---

## 전체 흐름

### PPTX 입력

```
presentation.pptx
        │
        ▼
[Step 0] LibreOffice (headless) → PDF
         pdftoppm → slides/slide-1.jpg, slide-2.jpg, …
        │
        ├──────────────────────┐
        ▼                      ▼
[Path A] PPTX XML 파싱       [Path B] LightOnOCR API
  • 도형 위치·크기 (EMU)        • JPEG → 텍스트 추출 (보조)
  • 화살표 연결 관계
  • 표·차트·SmartArt
  • 네임스페이스 자동 감지
        │                      │
        └──────────┬───────────┘
                   ▼
          [Step 1] 기하학 분석
            • 포함 관계 (containment)
            • 가로/세로 정렬 감지
            • 흐름 순서 (flow sequence)
            • 슬라이드 XML 요약 생성
                   │
                   ▼
          [Step 2] Gemma4 VLM API
            입력: 이미지 + OCR 텍스트 + XML 요약
            출력: Markdown
                   │
                   ▼
          [Step 3] 결과 통합
            output/output.md  /  analysis_report.json
```

### PDF 입력

```
document.pdf
        │
        ├───────────────────────────────────────┐
        │                                       │
        ├──────────────────────┐                ▼
        ▼                      ▼        [Path C] OpenDataLoader
[Path A] pypdf 텍스트 추출    [Path B] LightOnOCR API       (opendataloader_pdf 패키지)
  • 페이지별 텍스트·제목        • JPEG → 텍스트 추출 (보조)   • PDFConverter로 직접 추출
  • SlideXMLStructure 변환                                    • 폰트 기반, 가장 정확
        │                      │                │
        └──────────┬────────────────────────────┘
                   ▼
          [Step 1] 기하학 분석
                   │
                   ▼
          [Step 2] Gemma4 VLM API
            입력: 이미지
                + OpenDataLoader 텍스트 (1순위 — 가장 정확)
                + OCR 텍스트 (2순위 — 보조)
                + XML 요약
            출력: Markdown
                   │
                   ▼
          [Step 3] 결과 통합
            output/output.md  /  analysis_report.json
```

> **텍스트 소스 우선순위 (PDF 입력 시)**  
> OpenDataLoader (폰트 기반 직접 추출) > OCR (이미지 인식) > XML 텍스트

---

## 시스템 요구 사항

| 항목 | 내용 |
|------|------|
| Python | 3.10 이상 |
| LibreOffice | 7.x 이상 (`soffice` 명령어, PPTX 입력 시) |
| Poppler | `pdftoppm` 명령어 (`poppler-utils`) |

### 시스템 패키지 설치

```bash
# Ubuntu / Debian
sudo apt install libreoffice poppler-utils

# RHEL / Rocky Linux
sudo dnf install libreoffice poppler-utils
```

### Python 패키지 설치

```bash
pip install -r requirements.txt
```

설치 항목:

| 패키지 | 용도 |
|--------|------|
| `lxml` | 빠른 XML 파싱 (선택) |
| `Pillow` | 이미지 처리 (선택) |
| `pypdf` | PDF 텍스트 추출 (Path A, PDF 입력 시) |
| `opendataloader_pdf` | PDF 직접 텍스트 추출 (Path C, PDF 입력 시) |

> **폐쇄망 환경**: `pip download -r requirements.txt -d ./wheels` 로 사전 다운로드 후  
> `pip install --no-index --find-links ./wheels -r requirements.txt` 로 오프라인 설치합니다.

---

## 빠른 시작

### 1. API 엔드포인트 설정

환경변수로 OCR·VLM API 주소와 인증 키를 지정합니다.  
OpenDataLoader는 로컬 Python 패키지이므로 API 설정이 불필요합니다.

```bash
export OCR_API_URL="http://<ocr-server>:<port>/v1/chat/completions"
export OCR_API_KEY="your-ocr-key"   # 인증 불필요 시 생략
export OCR_MODEL="lightonai/LightOnOCR-2-1B"

export VLM_API_URL="http://<vlm-server>:<port>/v1/chat/completions"
export VLM_API_KEY="your-vlm-key"   # 인증 불필요 시 생략
export VLM_MODEL="gemma4"
```

### 2. 실행

```bash
# PPTX 파일
python main.py presentation.pptx

# PDF 파일 (OpenDataLoader + OCR + VLM 모두 사용)
python main.py document.pdf
```

결과물은 `./output/` 디렉터리에 생성됩니다.

---

## 사용 예시

```bash
# 기본 실행 (OCR + VLM 모두 사용)
python main.py presentation.pptx

# PDF 입력 — OpenDataLoader + OCR + VLM
python main.py document.pdf

# PDF 입력 — OpenDataLoader 건너뜀
python main.py document.pdf --no-opendataloader

# 출력 경로와 이미지 품질 지정
python main.py presentation.pptx --output ./results --dpi 200 --quality 90

# VLM 없이 XML + OCR만 사용
python main.py presentation.pptx --no-vlm

# OCR·VLM 모두 건너뛰고 XML 구조만 분석
python main.py presentation.pptx --no-ocr --no-vlm

# API 주소를 CLI 플래그로 직접 지정
python main.py presentation.pptx \
  --ocr-url http://192.168.1.10:8000/v1/chat/completions \
  --vlm-url http://192.168.1.20:8001/v1/chat/completions \
  --vlm-model gemma4

# 상세 로그 출력
python main.py presentation.pptx --log-level DEBUG
```

---

## CLI 옵션 전체 목록

```
positional arguments:
  input                 변환할 .pptx 또는 .pdf 파일 경로

options:
  -o, --output DIR          출력 디렉터리 (기본값: ./output)
  --dpi N                   슬라이드 렌더링 DPI (기본값: 150)
  --quality N               JPEG 압축 품질 0~100 (기본값: 85)
  --no-ocr                  OCR 단계 건너뜀
  --no-vlm                  VLM 단계 건너뜀 (XML + 텍스트 결과로 대체)
  --no-opendataloader       OpenDataLoader 단계 건너뜀 (PDF 입력 시)
  --ocr-url URL             OCR API URL 재정의
  --ocr-key KEY             OCR API 인증 키 재정의
  --ocr-model NAME          OCR 모델명 재정의 (기본값: lightonai/LightOnOCR-2-1B)
  --vlm-url URL             VLM API URL 재정의
  --vlm-key KEY             VLM API 인증 키 재정의
  --vlm-model NAME          VLM 모델명 재정의 (기본값: gemma4)
  --log-level LEVEL         로그 수준: DEBUG / INFO / WARNING / ERROR
```

---

## 환경변수 참조표

| 환경변수 | 기본값 | 설명 |
|----------|--------|------|
| `OCR_API_URL` | `http://localhost:8000/v1/chat/completions` | LightOnOCR 엔드포인트 |
| `OCR_API_KEY` | (없음) | OCR API 인증 키 |
| `OCR_MODEL` | `lightonai/LightOnOCR-2-1B` | LightOnOCR 모델명 |
| `VLM_API_URL` | `http://localhost:8001/v1/chat/completions` | Gemma4 엔드포인트 |
| `VLM_API_KEY` | (없음) | VLM API 인증 키 |
| `VLM_MODEL` | `gemma4` | VLM 모델명 |

> OpenDataLoader는 로컬 Python 패키지(`opendataloader_pdf`)를 직접 호출하므로 별도 환경변수가 없습니다.

---

## 출력 파일 설명

```
output/
├── output.md
│     전체 슬라이드/페이지를 하나로 합친 Markdown 파일.
│     제목, 본문, 표, 흐름도(→ 표기), 화살표 관계 포함.
│
├── analysis_report.json
│     슬라이드/페이지별 상세 분석 결과:
│     • 도형 목록 (ID, 타입, 위치, 텍스트)
│     • 관계 목록 (화살표 연결, 포함, 정렬)
│     • XML 요약, OpenDataLoader 텍스트, OCR 텍스트, VLM 신뢰도
│
├── slides/
│     slide-1.jpg, slide-2.jpg, …  (pdftoppm 변환 이미지)
│
└── cache/
      odl_<hash>_allpages.json     (OpenDataLoader 변환 캐시)
      ocr_<hash>_slideN.json       (OCR 결과 캐시)
      vlm_<hash>_slideN.json       (VLM 결과 캐시)
```

> 캐시가 존재하면 동일 파일 재처리 시 변환/API 호출을 생략합니다.

---

## 프로젝트 구조

```
.
├── main.py                      # CLI 진입점
├── requirements.txt
└── ppt_parser/
    ├── __init__.py
    ├── config.py                # 설정 (API, DPI, 임계값 등)
    ├── models.py                # 데이터 모델 (BoundingBox, ShapeInfo, …)
    ├── utils.py                 # base64, 캐시, 재시도/backoff, 해시
    ├── step0_converter.py       # PPTX → PDF → JPEG  /  PDF → JPEG
    ├── path_a_xml_parser.py     # PPTX XML 파싱 (도형·화살표·표)
    ├── pdf_text_parser.py       # PDF pypdf 텍스트 추출 (Path A, PDF 입력)
    ├── path_b_ocr.py            # LightOnOCR HTTP 클라이언트 (보조)
    ├── path_c_opendataloader.py # opendataloader_pdf 직접 추출 (주요, PDF 입력)
    ├── step1_geometry.py        # 기하학 분석 + XML 요약 직렬화
    ├── step2_vlm.py             # Gemma4 VLM HTTP 클라이언트
    └── step3_integrator.py      # Markdown + JSON 리포트 저장
```

---

## Markdown 폴백 우선순위

VLM 오류 발생 시 자동으로 하위 방법으로 대체합니다.

```
VLM 결과
  → (실패 시) XML 구조 기반 생성
  → (실패 시) OpenDataLoader 원문 텍스트
  → (실패 시) OCR 원문 텍스트
```

XML 기반 폴백은 화살표 연결을 `[Step 1] → [Step 2] → [Step 3]` 형태로,  
표는 GitHub Flavored Markdown 표로, 제목은 `#` 헤더로 자동 변환합니다.

---

## VLM 텍스트 앙상블 (PDF 입력 시)

PDF 입력에서 VLM은 세 가지 텍스트 소스를 동시에 받아 처리합니다.

| 소스 | 방식 | 우선순위 | VLM 역할 |
|------|------|----------|----------|
| OpenDataLoader | 폰트 기반 직접 추출 | **1순위** | 주요 텍스트로 사용 |
| OCR (LightOnOCR) | 이미지 인식 | 2순위 | 누락된 부분 보완 |
| 슬라이드 이미지 | 시각 정보 | — | 레이아웃·다이어그램 파악 |

VLM 시스템 프롬프트에 우선순위가 명시되어 있어, OpenDataLoader 텍스트의  
표기·철자·내용을 OCR 결과보다 우선 반영합니다.
