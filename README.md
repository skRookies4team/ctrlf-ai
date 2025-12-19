# CTRL+F AI Gateway

CTRL+F 프로젝트의 AI Gateway 서비스입니다.
FastAPI 기반으로 RAG(Retrieval-Augmented Generation), LLM, 벡터 검색 서비스와 연동하여 기업 내부 정보보호 및 사규 안내 AI 기능을 제공합니다.

## 주요 기능

- **AI 채팅**: 사규/정책, 교육, HR 관련 질의응답
- **스트리밍 응답**: 실시간 토큰 스트리밍 지원
- **RAG 검색**: Milvus/RAGFlow 기반 문서 검색
- **FAQ 생성**: 문서 기반 FAQ 자동 생성
- **퀴즈 생성**: 교육 콘텐츠 기반 퀴즈 자동 생성
- **PII 마스킹**: 개인정보 자동 탐지 및 마스킹
- **의도 분류**: 사용자 질문 의도 분류 및 라우팅

## 연동 서비스

| 서비스 | 설명 |
|--------|------|
| **ctrlf-back** (Spring) | 백엔드 비즈니스 로직, HR/교육 데이터 |
| **ctrlf-front** (React) | 프론트엔드 UI |
| **Milvus** | 벡터 데이터베이스 (Phase 24) |
| **vLLM** | LLM 서비스 (Qwen2.5-7B-Instruct) |
| **Embedding Server** | 임베딩 생성 (BGE-M3) |

## 디렉터리 구조

```
ctrlf-ai/
├── app/
│   ├── main.py                     # FastAPI 앱 진입점
│   ├── api/v1/                     # API 엔드포인트
│   │   ├── chat.py                 # AI 채팅 API
│   │   ├── chat_stream.py          # 스트리밍 채팅 API
│   │   ├── faq.py                  # FAQ 생성 API
│   │   ├── quiz_generate.py        # 퀴즈 생성 API
│   │   ├── search.py               # RAG 검색 API
│   │   ├── video.py                # 영상 진도 API
│   │   ├── gap_suggestions.py      # RAG Gap 제안 API
│   │   ├── ingest.py               # 문서 인제스트 API
│   │   └── health.py               # 헬스체크 API
│   ├── clients/                    # 외부 서비스 클라이언트
│   │   ├── milvus_client.py        # Milvus 벡터 검색 클라이언트
│   │   ├── llm_client.py           # LLM 서비스 클라이언트
│   │   ├── ragflow_client.py       # RAGFlow 클라이언트
│   │   ├── backend_data_client.py  # 백엔드 데이터 클라이언트
│   │   └── personalization_client.py # 개인화 클라이언트
│   ├── services/                   # 비즈니스 로직
│   │   ├── chat_service.py         # 채팅 서비스
│   │   ├── chat_stream_service.py  # 스트리밍 채팅 서비스
│   │   ├── faq_service.py          # FAQ 생성 서비스
│   │   ├── quiz_generate_service.py # 퀴즈 생성 서비스
│   │   ├── intent_service.py       # 의도 분류 서비스
│   │   ├── pii_service.py          # PII 마스킹 서비스
│   │   ├── router_orchestrator.py  # 라우터 오케스트레이터
│   │   ├── rule_router.py          # 규칙 기반 라우터
│   │   ├── llm_router.py           # LLM 기반 라우터
│   │   └── guardrail_service.py    # 가드레일 서비스
│   ├── models/                     # Pydantic 모델
│   │   ├── chat.py                 # 채팅 모델
│   │   ├── intent.py               # 의도/라우팅 모델
│   │   ├── router_types.py         # 라우터 타입 정의
│   │   └── ...
│   └── core/                       # 핵심 유틸리티
│       ├── config.py               # 설정 관리
│       ├── logging.py              # 로깅 설정
│       ├── metrics.py              # 메트릭 수집
│       └── exceptions.py           # 예외 정의
├── tests/                          # 테스트
├── scripts/                        # 유틸리티 스크립트
├── docs/                           # 문서
├── .env.example                    # 환경변수 예시
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 빠른 시작

### 1. 환경 설정

```bash
# Python 가상환경 생성 (Python 3.12+)
python -m venv .venv

# 가상환경 활성화
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# Linux/macOS:
source .venv/bin/activate

# 의존성 설치
pip install -r requirements.txt
```

### 2. 환경변수 설정

```bash
cp .env.example .env
# .env 파일 수정
```

필수 환경변수:
```env
# LLM 서버 (vLLM)
LLM_BASE_URL=http://your-llm-server:port/v1

# Milvus 벡터 DB (Phase 24)
MILVUS_ENABLED=true
MILVUS_HOST=your-milvus-server
MILVUS_PORT=19530
```

### 3. 서버 실행

```bash
# 개발 모드
uvicorn app.main:app --reload --port 8000

# 프로덕션 모드
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 4. 확인

```bash
# 헬스체크
curl http://localhost:8000/health

# API 문서
# Swagger UI: http://localhost:8000/docs
# ReDoc: http://localhost:8000/redoc
```

