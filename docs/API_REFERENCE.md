# CTRL+F AI Gateway API Reference

> **목적**: 기업 내부 정보보호 AI 어시스턴트 (LLM + RAG 기반)
> **Base URL**: `http://{AI_GATEWAY_HOST}:{PORT}`
> **문서 버전**: 2025-12-20 (V1 API 제거)
> **상태**: 리팩토링 완료 (검증 완료, 백엔드 연동 준비)

---

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
| Render | POST | `/api/render-jobs/{job_id}/start` | ✅ Active |
| Render | POST | `/api/render-jobs/{job_id}/retry` | ✅ Active |
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

#### POST /api/render-jobs/{job_id}/start

렌더 잡 시작 (Phase 38)

**Response 200**
```json
{
  "jobId": "RJ-2025-001",
  "status": "RUNNING",
  "started": true,
  "message": "렌더 파이프라인을 시작합니다."
}
```

#### POST /api/render-jobs/{job_id}/retry

렌더 잡 재시도 (Phase 38)

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

## 8. 수정 이력

| 날짜 | 변경 내용 |
|------|-----------|
| 2025-12-19 | 초기 문서 작성 |
| 2025-12-20 | Video Progress API 필드명 수정 (camelCase → snake_case) |
| 2025-12-20 | 누락 API 추가 (FAQ Batch, Script Editor 등 7개) |
| 2025-12-20 | Deprecated API 섹션 추가 |
| 2025-12-20 | Chat Response meta 필드 보강 (PII, latency 등) |
| 2025-12-20 | **V1 Render API 제거** (V2로 완전 이전) |
| 2025-12-20 | **백엔드 책임 API 제거**: Video Progress, Admin, Publish API → Spring 백엔드로 이전 |

---

**문서 버전**: 2025-12-20
**리팩토링 상태**: 완료 (AI 핵심 기능만 유지, 백엔드 책임 API 분리)
