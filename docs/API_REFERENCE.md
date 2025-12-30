# CTRL+F AI Gateway API Reference

> **목적**: 기업 내부 정보보호 AI 어시스턴트 (LLM + RAG 기반)
> **Base URL**: `http://{AI_GATEWAY_HOST}:{PORT}`
> **문서 버전**: 2025-12-31 v3.0 (실제 AI 서버 API만 반영)

---

## 문서 구조

이 문서는 두 가지 관점에서 API를 정의합니다:

| Part | 설명 | 구현 주체 |
|------|------|----------|
| **Part 1** | AI 서버가 제공하는 API | AI 서버 (Python/FastAPI) |
| **Part 2** | 백엔드가 AI를 위해 제공해야 할 API | 백엔드 (Spring) |

---

# Part 1: AI 서버가 제공하는 API

> 백엔드(Spring)가 AI 서버를 호출할 때 사용하는 API입니다.

## 1. API 인벤토리

### 1.1 활성 API (현재 사용 중)

| Category | Method | Endpoint | Status |
|----------|--------|----------|--------|
| Health | GET | `/health` | Active |
| Health | GET | `/health/ready` | Active |
| Chat | POST | `/ai/chat/messages` | Active |
| Chat | POST | `/ai/chat/stream` | Active |
| Quiz | POST | `/ai/quiz/generate` | Active |
| FAQ | POST | `/ai/faq/generate` | Active |
| Gap | POST | `/ai/gap/policy-edu/suggestions` | Active |
| SourceSet | POST | `/internal/ai/source-sets/{id}/start` | Active (Internal) |
| SourceSet | GET | `/internal/ai/source-sets/{id}/status` | Active (Internal) |
| Render | POST | `/internal/ai/render-jobs` | Active (Internal) |
| Render | POST | `/ai/video/job/{job_id}/start` | Active (Internal) |
| Render | POST | `/ai/video/job/{job_id}/retry` | Active (Internal) |
| RAG Ingest | POST | `/internal/ai/rag-documents/ingest` | Active (Internal) |
| Callback | POST | `/internal/ai/callbacks/ragflow/ingest` | Active (Internal) |
| Feedback | POST | `/internal/ai/feedback` | Active (Internal) |
| WebSocket | WS | `/ws/videos/{id}/render-progress` | Active |

### 1.2 제거된 API (410 Gone)

| Method | Endpoint | 제거 사유 | 대체 API |
|--------|----------|-----------|----------|
| POST | `/internal/rag/index` | Phase 42 제거 | `/internal/ai/source-sets/{id}/start` |
| POST | `/internal/rag/delete` | Phase 42 제거 | RAGFlow 직접 삭제 |
| GET | `/internal/jobs/{job_id}` | Phase 42 제거 | `/internal/ai/source-sets/{id}/status` |

### 1.3 백엔드(Spring)에서 제공하는 API

> 다음 API들은 AI 서버가 아닌 Spring 백엔드에서 제공합니다.

- 영상 진행률: `/api/video/play/start`, `/api/video/progress`, `/api/video/complete`
- 관리자 API: `/api/admin/education/*`
- 스크립트 관리: `/api/scripts/*`
- 영상 관리: `/api/videos/*`, `/api/v2/videos/*`
- 렌더 잡 조회: `/api/render-jobs/*`

---

## 2. 핵심 API 명세

### 2.1 AI 채팅 (Core)

#### POST /ai/chat/messages

사용자 질문에 대한 AI 응답 생성 (RAG 기반)

