# CTRL+F AI Gateway API 명세서 (백엔드팀 전달용)

> **Base URL**: `http://{AI_GATEWAY_HOST}:{PORT}`
> **Content-Type**: `application/json`
> **문서 버전**: 2025-12-19 v2.0
> **OpenAPI 문서**: `/docs` (Swagger UI), `/redoc` (ReDoc)

---

## 목차

1. [헬스체크 API](#1-헬스체크-api)
2. [채팅 API](#2-채팅-api-핵심)
3. [스트리밍 채팅 API](#3-스트리밍-채팅-api)
4. [Internal RAG API](#4-internal-rag-api-phase-25)
5. [퀴즈 자동 생성 API](#5-퀴즈-자동-생성-api)
6. [FAQ 초안 생성 API](#6-faq-초안-생성-api)
7. [RAG Gap 보완 제안 API](#7-rag-gap-보완-제안-api)
8. [영상 진행률 API](#8-영상-진행률-api-phase-22)
9. [관리자 API](#9-관리자-api-phase-26)
10. [영상 생성 API](#10-영상-생성-api-phase-27-42)
11. [에러 응답 표준](#11-에러-응답-표준)
12. [중요 연동 가이드](#12-중요-연동-가이드)

---

## 1. 헬스체크 API

### 1.1 Liveness Check

서비스가 살아있는지 확인합니다.

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

트래픽을 받을 준비가 되었는지 확인합니다 (의존 서비스 상태 포함).

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

> **백엔드 참고**: K8s/로드밸런서 헬스체크에서 `/health/ready`를 사용하면 RAGFlow/LLM 장애 시 트래픽을 차단할 수 있습니다.

---

## 2. 채팅 API (핵심)

### 2.1 AI 채팅 응답 생성

사용자 질문에 대한 AI 응답을 생성합니다.

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
| `session_id` | string | O | 채팅 세션 ID (백엔드에서 관리) |
| `user_id` | string | O | 사용자 ID (사번 등) |
| `user_role` | string | O | 역할: `EMPLOYEE`, `MANAGER`, `ADMIN`, `INCIDENT_MANAGER` |
| `department` | string | X | 소속 부서 |
| `domain` | string | X | 도메인 힌트: `POLICY`, `INCIDENT`, `EDUCATION` (미지정 시 AI가 판단) |
| `channel` | string | X | 채널: `WEB`, `MOBILE` (기본: `WEB`) |
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
    "used_model": "gpt-4o-mini",
    "route": "RAG_INTERNAL",
    "intent": "POLICY_QA",
    "domain": "POLICY",
    "rag_gap_candidate": false
  }
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `answer` | string | AI 생성 응답 |
| `sources` | array | RAG 검색 결과 (문서 출처) |
| `sources[].doc_id` | string | 문서 ID |
| `sources[].title` | string | 문서 제목 |
| `sources[].page` | int | 페이지 번호 |
| `sources[].score` | float | 유사도 점수 (0~1) |
| `sources[].snippet` | string | 관련 텍스트 스니펫 |
| `sources[].article_label` | string | 조항 라벨 |
| `sources[].article_path` | string | 조항 경로 |
| `meta.user_role` | string | 사용자 역할 |
| `meta.used_model` | string | 사용된 LLM 모델명 |
| `meta.route` | string | 라우팅 경로: `RAG_INTERNAL`, `GENERAL`, `CLARIFY` |
| `meta.intent` | string | 분류된 의도 |
| `meta.domain` | string | 분류된 도메인 |
| `meta.rag_gap_candidate` | bool | RAG Gap 후보 여부 |

---

## 3. 스트리밍 채팅 API

HTTP 청크 스트리밍으로 AI 응답을 실시간 전송합니다.

```
POST /ai/chat/stream
```

**Content-Type**
- Request: `application/json`
- Response: `application/x-ndjson`

**Request Body** (채팅 API와 동일)
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

**Response (NDJSON 스트림)**

각 줄은 독립적인 JSON 객체입니다:

```json
{"type": "meta", "route": "RAG_INTERNAL", "intent": "POLICY_QA", "domain": "POLICY"}
{"type": "token", "content": "연차"}
{"type": "token", "content": "휴가는"}
{"type": "token", "content": " 입사"}
...
{"type": "done", "answer": "연차휴가는 입사 1년 경과 시...", "sources": [...], "meta": {...}}
```

**이벤트 타입**

| 타입 | 설명 | 발생 시점 |
|------|------|----------|
| `meta` | 라우팅/의도 메타정보 | 스트림 시작 시 1회 |
| `token` | 토큰 단위 응답 | 응답 생성 중 반복 |
| `done` | 완료 + 전체 응답 | 스트림 종료 시 1회 |
| `error` | 에러 정보 | 오류 발생 시 |

**에러 이벤트 예시**
```json
{"type": "error", "error_code": "LLM_ERROR", "message": "LLM 서비스 오류"}
```

> **백엔드 구현 가이드**: Spring에서 NDJSON을 줄 단위로 읽어서 SSE(Server-Sent Events)로 프론트엔드에 전달하는 구조를 권장합니다.

---

## 4. Internal RAG API (Phase 42 - REMOVED)

> **⚠️ Phase 42 (2025-12-22)에서 제거됨**
>
> Direct Milvus 인덱싱 파이프라인이 제거되었습니다.
> 모든 엔드포인트는 **410 Gone**을 반환합니다.

### 제거된 엔드포인트

| Method | Endpoint | 대체 경로 |
|--------|----------|-----------|
| POST | `/internal/rag/index` | SourceSet Orchestrator → RAGFlow |
| POST | `/internal/rag/delete` | SourceSet Orchestrator → RAGFlow |
| GET | `/internal/jobs/{job_id}` | SourceSet Orchestrator 내부 관리 |

### 대체 API

문서 인덱싱은 **SourceSet Orchestrator**를 통해 처리됩니다:

```
POST /internal/ai/source-sets/{sourceSetId}/start
```

자세한 내용은 `ctrlf_api_spec_fastapi_orchestrator_v2_3_db_aligned.md`를 참조하세요.

---

## 5. 퀴즈 자동 생성 API

교육 콘텐츠 기반으로 퀴즈 문제를 자동 생성합니다.

```
POST /ai/quiz/generate
```

**Request Body**
```json
{
  "education_id": "EDU-2025-001",
  "content": "개인정보보호법에 따르면 개인정보란 살아 있는 개인에 관한 정보로서...",
  "num_questions": 5,
  "difficulty": "medium"
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `education_id` | string | O | 교육 ID |
| `content` | string | O | 퀴즈 생성 대상 콘텐츠 |
| `num_questions` | int | X | 생성할 문제 수 (기본: 5) |
| `difficulty` | string | X | 난이도: `easy`, `medium`, `hard` |

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
    "model": "gpt-4o-mini"
  }
}
```

---

## 6. FAQ 초안 생성 API

### 6.1 단건 FAQ 생성

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

**Response 200**
```json
{
  "document_id": "DOC-HR-001",
  "faqs": [
    {
      "question": "연차휴가는 며칠이 부여되나요?",
      "answer": "1년간 80% 이상 출근한 근로자에게 15일의 유급 연차휴가가 부여됩니다."
    }
  ]
}
```

### 6.2 배치 FAQ 생성

```
POST /ai/faq/generate/batch
```

**Request Body**
```json
{
  "documents": [
    {"document_id": "DOC-001", "content": "..."},
    {"document_id": "DOC-002", "content": "..."}
  ],
  "num_faqs_per_doc": 3
}
```

---

## 7. RAG Gap 보완 제안 API

RAG 검색에서 적합한 문서를 찾지 못한 질문들에 대해 보완 문서 제안을 생성합니다.

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

**Response 200**
```json
{
  "suggestions": [
    {
      "topic": "재택근무 제도",
      "suggested_content": "재택근무 신청 절차, 승인 기준, 근태 관리 방법",
      "priority": "high",
      "related_questions": ["재택근무 신청 절차가 어떻게 되나요?"],
      "question_count": 15
    }
  ],
  "meta": {
    "total_questions_analyzed": 50,
    "suggestions_generated": 5
  }
}
```

---

## 8. 영상 진행률 API (Phase 22)

교육 영상 시청 진행률을 서버에서 검증하고 관리합니다.

### 8.1 영상 재생 시작

```
POST /api/video/play/start
```

**Request Body**
```json
{
  "educationId": "EDU-2025-001",
  "userId": "EMP-12345",
  "videoDurationSec": 600
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `educationId` | string | O | 교육 ID |
| `userId` | string | O | 사용자 ID |
| `videoDurationSec` | int | O | 영상 전체 길이 (초) |

**Response 200**
```json
{
  "sessionToken": "tok-abc123",
  "resumePositionSec": 120,
  "status": "IN_PROGRESS",
  "message": "재생 시작"
}
```

**Response 403 (만료된 교육)**
```json
{
  "detail": "교육 기한이 만료되었습니다.",
  "error_code": "EDUCATION_EXPIRED"
}
```

### 8.2 진행률 업데이트

```
POST /api/video/progress
```

**Request Body**
```json
{
  "sessionToken": "tok-abc123",
  "currentPositionSec": 180,
  "clientTimestamp": "2025-01-15T10:35:00Z"
}
```

**Response 200**
```json
{
  "accepted": true,
  "serverPositionSec": 180,
  "progressPercent": 30.0
}
```

**Response 400 (비정상 진행)**
```json
{
  "accepted": false,
  "reason": "SKIP_DETECTED",
  "serverPositionSec": 120,
  "message": "비정상적인 구간 스킵이 감지되었습니다."
}
```

### 8.3 영상 완료 요청

```
POST /api/video/complete
```

**Request Body**
```json
{
  "sessionToken": "tok-abc123"
}
```

**Response 200**
```json
{
  "completed": true,
  "finalProgressPercent": 100.0,
  "canStartQuiz": true
}
```

**Response 400 (미완료)**
```json
{
  "completed": false,
  "currentProgressPercent": 85.0,
  "requiredPercent": 95.0,
  "message": "영상을 95% 이상 시청해야 완료됩니다."
}
```

### 8.4 영상 상태 조회

```
GET /api/video/status?educationId={educationId}&userId={userId}
```

**Response 200**
```json
{
  "educationId": "EDU-2025-001",
  "userId": "EMP-12345",
  "status": "IN_PROGRESS",
  "progressPercent": 45.0,
  "lastPositionSec": 270,
  "videoDurationSec": 600,
  "completedAt": null
}
```

| status 값 | 설명 |
|-----------|------|
| `NOT_STARTED` | 미시작 |
| `IN_PROGRESS` | 진행 중 |
| `COMPLETED` | 완료 |

### 8.5 퀴즈 시작 가능 여부 확인

```
GET /api/video/quiz/check?educationId={educationId}&userId={userId}
```

**Response 200**
```json
{
  "canStartQuiz": true,
  "reason": null
}
```

**Response 200 (불가)**
```json
{
  "canStartQuiz": false,
  "reason": "영상 시청이 완료되지 않았습니다. (현재 85%)"
}
```

---

## 9. 관리자 API (Phase 26)

### 9.1 교육 재발행 (복제 발행)

기존 교육을 복제하여 새로운 교육으로 발행합니다.

```
POST /api/admin/education/reissue
```

**Request Body**
```json
{
  "sourceEducationId": "EDU-2025-001",
  "newTitle": "2025년 정보보호 교육 (재발행)",
  "newDeadline": "2025-06-30T23:59:59Z",
  "targetUserIds": ["EMP-001", "EMP-002"],
  "requestedBy": "ADMIN-001"
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `sourceEducationId` | string | O | 복제할 원본 교육 ID |
| `newTitle` | string | X | 새 제목 (없으면 원본 + " (재발행)") |
| `newDeadline` | string | O | 새 마감일 (ISO 8601) |
| `targetUserIds` | array | X | 대상 사용자 목록 (없으면 원본과 동일) |
| `requestedBy` | string | O | 요청 관리자 ID |

**Response 201 Created**
```json
{
  "newEducationId": "EDU-2025-002",
  "sourceEducationId": "EDU-2025-001",
  "title": "2025년 정보보호 교육 (재발행)",
  "deadline": "2025-06-30T23:59:59Z",
  "targetUserCount": 150,
  "createdAt": "2025-01-15T10:30:00Z"
}
```

### 9.2 교육 메타데이터 조회

```
GET /api/admin/education/{education_id}
```

**Response 200**
```json
{
  "educationId": "EDU-2025-001",
  "title": "2025년 정보보호 교육",
  "description": "연간 필수 정보보호 교육",
  "deadline": "2025-03-31T23:59:59Z",
  "status": "ACTIVE",
  "videoId": "VID-001",
  "videoDurationSec": 600,
  "quizId": "QUIZ-001",
  "targetUserCount": 500,
  "completedUserCount": 320,
  "createdAt": "2025-01-01T00:00:00Z",
  "createdBy": "ADMIN-001"
}
```

---

## 10. 영상 생성 API (Phase 27-42)

### 10.1 스크립트 생성

문서 기반으로 교육 영상 스크립트를 자동 생성합니다.

```
POST /api/scripts
```

**Request Body**
```json
{
  "documentId": "DOC-2025-001",
  "title": "개인정보보호 교육 영상",
  "targetDurationSec": 300,
  "style": "formal"
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `documentId` | string | O | 원본 문서 ID |
| `title` | string | O | 스크립트 제목 |
| `targetDurationSec` | int | X | 목표 영상 길이 (초, 기본: 300) |
| `style` | string | X | 스타일: `formal`, `casual` (기본: `formal`) |

**Response 201 Created**
```json
{
  "scriptId": "SCR-2025-001",
  "status": "DRAFT",
  "title": "개인정보보호 교육 영상",
  "scenes": [
    {
      "sceneNo": 1,
      "narration": "안녕하세요. 오늘은 개인정보보호에 대해 알아보겠습니다.",
      "durationSec": 10,
      "visualHint": "인트로 화면, 제목 표시"
    }
  ],
  "estimatedDurationSec": 295,
  "createdAt": "2025-01-15T10:30:00Z"
}
```

### 10.2 스크립트 승인

```
POST /api/scripts/{script_id}/approve
```

**Request Body**
```json
{
  "approvedBy": "ADMIN-001",
  "comment": "수정 없이 승인"
}
```

**Response 200**
```json
{
  "scriptId": "SCR-2025-001",
  "status": "APPROVED",
  "approvedBy": "ADMIN-001",
  "approvedAt": "2025-01-15T11:00:00Z"
}
```

### 10.3 스크립트 조회

```
GET /api/scripts/{script_id}
```

**Response 200**
```json
{
  "scriptId": "SCR-2025-001",
  "status": "APPROVED",
  "title": "개인정보보호 교육 영상",
  "documentId": "DOC-2025-001",
  "scenes": [...],
  "estimatedDurationSec": 295,
  "createdAt": "2025-01-15T10:30:00Z",
  "approvedAt": "2025-01-15T11:00:00Z",
  "approvedBy": "ADMIN-001"
}
```

### 10.4 스크립트 자동 생성 (from Video)

기존 영상에 대해 스크립트를 자동 생성합니다. (Phase 31)

```
POST /api/videos/{video_id}/scripts/generate
```

**Request Body**
```json
{
  "documentId": "DOC-2025-001",
  "style": "formal"
}
```

**Response 201 Created**
```json
{
  "scriptId": "SCR-2025-002",
  "videoId": "VID-001",
  "status": "DRAFT",
  "scenes": [...]
}
```

### 10.5 스크립트 편집기 조회 (Phase 42)

```
GET /api/scripts/{script_id}/editor
```

**Response 200**
```json
{
  "scriptId": "SCR-2025-001",
  "status": "DRAFT",
  "title": "개인정보보호 교육 영상",
  "scenes": [
    {
      "sceneNo": 1,
      "narration": "안녕하세요.",
      "visualHint": "인트로",
      "durationSec": 10,
      "editable": true
    }
  ],
  "totalDurationSec": 295
}
```

### 10.6 스크립트 편집기 수정 (Phase 42)

```
PATCH /api/scripts/{script_id}/editor
```

**Request Body**
```json
{
  "scenes": [
    {
      "sceneNo": 1,
      "narration": "안녕하세요. 반갑습니다.",
      "visualHint": "인트로 수정",
      "durationSec": 12
    }
  ]
}
```

**Response 200**
```json
{
  "scriptId": "SCR-2025-001",
  "status": "DRAFT",
  "updatedScenes": [1],
  "totalDurationSec": 297
}
```

### 10.7 렌더 잡 생성 (V2 - Phase 33)

승인된 스크립트를 영상으로 렌더링합니다. V2는 Idempotent하게 동작합니다.

```
POST /api/v2/videos/{video_id}/render-jobs
```

**Request Body**
```json
{
  "scriptId": "SCR-2025-001",
  "resolution": "1080p",
  "voiceType": "female_1"
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `scriptId` | string | O | 스크립트 ID |
| `resolution` | string | X | 해상도: `720p`, `1080p` (기본: `1080p`) |
| `voiceType` | string | X | 음성 타입 |

**Response 201 Created**
```json
{
  "jobId": "RJ-2025-001",
  "videoId": "VID-001",
  "status": "QUEUED",
  "scriptId": "SCR-2025-001",
  "createdAt": "2025-01-15T11:30:00Z"
}
```

**Response 200 (이미 존재하는 잡)**
```json
{
  "jobId": "RJ-2025-001",
  "videoId": "VID-001",
  "status": "RENDERING",
  "progress": 45,
  "message": "Existing job returned (idempotent)"
}
```

### 10.8 렌더 잡 목록 조회 (V2 - Phase 33)

```
GET /api/v2/videos/{video_id}/render-jobs
```

**Response 200**
```json
{
  "videoId": "VID-001",
  "jobs": [
    {
      "jobId": "RJ-2025-001",
      "status": "COMPLETED",
      "progress": 100,
      "createdAt": "2025-01-15T11:30:00Z",
      "completedAt": "2025-01-15T11:45:00Z"
    },
    {
      "jobId": "RJ-2025-002",
      "status": "RENDERING",
      "progress": 30,
      "createdAt": "2025-01-15T12:00:00Z"
    }
  ]
}
```

### 10.9 렌더 잡 상세 조회

```
GET /api/render-jobs/{job_id}
```

**Response 200**
```json
{
  "jobId": "RJ-2025-001",
  "videoId": "VID-001",
  "status": "RENDERING",
  "progress": 65,
  "currentStep": "audio_synthesis",
  "steps": [
    {"name": "script_validation", "status": "completed"},
    {"name": "audio_synthesis", "status": "in_progress"},
    {"name": "video_composition", "status": "pending"}
  ],
  "estimatedRemainingTimeSec": 120,
  "createdAt": "2025-01-15T11:30:00Z"
}
```

| status 값 | 설명 |
|-----------|------|
| `QUEUED` | 대기 중 |
| `RENDERING` | 렌더링 중 |
| `COMPLETED` | 완료 |
| `FAILED` | 실패 |
| `CANCELLED` | 취소됨 |

### 10.10 렌더 잡 시작 (Phase 38)

대기 중인 잡을 수동으로 시작합니다.

```
POST /api/render-jobs/{job_id}/start
```

**Response 200**
```json
{
  "jobId": "RJ-2025-001",
  "status": "RENDERING",
  "message": "Render job started"
}
```

### 10.11 렌더 잡 재시도 (Phase 38)

실패한 잡을 재시도합니다.

```
POST /api/render-jobs/{job_id}/retry
```

**Response 200**
```json
{
  "jobId": "RJ-2025-001",
  "status": "QUEUED",
  "retryCount": 2,
  "message": "Render job queued for retry"
}
```

### 10.12 렌더 잡 취소 (V2)

```
POST /api/v2/videos/{video_id}/render-jobs/{job_id}/cancel
```

**Response 200**
```json
{
  "jobId": "RJ-2025-001",
  "status": "CANCELLED",
  "cancelledAt": "2025-01-15T11:35:00Z"
}
```

### 10.13 비디오 에셋 조회

```
GET /api/videos/{video_id}/asset
```

**Response 200**
```json
{
  "videoId": "VID-001",
  "assetUrl": "https://cdn.internal/videos/VID-001.mp4",
  "thumbnailUrl": "https://cdn.internal/videos/VID-001-thumb.jpg",
  "durationSec": 295,
  "resolution": "1080p",
  "fileSize": 52428800,
  "createdAt": "2025-01-15T11:45:00Z"
}
```

### 10.14 발행된 에셋 조회 (V2 - Phase 33)

```
GET /api/v2/videos/{video_id}/assets/published
```

**Response 200**
```json
{
  "videoId": "VID-001",
  "publishedAsset": {
    "assetUrl": "https://cdn.internal/videos/VID-001-published.mp4",
    "thumbnailUrl": "https://cdn.internal/videos/VID-001-thumb.jpg",
    "publishedAt": "2025-01-15T12:00:00Z",
    "publishedBy": "ADMIN-001"
  }
}
```

### 10.15 영상 발행 (Phase 28)

렌더링 완료된 영상을 발행하고 KB에 인덱싱합니다.

```
POST /api/videos/{video_id}/publish
```

**Request Body**
```json
{
  "publishedBy": "ADMIN-001",
  "indexToKb": true
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `publishedBy` | string | O | 발행자 ID |
| `indexToKb` | bool | X | KB 인덱싱 여부 (기본: true) |

**Response 200**
```json
{
  "videoId": "VID-001",
  "status": "PUBLISHED",
  "publishedAt": "2025-01-15T12:00:00Z",
  "kbIndexJobId": "job-kb-001",
  "kbIndexStatus": "queued"
}
```

### 10.16 KB 인덱싱 상태 조회 (Phase 28)

```
GET /api/videos/{video_id}/kb-status
```

**Response 200**
```json
{
  "videoId": "VID-001",
  "kbIndexStatus": "completed",
  "indexedAt": "2025-01-15T12:05:00Z",
  "chunksIndexed": 15
}
```

### 10.17 WebSocket 렌더 진행률 (Phase 32)

실시간으로 렌더링 진행률을 구독합니다.

```
WS /ws/videos/{video_id}/render-progress
```

**Connection URL 예시**
```
ws://ai-gateway:8000/ws/videos/VID-001/render-progress
```

**서버 → 클라이언트 메시지**
```json
{"type": "progress", "jobId": "RJ-001", "progress": 45, "step": "audio_synthesis"}
{"type": "progress", "jobId": "RJ-001", "progress": 100, "step": "completed"}
{"type": "completed", "jobId": "RJ-001", "assetUrl": "https://..."}
{"type": "failed", "jobId": "RJ-001", "error": "TTS 서비스 오류"}
```

**메시지 타입**

| type | 설명 |
|------|------|
| `progress` | 진행률 업데이트 |
| `completed` | 렌더링 완료 |
| `failed` | 렌더링 실패 |
| `cancelled` | 렌더링 취소 |

---

## 11. 에러 응답 표준

모든 API는 일관된 에러 응답 형식을 사용합니다.

**에러 응답 형식**
```json
{
  "detail": "에러 메시지",
  "error_code": "ERROR_CODE",
  "timestamp": "2025-01-15T10:30:00Z"
}
```

**HTTP 상태 코드**

| 코드 | 설명 | 사용 예시 |
|------|------|----------|
| 200 | 성공 | 정상 응답 |
| 201 | 생성됨 | 리소스 생성 완료 |
| 202 | 수락됨 | 비동기 작업 접수 |
| 400 | 잘못된 요청 | 파라미터 오류, 유효성 검사 실패 |
| 401 | 인증 실패 | 토큰 없음/만료 |
| 403 | 권한 없음 | 역할 권한 부족 |
| 404 | 리소스 없음 | ID에 해당하는 리소스 없음 |
| 409 | 충돌 | 상태 충돌 (예: 이미 발행됨) |
| 500 | 서버 오류 | 내부 오류 |
| 503 | 서비스 불가 | 의존 서비스 장애 |

**에러 코드 목록**

| error_code | 설명 |
|------------|------|
| `VALIDATION_ERROR` | 입력값 유효성 검사 실패 |
| `RESOURCE_NOT_FOUND` | 리소스를 찾을 수 없음 |
| `EDUCATION_EXPIRED` | 교육 기한 만료 |
| `SKIP_DETECTED` | 영상 구간 스킵 감지 |
| `LLM_ERROR` | LLM 서비스 오류 |
| `RAG_ERROR` | RAG 검색 오류 |
| `RENDER_ERROR` | 렌더링 오류 |
| `ALREADY_PUBLISHED` | 이미 발행된 리소스 |
| `SCRIPT_NOT_APPROVED` | 스크립트 미승인 |

---

## 12. 중요 연동 가이드

### 12.1 세션 관리

- `session_id`는 백엔드에서 생성하여 AI Gateway에 전달
- 동일 세션 내 대화는 같은 `session_id` 사용
- 세션별 대화 이력은 `messages` 배열로 전달

```json
{
  "session_id": "sess-uuid-001",
  "messages": [
    {"role": "user", "content": "첫 번째 질문"},
    {"role": "assistant", "content": "첫 번째 답변"},
    {"role": "user", "content": "두 번째 질문 (현재)"}
  ]
}
```

### 12.2 사용자 역할 (user_role)

| 역할 | 설명 | 접근 가능 도메인 |
|------|------|------------------|
| `EMPLOYEE` | 일반 직원 | POLICY, EDUCATION |
| `MANAGER` | 팀장/관리자 | POLICY, EDUCATION, INCIDENT(제한) |
| `ADMIN` | 시스템 관리자 | 전체 |
| `INCIDENT_MANAGER` | 사고 관리자 | 전체 (사고 처리 특화) |

### 12.3 도메인 (domain)

| 도메인 | 설명 | 검색 대상 |
|--------|------|----------|
| `POLICY` | 정책/규정 | 인사규정, 복리후생, 업무 매뉴얼 등 |
| `INCIDENT` | 사건/사고 | 사고 보고서, 대응 이력 (권한 필요) |
| `EDUCATION` | 교육 | 교육 자료, 영상 스크립트 |

### 12.4 PII 처리

AI Gateway는 내부적으로 PII(개인식별정보) 마스킹을 수행합니다.
- 입력 단계: 질문 내 PII 마스킹
- 출력 단계: 응답 내 PII 복원
- 백엔드에서 추가 마스킹 불필요

### 12.5 RAG Gap 로깅

`meta.rag_gap_candidate: true`인 응답은 관리자 대시보드에서 모니터링:
- 적합한 문서를 찾지 못한 질문
- 문서 보완이 필요한 영역 식별

### 12.6 영상 진행률 Flow

```
1. 재생 시작
   POST /api/video/play/start
   → sessionToken 발급

2. 진행률 업데이트 (10초 간격 권장)
   POST /api/video/progress
   → 서버 검증 (스킵 감지)

3. 완료 요청
   POST /api/video/complete
   → 95% 이상 시청 확인

4. 퀴즈 시작 전 확인
   GET /api/video/quiz/check
   → canStartQuiz: true 확인 후 퀴즈 진행
```

### 12.7 타임아웃 설정 권장값

| API | 권장 타임아웃 |
|-----|---------------|
| `/ai/chat/messages` | 30초 |
| `/ai/chat/stream` | 60초 |
| `/internal/rag/index` | 120초 |
| `/internal/rag/delete` | 30초 |
| `/ai/quiz/generate` | 60초 |
| `/api/video/*` | 5초 |
| `/api/scripts` | 60초 |
| `/api/scripts/{id}/approve` | 10초 |
| `/api/*/render-jobs` | 30초 |
| `/api/videos/{id}/publish` | 60초 |
| WebSocket 연결 | 5분 (keepalive 권장) |

### 12.8 영상 생성 파이프라인 Flow

```
1. 스크립트 생성
   POST /api/scripts
   → scriptId 발급, status: DRAFT

2. (선택) 스크립트 편집
   GET /api/scripts/{id}/editor
   PATCH /api/scripts/{id}/editor

3. 스크립트 승인
   POST /api/scripts/{id}/approve
   → status: APPROVED

4. 렌더 잡 생성
   POST /api/v2/videos/{id}/render-jobs
   → jobId 발급, status: QUEUED

5. 렌더링 진행 모니터링
   WS /ws/videos/{id}/render-progress
   또는 GET /api/render-jobs/{job_id} (폴링)

6. 영상 발행
   POST /api/videos/{id}/publish
   → KB 인덱싱 자동 수행

7. KB 인덱싱 확인
   GET /api/videos/{id}/kb-status
```

---

## 부록: 전체 API 요약

| Method | Endpoint | 설명 | Phase |
|--------|----------|------|-------|
| GET | `/health` | Liveness 체크 | - |
| GET | `/health/ready` | Readiness 체크 | - |
| POST | `/ai/chat/messages` | AI 채팅 응답 생성 | - |
| POST | `/ai/chat/stream` | 스트리밍 채팅 응답 | - |
| POST | `/internal/rag/index` | 문서 인덱싱 (Milvus) | 25 |
| POST | `/internal/rag/delete` | 문서 삭제 (Milvus) | 25 |
| GET | `/internal/jobs/{job_id}` | 작업 상태 조회 | 25 |
| POST | `/ai/quiz/generate` | 퀴즈 자동 생성 | 16 |
| POST | `/ai/faq/generate` | FAQ 초안 생성 | 18 |
| POST | `/ai/faq/generate/batch` | 배치 FAQ 생성 | 18 |
| POST | `/ai/gap/policy-edu/suggestions` | RAG Gap 보완 제안 | 15 |
| POST | `/api/video/play/start` | 영상 재생 시작 | 22 |
| POST | `/api/video/progress` | 영상 진행률 업데이트 | 22 |
| POST | `/api/video/complete` | 영상 완료 요청 | 22 |
| GET | `/api/video/status` | 영상 상태 조회 | 22 |
| GET | `/api/video/quiz/check` | 퀴즈 시작 가능 여부 | 22 |
| POST | `/api/admin/education/reissue` | 교육 재발행 | 26 |
| GET | `/api/admin/education/{id}` | 교육 메타데이터 조회 | 26 |
| POST | `/api/scripts` | 스크립트 생성 | 27 |
| POST | `/api/scripts/{id}/approve` | 스크립트 승인 | 27 |
| GET | `/api/scripts/{id}` | 스크립트 조회 | 27 |
| POST | `/api/videos/{id}/scripts/generate` | 스크립트 자동 생성 | 31 |
| GET | `/api/scripts/{id}/editor` | 스크립트 편집기 조회 | 42 |
| PATCH | `/api/scripts/{id}/editor` | 스크립트 편집기 수정 | 42 |
| POST | `/api/v2/videos/{id}/render-jobs` | 렌더 잡 생성 (V2) | 33 |
| GET | `/api/v2/videos/{id}/render-jobs` | 렌더 잡 목록 조회 | 33 |
| GET | `/api/v2/videos/{id}/render-jobs/{job_id}` | 렌더 잡 상세 (V2) | 33 |
| POST | `/api/v2/videos/{id}/render-jobs/{job_id}/cancel` | 렌더 잡 취소 (V2) | 33 |
| GET | `/api/v2/videos/{id}/assets/published` | 발행 에셋 조회 | 33 |
| GET | `/api/render-jobs/{job_id}` | 렌더 잡 상태 조회 | 27 |
| POST | `/api/render-jobs/{job_id}/start` | 렌더 잡 시작 | 38 |
| POST | `/api/render-jobs/{job_id}/retry` | 렌더 잡 재시도 | 38 |
| GET | `/api/videos/{id}/asset` | 비디오 에셋 조회 | 27 |
| POST | `/api/videos/{id}/publish` | 영상 발행 | 28 |
| GET | `/api/videos/{id}/kb-status` | KB 인덱싱 상태 | 28 |
| WS | `/ws/videos/{id}/render-progress` | 렌더 진행률 (실시간) | 32 |

---

## 변경 이력

### 2025-12-19 v2.0

- 문서 전면 재작성 (상세 명세 추가)
- Phase 31, 38, 42 API 추가
- 스트리밍 API 상세 이벤트 타입 명세
- 에러 코드 표준화
- 연동 가이드 섹션 대폭 확장

### 삭제된 API (레거시)

| API | 대체 API | 삭제 사유 |
|-----|----------|-----------|
| `POST /search` | ChatService 내부 Milvus 직접 검색 | RAGFlow 레거시, Phase 25 전환 |
| `POST /ingest` | `POST /internal/rag/index` | RAGFlow 레거시, Milvus 직접 인덱싱으로 대체 |
| `POST /ai/rag/process` | `POST /internal/rag/index` | RagflowClient 레거시 |

### Deprecated API

| V1 API | V2 API | 비고 |
|--------|--------|------|
| `POST /api/videos/{id}/render-jobs` | `POST /api/v2/videos/{id}/render-jobs` | V2는 Idempotent |
| `POST /api/render-jobs/{id}/cancel` | `POST /api/v2/.../cancel` | V2 경로 표준화 |

---

**문의**: AI팀
**최종 수정**: 2025-12-19 v2.0
