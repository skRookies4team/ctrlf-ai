# Document Ingestion Service

PDF 문서 전처리·청킹·임베딩 Ingestion Service

## 프로젝트 개요

사내 PDF/HWP/DOCX/PPTX 문서를 다음 단계까지 자동 처리하는 Ingestion Service입니다:

1. 파일 형식 통일 (HWP/DOCX/PPTX → PDF)
2. 텍스트 추출 (Parser)
3. 텍스트 클리닝 (Cleaner)
4. 구조적 정리 (Structure Normalizer)
5. 청킹 (Chunking)
6. 청킹 품질 평가 (Evaluator)
7. 임베딩 (Embedder - 향후 구현)
8. 벡터DB 저장 (VectorStore - 향후 구현)

## 디렉토리 구조

```
chunking/
├── core/                    # 핵심 라이브러리
│   ├── models.py           # 데이터 모델
│   ├── parser.py           # PDF 파서
│   ├── cleaner.py          # 텍스트 클리너
│   ├── structure.py        # 구조 정규화
│   ├── chunker.py          # 청킹
│   ├── evaluator.py        # 품질 평가
│   ├── embedder.py         # 임베딩 (stub)
│   ├── vector_store.py     # 벡터 스토어 (stub)
│   └── pipeline.py         # 파이프라인 오케스트레이션
├── app/                     # FastAPI 애플리케이션
│   ├── main.py             # 메인 앱
│   ├── routers/            # API 라우터
│   │   ├── ingest.py       # Ingest 엔드포인트
│   │   └── reports.py      # Reports 엔드포인트
│   └── schemas/            # Pydantic 스키마
│       ├── ingest.py
│       └── reports.py
├── data/                    # 데이터 저장소
│   ├── files/              # 업로드된 파일
│   └── reports/            # ChunkingReport JSONL
├── requirements.txt
└── README.md
```

## 설치 및 실행

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. 서버 실행

```bash
# 개발 모드 (auto-reload)
python app/main.py

# 또는 uvicorn 직접 실행
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. API 문서 확인

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## API 엔드포인트

### Health Check

```bash
GET /api/v1/ingest/health
```

### 파일 업로드 및 처리

```bash
POST /api/v1/ingest/file
Content-Type: multipart/form-data

Parameters:
- file: PDF 파일 (required)
- max_chars: 청크 최대 문자 수 (default: 1000)
- overlap_chars: 청크 간 겹침 문자 수 (default: 200)

Response:
{
  "success": true,
  "ingest_id": "uuid",
  "file_name": "example.pdf",
  "message": "File processed successfully",
  "num_chunks": 10,
  "status": "OK",
  "reasons": ["All checks passed..."]
}
```

### 파일 처리 (상세 정보 포함)

```bash
POST /api/v1/ingest/file/detail
Content-Type: multipart/form-data

Parameters:
- file: PDF 파일 (required)
- max_chars: 청크 최대 문자 수 (default: 1000)
- overlap_chars: 청크 간 겹침 문자 수 (default: 200)

Response: (청크 데이터 포함)
```

### 리포트 목록 조회

```bash
GET /api/v1/ingest/reports?limit=100&offset=0&status=OK

Query Parameters:
- limit: 반환할 리포트 수 (default: 100, max: 1000)
- offset: 시작 위치 (default: 0)
- status: 상태 필터 (OK|WARN|FAIL, optional)
```

### 특정 리포트 조회

```bash
GET /api/v1/ingest/reports/{ingest_id}
```

## 사용 예시

### Python

```python
import requests

# 파일 업로드
with open("document.pdf", "rb") as f:
    files = {"file": f}
    data = {"max_chars": 1000, "overlap_chars": 200}
    response = requests.post(
        "http://localhost:8000/api/v1/ingest/file",
        files=files,
        data=data
    )
    result = response.json()
    print(f"Ingest ID: {result['ingest_id']}")
    print(f"Status: {result['status']}")

# 리포트 조회
ingest_id = result['ingest_id']
response = requests.get(
    f"http://localhost:8000/api/v1/ingest/reports/{ingest_id}"
)
report = response.json()
print(report)
```

### cURL

```bash
# 파일 업로드
curl -X POST "http://localhost:8000/api/v1/ingest/file" \
  -F "file=@document.pdf" \
  -F "max_chars=1000" \
  -F "overlap_chars=200"

# 리포트 목록
curl "http://localhost:8000/api/v1/ingest/reports?limit=10&status=OK"

# 특정 리포트
curl "http://localhost:8000/api/v1/ingest/reports/{ingest_id}"
```

## ChunkingReport 필드

각 파일 처리 후 생성되는 리포트에는 다음 정보가 포함됩니다:

- `ingest_id`: 고유 ID
- `file_name`: 파일명
- `file_path`: 파일 경로
- `raw_text_len`: 원본 텍스트 길이
- `cleaned_text_len`: 클리닝된 텍스트 길이
- `num_chunks`: 청크 개수
- `chunk_lengths`: 각 청크의 길이 리스트
- `status`: 상태 (OK | WARN | FAIL)
- `reasons`: 상태 판정 이유
- `chunk_strategy`: 청킹 전략 (character_window)
- `max_chars`: 청크 최대 문자 수
- `overlap_chars`: 청크 간 겹침 문자 수
- `created_at`: 생성 시각

## 향후 확장 계획

1. **Embedder 구현**
   - OpenAI Embedding API 통합
   - Sentence Transformers 지원
   - 커스텀 임베딩 모델 지원

2. **VectorStore 구현**
   - Chroma DB 통합
   - Pinecone 지원
   - Qdrant 지원

3. **Chunker 확장**
   - Heading 기반 청킹
   - Semantic 청킹
   - 문장 단위 청킹

4. **구조 분석 강화**
   - 목차 자동 추출
   - 섹션 계층 구조 분석
   - 표/그림 메타데이터 추출

5. **리포트 대시보드**
   - 웹 UI 대시보드
   - 통계 분석
   - 품질 모니터링

## 개발 가이드

### 코드 스타일

- PEP8 준수
- 타입 힌트 필수
- Docstring 작성 권장

### 로깅

모든 주요 동작은 로그로 기록됩니다:
- 콘솔 출력
- `ingestion_service.log` 파일

### 테스트

```bash
# 향후 pytest로 테스트 추가 예정
pytest tests/
```

## 라이센스

Internal Use Only

## 문의

문제가 발생하거나 기능 요청이 있으면 이슈를 등록해주세요.