**Request**
```json
{
  "session_id": "sess-uuid-001",
  "user_id": "EMP-12345",
  "user_role": "EMPLOYEE",
  "department": "개발팀",
  "domain": "POLICY",
  "channel": "WEB",
  "messages": [
    {"role": "user", "content": "연차휴가 규정이 어떻게 되나요?"}
  ]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| session_id | string | Yes | 채팅 세션 ID |
| user_id | string | Yes | 사용자 ID |
| user_role | string | Yes | `EMPLOYEE` / `MANAGER` / `ADMIN` / `INCIDENT_MANAGER` |
| department | string | No | 소속 부서 |
| domain | string | No | `POLICY` / `INCIDENT` / `EDUCATION` |
| channel | string | No | `WEB` / `MOBILE` (default: `WEB`) |
| messages | array | Yes | 대화 이력 |

**Response 200**
```json
{
  "answer": "연차휴가는 입사 1년 경과 시 15일이 부여됩니다.",
  "sources": [
    {
      "doc_id": "DOC-HR-001",
      "title": "인사규정",
      "page": 15,
      "score": 0.92,
      "snippet": "제15조(연차휴가)...",
      "article_label": "제15조",
      "article_path": "제4장 휴가 > 제15조",
      "source_type": "POLICY"
    }
  ],
  "meta": {
    "user_role": "EMPLOYEE",
    "used_model": "qwen2.5-7b",
    "route": "RAG_INTERNAL",
    "intent": "POLICY_QA",
    "domain": "POLICY",
    "rag_used": true,
    "rag_source_count": 3,
    "masked": false,
    "has_pii_input": false,
    "has_pii_output": false,
    "latency_ms": 1500,
    "rag_latency_ms": 200,
    "llm_latency_ms": 1200
  }
}
```

**에러 응답**

| Status | 상황 |
|--------|------|
| 422 | 유효성 검증 실패 |
| 503 | RAGFlow 서비스 불가 |

#### POST /ai/chat/stream

스트리밍 채팅 응답 (NDJSON)

> 자세한 내용은 [STREAMING_API_GUIDE.md](./STREAMING_API_GUIDE.md) 참조

**Request**: `/ai/chat/messages`와 동일 + `request_id` 필드 추가

**Response**: `application/x-ndjson`
```
{"type":"meta","request_id":"req-001","model":"qwen2.5-7b","timestamp":"..."}
{"type":"token","text":"연차"}
{"type":"token","text":"휴가는"}
{"type":"done","finish_reason":"stop","total_tokens":123,"elapsed_ms":4567}
```

| Event Type | Description |
|------------|-------------|
| `meta` | 시작 정보 (1회) |
| `token` | 토큰 스트림 (반복) |
| `done` | 완료 |
| `error` | 에러 |

---

### 2.2 부가 기능 API

#### POST /ai/quiz/generate

퀴즈 생성

**Request**
```json
{
  "education_id": "EDU-001",
  "question_count": 5,
  "difficulty": "medium"
}
```

#### POST /ai/faq/generate

FAQ 생성

**Request**
```json
{
  "document_id": "DOC-001",
  "max_questions": 10
}
```

#### POST /ai/gap/policy-edu/suggestions

RAG Gap 보완 제안

**Request**
```json
{
  "domain": "POLICY",
  "limit": 10
}
```

---

### 2.3 Internal API (Backend → AI)

> **인증**: `X-Internal-Token` 헤더 필수

#### POST /internal/ai/source-sets/{sourceSetId}/start

소스셋 처리 시작 (문서 → 스크립트)

**Request**
```json
{
  "video_id": "VID-001",
  "education_id": "EDU-001"
}
```

**Response 202**
```json
{
  "source_set_id": "SS-001",
  "status": "PROCESSING",
  "message": "Processing started"
}
```

#### POST /internal/ai/render-jobs

렌더 잡 생성/시작

**Request**
```json
{
  "jobId": "RJ-001",
  "videoId": "VID-001",
  "scriptId": "SCR-001"
}
```

**Response 202**
```json
{
  "received": true,
  "jobId": "RJ-001",
  "status": "PROCESSING"
}
```

#### POST /ai/video/job/{job_id}/start

렌더 잡 시작 (레거시 호환)

**Response 200**
```json
{
  "job_id": "RJ-001",
  "status": "PROCESSING",
  "started": true,
  "message": "렌더 파이프라인을 시작합니다."
}
```

#### POST /ai/video/job/{job_id}/retry

렌더 잡 재시도 (레거시 호환)

#### POST /internal/ai/rag-documents/ingest

사내규정 문서 RAGFlow ingest

**Request**
```json
{
  "ragDocumentPk": "uuid-001",
  "documentId": "POL-001",
  "version": 1,
  "sourceUrl": "https://s3.../doc.pdf",
  "domain": "POLICY",
  "requestId": "req-001",
  "traceId": "trace-001"
}
```

**Response 202**
```json
{
  "received": true,
  "ragDocumentPk": "uuid-001",
  "documentId": "POL-001",
  "version": 1,
  "status": "PROCESSING",
  "requestId": "req-001",
  "traceId": "trace-001"
}
```

#### WS /ws/videos/{video_id}/render-progress

실시간 렌더 진행률 (WebSocket)

**Server → Client**
```json
{"type": "progress", "jobId": "RJ-001", "progress": 45}
{"type": "completed", "jobId": "RJ-001", "assetUrl": "https://..."}
{"type": "failed", "jobId": "RJ-001", "error": "TTS 오류"}
```

---

## 3. 에러 응답 표준

### 3.1 HTTP 상태 코드

| Code | Description |
|------|-------------|
| 200 | 성공 |
| 201 | 생성됨 |
| 202 | 수락됨 (비동기) |
| 400 | 잘못된 요청 |
| 401 | 인증 실패 |
| 403 | 권한 없음 |
| 404 | 리소스 없음 |
| 409 | 상태 충돌 |
| 410 | 제거된 엔드포인트 |
| 422 | 유효성 검증 실패 |
| 500 | 서버 오류 |
| 502 | 업스트림 오류 |
| 503 | 서비스 불가 (RAGFlow 장애) |

### 3.2 에러 응답 형식

```json
{
  "detail": "에러 메시지",
  "error_code": "ERROR_CODE"
}
```

### 3.3 에러 코드

| Code | Description |
|------|-------------|
| `VALIDATION_ERROR` | 입력값 유효성 실패 |
| `RESOURCE_NOT_FOUND` | 리소스 없음 |
| `LLM_ERROR` | LLM 서비스 오류 |
| `RAG_SERVICE_UNAVAILABLE` | RAGFlow 서비스 불가 |
| `RENDER_ERROR` | 렌더링 오류 |
| `JOB_NOT_FOUND` | 렌더 잡 없음 |
| `ENDPOINT_REMOVED` | 제거된 엔드포인트 (410) |

---

## 4. 타임아웃 권장값

| API | Timeout |
|-----|---------|
| `/ai/chat/messages` | 30s |
| `/ai/chat/stream` | 60s |
| `/ai/quiz/generate` | 60s |
| `/ai/faq/generate` | 60s |
| `/internal/ai/*` | 30s |
| WebSocket | 5min (keepalive) |

---

## 5. 환경 변수

| Variable | Description | Default |
|----------|-------------|---------|
| `AI_ENV` | 환경 (mock/real) | `mock` |
| `LLM_BASE_URL` | LLM 서비스 URL | - |
| `LLM_MODEL_NAME` | LLM 모델명 | `qwen2.5-7b` |
| `RAGFLOW_BASE_URL` | RAGFlow URL | - |
| `MILVUS_HOST` | Milvus 호스트 | `localhost` |
| `MILVUS_PORT` | Milvus 포트 | `19530` |
| `BACKEND_BASE_URL` | Spring 백엔드 URL | - |
| `BACKEND_INTERNAL_TOKEN` | Internal API 토큰 | - |

---

# Part 2: 백엔드가 AI를 위해 제공해야 할 API

> AI 서버가 백엔드를 호출할 때 사용하는 API입니다.
> **백엔드 개발팀이 구현해야 합니다.**

## 6. 백엔드 API 인벤토리

### 6.1 필수 API

| Category | Method | Endpoint | 용도 |
|----------|--------|----------|------|
| AI 로그 | POST | `/api/ai-logs` | AI 대화 로그 저장 |
| 렌더 스펙 | GET | `/internal/scripts/{scriptId}/render-spec` | 영상 렌더 스펙 조회 |
| 잡 완료 콜백 | POST | `/internal/callbacks/render-jobs/{jobId}/complete` | 영상 생성 완료 알림 |
| 헬스체크 | GET | `/actuator/health` | 백엔드 상태 확인 |

### 6.2 AI 로그 저장

#### POST /api/ai-logs

**Request Body** (camelCase)
```json
{
  "log": {
    "sessionId": "sess-uuid-001",
    "userId": "EMP-12345",
    "userRole": "EMPLOYEE",
    "domain": "POLICY",
    "intent": "POLICY_QA",
    "route": "RAG_INTERNAL",
    "ragUsed": true,
    "ragSourceCount": 3,
    "latencyMs": 1500,
    "hasPiiInput": false,
    "hasPiiOutput": false,
    "questionMasked": "[마스킹된 질문]",
    "answerMasked": "[마스킹된 응답]"
  }
}
```

### 6.3 렌더 스펙 조회

#### GET /internal/scripts/{scriptId}/render-spec

**Headers**
```
X-Internal-Token: {BACKEND_INTERNAL_TOKEN}
```

**Response 200**
```json
{
  "scriptId": "SCR-001",
  "videoId": "VID-001",
  "title": "개인정보보호 교육",
  "totalDurationSec": 180.0,
  "scenes": [
    {
      "sceneId": "SCENE-001",
      "sceneOrder": 1,
      "narration": "안녕하세요...",
      "durationSec": 15.0
    }
  ]
}
```

### 6.4 잡 완료 콜백

#### POST /internal/callbacks/render-jobs/{jobId}/complete

**Headers**
```
X-Internal-Token: {BACKEND_INTERNAL_TOKEN}
```

**Request Body**
```json
{
  "jobId": "RJ-001",
  "status": "COMPLETED",
  "videoUrl": "s3://bucket/videos/VID-001/render.mp4",
  "durationSec": 180
}
```

---

## 변경 이력

| 날짜 | 버전 | 내용 |
|------|------|------|
| 2025-12-31 | 3.0 | 실제 AI 서버 API만 반영, 백엔드 API 분리 |
| 2025-12-20 | 2.2 | 잡 완료 콜백 추가, 렌더 경로 변경 |
| 2025-12-19 | 1.0 | 초기 작성 |
