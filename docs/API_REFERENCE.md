# CTRL+F AI Gateway API Reference

> **목적**: 기업 내부 정보보호 AI 어시스턴트 (LLM + RAG 기반)
> **Base URL**: `http://{AI_GATEWAY_HOST}:{PORT}`
> **문서 버전**: 2025-12-20 v2.2 (잡 완료 콜백 추가, 렌더 경로 변경)
> **상태**: 리팩토링 완료 (검증 완료, 백엔드 연동 준비)

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
| Health | GET | `/health` | ✅ Active |
| Health | GET | `/health/ready` | ✅ Active |
| Chat | POST | `/ai/chat/messages` | ✅ Active |
| Chat | POST | `/ai/chat/stream` | ✅ Active |
| RAG | POST | `/internal/rag/index` | ✅ Active |
| RAG | POST | `/internal/rag/delete` | ✅ Active |
| RAG | GET | `/internal/jobs/{job_id}` | ✅ Active |
| Quiz | POST | `/ai/quiz/generate` | ✅ Active |
| FAQ | POST | `/ai/faq/generate` | ✅ Active |
| FAQ | POST | `/ai/faq/generate/batch` | ✅ Active |
| Gap | POST | `/ai/gap/policy-edu/suggestions` | ✅ Active |
| Script | POST | `/api/scripts` | ✅ Active |
| Script | GET | `/api/scripts/{id}` | ✅ Active |
| Script | GET | `/api/scripts/{id}/editor` | ✅ Active |
| Script | PATCH | `/api/scripts/{id}/editor` | ✅ Active |
| Script | POST | `/api/videos/{id}/scripts/generate` | ✅ Active |
| Render | POST | `/ai/video/job/{job_id}/start` | ✅ Active |
| Render | POST | `/ai/video/job/{job_id}/retry` | ✅ Active |
| Render V2 | POST | `/api/v2/videos/{id}/render-jobs` | ✅ Active |
| Render V2 | GET | `/api/v2/videos/{id}/render-jobs` | ✅ Active |
| Render V2 | GET | `/api/v2/videos/{id}/render-jobs/{job_id}` | ✅ Active |
| Render V2 | POST | `/api/v2/videos/{id}/render-jobs/{job_id}/cancel` | ✅ Active |
| Render V2 | GET | `/api/v2/videos/{id}/assets/published` | ✅ Active |
| WebSocket | WS | `/ws/videos/{id}/render-progress` | ✅ Active |

### 1.2 제거된 API (레거시)