## API 엔드포인트

### 채팅 API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/ai/chat` | AI 채팅 응답 생성 |
| POST | `/ai/chat/stream` | 스트리밍 채팅 응답 |

### FAQ/퀴즈 API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/ai/faq/draft` | FAQ 초안 생성 |
| POST | `/ai/faq/draft/batch` | FAQ 배치 생성 |
| POST | `/ai/quiz/generate` | 퀴즈 생성 |

### 검색 API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/ai/search` | RAG 문서 검색 |

### 헬스체크 API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/health` | Liveness 체크 |
| GET | `/health/ready` | Readiness 체크 |

### Internal RAG API (Phase 25)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/internal/rag/index` | 문서 인덱싱 요청 |
| POST | `/internal/rag/delete` | 문서 삭제 요청 |
| GET | `/internal/jobs/{jobId}` | 작업 상태 조회 |

## 채팅 API 상세

### POST /ai/chat

```json
// Request
{
  "session_id": "chat-session-123",
  "user_id": "employee-456",
  "user_role": "EMPLOYEE",
  "department": "HR",
  "domain": "POLICY",
  "messages": [
    {"role": "user", "content": "연차 이월 규정이 어떻게 되나요?"}
  ]
}

// Response
{
  "answer": "연차 이월 규정은...",
  "sources": [
    {
      "doc_id": "HR-001",
      "title": "인사규정",
      "snippet": "...",
      "score": 0.95
    }
  ],
  "meta": {
    "user_role": "EMPLOYEE",
    "route": "RAG_INTERNAL",
    "intent": "POLICY_QA",
    "domain": "POLICY",
    "rag_used": true,
    "latency_ms": 1500
  }
}
```

### POST /ai/chat/stream

NDJSON 형식의 스트리밍 응답:

```json
{"type":"meta","route":"RAG_INTERNAL","intent":"POLICY_QA"}
{"type":"token","content":"연차"}
{"type":"token","content":" 이월"}
{"type":"token","content":" 규정은"}
{"type":"done","sources":[...],"latency_ms":1500}
```

## Internal RAG API 상세 (Phase 25)

### POST /internal/rag/index

문서를 다운로드하여 Milvus에 인덱싱합니다.

```json
// Request
{
  "documentId": "DOC-001",
  "versionNo": 1,
  "title": "인사규정 v2.0",
  "domain": "POLICY",
  "fileUrl": "https://storage.example.com/docs/hr-policy.pdf",
  "requestedBy": "admin-001",
  "jobId": "job-uuid-1234"
}

// Response (202 Accepted)
{
  "jobId": "job-uuid-1234",
  "status": "queued",
  "message": "Indexing job job-uuid-1234 has been queued"
}
```

### POST /internal/rag/delete

Milvus에서 문서를 삭제합니다.

```json
// Request
{
  "documentId": "DOC-001",
  "versionNo": 1  // 선택: 없으면 전체 버전 삭제
}

// Response
{
  "status": "completed",
  "deletedCount": 15,
  "message": "Deleted 15 chunks for document DOC-001"
}
```

### GET /internal/jobs/{jobId}

작업 상태를 조회합니다.

```json
// Response
{
  "jobId": "job-uuid-1234",
  "status": "running",  // queued | running | completed | failed
  "documentId": "DOC-001",
  "versionNo": 1,
  "progress": "embedding",  // downloading | extracting | chunking | embedding | upserting | cleaning
  "chunksProcessed": 15,
  "errorMessage": null,
  "createdAt": "2025-01-15T10:30:00Z",
  "updatedAt": "2025-01-15T10:30:05Z"
}
```

### curl 예시

```bash
# 1. 문서 인덱싱 요청
curl -X POST http://localhost:8000/internal/rag/index \
  -H "Content-Type: application/json" \
  -d '{
    "documentId": "DOC-001",
    "versionNo": 1,
    "title": "인사규정 v2.0",
    "domain": "POLICY",
    "fileUrl": "https://storage.example.com/docs/hr-policy.pdf",
    "requestedBy": "admin-001",
    "jobId": "job-uuid-1234"
  }'

# 2. 작업 상태 폴링
curl http://localhost:8000/internal/jobs/job-uuid-1234

# 3. 문서 전체 삭제
curl -X POST http://localhost:8000/internal/rag/delete \
  -H "Content-Type: application/json" \
  -d '{
    "documentId": "DOC-001"
  }'

# 4. 특정 버전만 삭제
curl -X POST http://localhost:8000/internal/rag/delete \
  -H "Content-Type: application/json" \
  -d '{
    "documentId": "DOC-001",
    "versionNo": 1
  }'
```

### 인덱싱 파이프라인

새 버전 인덱싱 시 이전 버전 자동 삭제 보장:

1. `fileUrl`에서 파일 다운로드
2. 텍스트 추출 및 청킹
3. 임베딩 생성 (BGE-M3)
4. Milvus에 upsert
5. **upsert 성공 후** 이전 버전 삭제 (`version_no < current`)

