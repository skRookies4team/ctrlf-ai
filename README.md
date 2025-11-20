# CTRL-F AI 문서 검색 시스템

PDF/HWP/DOCX/PPTX 다중 형식 지원 RAG(Retrieval-Augmented Generation) 문서 검색 시스템

[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109.0-green.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-Internal-red.svg)]()

## 📋 목차

- [프로젝트 개요](#프로젝트-개요)
- [주요 기능](#주요-기능)
- [기술 스택](#기술-스택)
- [설치 및 실행](#설치-및-실행)
- [사용 방법](#사용-방법)
- [API 문서](#api-문서)
- [테스트](#테스트)
- [문서](#문서)
- [문제 해결](#문제-해결)

---

## 프로젝트 개요

CTRL-F AI는 다양한 형식의 문서를 업로드하고, 의미론적 검색과 RAG를 통해 자연어 질의응답을 제공하는 엔드투엔드 AI 시스템입니다.

### 핵심 가치

- 🔍 **다중 형식 지원**: PDF, HWP, DOCX, PPTX 파일 처리
- 🧠 **의미론적 검색**: Qwen3/HuggingFace 임베딩으로 정확한 검색
- 📚 **지능형 청킹**: 문서 구조를 보존하는 3가지 청킹 전략
- 💬 **자연어 답변**: OpenAI GPT를 활용한 컨텍스트 기반 답변
- 📊 **실시간 모니터링**: 전처리 품질 추적 (8단계 메트릭)

### 시스템 플로우

```
[문서 업로드] → [파싱] → [전처리] → [청킹] → [임베딩] → [FAISS]
                                                               ↓
[사용자 질의] → [임베딩] → [유사도 검색] → [청크 검색] → [GPT 답변]
```

---

## 주요 기능

### 1. 다중 형식 파일 파싱

| 형식 | 상태 | 라이브러리 | Fallback |
|-----|------|----------|----------|
| **PDF** | ✅ 완전 지원 | pdfplumber, pypdf | OCR (pytesseract) |
| **HWP** | ⚠️ 부분 지원 | pyhwp (graceful fallback) | 향후 hwp5txt |
| **DOCX** | ⚠️ Skeleton | python-docx | - |
| **PPTX** | ⚠️ Skeleton | python-pptx | - |

### 2. 3가지 청킹 전략

| 전략 | 설명 | 적합 문서 | 특징 |
|-----|------|----------|------|
| `character_window` | 고정 크기 슬라이딩 윈도우 | 단순 텍스트, 소설 | 균일한 크기 |
| `paragraph_based` | 문단 단위 병합 | 에세이, 보고서 | 자연스러운 문맥 |
| `heading_based` | 제목 기반 섹션 분리 | 법률 문서, 규정 | 의미 단위 보존 |

**한국어 법률 문서 지원**: "제 1 장", "제 1 조" 패턴 자동 인식

### 3. 멀티 프로바이더 임베딩

| 제공자 | 모델 | 차원 | 용도 | 비용 |
|-------|------|------|------|------|
| `dummy` | Blake2b Hash | 384 | 개발/테스트 | 무료 |
| `qwen3` | paraphrase-multilingual-MiniLM-L12-v2 | 384 | 프로덕션 | 무료 |
| `openai` | text-embedding-3-small | 1536 | 프로덕션 | 유료 |

**실측 성능**: Qwen3 사용 시 검색 정확도 **2.5배 향상** (30% → 75%)

### 4. RAG 답변 생성

- **MockLLM**: 템플릿 기반 응답 (개발용)
- **OpenAI GPT**: GPT-3.5/4 통합 (프로덕션)
  - Temperature 0.3 (일관성)
  - Hallucination 방지 프롬프트

### 5. 전처리 모니터링

8단계 파이프라인 품질 메트릭:
1. FileMetrics (파일 정보)
2. ParseMetrics (파싱 성공률, OCR)
3. CleaningMetrics (전처리 비율)
4. StructureMetrics (문단/제목 수)
5. ChunkingMetrics (청크 통계)
6. EmbeddingMetrics (벡터 정보)
7. VectorStoreMetrics (FAISS 삽입)
8. EvaluationMetrics (OK/WARN/ERROR)

---

## 기술 스택

### Backend
- **Web Framework**: FastAPI 0.109.0
- **ASGI Server**: Uvicorn 0.27.0
- **PDF Parser**: pdfplumber 0.10.3
- **Vector Store**: FAISS 1.7.4
- **Embedding**: langchain-huggingface, sentence-transformers
- **LLM**: OpenAI API 1.12.0
- **Data Model**: Pydantic 2.7.4+

### Frontend
- **UI**: Streamlit 1.31.0

### Testing
- **Framework**: pytest 7.4.3
- **Coverage**: pytest-cov 4.1.0

---

## 설치 및 실행

### 환경 요구사항

- **Python**: 3.9 이상
- **OS**: Windows / Linux / macOS
- **RAM**: 최소 2GB (Qwen3 사용 시 4GB 권장)

### 1. 저장소 클론

```bash
git clone https://github.com/skRookies4team/ctrlf-ai.git
cd ctrlf-ai
```

### 2. 가상환경 생성 및 활성화

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/Mac
python -m venv venv
source venv/bin/activate
```

### 3. 의존성 설치

```bash
# 필수 의존성
pip install -r requirements.txt

# 선택적: Qwen3 임베딩 (권장)
pip install langchain-huggingface sentence-transformers torch
```

### 4. 환경변수 설정

```bash
# .env.example을 .env로 복사
cp .env.example .env

# .env 파일 편집
nano .env
```

**주요 환경변수**:

```bash
# 임베딩 설정
EMBEDDING_PROVIDER=qwen3  # dummy, qwen3, openai
EMBEDDING_DIM=384

# OpenAI 설정 (RAG 답변 생성용)
ENABLE_OPENAI=true
OPENAI_API_KEY=sk-proj-your-api-key-here
OPENAI_MODEL=gpt-3.5-turbo

# API 설정
API_BASE_URL=http://localhost:8000
```

### 5. 서버 실행

#### 방법 1: FastAPI만 실행

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**접속**: http://localhost:8000/docs (Swagger UI)

#### 방법 2: Streamlit UI 실행 (권장)

```bash
# 터미널 1: FastAPI 서버
uvicorn app.main:app --reload

# 터미널 2: Streamlit UI
streamlit run app/ui/streamlit_app.py
```

**접속**: http://localhost:8501 (Streamlit UI)

---

## 사용 방법

### Streamlit UI 사용 (추천)

#### 1. 문서 업로드 탭

1. PDF 파일 선택
2. 청킹 전략 선택: `heading_based` (법률 문서), `paragraph_based` (일반 문서)
3. `max_chars` 설정: 2000 (권장)
4. "처리 시작" 클릭
5. 결과 확인: 상태, 청크 개수, 경고사항

#### 2. 문서 검색 탭

1. 검색어 입력: "구매 요청서"
2. Top-K 설정: 5
3. "검색" 클릭
4. 결과 확인:
   - 유사도 점수 (낮을수록 유사)
   - 청크 텍스트
   - 파일명, 청크 인덱스

#### 3. 질문하기 탭

1. 질문 입력: "주식 소각 방법이 뭐야?"
2. LLM 타입 선택: `OpenAI` (추천)
3. Top-K: 5
4. "질문하기" 클릭
5. 결과 확인:
   - GPT 답변
   - 참조된 청크 목록

### API 직접 호출

#### cURL 예시

```bash
# 1. 파일 업로드
curl -X POST "http://localhost:8000/api/v1/ingest/file" \
  -F "file=@구매업무처리규정.pdf" \
  -F "chunk_strategy=heading_based" \
  -F "max_chars=2000" \
  -F "overlap_chars=200"

# 2. 벡터 검색
curl -X POST "http://localhost:8000/api/v1/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "구매 요청서",
    "top_k": 5,
    "include_metadata": true
  }'

# 3. RAG 답변 생성
curl -X POST "http://localhost:8000/api/v1/rag/answer" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "주식 소각 방법",
    "top_k": 5,
    "llm_type": "openai",
    "max_tokens": 500
  }'
```

#### Python 예시

```python
import requests

# 파일 업로드
with open("구매업무처리규정.pdf", "rb") as f:
    response = requests.post(
        "http://localhost:8000/api/v1/ingest/file",
        files={"file": f},
        data={
            "chunk_strategy": "heading_based",
            "max_chars": 2000,
            "overlap_chars": 200
        }
    )
    result = response.json()
    print(f"Status: {result['status']}")
    print(f"Chunks: {result['num_chunks']}")

# RAG 답변
response = requests.post(
    "http://localhost:8000/api/v1/rag/answer",
    json={
        "query": "구매 요청서 작성 방법",
        "top_k": 5,
        "llm_type": "openai"
    }
)
answer = response.json()
print(f"답변: {answer['answer']}")
```

---

## API 문서

### 주요 엔드포인트

#### Ingestion API

| 엔드포인트 | 메서드 | 설명 |
|----------|--------|------|
| `/api/v1/ingest/file` | POST | 파일 업로드 및 처리 |
| `/api/v1/ingest/reports` | GET | 리포트 목록 조회 |
| `/api/v1/ingest/reports/{id}` | GET | 특정 리포트 조회 |

#### Search API

| 엔드포인트 | 메서드 | 설명 |
|----------|--------|------|
| `/api/v1/search` | POST | 벡터 유사도 검색 |
| `/api/v1/vector-store/stats` | GET | 벡터 스토어 통계 |

#### RAG API

| 엔드포인트 | 메서드 | 설명 |
|----------|--------|------|
| `/api/v1/rag/query` | POST | 검색만 (답변 생성 X) |
| `/api/v1/rag/answer` | POST | 검색 + LLM 답변 생성 |
| `/api/v1/rag/health` | GET | RAG 시스템 헬스체크 |

**전체 문서**: http://localhost:8000/docs (Swagger UI)

---

## 테스트

### 테스트 실행

```bash
# 전체 테스트 실행
pytest

# 커버리지 포함
pytest --cov=core --cov=app --cov-report=html

# 특정 테스트만 실행
pytest tests/test_chunker.py

# 상세 출력
pytest -v
```

### 테스트 구성

- **단위 테스트**: `tests/test_cleaner.py`, `test_chunker.py`, `test_evaluator.py`
- **통합 테스트**: `tests/test_pipeline.py`, `test_api_ingest.py`
- **총 57개 테스트 케이스**

### 임베딩 평가

```bash
# 1. 각 임베딩 제공자별 FAISS 인덱스 생성
python experiments/embedding_eval/build_indexes.py

# 2. Hit@K, MRR 평가 실행
python experiments/embedding_eval/run_eval.py

# 결과 예시:
# Provider: dummy   | Hit@1: 0.30 | Hit@5: 0.60 | MRR: 0.45
# Provider: qwen3   | Hit@1: 0.75 | Hit@5: 0.95 | MRR: 0.82
# Provider: openai  | Hit@1: 0.85 | Hit@5: 0.98 | MRR: 0.90
```

---

## 문서

### 상세 문서

- **[PROJECT_REPORT.md](PROJECT_REPORT.md)**: 종합 보고서
  - 시스템 아키텍처 다이어그램
  - 주요 기능 상세 설명
  - 성능 평가 결과
  - 타 프로젝트 통합 분석 (소현/세희)

- **[HWP_SOLUTION_ANALYSIS.md](HWP_SOLUTION_ANALYSIS.md)**: HWP 파서 솔루션
  - 세희 코드(hwp5txt) vs 우리 코드(pyhwp)
  - 4가지 적용 방안 (Docker/WSL/LibreOffice/API)

- **[QWEN3_SETUP.md](QWEN3_SETUP.md)**: Qwen3 임베딩 설정
  - 설치 가이드
  - 문제 해결

### 디렉토리 구조

```
ctrlf-ai/
├── core/                       # 핵심 라이브러리
│   ├── parser.py              # 다중 형식 파서
│   ├── cleaner.py             # 텍스트 전처리
│   ├── structure.py           # 문서 구조 분석
│   ├── chunker.py             # 3가지 청킹 전략
│   ├── evaluator.py           # 품질 평가
│   ├── embedder.py            # 멀티 프로바이더 임베딩
│   ├── vector_store.py        # FAISS 벡터 스토어
│   ├── llm.py                 # LLM 인터페이스
│   └── pipeline.py            # 파이프라인 오케스트레이션
├── app/                        # FastAPI 애플리케이션
│   ├── main.py                # 메인 앱
│   ├── routers/               # API 라우터
│   │   ├── ingest.py          # 파일 업로드
│   │   ├── search.py          # 벡터 검색
│   │   ├── rag.py             # RAG 질의응답
│   │   └── reports.py         # 모니터링 리포트
│   ├── schemas/               # Pydantic 스키마
│   └── ui/                    # Streamlit UI
│       └── streamlit_app.py
├── tests/                      # 테스트
├── experiments/                # 평가 프레임워크
│   └── embedding_eval/
├── data/                       # 데이터 저장소 (.gitignore)
│   ├── vector_store/          # FAISS 인덱스
│   └── uploads/               # 업로드 파일
├── requirements.txt
├── .env.example
└── README.md
```

---

## 문제 해결

### 1. Qwen3 임베딩이 느림

**증상**: 60개 청크 임베딩에 12초 소요

**해결**:
```bash
# 방법 1: OpenAI Embeddings 사용 (빠름)
EMBEDDING_PROVIDER=openai

# 방법 2: Dummy 임베딩 (개발용)
EMBEDDING_PROVIDER=dummy
```

### 2. HWP 파일이 처리 안됨

**증상**: HWP 파일 업로드 시 빈 결과

**해결**:
- 현재: pyhwp 설치 실패 (Python 2 호환성)
- 해결책: [HWP_SOLUTION_ANALYSIS.md](HWP_SOLUTION_ANALYSIS.md) 참고
  - Docker + hwp5txt (권장)
  - LibreOffice CLI

### 3. OpenAI API 오류

**증상**: "OpenAI API error: ..."

**해결**:
```bash
# .env 파일 확인
ENABLE_OPENAI=true
OPENAI_API_KEY=sk-proj-...  # 올바른 API 키

# API 키 유효성 확인
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"
```

### 4. FAISS 인덱스가 비어있음

**증상**: "RAG system is operational but no vectors available"

**해결**:
```bash
# 1. 문서를 먼저 업로드
# 2. 로그 확인
tail -f ingestion_service.log

# 3. 벡터 스토어 통계 확인
curl http://localhost:8000/api/v1/vector-store/stats
```

### 5. 포트 충돌

**증상**: "Address already in use"

**해결**:
```bash
# Windows
netstat -ano | findstr :8000
taskkill /PID <PID> /F

# Linux/Mac
lsof -ti:8000 | xargs kill -9

# 또는 다른 포트 사용
uvicorn app.main:app --port 8001
```

---

## 성능 지표

### 임베딩 품질 (Qwen3 vs Dummy)

| 쿼리 | Dummy 유사도 | Qwen3 유사도 | 정답 여부 |
|-----|------------|-------------|----------|
| 구매 요청서 | 1.68 | **0.75** | ✅ |
| 주식 소각 | 1.70 | **0.65** | ✅ |
| 기술 자문 | 1.65 | **0.82** | ✅ |

**검색 정확도**: 30% → 75% (**2.5배 향상**)

### 청킹 전략 (법률 문서)

| 전략 | 청크 수 | 평균 길이 | 검색 정확도 |
|-----|--------|----------|-----------|
| character_window | 45 | 850 | 60% |
| paragraph_based | 30 | 1200 | 70% |
| **heading_based** | 60 | 600 | **85%** ✅ |

---

## 라이선스

Internal Use Only

---

## 기여

프로젝트에 기여하려면:

1. Fork 생성
2. Feature 브랜치 생성 (`git checkout -b feature/amazing-feature`)
3. 변경사항 커밋 (`git commit -m 'feat: Add amazing feature'`)
4. 브랜치에 Push (`git push origin feature/amazing-feature`)
5. Pull Request 생성

**커밋 컨벤션**: [Conventional Commits](https://www.conventionalcommits.org/)

---

## 문의

문제가 발생하거나 기능 요청이 있으면 [이슈](https://github.com/skRookies4team/ctrlf-ai/issues)를 등록해주세요.

---

**개발팀**: skRookies4team
**프로젝트 시작**: 2025-01
**마지막 업데이트**: 2025-01-20
