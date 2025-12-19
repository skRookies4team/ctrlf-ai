# CTRL+F AI Gateway API 명세서 (백엔드팀 전달용)

> **Base URL**: `http://{AI_GATEWAY_HOST}:{PORT}`
> **Content-Type**: `application/json`
> **문서 버전**: 2025-12-19
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

\`\`\`
GET /health
\`\`\`

**Response 200**
\`\`\`json
{
  "status": "ok",
  "app": "ctrlf-ai-gateway",
  "version": "0.1.0",
  "env": "dev"
}
\`\`\`

### 1.2 Readiness Check

트래픽을 받을 준비가 되었는지 확인합니다 (의존 서비스 상태 포함).

\`\`\`
GET /health/ready
\`\`\`

**Response 200**
\`\`\`json
{
  "ready": true,
  "checks": {
    "ragflow": true,
    "llm": true,
    "backend": true
  }
}
\`\`\`

> **백엔드 참고**: K8s/로드밸런서 헬스체크에서 \`/health/ready\`를 사용하면 RAGFlow/LLM 장애 시 트래픽을 차단할 수 있습니다.

---

## 2. 채팅 API (핵심)

### 2.1 AI 채팅 응답 생성

사용자 질문에 대한 AI 응답을 생성합니다.

\`\`\`
POST /ai/chat/messages
\`\`\`

**Request Body**
\`\`\`json
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
\`\`\`

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| \`session_id\` | string | O | 채팅 세션 ID (백엔드에서 관리) |
| \`user_id\` | string | O | 사용자 ID (사번 등) |
| \`user_role\` | string | O | 역할: \`EMPLOYEE\`, \`MANAGER\`, \`ADMIN\`, \`INCIDENT_MANAGER\` |
| \`department\` | string | X | 소속 부서 |
| \`domain\` | string | X | 도메인 힌트: \`POLICY\`, \`INCIDENT\`, \`EDUCATION\` (미지정 시 AI가 판단) |
| \`channel\` | string | X | 채널: \`WEB\`, \`MOBILE\` (기본: \`WEB\`) |
| \`messages\` | array | O | 대화 이력 (마지막이 현재 질문) |

**Response 200**
\`\`\`json
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
\`\`\`

---

## 3. 스트리밍 채팅 API

HTTP 청크 스트리밍으로 AI 응답을 실시간 전송합니다.

\`\`\`
POST /ai/chat/stream
\`\`\`

**Content-Type**
- Request: \`application/json\`
- Response: \`application/x-ndjson\`

**이벤트 타입**: meta, token, done, error

---

## 4. Internal RAG API (Phase 25)

RAGFlow를 우회하고 AI 서버가 Milvus에 직접 문서를 인덱싱/삭제합니다.

### 4.1 문서 인덱싱 요청

\`\`\`
POST /internal/rag/index
\`\`\`

**Request Body**
\`\`\`json
{
  "job_id": "job-uuid-001",
  "document_id": "DOC-2025-00123",
  "version_no": 3,
  "domain": "POLICY",
  "file_url": "https://files.internal/documents/DOC-2025-00123.pdf"
}
\`\`\`

**Response 202 Accepted**
\`\`\`json
{
  "job_id": "job-uuid-001",
  "status": "queued",
  "message": "Indexing job queued"
}
\`\`\`

### 4.2 문서 삭제 요청

\`\`\`
POST /internal/rag/delete
\`\`\`

### 4.3 작업 상태 조회

\`\`\`
GET /internal/jobs/{job_id}
\`\`\`

---

## 5. 퀴즈 자동 생성 API

\`\`\`
POST /ai/quiz/generate
\`\`\`

---

## 6. FAQ 초안 생성 API

\`\`\`
POST /ai/faq/generate
POST /ai/faq/generate/batch
\`\`\`

---

## 7. RAG Gap 보완 제안 API

\`\`\`
POST /ai/gap/policy-edu/suggestions
\`\`\`

---

## 8. 영상 진행률 API (Phase 22)

\`\`\`
POST /api/video/play/start
POST /api/video/progress
POST /api/video/complete
GET /api/video/status
GET /api/video/quiz/check
\`\`\`

---

## 9. 관리자 API (Phase 26)

### 9.1 교육 재발행

\`\`\`
POST /api/admin/education/reissue
\`\`\`

### 9.2 교육 메타데이터 조회

\`\`\`
GET /api/admin/education/{education_id}
\`\`\`

---

## 10. 영상 생성 API (Phase 27-42)

### 10.1 스크립트 생성/승인/조회

\`\`\`
POST /api/scripts
POST /api/scripts/{script_id}/approve
GET /api/scripts/{script_id}
\`\`\`

### 10.2 렌더 잡 (V2)

\`\`\`
POST /api/v2/videos/{video_id}/render-jobs
GET /api/render-jobs/{job_id}
\`\`\`

### 10.3 영상 발행

\`\`\`
POST /api/videos/{video_id}/publish
\`\`\`

### 10.4 WebSocket 렌더 진행률

\`\`\`
WS /ws/videos/{video_id}/render-progress
\`\`\`

---

## 11. 에러 응답 표준

| 코드 | 설명 |
|------|------|
| 200 | 성공 |
| 201 | 생성됨 |
| 202 | 수락됨 (비동기 처리) |
| 400 | 잘못된 요청 |
| 404 | 리소스 없음 |
| 500 | 서버 내부 오류 |

---

## 12. 중요 연동 가이드

### 12.7 타임아웃 설정 권장값

| API | 권장 타임아웃 |
|-----|---------------|
| \`/ai/chat/messages\` | 30초 |
| \`/ai/chat/stream\` | 60초 |
| \`/internal/rag/index\` | 120초 |
| \`/internal/rag/delete\` | 30초 |
| \`/ai/quiz/generate\` | 60초 |
| \`/api/video/*\` | 5초 |
| \`/api/scripts/*\` | 60초 |

---

## 부록: 전체 API 요약

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | \`/health\` | Liveness 체크 |
| GET | \`/health/ready\` | Readiness 체크 |
| POST | \`/ai/chat/messages\` | AI 채팅 응답 생성 |
| POST | \`/ai/chat/stream\` | 스트리밍 채팅 응답 |
| POST | \`/internal/rag/index\` | 문서 인덱싱 (Milvus) |
| POST | \`/internal/rag/delete\` | 문서 삭제 (Milvus) |
| GET | \`/internal/jobs/{job_id}\` | 작업 상태 조회 |
| POST | \`/ai/quiz/generate\` | 퀴즈 자동 생성 |
| POST | \`/ai/faq/generate\` | FAQ 초안 생성 |
| POST | \`/ai/gap/policy-edu/suggestions\` | RAG Gap 보완 제안 |
| POST | \`/api/video/play/start\` | 영상 재생 시작 |
| POST | \`/api/video/progress\` | 영상 진행률 업데이트 |
| POST | \`/api/video/complete\` | 영상 완료 요청 |
| GET | \`/api/video/status\` | 영상 상태 조회 |
| GET | \`/api/video/quiz/check\` | 퀴즈 시작 가능 여부 |
| POST | \`/api/admin/education/reissue\` | 교육 재발행 |
| GET | \`/api/admin/education/{id}\` | 교육 메타데이터 조회 |
| POST | \`/api/scripts\` | 스크립트 생성 |
| POST | \`/api/scripts/{id}/approve\` | 스크립트 승인 |
| GET | \`/api/scripts/{id}\` | 스크립트 조회 |
| POST | \`/api/v2/videos/{id}/render-jobs\` | 렌더 잡 생성 (V2) |
| GET | \`/api/render-jobs/{id}\` | 렌더 잡 상태 조회 |
| POST | \`/api/videos/{id}/publish\` | 영상 발행 |
| WS | \`/ws/videos/{id}/render-progress\` | 렌더 진행률 (실시간) |

---

## 변경 이력 (2025-12-19)

### 삭제된 API

| API | 대체 API | 삭제 사유 |
|-----|----------|-----------|
| \`POST /search\` | ChatService 내부 Milvus 직접 검색 | RAGFlow 레거시, Phase 25 전환 |
| \`POST /ingest\` | \`POST /internal/rag/index\` | RAGFlow 레거시, Milvus 직접 인덱싱으로 대체 |
| \`POST /ai/rag/process\` | \`POST /internal/rag/index\` | RagflowClient 레거시 |

### 신규 추가된 API

| API | Phase | 설명 |
|-----|-------|------|
| \`POST /internal/rag/index\` | Phase 25 | Milvus 직접 문서 인덱싱 |
| \`POST /internal/rag/delete\` | Phase 25 | Milvus 직접 문서 삭제 |
| \`GET /internal/jobs/{job_id}\` | Phase 25 | 인덱싱 작업 상태 조회 |
| \`POST /api/admin/education/reissue\` | Phase 26 | 교육 재발행 |
| \`GET /api/admin/education/{id}\` | Phase 26 | 교육 메타데이터 조회 |
| \`POST /api/scripts\` | Phase 27 | 스크립트 생성 |
| \`POST /api/scripts/{id}/approve\` | Phase 27 | 스크립트 승인 |
| \`POST /api/v2/videos/{id}/render-jobs\` | Phase 33 | 렌더 잡 생성 (V2) |
| \`POST /api/videos/{id}/publish\` | Phase 28 | 영상 발행 + KB 인덱싱 |
| \`WS /ws/videos/{id}/render-progress\` | Phase 32 | 렌더 진행률 WebSocket |

### Deprecated API

| V1 API | V2 API | 비고 |
|--------|--------|------|
| \`POST /api/videos/{id}/render-jobs\` | \`POST /api/v2/videos/{id}/render-jobs\` | V2는 Idempotent |
| \`POST /api/render-jobs/{id}/cancel\` | \`POST /api/v2/.../cancel\` | V2 경로 표준화 |

---

**문의**: AI팀
**최종 수정**: 2025-12-19
