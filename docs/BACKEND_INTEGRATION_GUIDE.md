# 백엔드팀 연동 가이드

> **최종 수정일**: 2025-12-31
> **버전**: 2.0
> **대상**: ctrlf-back (Spring Backend) 개발팀

---

## 핵심 요약 (Quick Start)

### 서비스 연결 정보

| 서비스 | URL | 용도 |
|--------|-----|------|
| **AI Gateway** | `http://localhost:8000` | 채팅/RAG/영상 생성 통합 API |
| **RAGFlow** | `http://your-ragflow-host:9380` | RAG 검색 |
| **LLM Server** | `http://your-llm-server:port/v1` | LLM 응답 생성 |
| **Milvus** | `<MILVUS_HOST>:<MILVUS_PORT>` | 벡터 DB (RAG 검색) |
| **Embedding Server** | `http://your-embedding-server:port/v1/embeddings` | 임베딩 생성 |

### Milvus 핵심 정보

```
Collection: ragflow_chunks_sroberta
Embedding Model: jhgan/ko-sroberta-multitask
Dimension: 768
Metric: COSINE
```

### Dataset ID (한글 카테고리명)

| dataset_id | 도메인 | 청크 수 |
|------------|--------|--------|
| `사내규정` | POLICY | 390 |
| `정보보안교육` | EDUCATION | 1,442 |
| `직장내성희롱교육` | EDUCATION | 486 |
| `직무교육` | EDUCATION | 330 |
| `직장내괴롭힘교육` | EDUCATION | 152 |
| `장애인인식개선교육` | EDUCATION | 125 |

> **주의**: `kb_policy_001` 같은 ID는 존재하지 않습니다. 실제 dataset_id는 위의 한글 값입니다.

---

## 목차

