# API 인벤토리

> **최종 수정일**: 2025-12-31
> **버전**: 2.0 (Phase 42 이후 정리)

---

## 개요

이 문서는 ctrlf-ai (FastAPI) 서버에서 **실제로 제공하는 API 엔드포인트** 목록입니다.

> **참고**: 영상 진행률, 관리자, 스크립트 편집 등의 API는 ctrlf-back (Spring 백엔드)에서 제공합니다.

---

## 분류 기준

| 라벨 | 정의 |
|------|------|
| **ACTIVE** | 현재 사용 중인 API |
| **DEPRECATED** | 제거됨 (410 Gone 반환) |
| **INTERNAL** | 내부 시스템 간 통신용 (X-Internal-Token 필요) |

---

## 1. 헬스체크

| Method | Path | 라벨 | 설명 |
|--------|------|------|------|
| GET | `/health` | **ACTIVE** | 서버 상태 확인 (K8s Liveness) |
| GET | `/health/ready` | **ACTIVE** | 준비 상태 확인 (K8s Readiness) |

**파일**: `app/api/v1/health.py`

---

## 2. 채팅 API

| Method | Path | 라벨 | 설명 |
|--------|------|------|------|
| POST | `/ai/chat/messages` | **ACTIVE** | 동기 채팅 응답 생성 |
| POST | `/ai/chat/stream` | **ACTIVE** | 스트리밍 채팅 응답 (NDJSON) |

**파일**: `app/api/v1/chat.py`, `app/api/v1/chat_stream.py`

**호출 주체**: Spring 백엔드 (ctrlf-back)

### 주요 특징
- RAGFlow 기반 RAG 검색
- Phase 42: RAGFlow 장애 시 503 반환 (fallback 없음)
- 스트리밍: NDJSON 형식 (meta → token → done/error)

---

## 3. 부가 기능 API

| Method | Path | 라벨 | 설명 |
|--------|------|------|------|
| POST | `/ai/gap/policy-edu/suggestions` | **ACTIVE** | RAG Gap 보완 제안 |
| POST | `/ai/quiz/generate` | **ACTIVE** | 퀴즈 생성 |
| POST | `/ai/faq/generate` | **ACTIVE** | FAQ 생성 |

**파일**: `app/api/v1/gap_suggestions.py`, `app/api/v1/quiz_generate.py`, `app/api/v1/faq.py`

**호출 주체**: Spring 백엔드 (ctrlf-back)

---

## 4. Internal API (Backend → AI)

> **인증**: `X-Internal-Token` 헤더 필수

### 4.1 SourceSet 오케스트레이션

| Method | Path | 라벨 | 설명 |
|--------|------|------|------|
| POST | `/internal/ai/source-sets/{sourceSetId}/start` | **INTERNAL** | 소스셋 처리 시작 (문서 → 스크립트) |
| GET | `/internal/ai/source-sets/{sourceSetId}/status` | **INTERNAL** | 처리 상태 조회 |

**파일**: `app/api/v1/source_sets.py`

### 4.2 렌더 잡 (영상 생성)

| Method | Path | 라벨 | 설명 |
|--------|------|------|------|
| POST | `/internal/ai/render-jobs` | **INTERNAL** | 렌더 잡 생성/시작 |
| POST | `/ai/video/job/{job_id}/start` | **INTERNAL** | 잡 시작 (레거시 호환) |
| POST | `/ai/video/job/{job_id}/retry` | **INTERNAL** | 잡 재시도 (레거시 호환) |

**파일**: `app/api/v1/render_jobs.py`

### 4.3 RAG 문서 Ingest (사내규정)

| Method | Path | 라벨 | 설명 |
|--------|------|------|------|
| POST | `/internal/ai/rag-documents/ingest` | **INTERNAL** | 사내규정 문서 RAGFlow ingest |
| POST | `/internal/ai/callbacks/ragflow/ingest` | **INTERNAL** | RAGFlow 콜백 수신 |

**파일**: `app/api/v1/rag_documents.py`

### 4.4 피드백

| Method | Path | 라벨 | 설명 |
|--------|------|------|------|
| POST | `/internal/ai/feedback` | **INTERNAL** | 사용자 피드백 저장 |

**파일**: `app/api/v1/feedback.py`

---

## 5. WebSocket

| Method | Path | 라벨 | 설명 |
|--------|------|------|------|
| WS | `/ws/videos/{video_id}/render-progress` | **ACTIVE** | 렌더링 진행률 실시간 전송 |

**파일**: `app/api/v1/ws_render_progress.py`

---

## 6. Deprecated API (410 Gone)

> Phase 42에서 제거됨. Direct Milvus 인덱싱 → RAGFlow 경유로 대체.

| Method | Path | 상태 | 대체 경로 |
|--------|------|------|-----------|
| POST | `/internal/rag/index` | 410 Gone | `/internal/ai/source-sets/{id}/start` |
| POST | `/internal/rag/delete` | 410 Gone | RAGFlow에서 직접 삭제 |
| GET | `/internal/jobs/{job_id}` | 410 Gone | `/internal/ai/source-sets/{id}/status` |

**파일**: `app/api/v1/internal_rag.py`

---

## API 라우터 등록 현황

`app/api/v1/__init__.py`에서 등록된 라우터:

```python
# 활성 라우터
from app.api.v1.health import router as health_router
from app.api.v1.chat import router as chat_router
from app.api.v1.chat_stream import router as chat_stream_router
from app.api.v1.gap_suggestions import router as gap_suggestions_router
from app.api.v1.quiz_generate import router as quiz_router
from app.api.v1.faq import router as faq_router
from app.api.v1.internal_rag import router as internal_rag_router
from app.api.v1.render_jobs import internal_router, ai_router
from app.api.v1.ws_render_progress import router as ws_router
from app.api.v1.source_sets import router as source_sets_router
from app.api.v1.rag_documents import router as rag_documents_router
from app.api.v1.feedback import router as feedback_router
```

---

## 참고: 백엔드(ctrlf-back)에서 제공하는 API

다음 API들은 AI 서버가 아닌 **Spring 백엔드**에서 제공합니다:

- **영상 진행률**: `/api/video/play/start`, `/api/video/progress`, `/api/video/complete`
- **관리자 API**: `/api/admin/education/*`
- **스크립트 관리**: `/api/scripts/*`
- **영상 관리**: `/api/videos/*`, `/api/v2/videos/*`
- **렌더 잡 조회**: `/api/render-jobs/*`

---

## 변경 이력

| 날짜 | 버전 | 내용 |
|------|------|------|
| 2025-12-31 | 2.0 | 실제 AI 서버 API만 반영, 백엔드 API 분리 |
| 2025-12-19 | 1.0 | 초기 작성 |
