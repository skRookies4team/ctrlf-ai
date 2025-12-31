# CTRL+F AI Gateway API 명세서 (백엔드팀 전달용)

> **Base URL**: `http://{AI_GATEWAY_HOST}:{PORT}`
> **Content-Type**: `application/json`
> **문서 버전**: 2025-12-31 v3.0
> **OpenAPI 문서**: `/docs` (Swagger UI), `/redoc` (ReDoc)

---

## 목차

1. [헬스체크 API](#1-헬스체크-api)
2. [채팅 API](#2-채팅-api-핵심)
3. [스트리밍 채팅 API](#3-스트리밍-채팅-api)
4. [부가 기능 API](#4-부가-기능-api)
5. [Internal API (Backend → AI)](#5-internal-api-backend--ai)
6. [에러 응답 표준](#6-에러-응답-표준)
7. [중요 연동 가이드](#7-중요-연동-가이드)

---

## 중요 안내: API 책임 분리

> **Phase 42 이후 AI 서버(ctrlf-ai)와 백엔드(ctrlf-back) 간 API 책임이 분리되었습니다.**

### AI 서버 (ctrlf-ai) 제공 API

| 카테고리 | 엔드포인트 | 설명 |
|---------|-----------|------|
| Health | `/health`, `/health/ready` | 헬스체크 |
| Chat | `/ai/chat/messages`, `/ai/chat/stream` | AI 채팅 |
| Quiz | `/ai/quiz/generate` | 퀴즈 생성 |
| FAQ | `/ai/faq/generate` | FAQ 생성 |
| Gap | `/ai/gap/policy-edu/suggestions` | RAG Gap 제안 |
| SourceSet | `/internal/ai/source-sets/*` | 소스셋 오케스트레이션 |
| Render | `/internal/ai/render-jobs`, `/ai/video/job/*` | 영상 렌더링 |
| RAG Ingest | `/internal/ai/rag-documents/*` | 문서 인덱싱 |
| WebSocket | `/ws/videos/*/render-progress` | 렌더 진행률 |

### 백엔드 (ctrlf-back) 제공 API

| 카테고리 | 엔드포인트 | 설명 |
|---------|-----------|------|
| 영상 진행률 | `/api/video/play/start`, `/api/video/progress`, `/api/video/complete` | 교육 영상 시청 |
| 관리자 | `/api/admin/education/*` | 교육 관리 |
| 스크립트 | `/api/scripts/*` | 스크립트 관리/편집 |
| 영상 관리 | `/api/videos/*`, `/api/v2/videos/*` | 영상 CRUD |
| 렌더 잡 조회 | `/api/render-jobs/*` | 렌더 잡 상태 조회 |

---

## 1. 헬스체크 API

### 1.1 Liveness Check

```
GET /health
```

**Response 200**
```json
{
  "status": "ok",
  "app": "ctrlf-ai-gateway",
  "version": "0.1.0",
  "env": "dev"
}
```

### 1.2 Readiness Check

```
GET /health/ready
```

**Response 200**
```json
{
  "ready": true,
  "checks": {
    "ragflow": true,
    "llm": true,
    "backend": true
  }
}
```

---

## 2. 채팅 API (핵심)

### 2.1 AI 채팅 응답 생성

```
POST /ai/chat/messages
```

**Request Body**
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

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `session_id` | string | O | 채팅 세션 ID |
| `user_id` | string | O | 사용자 ID (사번 등) |
| `user_role` | string | O | `EMPLOYEE`, `MANAGER`, `ADMIN`, `INCIDENT_MANAGER` |
| `department` | string | X | 소속 부서 |
| `domain` | string | X | `POLICY`, `INCIDENT`, `EDUCATION` (미지정 시 AI가 판단) |
| `channel` | string | X | `WEB`, `MOBILE` (기본: `WEB`) |
| `messages` | array | O | 대화 이력 (마지막이 현재 질문) |

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
      "snippet": "제15조(연차휴가) 1년간 80% 이상 출근한 근로자에게...",
      "article_label": "제15조 (연차휴가)",
      "article_path": "제4장 휴가 > 제15조 연차휴가"
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
    "has_pii_input": false,
    "has_pii_output": false,
    "latency_ms": 1500,
    "rag_latency_ms": 200,
    "llm_latency_ms": 1200
  }
}
```

---

## 3. 스트리밍 채팅 API

```
POST /ai/chat/stream
```

**Content-Type**
- Request: `application/json`
- Response: `application/x-ndjson`

**Request Body** (채팅 API + `request_id`)
```json
{
  "request_id": "req-uuid-001",
  "session_id": "sess-uuid-001",
  "user_id": "EMP-12345",
  "user_role": "EMPLOYEE",
  "messages": [
    {"role": "user", "content": "연차휴가 규정이 어떻게 되나요?"}
  ]
}
```

**Response (NDJSON 스트림)**
```json
{"type":"meta","request_id":"req-uuid-001","model":"qwen2.5-7b","timestamp":"..."}
{"type":"token","text":"연차"}
{"type":"token","text":"휴가는"}
{"type":"done","finish_reason":"stop","total_tokens":123,"elapsed_ms":4567,"ttfb_ms":200}
```

**이벤트 타입**

| 타입 | 설명 |
|------|------|
| `meta` | 시작 메타정보 (1회) |
| `token` | 토큰 단위 응답 (반복) |
| `done` | 완료 (1회) |
| `error` | 에러 (1회) |

> 자세한 내용은 [STREAMING_API_GUIDE.md](./STREAMING_API_GUIDE.md) 참조

---

## 4. 부가 기능 API

### 4.1 퀴즈 자동 생성

```
POST /ai/quiz/generate
```

**Request Body**
```json
{
  "education_id": "EDU-2025-001",
  "content": "개인정보보호법에 따르면...",
  "num_questions": 5,
  "difficulty": "medium"
}
```

**Response 200**
```json
{
  "education_id": "EDU-2025-001",
  "questions": [
    {
      "question_id": "Q1",
      "question": "개인정보보호법에서 정의하는 '개인정보'에 해당하지 않는 것은?",
      "choices": [
        {"label": "A", "text": "성명"},
        {"label": "B", "text": "주민등록번호"},
        {"label": "C", "text": "회사 대표전화번호"},
        {"label": "D", "text": "이메일 주소"}
      ],
      "correct_answer": "C",
      "explanation": "회사 대표전화번호는 법인에 관한 정보로 개인정보에 해당하지 않습니다."
    }
  ],
  "meta": {
    "generated_count": 5,
    "model": "qwen2.5-7b"
  }
}
```

### 4.2 FAQ 초안 생성

```
POST /ai/faq/generate
```

**Request Body**
```json
{
  "document_id": "DOC-HR-001",
  "content": "제15조(연차휴가) 1년간 80% 이상 출근한 근로자에게 15일의 유급휴가를 준다.",
  "num_faqs": 3
}
```

### 4.3 RAG Gap 보완 제안

```
POST /ai/gap/policy-edu/suggestions
```

**Request Body**
```json
{
  "unanswered_questions": [
    {
      "question": "재택근무 신청 절차가 어떻게 되나요?",
      "session_id": "sess-001",
      "asked_at": "2025-01-15T10:30:00Z"
    }
  ],
  "limit": 10
}
```

---

## 5. Internal API (Backend → AI)

> **인증**: `X-Internal-Token` 헤더 필수

### 5.1 SourceSet 오케스트레이션

#### POST /internal/ai/source-sets/{sourceSetId}/start

소스셋 처리 시작 (문서 → RAGFlow 적재 → 스크립트 생성)

**Request Body**
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

#### GET /internal/ai/source-sets/{sourceSetId}/status

처리 상태 조회

**Response 200**
```json
{
  "source_set_id": "SS-001",
  "status": "COMPLETED",
  "progress": 100,
  "message": "Processing completed"
}
```

### 5.2 렌더 잡 (영상 생성)

#### POST /internal/ai/render-jobs

렌더 잡 생성/시작

**Request Body**
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

렌더 잡 재시도

### 5.3 RAG 문서 Ingest

#### POST /internal/ai/rag-documents/ingest

사내규정 문서를 RAGFlow에 적재

**Request Body**
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
  "status": "PROCESSING"
}
```

### 5.4 WebSocket 렌더 진행률

```
WS /ws/videos/{video_id}/render-progress
```

**서버 → 클라이언트 메시지**
```json
{"type": "progress", "jobId": "RJ-001", "progress": 45}
{"type": "completed", "jobId": "RJ-001", "assetUrl": "https://..."}
{"type": "failed", "jobId": "RJ-001", "error": "TTS 서비스 오류"}
```

### 5.5 Deprecated API (410 Gone)

> Phase 42에서 제거됨

| Method | Endpoint | 대체 경로 |
|--------|----------|-----------|
| POST | `/internal/rag/index` | `/internal/ai/source-sets/{id}/start` |
| POST | `/internal/rag/delete` | RAGFlow 직접 삭제 |
| GET | `/internal/jobs/{job_id}` | `/internal/ai/source-sets/{id}/status` |

---

## 6. 에러 응답 표준

**에러 응답 형식**
```json
{
  "detail": "에러 메시지",
  "error_code": "ERROR_CODE"
}
```

**HTTP 상태 코드**

| 코드 | 설명 |
|------|------|
| 200 | 성공 |
| 201 | 생성됨 |
| 202 | 수락됨 (비동기) |
| 400 | 잘못된 요청 |
| 401 | 인증 실패 |
| 403 | 권한 없음 |
| 404 | 리소스 없음 |
| 410 | 제거된 엔드포인트 |
| 422 | 유효성 검증 실패 |
| 500 | 서버 오류 |
| 503 | 서비스 불가 (RAGFlow 장애) |

**에러 코드**

| error_code | 설명 |
|------------|------|
| `VALIDATION_ERROR` | 입력값 유효성 검사 실패 |
| `RESOURCE_NOT_FOUND` | 리소스를 찾을 수 없음 |
| `LLM_ERROR` | LLM 서비스 오류 |
| `RAG_SERVICE_UNAVAILABLE` | RAGFlow 서비스 불가 |
| `RENDER_ERROR` | 렌더링 오류 |
| `ENDPOINT_REMOVED` | 제거된 엔드포인트 (410) |

---

## 7. 중요 연동 가이드

### 7.1 세션 관리

- `session_id`는 백엔드에서 생성하여 AI Gateway에 전달
- 동일 세션 내 대화는 같은 `session_id` 사용
- 세션별 대화 이력은 `messages` 배열로 전달

### 7.2 사용자 역할 (user_role)

| 역할 | 설명 | 접근 가능 도메인 |
|------|------|------------------|
| `EMPLOYEE` | 일반 직원 | POLICY, EDUCATION |
| `MANAGER` | 팀장/관리자 | POLICY, EDUCATION, INCIDENT(제한) |
| `ADMIN` | 시스템 관리자 | 전체 |
| `INCIDENT_MANAGER` | 사고 관리자 | 전체 (사고 처리 특화) |

### 7.3 도메인 (domain)

| 도메인 | 설명 | 검색 대상 |
|--------|------|----------|
| `POLICY` | 정책/규정 | 인사규정, 복리후생, 업무 매뉴얼 등 |
| `INCIDENT` | 사건/사고 | 사고 보고서, 대응 이력 |
| `EDUCATION` | 교육 | 교육 자료, 영상 스크립트 |

### 7.4 타임아웃 설정 권장값

| API | 권장 타임아웃 |
|-----|---------------|
| `/ai/chat/messages` | 30초 |
| `/ai/chat/stream` | 60초 |
| `/ai/quiz/generate` | 60초 |
| `/ai/faq/generate` | 60초 |
| `/internal/ai/*` | 30초 |
| WebSocket 연결 | 5분 (keepalive) |

---

## 부록: 전체 AI 서버 API 요약

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | `/health` | Liveness 체크 |
| GET | `/health/ready` | Readiness 체크 |
| POST | `/ai/chat/messages` | AI 채팅 응답 |
| POST | `/ai/chat/stream` | 스트리밍 채팅 |
| POST | `/ai/quiz/generate` | 퀴즈 생성 |
| POST | `/ai/faq/generate` | FAQ 생성 |
| POST | `/ai/gap/policy-edu/suggestions` | RAG Gap 제안 |
| POST | `/internal/ai/source-sets/{id}/start` | 소스셋 시작 |
| GET | `/internal/ai/source-sets/{id}/status` | 소스셋 상태 |
| POST | `/internal/ai/render-jobs` | 렌더 잡 생성 |
| POST | `/ai/video/job/{id}/start` | 렌더 잡 시작 |
| POST | `/ai/video/job/{id}/retry` | 렌더 잡 재시도 |
| POST | `/internal/ai/rag-documents/ingest` | RAG 문서 적재 |
| POST | `/internal/ai/feedback` | 피드백 저장 |
| WS | `/ws/videos/{id}/render-progress` | 렌더 진행률 |

---

## 변경 이력

| 날짜 | 버전 | 내용 |
|------|------|------|
| 2025-12-31 | 3.0 | 실제 AI 서버 API만 반영, 백엔드 API 분리 |
| 2025-12-19 | 2.0 | Phase 42 API 추가 |
| 2025-01-01 | 1.0 | 초기 작성 |

---

**문의**: AI팀
