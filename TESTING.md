# Testing Guide - Document Ingestion Service

이 문서는 전처리·청킹·임베딩 Ingestion Service의 테스트 방법을 설명합니다.

## 목차
- [환경 설정](#환경-설정)
- [의존성 설치](#의존성-설치)
- [테스트 데이터 준비](#테스트-데이터-준비)
- [테스트 실행](#테스트-실행)
- [수동 테스트](#수동-테스트)
- [트러블슈팅](#트러블슈팅)

---

## 환경 설정

### 1. Python 버전 확인
Python 3.9 ~ 3.11 권장 (3.10이 가장 안정적)

```bash
python -V
# 또는
python3 -V
```

### 2. 가상환경 생성 및 활성화

**Linux/Mac:**
```bash
python -m venv venv
source venv/bin/activate
```

**Windows:**
```cmd
python -m venv venv
venv\Scripts\activate
```

---

## 의존성 설치

### 1. Python 패키지 설치

프로젝트 루트에서 실행:

```bash
pip install -r requirements.txt
```

또는 개별 설치:

```bash
pip install fastapi uvicorn pydantic
pip install pypdf
pip install faiss-cpu numpy
pip install pdf2image pillow
pip install pytesseract
pip install python-multipart
pip install pytest pytest-cov
```

### 2. 시스템 패키지 설치 (OCR 관련)

OCR 기능을 사용하려면 Tesseract OCR과 Poppler가 필요합니다.

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-kor
sudo apt-get install -y poppler-utils
```

**macOS (Homebrew):**
```bash
brew install tesseract tesseract-lang
brew install poppler
```

**Windows:**
- Tesseract: https://github.com/UB-Mannheim/tesseract/wiki 에서 설치
- Poppler: https://github.com/oschwartz10612/poppler-windows/releases/ 에서 다운로드 및 PATH 설정

**OCR 설치를 건너뛰는 경우:**
OCR 의존성이 설치되지 않아도 대부분의 기능은 동작합니다. OCR은 텍스트 추출이 실패한 스캔 PDF에만 필요하므로, 일반 텍스트 PDF 테스트 시에는 선택사항입니다.

### 3. 필수 디렉토리 생성

```bash
mkdir -p data/files
mkdir -p data/reports
mkdir -p data/vector_store
```

---

## 테스트 데이터 준비

### 1. 샘플 PDF 생성

간단한 테스트용 PDF 파일을 생성하는 Python 스크립트:

```python
# tests/data/create_sample_pdf.py
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

def create_sample_pdf(filename):
    """간단한 테스트용 PDF 생성"""
    c = canvas.Canvas(filename, pagesize=letter)

    # 텍스트 추가
    c.drawString(100, 750, "Sample Document")
    c.drawString(100, 700, "This is a test PDF for ingestion service.")
    c.drawString(100, 650, "It contains multiple lines of text.")
    c.drawString(100, 600, "The chunking system will process this content.")

    c.save()
    print(f"Created: {filename}")

if __name__ == "__main__":
    create_sample_pdf("tests/data/sample.pdf")
```

실행:
```bash
pip install reportlab
python tests/data/create_sample_pdf.py
```

### 2. 또는 기존 PDF 사용

`tests/data/` 디렉토리에 테스트용 PDF 파일을 직접 복사해도 됩니다.

---

## 테스트 실행

### 1. pytest 유닛 테스트

**전체 테스트 실행:**
```bash
pytest
```

**간략한 출력 (quiet mode):**
```bash
pytest -q
```

**상세 출력 (verbose mode):**
```bash
pytest -v
```

**특정 테스트 파일만 실행:**
```bash
pytest tests/test_cleaner.py
pytest tests/test_chunker.py
pytest tests/test_evaluator.py
pytest tests/test_pipeline.py
pytest tests/test_api_ingest.py
```

**커버리지 리포트 생성:**
```bash
pytest --cov=core --cov=app --cov-report=html
```

커버리지 리포트는 `htmlcov/index.html`에서 확인 가능합니다.

### 2. 테스트 종류별 실행

**유닛 테스트만:**
```bash
pytest tests/test_cleaner.py tests/test_chunker.py tests/test_evaluator.py
```

**통합 테스트만:**
```bash
pytest tests/test_pipeline.py
```

**API 테스트만:**
```bash
pytest tests/test_api_ingest.py
```

---

## 수동 테스트

pytest 외에도 서버를 직접 띄워서 수동으로 테스트할 수 있습니다.

### 1. 서버 실행

```bash
uvicorn app.main:app --reload --port 8000
```

서버가 정상 실행되면:
```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

### 2. Swagger UI 확인

브라우저에서 접속:
```
http://localhost:8000/docs
```

모든 API 엔드포인트를 확인하고 직접 테스트할 수 있습니다.

### 3. curl을 이용한 API 테스트

**헬스체크:**
```bash
curl http://localhost:8000/health
```

**파일 업로드 (character_window):**
```bash
curl -X POST "http://localhost:8000/api/v1/ingest/file" \
  -F "file=@tests/data/sample.pdf"
```

**파일 업로드 (paragraph_based):**
```bash
curl -X POST "http://localhost:8000/api/v1/ingest/file" \
  -F "file=@tests/data/sample.pdf" \
  -F "chunk_strategy=paragraph_based"
```

**파일 업로드 (heading_based):**
```bash
curl -X POST "http://localhost:8000/api/v1/ingest/file" \
  -F "file=@tests/data/sample.pdf" \
  -F "chunk_strategy=heading_based"
```

**리포트 조회:**
```bash
curl "http://localhost:8000/api/v1/ingest/reports?limit=10"
```

**특정 리포트 조회:**
```bash
curl "http://localhost:8000/api/v1/ingest/reports/{ingest_id}"
```

**벡터 검색:**
```bash
curl -X POST "http://localhost:8000/api/v1/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "계정 관리",
    "top_k": 5
  }'
```

**RAG 쿼리:**
```bash
curl -X POST "http://localhost:8000/api/v1/rag/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "정보보안 계정 관리 규정",
    "top_k": 3,
    "include_context": true
  }'
```

**RAG 헬스체크:**
```bash
curl "http://localhost:8000/api/v1/rag/health"
```

**벡터 스토어 통계:**
```bash
curl "http://localhost:8000/api/v1/vector-store/stats"
```

### 4. 결과 확인

업로드 후 다음 파일들을 확인:

- `data/files/` - 업로드된 PDF 파일
- `data/reports/chunking_reports.jsonl` - 처리 리포트
- `data/vector_store/faiss.index` - FAISS 인덱스 파일
- `data/vector_store/metadata.jsonl` - 벡터 메타데이터

---

## 트러블슈팅

### 1. OCR 관련 에러

**증상:**
```
ImportError: No module named 'pytesseract'
```

**해결:**
```bash
pip install pytesseract pdf2image
```

시스템 패키지도 설치 필요:
```bash
# Ubuntu
sudo apt-get install tesseract-ocr poppler-utils

# macOS
brew install tesseract poppler
```

**OCR 없이 테스트:**
OCR이 필요 없는 텍스트 PDF만 사용하거나, `use_ocr_fallback=False` 파라미터로 OCR 비활성화

### 2. FAISS 관련 에러

**증상:**
```
ImportError: No module named 'faiss'
```

**해결:**
```bash
pip install faiss-cpu
```

### 3. pytest 미발견 에러

**증상:**
```
No tests ran in X.XXs
```

**해결:**
- 테스트 파일명이 `test_*.py` 패턴인지 확인
- 테스트 함수명이 `test_*` 패턴인지 확인
- `tests/` 디렉토리에 `__init__.py` 파일이 있는지 확인

### 4. 임포트 에러

**증상:**
```
ModuleNotFoundError: No module named 'core'
```

**해결:**
프로젝트 루트에서 실행:
```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
# Windows의 경우
set PYTHONPATH=%PYTHONPATH%;%cd%
```

또는 pytest 실행 시:
```bash
python -m pytest
```

### 5. 파일 경로 문제 (Windows)

**증상:**
경로 구분자 관련 에러

**해결:**
Python의 `pathlib.Path` 사용 또는 `os.path.join()` 사용

### 6. 포트 충돌

**증상:**
```
Error: [Errno 48] Address already in use
```

**해결:**
다른 포트 사용:
```bash
uvicorn app.main:app --reload --port 8001
```

기존 프로세스 종료:
```bash
# Linux/Mac
lsof -ti:8000 | xargs kill -9

# Windows
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```

---

## 테스트 체크리스트

프로젝트 배포 전 다음 항목들을 확인하세요:

- [ ] 모든 pytest 테스트 통과
- [ ] 서버가 정상적으로 시작됨
- [ ] Swagger UI 접근 가능
- [ ] 파일 업로드가 성공적으로 동작
- [ ] 리포트가 JSONL 파일에 저장됨
- [ ] 벡터 스토어에 임베딩 저장됨
- [ ] 검색 API가 정상 동작
- [ ] RAG 쿼리 API가 정상 동작
- [ ] 모든 청킹 전략(character_window, paragraph_based, heading_based)이 동작
- [ ] OCR fallback이 동작 (선택사항)

---

## 추가 리소스

- FastAPI 문서: https://fastapi.tiangolo.com/
- pytest 문서: https://docs.pytest.org/
- FAISS 문서: https://github.com/facebookresearch/faiss/wiki
- Tesseract OCR: https://github.com/tesseract-ocr/tesseract

---

## 문의

테스트 관련 문제가 있으면 프로젝트 이슈 트래커에 보고해주세요.
