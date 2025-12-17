# CTRL+F AI Gateway API 명세서 (백엔드팀 전달용)

> **Base URL**: `http://{AI_GATEWAY_HOST}:{PORT}`
> **Content-Type**: `application/json`
> **문서 버전**: 2025-12-17
> **OpenAPI 문서**: `/docs` (Swagger UI), `/redoc` (ReDoc)

---

## 목차

1. [헬스체크 API](#1-헬스체크-api)
2. [채팅 API](#2-채팅-api-핵심)
3. [스트리밍 채팅 API](#3-스트리밍-채팅-api)
4. [RAG 검색 API](#4-rag-검색-api)
5. [문서 인덱싱 API](#5-문서-인덱싱-api)
6. [퀴즈 자동 생성 API](#6-퀴즈-자동-생성-api)
7. [FAQ 초안 생성 API](#7-faq-초안-생성-api)
8. [RAG Gap 보완 제안 API](#8-rag-gap-보완-제안-api)
9. [영상 진행률 API](#9-영상-진행률-api-phase-22)
10. [에러 응답 표준](#10-에러-응답-표준)
11. [중요 연동 가이드](#11-중요-연동-가이드)

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
  "answer": "연차휴가는 입사 1년 경과 시 15일이 부여됩니다. 상세 내용은 인사규정 제15조를 참고해 주세요.",
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
    "masked": false,
    "has_pii_input": false,
    "has_pii_output": false,
    "rag_used": true,
    "rag_source_count": 3,
    "latency_ms": 1250,
    "rag_latency_ms": 450,
    "llm_latency_ms": 800,
    "rag_gap_candidate": false
  }
}
```

**Response 필드 설명**

| 필드 | 설명 |
|------|------|
| `answer` | AI 생성 응답 텍스트 |
| `sources` | RAG에서 찾은 근거 문서 목록 |
| `sources[].article_label` | 조항 라벨 (예: "제15조 (연차휴가)") |
| `sources[].article_path` | 조항 경로 (예: "제4장 > 제15조") |
| `meta.route` | 라우팅 경로 (`RAG_INTERNAL`, `BACKEND_API`, `LLM_ONLY`, `FALLBACK`) |
| `meta.intent` | 분류된 의도 (`POLICY_QA`, `EDUCATION_QA`, `INCIDENT_REPORT` 등) |
| `meta.masked` | PII 마스킹 적용 여부 |
| `meta.rag_gap_candidate` | RAG Gap 후보 여부 (POLICY/EDU 도메인에서 근거 문서 없을 때 true) |
| `meta.error_type` | 에러 발생 시: `UPSTREAM_TIMEOUT`, `UPSTREAM_ERROR`, `BAD_REQUEST` |
| `meta.fallback_reason` | Fallback 발생 시 원인: `RAG_FAIL`, `LLM_FAIL`, `BACKEND_FAIL` |

> **중요**: `meta.rag_gap_candidate=true`인 경우 해당 질문을 로깅하여 추후 RAG Gap 분석에 활용할 수 있습니다.

---

## 3. 스트리밍 채팅 API

HTTP 청크 스트리밍으로 AI 응답을 실시간 전송합니다. 백엔드(Spring)는 NDJSON을 줄 단위로 읽어서 SSE로 변환합니다.

### 3.1 스트리밍 채팅 응답 생성

```
POST /ai/chat/stream
```

**Content-Type**
- Request: `application/json`
- Response: `application/x-ndjson`
- Transfer-Encoding: `chunked`

**Request Body**
```json
{
  "request_id": "req-uuid-001",
  "user_message": "연차휴가 규정이 어떻게 되나요?",
  "role": "employee",
  "session_id": "sess-uuid-001",
  "metadata": {
    "department": "개발팀"
  }
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `request_id` | string | **O** | 중복 방지 / 재시도용 고유 키 (Idempotency Key) |
| `user_message` | string | **O** | 사용자 메시지 |
| `role` | string | X | 사용자 역할: `employee`, `creator`, `reviewer`, `admin` (기본: `employee`) |
| `session_id` | string | X | 채팅 세션 ID |
| `metadata` | object | X | 추가 메타데이터 |

### 3.2 응답 형식 (NDJSON)

**NDJSON (Newline Delimited JSON)** 형식으로 응답합니다.

**중요 규칙:**
- 한 줄 = 한 JSON
- 각 JSON 뒤에 반드시 `\n` (줄바꿈)
- 백엔드는 줄 단위로 파싱하여 SSE로 변환

### 3.3 이벤트 타입

#### 3.3.1 meta (시작)

연결 직후 1회 전송. 침묵 시간을 없애고 연결 상태를 확정합니다.

```json
{"type":"meta","request_id":"req-uuid-001","model":"gpt-4o-mini","timestamp":"2025-01-01T10:00:00.000000"}
```

| 필드 | 설명 |
|------|------|
| `type` | `"meta"` |
| `request_id` | 요청 ID |
| `model` | 사용 모델명 |
| `timestamp` | 시작 시간 (ISO 8601) |

#### 3.3.2 token (토큰 스트림)

여러 번 전송. 텍스트는 **증분(delta)** 으로 전송됩니다.

```json
{"type":"token","text":"안"}
{"type":"token","text":"녕"}
{"type":"token","text":"하"}
{"type":"token","text":"세"}
{"type":"token","text":"요"}
```

| 필드 | 설명 |
|------|------|
| `type` | `"token"` |
| `text` | 토큰 텍스트 (증분, 백엔드가 이어붙임) |

#### 3.3.3 done (정상 종료)

정상 종료 시 1회 전송.

```json
{"type":"done","finish_reason":"stop","total_tokens":123,"elapsed_ms":4567,"ttfb_ms":200}
```

| 필드 | 설명 |
|------|------|
| `type` | `"done"` |
| `finish_reason` | 종료 사유 (`"stop"`) |
| `total_tokens` | 총 토큰 수 (선택) |
| `elapsed_ms` | 총 소요 시간 (ms) |
| `ttfb_ms` | 첫 토큰까지 시간 (ms) |

#### 3.3.4 error (에러)

에러 시 1회 전송. **에러 후 즉시 스트림 종료.**

```json
{"type":"error","code":"LLM_TIMEOUT","message":"LLM 응답 시간 초과","request_id":"req-uuid-001"}
```

| 필드 | 설명 |
|------|------|
| `type` | `"error"` |
| `code` | 에러 코드 |
| `message` | 에러 메시지 |
| `request_id` | 요청 ID |

### 3.4 에러 코드

| 코드 | 설명 |
|------|------|
| `LLM_TIMEOUT` | LLM 응답 시간 초과 |
| `LLM_ERROR` | LLM 서비스 오류 |
| `DUPLICATE_INFLIGHT` | 중복 요청 (이미 처리 중) |
| `INVALID_REQUEST` | 잘못된 요청 |
| `INTERNAL_ERROR` | 내부 서버 오류 |
| `CLIENT_DISCONNECTED` | 클라이언트 연결 끊김 |

### 3.5 중복 요청 방지

동일한 `request_id`로 요청이 이미 처리 중이면 `DUPLICATE_INFLIGHT` 에러를 반환합니다.

```json
{"type":"error","code":"DUPLICATE_INFLIGHT","message":"이미 처리 중인 요청입니다. 잠시 후 다시 시도해주세요.","request_id":"test-001"}
```

**재시도 버튼 대응:**
1. 프론트에서 재시도 클릭 시 **동일한 `request_id`** 전송
2. AI 서버가 이미 처리 중이면 `DUPLICATE_INFLIGHT` 반환
3. 백엔드는 이 에러를 받으면 "잠시 후 다시 시도" 안내

**캐시 TTL:** 완료된 요청은 10분간 캐시됨

### 3.6 Spring WebClient 연동 예제

```java
WebClient webClient = WebClient.create("http://ai-server:8000");

Flux<String> stream = webClient.post()
    .uri("/ai/chat/stream")
    .contentType(MediaType.APPLICATION_JSON)
    .bodyValue(request)
    .retrieve()
    .bodyToFlux(String.class)  // 줄 단위로 수신
    .map(line -> {
        JsonObject json = JsonParser.parseString(line).getAsJsonObject();
        String type = json.get("type").getAsString();

        switch (type) {
            case "meta":
                // 연결 확정
                break;
            case "token":
                // SSE로 전송
                return json.get("text").getAsString();
            case "done":
                // 완료 처리
                break;
            case "error":
                // 에러 처리
                throw new RuntimeException(json.get("message").getAsString());
        }
        return null;
    })
    .filter(Objects::nonNull);
```

### 3.7 curl 테스트 예제

```bash
curl -X POST http://localhost:8000/ai/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "request_id": "test-001",
    "user_message": "안녕하세요",
    "role": "employee",
    "session_id": "sess-001"
  }' \
  --no-buffer
```

**기대 출력:**
```
{"type":"meta","request_id":"test-001","model":"gpt-4o-mini","timestamp":"2025-01-01T10:00:00.000000"}
{"type":"token","text":"안"}
{"type":"token","text":"녕"}
{"type":"token","text":"하"}
{"type":"token","text":"세"}
{"type":"token","text":"요"}
{"type":"done","finish_reason":"stop","total_tokens":5,"elapsed_ms":1234,"ttfb_ms":150}
```

---

## 4. RAG 검색 API

### 4.1 RAG 문서 검색

RAGFlow를 통해 관련 문서를 검색합니다.

```
POST /search
```

**Request Body**
```json
{
  "query": "USB 반출 절차",
  "top_k": 5,
  "dataset": "policy"
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `query` | string | O | 검색 쿼리 |
| `top_k` | int | X | 반환 결과 수 (기본: 5, 최대: 100) |
| `dataset` | string | O | 데이터셋: `policy`, `training`, `incident`, `education` |

**Response 200**
```json
{
  "results": [
    {
      "doc_id": "chunk-001",
      "title": "정보보안규정",
      "page": 12,
      "score": 0.89,
      "snippet": "USB 메모리 반출 시에는 정보보호팀의 사전 승인을...",
      "dataset": "policy",
      "source": "ragflow"
    }
  ]
}
```

**에러 응답 400**
```json
{
  "detail": "Dataset 'unknown' not found. Available: policy, training, incident, education"
}
```

---

## 5. 문서 인덱싱 API

### 5.1 문서 인덱싱 요청

RAGFlow에 문서를 인덱싱합니다.

```
POST /ingest
```

**Request Body**
```json
{
  "doc_id": "DOC-2025-00123",
  "source_type": "policy",
  "storage_url": "https://files.internal/documents/DOC-2025-00123.pdf",
  "file_name": "정보보안규정_v3.pdf",
  "mime_type": "application/pdf",
  "department": "정보보안팀",
  "acl": ["ROLE_EMPLOYEE", "DEPT_DEV"],
  "tags": ["보안", "USB", "반출"],
  "version": 3
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `doc_id` | string | O | 백엔드 문서 ID |
| `source_type` | string | O | 문서 유형: `policy`, `training`, `incident`, `education` |
| `storage_url` | string | O | 파일 다운로드 URL (AI Gateway가 접근 가능한 내부 URL) |
| `file_name` | string | O | 파일명 |
| `mime_type` | string | O | MIME 타입: `application/pdf`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document` 등 |
| `department` | string | X | 소속 부서 |
| `acl` | array | X | 접근 제어 리스트 |
| `tags` | array | X | 태그 목록 |
| `version` | int | X | 문서 버전 (기본: 1) |

**Response 200**
```json
{
  "task_id": "ingest-2025-000001",
  "status": "DONE"
}
```

| status 값 | 설명 |
|-----------|------|
| `DONE` | 인덱싱 완료 |
| `QUEUED` | 대기열 등록 |
| `PROCESSING` | 처리 중 |
| `FAILED` | 실패 |

**에러 응답**
- `400`: 알 수 없는 source_type
- `502`: RAGFlow 서비스 오류

---

## 6. 퀴즈 자동 생성 API

### 6.1 퀴즈 생성

교육/사규 문서에서 객관식 퀴즈를 자동 생성합니다.

```
POST /ai/quiz/generate
```

**Request Body**
```json
{
  "educationId": "EDU-SEC-2025-001",
  "docId": "DOC-SEC-001",
  "docVersion": "v1",
  "attemptNo": 1,
  "language": "ko",
  "numQuestions": 10,
  "difficultyDistribution": {
    "easy": 5,
    "normal": 3,
    "hard": 2
  },
  "questionType": "MCQ_SINGLE",
  "maxOptions": 4,
  "quizCandidateBlocks": [
    {
      "blockId": "BLOCK-001",
      "chapterId": "CH1",
      "learningObjectiveId": "LO-1",
      "text": "USB 메모리를 사외로 반출할 때에는 정보보호팀의 사전 승인을 받아야 한다.",
      "tags": ["USB", "반출", "승인"],
      "articlePath": "제3장 > 제2조 > 제1항"
    }
  ],
  "excludePreviousQuestions": []
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `educationId` | string | O | 교육/코스 ID |
| `docId` | string | O | 사규/교육 문서 ID |
| `docVersion` | string | X | 문서 버전 (기본: "v1") |
| `attemptNo` | int | X | 응시 차수 (기본: 1, 최대: 10) |
| `language` | string | X | 언어 (기본: "ko") |
| `numQuestions` | int | X | 생성할 문항 수 (기본: 10, 최대: 50) |
| `difficultyDistribution` | object | X | 난이도별 문항 수 분배 |
| `questionType` | string | X | 문제 유형: `MCQ_SINGLE` |
| `maxOptions` | int | X | 보기 개수 (기본: 4, 범위: 2-6) |
| `quizCandidateBlocks` | array | O | 퀴즈 생성에 사용할 텍스트 블록 목록 |
| `excludePreviousQuestions` | array | X | 2차 응시 시 제외할 기존 문항 |

**Response 200**
```json
{
  "educationId": "EDU-SEC-2025-001",
  "docId": "DOC-SEC-001",
  "docVersion": "v1",
  "attemptNo": 1,
  "generatedCount": 10,
  "questions": [
    {
      "questionId": "Q-20251212-ABCD1234",
      "status": "DRAFT_AI_GENERATED",
      "questionType": "MCQ_SINGLE",
      "stem": "USB 메모리를 사외로 반출할 때 필요한 조치는?",
      "options": [
        {"optionId": "OPT-1", "text": "정보보호팀의 사전 승인", "isCorrect": true},
        {"optionId": "OPT-2", "text": "팀장에게 구두 보고", "isCorrect": false},
        {"optionId": "OPT-3", "text": "자유롭게 반출", "isCorrect": false},
        {"optionId": "OPT-4", "text": "사후 보고", "isCorrect": false}
      ],
      "difficulty": "EASY",
      "learningObjectiveId": "LO-1",
      "chapterId": "CH1",
      "sourceBlockIds": ["BLOCK-001"],
      "sourceDocId": "DOC-SEC-001",
      "sourceDocVersion": "v1",
      "sourceArticlePath": "제3장 > 제2조 > 제1항",
      "tags": ["USB", "반출"],
      "explanation": "USB 반출 시에는 반드시 정보보호팀의 사전 승인을 받아야 합니다.",
      "rationale": "정보보안규정 제3장 제2조 제1항에 명시"
    }
  ]
}
```

**2차 응시 (기존 문항 제외)**
```json
{
  "educationId": "EDU-SEC-2025-001",
  "docId": "DOC-SEC-001",
  "attemptNo": 2,
  "numQuestions": 10,
  "quizCandidateBlocks": [...],
  "excludePreviousQuestions": [
    {"questionId": "Q-20251212-ABCD1234", "stem": "USB 메모리를 사외로 반출할 때 필요한 조치는?"}
  ]
}
```

---

## 7. FAQ 초안 생성 API

### 7.1 FAQ 초안 생성 (단건)

FAQ 클러스터를 기반으로 FAQ 초안을 생성합니다.

```
POST /ai/faq/generate
```

**Request Body**
```json
{
  "domain": "SEC_POLICY",
  "cluster_id": "cluster-001",
  "canonical_question": "USB 메모리 반출 시 어떤 절차가 필요한가요?",
  "sample_questions": [
    "USB 외부 반출 어떻게 하나요?",
    "USB 가져가도 되나요?"
  ],
  "top_docs": [
    {
      "doc_id": "DOC-SEC-001",
      "doc_version": "v1",
      "title": "정보보안규정",
      "snippet": "USB 메모리 반출 시에는...",
      "article_label": "제3장 제2조",
      "article_path": "제3장 > 제2조"
    }
  ],
  "answer_source_hint": "AI_RAG"
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `domain` | string | O | 도메인: `SEC_POLICY`, `PII_PRIVACY`, `HR_POLICY` 등 |
| `cluster_id` | string | O | FAQ 후보 클러스터 ID |
| `canonical_question` | string | O | 클러스터 대표 질문 |
| `sample_questions` | array | X | 실제 직원 질문 예시 |
| `top_docs` | array | X | RAG에서 뽑은 후보 문서 (있으면 RAG 재호출 스킵) |
| `answer_source_hint` | string | X | 답변 생성 힌트: `AI_RAG`, `LOG_REUSE` |

**Response 200 (성공)**
```json
{
  "status": "SUCCESS",
  "faq_draft": {
    "faq_draft_id": "FAQ-cluster-001-a1b2c3d4",
    "domain": "SEC_POLICY",
    "cluster_id": "cluster-001",
    "question": "USB 메모리 반출 시 어떤 절차가 필요한가요?",
    "answer_markdown": "**정보보호팀의 사전 승인이 필요합니다.**\n\n- USB 반출 신청서 작성\n- 정보보호팀 승인 요청\n- 승인 후 반출 가능",
    "summary": "정보보호팀의 사전 승인이 필요합니다.",
    "source_doc_id": "DOC-SEC-001",
    "source_doc_version": "v1",
    "source_article_label": "제3장 제2조",
    "source_article_path": "제3장 > 제2조",
    "answer_source": "AI_RAG",
    "ai_confidence": 0.85,
    "created_at": "2025-12-16T10:00:00Z"
  },
  "error_message": null
}
```

**Response 200 (실패)**
```json
{
  "status": "FAILED",
  "faq_draft": null,
  "error_message": "LLM 응답 파싱 실패: JSON 형식 오류"
}
```

### 7.2 FAQ 초안 배치 생성

다수의 FAQ를 한 번에 생성합니다. 각 항목은 독립적으로 처리됩니다.

```
POST /ai/faq/generate/batch
```

**Request Body**
```json
{
  "items": [
    {
      "domain": "SEC_POLICY",
      "cluster_id": "cluster-001",
      "canonical_question": "USB 반출 절차는?"
    },
    {
      "domain": "HR_POLICY",
      "cluster_id": "cluster-002",
      "canonical_question": "연차 신청 방법은?"
    }
  ],
  "concurrency": 3
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `items` | array | O | FAQ 생성 요청 리스트 (최소 1개) |
| `concurrency` | int | X | 동시 처리 수 (기본: 서버 설정, 범위: 1-10) |

**Response 200**
```json
{
  "items": [
    {"status": "SUCCESS", "faq_draft": {...}, "error_message": null},
    {"status": "FAILED", "faq_draft": null, "error_message": "PII_DETECTED"}
  ],
  "total_count": 2,
  "success_count": 1,
  "failed_count": 1
}
```

---

## 8. RAG Gap 보완 제안 API

### 8.1 보완 제안 생성

RAG Gap 질문들을 분석하여 사규/교육 보완 제안을 생성합니다.

```
POST /ai/gap/policy-edu/suggestions
```

**Request Body**
```json
{
  "timeRange": {
    "from": "2025-01-01T00:00:00Z",
    "to": "2025-12-31T23:59:59Z"
  },
  "domain": "POLICY",
  "groupingKey": "intent",
  "questions": [
    {
      "questionId": "log-123",
      "text": "재택근무할 때 VPN 안 쓰면 어떻게 되나요?",
      "userRole": "EMPLOYEE",
      "intent": "POLICY_QA",
      "domain": "POLICY",
      "askedCount": 15
    }
  ]
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `timeRange` | object | X | 분석 기간 |
| `domain` | string | X | 도메인 필터: `POLICY`, `EDU` |
| `groupingKey` | string | X | 그룹핑 기준: `intent`, `keyword`, `role` |
| `questions` | array | O | RAG Gap 질문 목록 |

**Response 200**
```json
{
  "summary": "재택근무 시 보안 규정에 대한 문서가 부족합니다. VPN 사용 의무 및 미사용 시 제재 사항을 명시한 조항을 추가하는 것을 권장합니다.",
  "suggestions": [
    {
      "id": "SUG-001",
      "title": "재택근무 시 정보보호 수칙 상세 예시 추가",
      "description": "VPN 사용 의무, 미사용 시 제재 사항 등을 포함한 조문을 신설하세요.",
      "relatedQuestionIds": ["log-123"],
      "priority": "HIGH"
    }
  ]
}
```

> **백엔드 활용 가이드**:
> 1. Chat API에서 `meta.rag_gap_candidate=true`인 질문을 수집
> 2. 주기적으로 (주 1회 등) 이 API를 호출하여 보완 제안 생성
> 3. 관리자 대시보드에서 제안 내용 검토 및 문서 개선

---

## 9. 영상 진행률 API (Phase 22)

교육 영상 시청 진행률을 추적하고 서버에서 검증합니다.

### 9.1 영상 재생 시작

```
POST /api/video/play/start
```

**Request Body**
```json
{
  "user_id": "EMP-12345",
  "training_id": "TRAIN-2025-001",
  "video_id": "VID-001",
  "total_duration": 600,
  "is_mandatory_edu": true
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `user_id` | string | O | 사용자 ID |
| `training_id` | string | O | 교육/영상 ID |
| `video_id` | string | X | 영상 ID (training_id와 같을 수 있음) |
| `total_duration` | int | O | 영상 총 길이 (초, 0보다 커야 함) |
| `is_mandatory_edu` | bool | X | 4대교육 여부 (기본: false) |

**Response 200**
```json
{
  "session_id": "sess-uuid-001",
  "user_id": "EMP-12345",
  "training_id": "TRAIN-2025-001",
  "state": "PLAYING",
  "seek_allowed": false,
  "created_at": "2025-12-16T10:00:00Z"
}
```

### 9.2 진행률 업데이트

```
POST /api/video/progress
```

**Request Body**
```json
{
  "user_id": "EMP-12345",
  "training_id": "TRAIN-2025-001",
  "current_position": 300,
  "watched_seconds": 295
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `user_id` | string | O | 사용자 ID |
| `training_id` | string | O | 교육/영상 ID |
| `current_position` | int | O | 현재 재생 위치 (초) |
| `watched_seconds` | int | O | 실제 시청한 누적 초 |

**Response 200 (수락)**
```json
{
  "user_id": "EMP-12345",
  "training_id": "TRAIN-2025-001",
  "progress_percent": 49.2,
  "watched_seconds": 295,
  "last_position": 300,
  "seek_allowed": false,
  "state": "PLAYING",
  "updated_at": "2025-12-16T10:05:00Z",
  "accepted": true,
  "rejection_reason": null
}
```

**Response 200 (거부 - 급상승 감지)**
```json
{
  "user_id": "EMP-12345",
  "training_id": "TRAIN-2025-001",
  "progress_percent": 10.0,
  "watched_seconds": 60,
  "last_position": 60,
  "seek_allowed": false,
  "state": "PLAYING",
  "updated_at": "2025-12-16T10:01:00Z",
  "accepted": false,
  "rejection_reason": "PROGRESS_SURGE"
}
```

**서버 검증 룰**
| 검증 | 조건 | 거부 사유 |
|------|------|-----------|
| 역행 금지 | 진행률이 감소 | `PROGRESS_REGRESSION` |
| 급상승 감지 | 10초 내 30% 이상 증가 | `PROGRESS_SURGE` |

### 9.3 영상 완료 요청

```
POST /api/video/complete
```

**Request Body**
```json
{
  "user_id": "EMP-12345",
  "training_id": "TRAIN-2025-001",
  "final_position": 590,
  "total_watched_seconds": 575
}
```

**Response 200 (완료)**
```json
{
  "user_id": "EMP-12345",
  "training_id": "TRAIN-2025-001",
  "completed": true,
  "progress_percent": 95.8,
  "quiz_unlocked": true,
  "seek_allowed": true,
  "completed_at": "2025-12-16T10:10:00Z",
  "rejection_reason": null
}
```

**Response 200 (거부)**
```json
{
  "user_id": "EMP-12345",
  "training_id": "TRAIN-2025-001",
  "completed": false,
  "progress_percent": 80.0,
  "quiz_unlocked": false,
  "seek_allowed": false,
  "completed_at": null,
  "rejection_reason": "COMPLETION_THRESHOLD_NOT_MET"
}
```

**완료 조건**
| 조건 | 설명 |
|------|------|
| 누적 시청률 >= 95% | `total_watched_seconds / total_duration >= 0.95` |
| 마지막 구간 시청 | 영상의 마지막 5% 구간 시청 기록 필요 |

**거부 사유**
| 코드 | 설명 |
|------|------|
| `COMPLETION_THRESHOLD_NOT_MET` | 95% 미달 |
| `FINAL_SEGMENT_NOT_WATCHED` | 마지막 구간 미시청 |
| `SESSION_NOT_FOUND` | 세션 없음 |
| `ALREADY_COMPLETED` | 이미 완료됨 |

### 9.4 상태 조회

```
GET /api/video/status?user_id=EMP-12345&training_id=TRAIN-2025-001
```

**Response 200**
```json
{
  "user_id": "EMP-12345",
  "training_id": "TRAIN-2025-001",
  "total_duration": 600,
  "watched_seconds": 575,
  "progress_percent": 95.8,
  "last_position": 590,
  "state": "COMPLETED",
  "seek_allowed": true,
  "quiz_unlocked": true,
  "is_mandatory_edu": true,
  "completed_at": "2025-12-16T10:10:00Z",
  "updated_at": "2025-12-16T10:10:00Z"
}
```

**Response 404**
```json
{
  "detail": "No video progress record found for user_id=EMP-12345, training_id=TRAIN-2025-001"
}
```

### 9.5 퀴즈 시작 가능 여부 확인

4대교육의 경우 영상 완료 후에만 퀴즈 시작 가능합니다.

```
GET /api/video/quiz/check?user_id=EMP-12345&training_id=TRAIN-2025-001
```

**Response 200 (시작 가능)**
```json
{
  "can_start": true,
  "reason": "Video completed, quiz unlocked"
}
```

**Response 403 (시작 불가)**
```json
{
  "detail": {
    "can_start": false,
    "reason": "Video not completed",
    "message": "영상을 먼저 완료해 주세요."
  }
}
```

---

## 10. 에러 응답 표준

### HTTP 상태 코드

| 코드 | 설명 |
|------|------|
| 200 | 성공 |
| 400 | 잘못된 요청 (파라미터 오류 등) |
| 403 | 접근 금지 (권한 없음) |
| 404 | 리소스 없음 |
| 422 | 유효성 검증 실패 |
| 500 | 서버 내부 오류 |
| 502 | 업스트림 서비스 오류 (RAGFlow, LLM 등) |

### 에러 응답 형식

```json
{
  "detail": "에러 상세 메시지"
}
```

또는 (422 Validation Error)

```json
{
  "detail": [
    {
      "loc": ["body", "user_id"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

---

## 11. 중요 연동 가이드

### 11.1 세션 ID 관리

- 백엔드에서 채팅 세션 ID(`session_id`)를 생성/관리합니다.
- 동일 세션 내 대화 연속성을 위해 `messages` 배열에 이전 대화 이력을 포함해야 합니다.
- AI Gateway는 세션 상태를 저장하지 않습니다 (Stateless).

### 11.2 사용자 역할 (user_role)

| 역할 | 설명 |
|------|------|
| `EMPLOYEE` | 일반 직원 |
| `MANAGER` | 관리자 |
| `ADMIN` | 시스템 관리자 |
| `INCIDENT_MANAGER` | 사고 담당자 |

### 11.3 도메인 (domain)

| 도메인 | 설명 |
|--------|------|
| `POLICY` | 사규/정책 |
| `INCIDENT` | 사고 신고/처리 |
| `EDUCATION` | 교육/훈련 |
| `SEC_POLICY` | 정보보안 정책 |
| `PII_PRIVACY` | 개인정보보호 |
| `HR_POLICY` | 인사 정책 |

### 11.4 PII 처리

- AI Gateway는 사용자 입력 및 LLM 출력에서 PII를 자동 감지/마스킹합니다.
- 강차단(Hard Block) 정책: 주민번호, 카드번호 등 민감정보 포함 시 요청 거부
- `meta.has_pii_input`, `meta.has_pii_output`으로 PII 감지 여부 확인 가능

### 11.5 RAG Gap 로깅

1. Chat API 응답에서 `meta.rag_gap_candidate=true` 확인
2. 해당 질문을 별도 테이블에 저장
3. 주기적으로 `/ai/gap/policy-edu/suggestions` 호출하여 보완 제안 생성

### 11.6 영상 진행률 연동 순서

```
1. POST /api/video/play/start  → 세션 생성
2. POST /api/video/progress    → 주기적 업데이트 (5초~10초 간격 권장)
3. POST /api/video/complete    → 완료 요청
4. GET /api/video/quiz/check   → 퀴즈 시작 전 확인 (4대교육)
```

### 11.7 타임아웃 설정 권장값

| API | 권장 타임아웃 |
|-----|---------------|
| `/ai/chat/messages` | 30초 |
| `/ai/chat/stream` | 60초 (첫 토큰 5초 + 스트리밍) |
| `/search` | 10초 |
| `/ingest` | 60초 |
| `/ai/quiz/generate` | 60초 |
| `/ai/faq/generate` | 30초 |
| `/ai/faq/generate/batch` | 120초 |
| `/api/video/*` | 5초 |

---

## 부록: 전체 API 요약

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | `/health` | Liveness 체크 |
| GET | `/health/ready` | Readiness 체크 |
| POST | `/ai/chat/messages` | AI 채팅 응답 생성 |
| POST | `/ai/chat/stream` | 스트리밍 채팅 응답 (NDJSON) |
| POST | `/search` | RAG 검색 |
| POST | `/ingest` | 문서 인덱싱 |
| POST | `/ai/quiz/generate` | 퀴즈 자동 생성 |
| POST | `/ai/faq/generate` | FAQ 초안 생성 (단건) |
| POST | `/ai/faq/generate/batch` | FAQ 초안 배치 생성 |
| POST | `/ai/gap/policy-edu/suggestions` | RAG Gap 보완 제안 |
| POST | `/api/video/play/start` | 영상 재생 시작 |
| POST | `/api/video/progress` | 영상 진행률 업데이트 |
| POST | `/api/video/complete` | 영상 완료 요청 |
| GET | `/api/video/status` | 영상 상태 조회 |
| GET | `/api/video/quiz/check` | 퀴즈 시작 가능 여부 |

---

**문의**: AI팀
**최종 수정**: 2025-12-17