1. [로컬 테스트 환경 구축](#1-로컬-테스트-환경-구축)
2. [AI Gateway API 연동](#2-ai-gateway-api-연동)
3. [채팅 API 상세](#3-채팅-api-상세)
4. [Internal API (Backend → AI)](#4-internal-api-backend--ai)
5. [에러 처리](#5-에러-처리)
6. [체크리스트](#6-체크리스트)

---

## 1. 로컬 테스트 환경 구축

### 1.1 아키텍처 개요

```
┌─────────────────────────────────────────────────────────────────┐
│                        로컬 PC                                  │
│  ┌─────────────────┐      ┌─────────────────┐                  │
│  │  AI Gateway     │◄────►│  Backend        │                  │
│  │  (FastAPI)      │      │  (Spring)       │                  │
│  │  localhost:8000 │      │  localhost:9002 │                  │
│  └────────┬────────┘      └─────────────────┘                  │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    외부 GPU 서버                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ vLLM (LLM)   │  │ Embedding    │  │ Milvus       │          │
│  │ :<LLM_PORT>  │  │ :<EMB_PORT>  │  │ :<MILVUS_PORT>│         │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 서비스별 역할

| 서비스 | 위치 | 포트 | 역할 |
|--------|------|------|------|
| **AI Gateway** | 로컬 | 8000 | 채팅/RAG API, LLM 호출 |
| **Backend** | 로컬 | 9002 | 콜백 수신, 사용자/세션 관리 |
| **vLLM** | 외부 | .env 참조 | LLM 응답 생성 (Qwen2.5-7B) |
| **Embedding** | 외부 | .env 참조 | 임베딩 벡터 생성 (ko-sroberta) |
| **Milvus** | 외부 | .env 참조 | 벡터 검색 |

### 1.3 테스트 시나리오별 최소 구성

| 테스트 목적 | 필요한 서비스 | 백엔드 |
|------------|--------------|--------|
| **기본 AI 채팅** | AI Gateway + vLLM + RAGFlow | 불필요 |
| **채팅 + 로그 저장** | 위 + chat-service | chat-service만 |
| **스크립트/영상 생성** | AI Gateway + vLLM + education-service + FFmpeg | education-service |
| **퀴즈 생성** | AI Gateway + vLLM + RAGFlow | quiz-service (선택) |
| **전체 통합 테스트** | 모든 서비스 | 모두 |

> **핵심**: 기본 AI 채팅은 **백엔드 없이도** 테스트 가능합니다.

### 1.4 AI Gateway 실행

```bash
# 1. ctrlf-ai 폴더로 이동
cd ctrlf-ai

# 2. 가상환경 생성 & 활성화
python -m venv venv
.\venv\Scripts\activate        # Windows
# source venv/bin/activate     # Linux/Mac

# 3. 의존성 설치
pip install -r requirements.txt

# 4. 서버 실행
uvicorn app.main:app --reload --port 8000
```

**확인:**
```bash
curl http://localhost:8000/health
```

---

## 2. AI Gateway API 연동

### 2.1 아키텍처

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────┐
│ ctrlf-back  │────▶│  AI Gateway     │────▶│  RAGFlow    │
│ (Spring)    │     │  (FastAPI)      │     │  (RAG)      │
└─────────────┘     └─────────────────┘     └─────────────┘
                            │
                            ▼
                    ┌─────────────────┐
                    │  LLM Server     │
                    │  (Qwen2.5-7B)   │
                    └─────────────────┘
```

### 2.2 API 엔드포인트

| 엔드포인트 | 메서드 | 설명 |
|-----------|--------|------|
| `/health` | GET | 서버 상태 확인 |
| `/health/ready` | GET | 준비 상태 확인 |
| `/ai/chat/messages` | POST | **동기 채팅 응답 생성** |
| `/ai/chat/stream` | POST | **스트리밍 채팅 응답 (NDJSON)** |
| `/ai/gap/policy-edu/suggestions` | POST | RAG Gap 보완 제안 |
| `/ai/quiz/generate` | POST | 퀴즈 생성 |
| `/ai/faq/generate` | POST | FAQ 생성 |

---

## 3. 채팅 API 상세

### 3.1 동기 채팅 - POST /ai/chat/messages

**요청:**

```json
{
  "session_id": "sess-uuid-1234",
  "user_id": "emp-001",
  "user_role": "EMPLOYEE",
  "domain": "POLICY",
  "department": "개발팀",
  "channel": "WEB",
  "messages": [
    {
      "role": "user",
      "content": "연차 이월 규정이 어떻게 되나요?"
    }
  ]
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `session_id` | string | O | 세션 식별자 |
| `user_id` | string | O | 사용자 ID |
| `user_role` | string | O | `EMPLOYEE`, `ADMIN`, `INCIDENT_MANAGER` |
| `domain` | string | X | `POLICY`, `EDU`, `INCIDENT` |
| `department` | string | X | 부서명 |
| `channel` | string | X | `WEB`, `MOBILE`, `SLACK` |
| `messages` | array | O | 메시지 배열 |

**응답:**

```json
{
  "answer": "연차휴가는 다음 해 말일까지 최대 10일까지 이월할 수 있습니다.\n\n[참고 근거]\n- 연차휴가 관리 규정 제10조(연차 이월) 참조",
  "sources": [
    {
      "doc_id": "doc-001",
      "title": "연차휴가 관리 규정",
      "page": 5,
      "score": 0.92,
      "snippet": "연차는 다음 해 말일까지 최대 10일까지 이월할 수 있다...",
      "article_label": "제10조(연차 이월) 참조"
    }
  ],
  "meta": {
    "user_role": "EMPLOYEE",
    "route": "RAG_INTERNAL",
    "intent": "POLICY_QA",
    "rag_used": true,
    "latency_ms": 1250
  }
}
```

### 3.2 스트리밍 채팅 - POST /ai/chat/stream

> 자세한 내용은 [STREAMING_API_GUIDE.md](./STREAMING_API_GUIDE.md) 참조

**요청:** 동기 채팅과 동일 + `request_id` 필드 추가

```json
{
  "request_id": "req-uuid-001",
  "session_id": "sess-uuid-1234",
  "user_id": "emp-001",
  "user_role": "EMPLOYEE",
  "messages": [{"role": "user", "content": "연차휴가 규정"}]
}
```

**응답:** NDJSON 스트리밍

### 3.3 Java/Spring 연동 예시

```java
@Service
public class AiGatewayClient {

    private final WebClient webClient;

    public AiGatewayClient(@Value("${ai.gateway.url}") String baseUrl) {
        this.webClient = WebClient.builder()
            .baseUrl(baseUrl)
            .build();
    }

    public Mono<ChatResponse> chat(ChatRequest request) {
        return webClient.post()
            .uri("/ai/chat/messages")
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue(request)
            .retrieve()
            .bodyToMono(ChatResponse.class);
    }
}
```

### 3.4 curl 테스트

```bash
curl -X POST http://localhost:8000/ai/chat/messages \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-001",
    "user_id": "emp-001",
    "user_role": "EMPLOYEE",
    "domain": "POLICY",
    "messages": [{"role": "user", "content": "연차 이월 규정이 어떻게 되나요?"}]
  }'
```

---

## 4. Internal API (Backend → AI)

> **인증**: `X-Internal-Token` 헤더 필수

### 4.1 SourceSet 오케스트레이션

**POST /internal/ai/source-sets/{sourceSetId}/start**

소스셋의 문서들을 RAGFlow로 적재하고 스크립트를 자동 생성합니다.

```bash
curl -X POST http://localhost:8000/internal/ai/source-sets/test-source-set-001/start \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: your-token" \
  -d '{
    "video_id": "video-001",
    "education_id": "edu-001"
  }'
```

**응답 (202 Accepted):**
```json
{
  "source_set_id": "test-source-set-001",
  "status": "PROCESSING",
  "message": "Processing started"
}
```

### 4.2 렌더 잡 (영상 생성)

**POST /internal/ai/render-jobs**

```bash
curl -X POST http://localhost:8000/internal/ai/render-jobs \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: your-token" \
  -d '{
    "jobId": "render-job-001",
    "videoId": "video-001",
    "scriptId": "script-001"
  }'
```

**응답 (202 Accepted):**
```json
{
  "received": true,
  "jobId": "render-job-001",
  "status": "PROCESSING"
}
```

### 4.3 RAG 문서 Ingest (사내규정)

**POST /internal/ai/rag-documents/ingest**

```bash
curl -X POST http://localhost:8000/internal/ai/rag-documents/ingest \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: your-token" \
  -d '{
    "ragDocumentPk": "uuid-001",
    "documentId": "POL-001",
    "version": 1,
    "sourceUrl": "https://s3.../doc.pdf",
    "domain": "POLICY",
    "requestId": "req-001",
    "traceId": "trace-001"
  }'
```

### 4.4 피드백

**POST /internal/ai/feedback**

사용자 피드백을 저장합니다.

---

## 5. 에러 처리

### 5.1 HTTP 상태 코드

| 코드 | 설명 |
|------|------|
| 200 | 성공 |
| 202 | 접수됨 (비동기 처리 시작) |
| 400 | 잘못된 요청 |
| 401 | 인증 실패 (Internal API) |
| 403 | 권한 없음 |
| 404 | 리소스 없음 |
| 410 | 제거된 엔드포인트 (Deprecated API) |
| 422 | 유효성 검증 실패 |
| 500 | 서버 내부 오류 |
| 503 | RAG 서비스 불가 (RAGFlow 장애) |

### 5.2 Fallback 응답

RAGFlow 장애 시:

```json
{
  "error": "RAG_SERVICE_UNAVAILABLE",
  "message": "RAG 검색 서비스가 일시적으로 불가합니다."
}
```

---

## 6. 체크리스트

### AI Gateway 연동 시

- [ ] `/health` 엔드포인트로 연결 확인
- [ ] `session_id`, `user_id`, `user_role` 필수 전달
- [ ] 타임아웃 설정 (권장: 30초)
- [ ] Fallback 응답 처리 (503 대응)

### Internal API 사용 시

- [ ] `X-Internal-Token` 헤더 설정
- [ ] 비동기 응답 (202) 처리
- [ ] 콜백 수신 엔드포인트 준비

---

## 변경 이력

| 날짜 | 버전 | 내용 |
|------|------|------|
| 2025-12-31 | 2.0 | 실제 AI 서버 API만 반영, 불필요한 내용 제거 |
| 2025-12-28 | 1.0 | 초기 작성 |

---

## 문의

- GitHub: https://github.com/skRookies4team/ctrlf-ai
- Swagger: http://[AI_GATEWAY_HOST]:8000/docs