| Method | Endpoint | 제거 사유 | 대체 API |
|--------|----------|-----------|----------|
| POST | `/search` | RAGFlow 레거시 | ChatService 내부 Milvus 직접 검색 |
| POST | `/ingest` | RAGFlow 레거시 | `POST /internal/rag/index` |
| POST | `/ai/rag/process` | RagflowClient 레거시 | `POST /internal/rag/index` |
| POST | `/api/videos/{id}/render-jobs` | V1→V2 이전 완료 (2025-12-20) | `POST /api/v2/videos/{id}/render-jobs` |
| GET | `/api/render-jobs/{job_id}` | V1→V2 이전 완료 (2025-12-20) | `GET /api/v2/videos/{id}/render-jobs/{job_id}` |
| POST | `/api/render-jobs/{job_id}/cancel` | V1→V2 이전 완료 (2025-12-20) | `POST /api/v2/.../cancel` |
| GET | `/api/videos/{id}/asset` | V1→V2 이전 완료 (2025-12-20) | `GET /api/v2/videos/{id}/assets/published` |
| POST | `/api/video/play/start` | 백엔드 책임 (2025-12-20) | Spring 백엔드로 이전 |
| POST | `/api/video/progress` | 백엔드 책임 (2025-12-20) | Spring 백엔드로 이전 |
| POST | `/api/video/complete` | 백엔드 책임 (2025-12-20) | Spring 백엔드로 이전 |
| GET | `/api/video/status` | 백엔드 책임 (2025-12-20) | Spring 백엔드로 이전 |
| GET | `/api/video/quiz/check` | 백엔드 책임 (2025-12-20) | Spring 백엔드로 이전 |
| POST | `/api/admin/education/reissue` | 백엔드 책임 (2025-12-20) | Spring 백엔드로 이전 |
| GET | `/api/admin/education/{id}` | 백엔드 책임 (2025-12-20) | Spring 백엔드로 이전 |
| POST | `/api/videos/{id}/publish` | 백엔드 책임 (2025-12-20) | Spring 백엔드 + `/internal/rag/index` |
| GET | `/api/videos/{id}/kb-status` | 백엔드 책임 (2025-12-20) | `/internal/jobs/{job_id}` |

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
| session_id | string | ✅ | 채팅 세션 ID |
| user_id | string | ✅ | 사용자 ID |
| user_role | string | ✅ | `EMPLOYEE` \| `MANAGER` \| `ADMIN` \| `INCIDENT_MANAGER` |
| department | string | ❌ | 소속 부서 |
| domain | string | ❌ | `POLICY` \| `INCIDENT` \| `EDUCATION` |
| channel | string | ❌ | `WEB` \| `MOBILE` (default: `WEB`) |
| messages | array | ✅ | 대화 이력 |

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
    "used_model": "gpt-4o-mini",
    "route": "RAG_INTERNAL",
    "intent": "POLICY_QA",
    "domain": "POLICY",
    "rag_used": true,
    "rag_source_count": 3,
    "rag_gap_candidate": false,
    "masked": false,
    "has_pii_input": false,
    "has_pii_output": false,
    "latency_ms": 1500,
    "rag_latency_ms": 200,
    "llm_latency_ms": 1200
  }
}
```

#### POST /ai/chat/stream

스트리밍 채팅 응답 (NDJSON)

**Response**: `application/x-ndjson`
```
{"type": "meta", "route": "RAG_INTERNAL", "intent": "POLICY_QA", "domain": "POLICY"}
{"type": "token", "content": "연차"}
{"type": "token", "content": "휴가는"}
{"type": "done", "answer": "...", "sources": [...], "meta": {...}}
```

| Event Type | Description |
|------------|-------------|
| `meta` | 라우팅/의도 정보 (1회) |
| `token` | 토큰 단위 응답 (반복) |
| `done` | 완료 + 전체 응답 |
| `error` | 에러 정보 |

---

### 2.2 문서 인덱싱 (Internal RAG)

#### POST /internal/rag/index

Milvus 직접 문서 인덱싱

**Request**
```json
{
  "jobId": "job-uuid-001",
  "documentId": "DOC-2025-00123",
  "versionNo": 3,
  "title": "인사규정 개정안",
  "domain": "POLICY",
  "fileUrl": "https://files.internal/documents/DOC-2025-00123.pdf",
  "requestedBy": "admin@company.com"
}
```

**Response 202**
```json
{
  "jobId": "job-uuid-001",
  "status": "queued",
  "message": "Indexing job queued"
}
```

#### POST /internal/rag/delete

문서 삭제

**Request**
```json
{
  "jobId": "job-uuid-002",
  "documentId": "DOC-2025-00123",
  "versionNo": 3,
  "domain": "POLICY"
}
```

#### GET /internal/jobs/{job_id}

작업 상태 조회

**Response 200**
```json
{
  "jobId": "job-uuid-001",
  "status": "completed",
  "documentId": "DOC-2025-00123",
  "versionNo": 3,
  "progress": "upserting",
  "chunksProcessed": 45,
  "createdAt": "2025-01-15T10:30:00Z",
  "updatedAt": "2025-01-15T10:31:23Z"
}
```

| Status | Description |
|--------|-------------|
| `queued` | 대기 중 |
| `running` | 처리 중 |
| `completed` | 완료 |
| `failed` | 실패 |

| Progress | Description |
|----------|-------------|
| `downloading` | 파일 다운로드 중 |
| `extracting` | 텍스트 추출 중 |
| `chunking` | 청킹 중 |
| `embedding` | 임베딩 생성 중 |
| `upserting` | Milvus 저장 중 |

---

### 2.3 영상 생성 파이프라인 (Video Render)

#### POST /api/scripts

스크립트 생성

**Request**
```json
{
  "documentId": "DOC-2025-001",
  "title": "개인정보보호 교육",
  "targetDurationSec": 300,
  "style": "formal"
}
```

**Response 201**
```json
{
  "scriptId": "SCR-2025-001",
  "status": "DRAFT",
  "scenes": [...],
  "estimatedDurationSec": 295
}
```

#### POST /api/v2/videos/{video_id}/render-jobs

렌더 잡 생성 (Idempotent)

**Request**
```json
{
  "scriptId": "SCR-2025-001",
  "resolution": "1080p"
}
```

**Response 201**
```json
{
  "jobId": "RJ-2025-001",
  "videoId": "VID-001",
  "status": "QUEUED"
}
```

#### GET /api/v2/videos/{video_id}/render-jobs/{job_id}

렌더 잡 상태 조회

**Response 200**
```json
{
  "jobId": "RJ-2025-001",
  "videoId": "VID-001",
  "scriptId": "SCR-001",
  "status": "RENDERING",
  "progress": 65,
  "step": "GENERATE_TTS",
  "message": "음성 합성 중..."
}
```

| Status | Description |
|--------|-------------|
| `PENDING` | 대기 중 |
| `RUNNING` | 렌더링 중 |
| `SUCCEEDED` | 완료 |
| `FAILED` | 실패 |
| `CANCELLED` | 취소됨 |

#### POST /ai/video/job/{job_id}/start

렌더 잡 시작 (Backend → AI)

> 백엔드에서 AI 서버로 영상 생성 시작을 요청합니다.

**Response 200**
```json
{
  "jobId": "RJ-2025-001",
  "status": "RUNNING",
  "started": true,
  "message": "렌더 파이프라인을 시작합니다."
}
```

#### POST /ai/video/job/{job_id}/retry

렌더 잡 재시도 (Backend → AI)

> 실패한 잡의 재시도를 요청합니다.

**Response 200**
```json
{
  "jobId": "RJ-2025-001",
  "status": "RUNNING",
  "started": true,
  "message": "렌더 파이프라인을 재시도합니다."
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
| 403 | 권한 없음 |
| 404 | 리소스 없음 |
| 409 | 상태 충돌 |
| 422 | 유효성 검증 실패 |
| 500 | 서버 오류 |
| 502 | 업스트림 오류 |
| 503 | 서비스 불가 |

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
| `RAG_ERROR` | RAG 검색 오류 |
| `RENDER_ERROR` | 렌더링 오류 |
| `SCRIPT_NOT_FOUND` | 스크립트 없음 |
| `JOB_NOT_FOUND` | 렌더 잡 없음 |
| `CANNOT_CANCEL` | 취소 불가 상태 |
| `PERMISSION_DENIED` | 권한 없음 |

---

## 4. 연결성 검증 결과

### 4.1 수정된 이슈

| Issue | Before | After |
|-------|--------|-------|
| RAGFlow 직접 호출 | `/search`, `/ingest` 엔드포인트 노출 | ChatService 내부 Milvus 직접 연동 |
| 렌더 잡 중복 생성 | V1 API 비멱등성 | V2 API Idempotent 처리 |
| 만료 교육 접근 | 클라이언트 검증만 | 서버 403 반환 |
| 필드명 불일치 | snake_case/camelCase 혼용 | Internal API는 camelCase 통일 |

### 4.2 스키마 컨벤션

**Internal RAG API**: camelCase (백엔드 Spring 호환)
```
jobId, documentId, versionNo, fileUrl, requestedBy, chunksProcessed
```

**Chat API**: snake_case (Python 표준)
```
session_id, user_id, user_role
```

**Video Render API**: snake_case (Python 표준)
```
video_id, script_id, job_id
```

---

## 5. 실행 및 테스트

### 5.1 서버 실행

```bash
# 개발 환경
cd ctrlf-ai
python -m uvicorn app.main:app --reload --port 8000

# 프로덕션
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 5.2 헬스체크

```bash
# Liveness
curl http://localhost:8000/health

# Readiness
curl http://localhost:8000/health/ready
```

### 5.3 테스트 실행

```bash
# 전체 테스트
python -m pytest tests/ -v

# 특정 Phase 테스트
python -m pytest tests/test_phase22_video_progress.py -v
python -m pytest tests/test_phase25_internal_rag.py -v

# 커버리지
python -m pytest tests/ --cov=app --cov-report=html
```

### 5.4 E2E 스모크 테스트

```bash
# 채팅 API 테스트
curl -X POST http://localhost:8000/ai/chat/messages \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-001",
    "user_id": "EMP-001",
    "user_role": "EMPLOYEE",
    "messages": [{"role": "user", "content": "연차휴가 규정"}]
  }'