> **주의**: upsert 실패 시 이전 버전 삭제는 실행되지 않음 (데이터 안전 보장)

## 환경변수

### 기본 설정

| 변수명 | 설명 | 기본값 |
|--------|------|--------|
| `APP_NAME` | 애플리케이션 이름 | `ctrlf-ai-gateway` |
| `APP_ENV` | 실행 환경 | `local` |
| `LOG_LEVEL` | 로그 레벨 | `INFO` |

### LLM 설정

| 변수명 | 설명 | 기본값 |
|--------|------|--------|
| `LLM_BASE_URL` | LLM 서버 URL | - |
| `LLM_MODEL_NAME` | LLM 모델명 | `Qwen/Qwen2.5-7B-Instruct` |

### Milvus 설정 (Phase 24)

| 변수명 | 설명 | 기본값 |
|--------|------|--------|
| `MILVUS_ENABLED` | Milvus 사용 여부 | `false` |
| `MILVUS_HOST` | Milvus 서버 호스트 | `localhost` |
| `MILVUS_PORT` | Milvus 서버 포트 | `19530` |
| `MILVUS_COLLECTION_NAME` | 컬렉션 이름 | `ragflow_chunks` |
| `MILVUS_TOP_K` | 검색 결과 수 | `5` |
| `EMBEDDING_MODEL_NAME` | 임베딩 모델 | `BAAI/bge-m3` |
| `EMBEDDING_DIMENSION` | 임베딩 차원 | `1024` |

### RAGFlow 설정

| 변수명 | 설명 | 기본값 |
|--------|------|--------|
| `RAGFLOW_BASE_URL` | RAGFlow URL | - |
| `RAGFLOW_API_KEY` | RAGFlow API Key | - |
| `RAGFLOW_TIMEOUT_SEC` | 타임아웃 (초) | `10.0` |

### PII 마스킹 설정

| 변수명 | 설명 | 기본값 |
|--------|------|--------|
| `PII_BASE_URL` | PII 서비스 URL | - |
| `PII_ENABLED` | PII 마스킹 활성화 | `true` |

### 백엔드 연동

| 변수명 | 설명 | 기본값 |
|--------|------|--------|
| `BACKEND_BASE_URL` | 백엔드 URL | - |
| `BACKEND_API_TOKEN` | API 인증 토큰 | - |

### 라우터 설정

| 변수명 | 설명 | 기본값 |
|--------|------|--------|
| `ROUTER_USE_LLM` | LLM 라우터 사용 | `true` |
| `ROUTER_RULE_CONFIDENCE_THRESHOLD` | 규칙 라우터 신뢰도 임계값 | `0.85` |

## 테스트

```bash
# 전체 테스트
pytest

# 상세 출력
pytest -v

# 특정 테스트
pytest tests/test_milvus_client.py -v

# 커버리지
pytest --cov=app tests/
```

## Docker

### 빌드 및 실행

```bash
# 이미지 빌드
docker build -t ctrlf-ai:latest .

# 컨테이너 실행
docker run -p 8000:8000 --env-file .env ctrlf-ai:latest
```

### Docker Compose

```bash
# 서비스 시작
docker compose up -d

# 로그 확인
docker compose logs -f

# 서비스 종료
docker compose down
```

## 아키텍처

```
┌─────────────┐     ┌─────────────────────────────────────┐
│  Frontend   │────▶│         AI Gateway (FastAPI)        │
│  (React)    │     │                                     │
└─────────────┘     │  ┌─────────────────────────────┐    │
                    │  │     Router Orchestrator      │    │
┌─────────────┐     │  │  (Rule Router + LLM Router)  │    │
│  Backend    │────▶│  └─────────────────────────────┘    │
│  (Spring)   │     │                │                    │
└─────────────┘     │  ┌─────────────▼─────────────┐      │
                    │  │      Chat Service          │      │
                    │  │  (PII, Intent, RAG, LLM)   │      │
                    │  └─────────────┬─────────────┘      │
                    └────────────────┼────────────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
              ┌──────────┐    ┌──────────┐    ┌──────────┐
              │  Milvus  │    │   vLLM   │    │ Embedding│
              │ (Vector) │    │  (Chat)  │    │ (BGE-M3) │
              └──────────┘    └──────────┘    └──────────┘
```

## 개발 히스토리

- **Phase 24**: Milvus 벡터 검색 통합
- **Phase 23**: 개인화 (Sub-Intent Q1~Q20)
- **Phase 22**: Router Orchestrator
- **Phase 21**: Intent Router (Rule + LLM)
- **Phase 20**: FAQ 배치/캐시 고도화
- **Phase 19**: FAQ 생성 서비스
- **Phase 18**: RAGFlow 검색 연동
- **Phase 14**: RAG Gap 탐지
- **Phase 12**: Fallback 전략
- **Phase 11**: Backend Data 연동
- **Phase 10**: 역할×도메인×의도 라우팅
- **Phase 4**: PII 마스킹 + 의도 분류

## 라이선스

Private - CTRL+F Team