# 스트리밍 테스트
curl -X POST http://localhost:8000/ai/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-002",
    "user_id": "EMP-001",
    "user_role": "EMPLOYEE",
    "messages": [{"role": "user", "content": "테스트"}]
  }'
```

---

## 6. 타임아웃 권장값

| API | Timeout |
|-----|---------|
| `/ai/chat/messages` | 30s |
| `/ai/chat/stream` | 60s |
| `/internal/rag/index` | 120s |
| `/internal/rag/delete` | 30s |
| `/ai/quiz/generate` | 60s |
| `/ai/faq/generate` | 60s |
| `/ai/faq/generate/batch` | 300s |
| `/api/scripts/*` | 60s |
| `/api/*/render-jobs` | 30s |
| WebSocket | 5min (keepalive) |

---

## 7. 환경 변수

| Variable | Description | Default |
|----------|-------------|---------|
| `APP_ENV` | 환경 (dev/prod) | `dev` |
| `LLM_BASE_URL` | LLM 서비스 URL | - |
| `LLM_MODEL_NAME` | LLM 모델명 | `gpt-4o-mini` |
| `MILVUS_HOST` | Milvus 호스트 | `localhost` |
| `MILVUS_PORT` | Milvus 포트 | `19530` |
| `BACKEND_BASE_URL` | Spring 백엔드 URL | - |
| `FAQ_BATCH_CONCURRENCY` | FAQ 배치 동시성 | `3` |

---

---

# Part 2: 백엔드가 AI를 위해 제공해야 할 API

> AI 서버가 백엔드를 호출할 때 사용하는 API입니다.
> **⚠️ 백엔드 개발팀이 구현해야 합니다.**

## 8. 백엔드 API 인벤토리

### 8.1 필수 API

| Category | Method | Endpoint | 용도 | AI 클라이언트 |
|----------|--------|----------|------|--------------|
| AI 로그 | POST | `/api/ai-logs` | AI 대화 로그 저장 | BackendClient |
| 렌더 스펙 | GET | `/internal/scripts/{scriptId}/render-spec` | 영상 렌더 스펙 조회 | BackendScriptClient |
| 스크립트 콜백 | POST | `/video/script/complete` | 스크립트 생성 완료 알림 | BackendCallbackClient |
| 잡 완료 콜백 | POST | `/video/job/{jobId}/complete` | 영상 생성 완료 알림 | BackendCallbackClient |
| 헬스체크 | GET | `/actuator/health` | 백엔드 상태 확인 | BackendClient |

### 8.2 선택 API (라우팅에 따라 필요)

> `BACKEND_API` 또는 `MIXED_BACKEND_RAG` 라우트 사용 시 필요

| Category | Method | Endpoint | 용도 | AI 클라이언트 |
|----------|--------|----------|------|--------------|
| 교육 현황 | GET | `/api/edu/status` | 직원 교육 현황 조회 | BackendDataClient |
| 교육 통계 | GET | `/api/edu/stats` | 부서 교육 통계 조회 | BackendDataClient |
| 사고 통계 | GET | `/api/incidents/overview` | 사고 현황 요약 | BackendDataClient |
| 사고 상세 | GET | `/api/incidents/{id}` | 사고 상세 조회 | BackendDataClient |
| 신고 안내 | GET | `/api/incidents/report-guide` | 신고 절차 안내 | BackendDataClient |

---

## 9. 백엔드 API 상세 명세

### 9.1 AI 로그 저장

#### POST /api/ai-logs

AI 대화 로그를 저장합니다.

**Request Headers**
```
Content-Type: application/json
Authorization: Bearer {BACKEND_API_TOKEN}  (선택)
```

**Request Body** (⚠️ **camelCase** 사용)
```json
{
  "log": {
    "sessionId": "sess-uuid-001",
    "userId": "EMP-12345",
    "turnIndex": 1,
    "channel": "WEB",
    "userRole": "EMPLOYEE",
    "department": "개발팀",
    "domain": "POLICY",
    "intent": "POLICY_QA",
    "route": "RAG_INTERNAL",
    "hasPiiInput": false,
    "hasPiiOutput": false,
    "modelName": "gpt-4o-mini",
    "ragUsed": true,
    "ragSourceCount": 3,
    "latencyMs": 1500,
    "ragLatencyMs": 200,
    "llmLatencyMs": 1200,
    "backendLatencyMs": 0,
    "ragGapCandidate": false,
    "errorCode": null,
    "errorMessage": null,
    "questionMasked": "[마스킹된 질문]",
    "answerMasked": "[마스킹된 응답]"
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| sessionId | string | ✅ | 채팅 세션 ID |
| userId | string | ✅ | 사용자 ID |
| turnIndex | int | ❌ | 대화 턴 인덱스 |
| channel | string | ✅ | WEB / MOBILE |
| userRole | string | ✅ | 사용자 역할 |
| department | string | ❌ | 부서명 |
| domain | string | ✅ | POLICY / EDU / INCIDENT |
| intent | string | ✅ | 분류된 의도 |
| route | string | ✅ | 라우팅 경로 |
| hasPiiInput | bool | ✅ | 입력 PII 검출 여부 |
| hasPiiOutput | bool | ✅ | 출력 PII 검출 여부 |
| modelName | string | ❌ | LLM 모델명 |
| ragUsed | bool | ✅ | RAG 사용 여부 |
| ragSourceCount | int | ✅ | RAG 검색 결과 수 |
| latencyMs | int | ✅ | 전체 응답 시간 (ms) |
| ragGapCandidate | bool | ✅ | RAG Gap 후보 여부 |
| errorCode | string | ❌ | 에러 코드 |
| errorMessage | string | ❌ | 에러 메시지 |
| questionMasked | string | ❌ | PII 마스킹된 질문 (로그용) |
| answerMasked | string | ❌ | PII 마스킹된 응답 (로그용) |

**Response 200/201**
```json
{
  "success": true,
  "logId": "LOG-2025-001",
  "message": "Log saved successfully"
}
```

---

### 9.2 렌더 스펙 조회

#### GET /internal/scripts/{scriptId}/render-spec

영상 렌더링에 필요한 스펙을 조회합니다. Job 시작 시 호출되어 스냅샷으로 저장됩니다.

**Request Headers**
```
X-Internal-Token: {BACKEND_INTERNAL_TOKEN}
```

**Response 200**
```json
{
  "scriptId": "SCR-2025-001",
  "videoId": "VID-001",
  "title": "개인정보보호 교육",
  "totalDurationSec": 180.0,
  "scenes": [
    {
      "sceneId": "SCENE-001",
      "sceneOrder": 1,
      "chapterTitle": "1. 개요",
      "purpose": "hook",
      "narration": "안녕하세요, 오늘은 개인정보보호에 대해 알아보겠습니다.",
      "caption": "개인정보보호의 중요성",
      "durationSec": 15.0,
      "visualSpec": {
        "backgroundColor": "#1a1a2e",
        "textColor": "#ffffff"
      }
    }
  ]
}
```

**에러 응답**

| Status | 상황 |
|--------|------|
| 401 | 토큰 없음/잘못됨 |
| 403 | 권한 없음 |
| 404 | 스크립트 없음 |

---

### 9.3 스크립트 생성 완료 콜백

#### POST /video/script/complete

스크립트 자동 생성 완료를 알립니다. (비동기 콜백)

**Request Headers**
```
Content-Type: application/json
X-Internal-Token: {BACKEND_INTERNAL_TOKEN}
```

**Request Body** (⚠️ **camelCase** 사용)
```json
{
  "materialId": "VID-001",
  "scriptId": "SCR-2025-001",
  "script": "{\"chapters\": [...]}",
  "version": 1
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| materialId | string | ✅ | 자료/영상 ID |
| scriptId | string | ✅ | 생성된 스크립트 ID |
| script | string | ✅ | 스크립트 JSON (문자열) |
| version | int | ✅ | 스크립트 버전 |

**Response 200**
```json
{
  "success": true,
  "message": "Script registered"
}
```

---

### 9.4 영상 생성 완료 콜백

#### POST /video/job/{jobId}/complete

영상 렌더링 완료를 알립니다. (비동기 콜백)

> AI 서버가 렌더링 완료 후 백엔드로 호출합니다.
> 렌더 성공 시 자동 호출되며, 실패해도 렌더 결과에 영향을 주지 않습니다 (non-blocking).

**Request Headers**
```
Content-Type: application/json
X-Internal-Token: {BACKEND_INTERNAL_TOKEN}
```

**Request Body** (camelCase 사용)
```json
{
  "jobId": "RJ-2025-001",
  "videoUrl": "s3://bucket/videos/VID-001/render.mp4",
  "duration": 180,
  "status": "COMPLETED"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| jobId | string | Yes | 렌더 잡 ID |
| videoUrl | string | Yes | S3 영상 경로 |
| duration | int | Yes | 영상 길이 (초) |
| status | string | Yes | 완료 상태 (`COMPLETED`, `FAILED`) |

**Response 200**
```json
{
  "saved": true
}
```

**에러 응답**

| Status | 상황 |
|--------|------|
| 401 | 토큰 없음/잘못됨 |
| 403 | 권한 없음 |
| 404 | Job 없음 |

---

### 9.5 교육 현황 조회 (선택)

#### GET /api/edu/status

직원 본인의 교육 수료 현황을 조회합니다.

**Query Parameters**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| userId | string | ✅ | 사용자 ID |
| year | int | ❌ | 조회 연도 |

**Response 200**
```json
{
  "userId": "EMP-12345",
  "totalRequired": 4,
  "completed": 3,
  "pending": 1,
  "courses": [
    {
      "name": "정보보호교육",
      "status": "completed",
      "completedAt": "2025-03-15"
    },
    {
      "name": "산업안전보건",
      "status": "pending",
      "deadline": "2025-12-31"
    }
  ],
  "nextDeadline": "2025-12-31"
}
```

---

### 9.6 교육 통계 조회 (선택)

#### GET /api/edu/stats

관리자용 부서별 교육 통계를 조회합니다.

**Query Parameters**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| departmentId | string | ❌ | 부서 ID (없으면 전체) |
| year | int | ❌ | 조회 연도 |

**Response 200**
```json
{
  "departmentId": "DEPT-001",
  "departmentName": "개발팀",
  "totalEmployees": 50,
  "completionRate": 85.0,
  "byCourse": [
    {"name": "정보보호교육", "completed": 45, "pending": 5},
    {"name": "개인정보보호교육", "completed": 42, "pending": 8}
  ],
  "pendingCount": 15
}
```

---

### 9.7 사고 통계 조회 (선택)

#### GET /api/incidents/overview

사고/위반 요약 통계를 조회합니다.

**Query Parameters**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| period | string | ❌ | month / quarter / year |
| status | string | ❌ | open / closed / all |
| type | string | ❌ | security / privacy / all |

**Response 200**
```json
{
  "period": "2025-Q4",
  "totalIncidents": 15,
  "byStatus": {
    "open": 3,
    "inProgress": 5,
    "closed": 7
  },
  "byType": {
    "security": 8,
    "privacy": 5,
    "compliance": 2
  },
  "trend": {
    "previousPeriod": 12,
    "changeRate": 25.0
  }
}
```

---

### 9.8 사고 상세 조회 (선택)

#### GET /api/incidents/{incident_id}

특정 사건의 상세 정보를 조회합니다.

**Response 200**
```json
{
  "incidentId": "INC-2025-001",
  "type": "security",
  "status": "in_progress",
  "reportedAt": "2025-10-15T09:30:00Z",
  "summary": "외부 이메일로 내부 문서 전송 건",
  "severity": "medium",
  "assignedTo": "보안팀",
  "relatedPolicies": ["정보보안정책 제3조", "개인정보처리방침 제5조"]
}
```

> ⚠️ 실명/사번 등 민감 정보는 익명화되어 반환해야 함

---

### 9.9 신고 안내 조회 (선택)

#### GET /api/incidents/report-guide

신고 절차 안내 정보를 조회합니다.

**Query Parameters**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| type | string | ❌ | security / privacy / harassment |

**Response 200**
```json
{
  "guideType": "security",
  "title": "보안사고 신고 안내",
  "steps": [
    "1. 사고 발생 일시 및 장소 확인",
    "2. 관련 증거 자료 수집",
    "3. 공식 신고 채널을 통해 접수",
    "4. 신고 접수 번호 수령 후 보관"
  ],
  "officialChannels": [
    {"name": "보안팀 직통", "contact": "security@company.com"},
    {"name": "신고 포털", "url": "https://report.company.com"}
  ],
  "warnings": [
    "개인정보(주민번호, 연락처 등)를 신고 내용에 포함하지 마세요."
  ]
}
```

---

## 10. 스키마 컨벤션 정리

### 10.1 AI 서버 제공 API (Backend → AI 호출)

| Category | Convention |
|----------|------------|
| Internal RAG API | camelCase (Spring 호환) |
| Chat API | snake_case (Python 표준) |
| Video Render API | snake_case |

### 10.2 백엔드 제공 API (AI → Backend 호출)

| Category | Convention |
|----------|------------|
| AI 로그 (`/api/ai-logs`) | camelCase (**중요**: `log` 객체 내부) |
| 렌더 스펙 (`/internal/scripts/*/render-spec`) | camelCase |
| 스크립트 콜백 (`/video/script/complete`) | camelCase |
| 데이터 조회 (`/api/edu/*`, `/api/incidents/*`) | camelCase |

---

## 11. 수정 이력

| 날짜 | 변경 내용 |
|------|-----------|
| 2025-12-19 | 초기 문서 작성 |
| 2025-12-20 | Video Progress API 필드명 수정 (camelCase → snake_case) |
| 2025-12-20 | 누락 API 추가 (FAQ Batch, Script Editor 등 7개) |
| 2025-12-20 | Deprecated API 섹션 추가 |
| 2025-12-20 | Chat Response meta 필드 보강 (PII, latency 등) |
| 2025-12-20 | **V1 Render API 제거** (V2로 완전 이전) |
| 2025-12-20 | **백엔드 책임 API 제거**: Video Progress, Admin, Publish API → Spring 백엔드로 이전 |
| 2025-12-20 | **Part 2: 백엔드 제공 API 섹션 신규 추가** (AI 로그, 렌더 스펙, 콜백, 데이터 조회) |
| 2025-12-20 | **렌더 잡 시작/재시도 경로 변경**: `/api/render-jobs/{job_id}/*` → `/ai/video/job/{job_id}/*` |
| 2025-12-20 | **잡 완료 콜백 추가**: `POST /video/job/{jobId}/complete` (9.4절) |

---

**문서 버전**: 2025-12-20 v2.2
**리팩토링 상태**: 완료 (AI 핵심 기능만 유지, 백엔드 책임 API 분리, 백엔드 제공 API 명세 추가)
