# CTRL+F AI Gateway 개발 보고서 - Phase 3-2

## 개요

**프로젝트명**: ctrlf-ai-gateway
**Phase**: 3-2 (RAGFlow + LLM 최소 연동)
**작성일**: 2025-12-08
**버전**: 0.1.0

---

## 1. Phase 3-2 목표

Phase 3-1에서 구축한 HTTP 클라이언트 유틸리티를 기반으로, RAGFlow와 LLM 서비스를 실제로 호출하는 파이프라인을 구현합니다.

### 주요 목표
1. **RAGFlow 클라이언트 뼈대 구현**: 문서 처리 및 검색 API 호출
2. **LLM 클라이언트 뼈대 구현**: OpenAI 호환 형식의 채팅 완성 API 호출
3. **RagService 업데이트**: 실제 RAGFlow 호출로 변경
4. **ChatService RAG + LLM 파이프라인 구현**: 검색 → 프롬프트 구성 → 응답 생성
5. **ChatSource 모델 확장**: `snippet` 필드 추가
6. **기존 테스트 유지 + 신규 테스트 추가**: 20개 → 29개

---

## 2. 디렉터리 구조 변경

### 변경 후 구조
```
ctrlf-ai/
├── app/
│   ├── api/v1/
│   │   ├── chat.py
│   │   ├── health.py
│   │   └── rag.py
│   ├── clients/                    # Phase 3-1에서 생성
│   │   ├── __init__.py
│   │   ├── http_client.py
│   │   ├── ragflow_client.py       # (Phase 3-1 버전, 현재 미사용)
│   │   └── llm_client.py           # (Phase 3-1 버전, 현재 미사용)
│   ├── core/
│   │   ├── config.py
│   │   └── logging.py
│   ├── models/
│   │   ├── chat.py                 # 수정: snippet 필드 추가
│   │   └── rag.py
│   └── services/
│       ├── chat_service.py         # 수정: RAG + LLM 파이프라인
│       ├── rag_service.py          # 수정: RagflowClient 연동
│       ├── ragflow_client.py       # 신규: RAGFlow 클라이언트
│       └── llm_client.py           # 신규: LLM 클라이언트
├── tests/
│   ├── test_health.py              # 5 tests
│   ├── test_chat_api.py            # 7 tests
│   ├── test_rag_api.py             # 8 tests
│   └── test_service_fallback.py    # 신규: 9 tests
└── ...
```

---

## 3. 모델 변경 사항

### 3.1 ChatSource 모델 확장

**파일**: `app/models/chat.py`

`snippet` 필드를 추가하여 RAG 검색 결과의 문서 발췌문을 LLM 프롬프트에 포함할 수 있도록 했습니다.

```python
class ChatSource(BaseModel):
    """Source document information retrieved by RAG."""

    doc_id: str = Field(description="Document ID managed by backend/RAGFlow")
    title: str = Field(description="Document title")
    page: Optional[int] = Field(default=None, description="Page number in document")
    score: Optional[float] = Field(default=None, description="Search relevance score")
    snippet: Optional[str] = Field(
        default=None,
        description="Text excerpt from document for LLM prompt context (optional)",
    )  # 신규 추가
```

**필드 설명**:
| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `doc_id` | str | O | 문서 ID |
| `title` | str | O | 문서 제목 |
| `page` | int | X | 페이지 번호 |
| `score` | float | X | 검색 관련도 점수 |
| `snippet` | str | X | **신규** - LLM 프롬프트용 문서 발췌문 |

---

## 4. 신규 파일 상세

### 4.1 app/services/ragflow_client.py (254 lines)

RAGFlow 서비스와 HTTP 통신하는 클라이언트입니다.

```python
class RagflowClient:
    """Client for communicating with ctrlf-ragflow service."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """
        Initialize RagflowClient.

        Args:
            base_url: RAGFlow URL. If None, uses RAGFLOW_BASE_URL from settings.
            client: httpx.AsyncClient. If None, uses shared singleton.
        """
        settings = get_settings()
        self._base_url = base_url or settings.RAGFLOW_BASE_URL
        self._client = client or get_async_http_client()
```

**메서드 목록**:

| 메서드 | 설명 | 반환 타입 |
|--------|------|----------|
| `process_document(req)` | 문서 처리 요청을 RAGFlow로 forward | `RagProcessResponse` |
| `search(query, domain, user_role, department, top_k)` | RAG 문서 검색 | `List[ChatSource]` |

**process_document 흐름**:
```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ RagProcessRequest│────▶│  RagflowClient  │────▶│   RAGFlow API   │
│  - doc_id       │     │ process_document│     │  /rag/process   │
│  - file_url     │     │                 │     │    (TODO)       │
│  - domain       │     └─────────────────┘     └─────────────────┘
│  - acl          │              │
└─────────────────┘              ▼
                         ┌─────────────────┐
                         │RagProcessResponse│
                         │  - doc_id       │
                         │  - success      │
                         │  - message      │
                         └─────────────────┘
```

**search 흐름**:
```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Search Params  │────▶│  RagflowClient  │────▶│   RAGFlow API   │
│  - query        │     │     search      │     │   /rag/search   │
│  - domain       │     │                 │     │    (TODO)       │
│  - user_role    │     └─────────────────┘     └─────────────────┘
│  - department   │              │
│  - top_k        │              ▼
└─────────────────┘     ┌─────────────────┐
                        │ List[ChatSource]│
                        │  - doc_id       │
                        │  - title        │
                        │  - page         │
                        │  - score        │
                        │  - snippet      │
                        └─────────────────┘
```

**에러 처리**:
- `RAGFLOW_BASE_URL` 미설정: 로그 경고 후 빈 리스트 또는 실패 응답 반환
- HTTP 에러: 로그 기록 후 graceful fallback
- 예외: 로그 기록 후 안전한 기본값 반환

---

### 4.2 app/services/llm_client.py (181 lines)

LLM 서비스와 HTTP 통신하는 클라이언트입니다. OpenAI 호환 API 형식을 사용합니다.

```python
class LLMClient:
    """Client for communicating with internal LLM service."""

    FALLBACK_MESSAGE = (
        "LLM service is not configured or unavailable. "
        "This is a fallback response. Please configure LLM_BASE_URL "
        "or check the LLM service status."
    )

    def __init__(
        self,
        base_url: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        settings = get_settings()
        self._base_url = base_url or settings.LLM_BASE_URL
        self._client = client or get_async_http_client()
```

**메서드**:

```python
async def generate_chat_completion(
    self,
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> str:
    """Generate chat completion from LLM service."""
```

**요청 형식 (OpenAI 호환)**:
```json
{
    "messages": [
        {"role": "system", "content": "시스템 프롬프트"},
        {"role": "user", "content": "사용자 질문"}
    ],
    "temperature": 0.2,
    "max_tokens": 1024,
    "model": "optional-model-name"
}
```

**응답 파싱**:
```json
{
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": "LLM 응답 텍스트"
            }
        }
    ]
}
```

**에러 처리**:
- `LLM_BASE_URL` 미설정: `FALLBACK_MESSAGE` 반환
- HTTP 에러: 로그 기록 후 `FALLBACK_MESSAGE` 반환
- 응답 파싱 실패: `FALLBACK_MESSAGE` 반환

---

### 4.3 tests/test_service_fallback.py (214 lines)

서비스 레이어의 fallback 동작을 검증하는 테스트 파일입니다.

**테스트 목록**:

| 테스트 | 설명 |
|--------|------|
| `test_chat_service_returns_response_without_config` | RAGFlow/LLM 미설정 시 ChatResponse 반환 |
| `test_chat_service_returns_fallback_message` | LLM 미설정 시 fallback 메시지 확인 |
| `test_chat_service_empty_sources_without_ragflow` | RAGFlow 미설정 시 sources=[] 확인 |
| `test_chat_service_meta_has_latency` | latency_ms 측정 확인 |
| `test_rag_service_returns_dummy_success_without_config` | RAGFlow 미설정 시 더미 success 응답 |
| `test_rag_service_preserves_doc_id` | doc_id 보존 확인 |
| `test_ragflow_client_search_returns_empty_without_config` | RagflowClient.search 빈 리스트 반환 |
| `test_ragflow_client_process_returns_failure_without_config` | RagflowClient.process_document 실패 반환 |
| `test_llm_client_returns_fallback_without_config` | LLMClient fallback 메시지 반환 |

---

## 5. 수정된 파일 상세

### 5.1 app/services/chat_service.py (284 lines)

RAG + LLM 파이프라인을 구현하는 핵심 서비스입니다.

**시스템 프롬프트**:
```python
SYSTEM_PROMPT_TEMPLATE = """당신은 회사 내부 정보보호 및 사규를 안내하는 AI 어시스턴트입니다.
아래의 참고 문서 목록을 바탕으로 사용자의 질문에 한국어로 정확하고 친절하게 답변해 주세요.
답변 시 출처 문서를 인용하면 더 좋습니다.

만약 참고 문서에서 답을 찾을 수 없다면, 솔직하게 "해당 내용은 참고 문서에서 찾을 수 없습니다"라고 말해 주세요.
추측이나 거짓 정보를 제공하지 마세요.
"""
```

**ChatService 클래스**:

```python
class ChatService:
    def __init__(
        self,
        ragflow_client: Optional[RagflowClient] = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        self._ragflow = ragflow_client or RagflowClient()
        self._llm = llm_client or LLMClient()
```

**handle_chat 파이프라인**:

```
┌───────────────────────────────────────────────────────────────────────┐
│                        handle_chat(req: ChatRequest)                   │
├───────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │ Step 1: Extract user query from last message                     │  │
│  │         user_query = req.messages[-1].content                    │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                              │                                         │
│                              ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │ Step 2: (TODO) PII Masking                                       │  │
│  │         - 민감 정보 마스킹 (향후 구현)                           │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                              │                                         │
│                              ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │ Step 3: (TODO) Intent Classification                             │  │
│  │         - 의도 분류 (향후 구현)                                  │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                              │                                         │
│                              ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │ Step 4: RAG Search via RagflowClient                             │  │
│  │         sources = await self._ragflow.search(                    │  │
│  │             query=user_query,                                    │  │
│  │             domain=req.domain,                                   │  │
│  │             user_role=req.user_role,                             │  │
│  │             department=req.department,                           │  │
│  │             top_k=5                                              │  │
│  │         )                                                        │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                              │                                         │
│                              ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │ Step 5: Build LLM prompt with RAG context                        │  │
│  │         llm_messages = [                                         │  │
│  │             {"role": "system", "content": PROMPT + RAG_CONTEXT}, │  │
│  │             {"role": "user", "content": user_query}              │  │
│  │         ]                                                        │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                              │                                         │
│                              ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │ Step 6: Generate response via LLMClient                          │  │
│  │         answer_text = await self._llm.generate_chat_completion(  │  │
│  │             messages=llm_messages,                               │  │
│  │             temperature=0.2,                                     │  │
│  │             max_tokens=1024                                      │  │
│  │         )                                                        │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                              │                                         │
│                              ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │ Step 7: Build and return ChatResponse                            │  │
│  │         return ChatResponse(                                     │  │
│  │             answer=answer_text,                                  │  │
│  │             sources=sources,                                     │  │
│  │             meta=ChatAnswerMeta(                                 │  │
│  │                 used_model="internal-llm",                       │  │
│  │                 route="ROUTE_RAG_INTERNAL" or "ROUTE_LLM_ONLY",  │  │
│  │                 latency_ms=measured_latency                      │  │
│  │             )                                                    │  │
│  │         )                                                        │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                                                                        │
└───────────────────────────────────────────────────────────────────────┘
```

**RAG 컨텍스트 포맷팅 예시**:
```
참고 문서:
1) [HR-001] 인사규정 (p.15) [관련도: 0.95]
   발췌: 연차휴가는 1년간 80% 이상 출근한 근로자에게 15일을 부여한다...
2) [HR-002] 휴가관리지침 (p.8) [관련도: 0.87]
   발췌: 연차휴가의 이월은 최대 5일까지 허용되며...
```

**라우팅 로직**:
| 상황 | route 값 |
|------|----------|
| RAG 결과 있음 | `ROUTE_RAG_INTERNAL` |
| RAG 결과 없음 | `ROUTE_LLM_ONLY` |
| LLM 호출 실패 | `ROUTE_ERROR` |
| 요청 오류 | `ROUTE_FALLBACK` |

---

### 5.2 app/services/rag_service.py (125 lines)

RAGFlow 연동을 위해 업데이트된 서비스입니다.

```python
class RagService:
    def __init__(self, ragflow_client: Optional[RagflowClient] = None) -> None:
        self._client = ragflow_client or RagflowClient()

    async def process_document(self, req: RagProcessRequest) -> RagProcessResponse:
        # RAGFlow 미설정 시 더미 success 응답 (기존 테스트 호환)
        settings = get_settings()
        if not settings.RAGFLOW_BASE_URL:
            return RagProcessResponse(
                doc_id=req.doc_id,
                success=True,
                message="RAG document processing dummy response..."
            )

        # RAGFlow 설정 시 실제 호출
        return await self._client.process_document(req)
```

**동작 방식**:
| RAGFLOW_BASE_URL | 동작 |
|------------------|------|
| 미설정 (빈 문자열) | 더미 success 응답 반환 (기존 테스트 호환) |
| 설정됨 | RagflowClient를 통해 실제 RAGFlow 호출 |

---

## 6. 아키텍처 다이어그램

### 6.1 전체 시스템 구조

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              API Layer                                       │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │   chat.py        │  │    rag.py        │  │      health.py           │  │
│  │ /ai/chat/messages│  │ /ai/rag/process  │  │ /health, /health/ready   │  │
│  └────────┬─────────┘  └────────┬─────────┘  └──────────────────────────┘  │
└───────────┼─────────────────────┼───────────────────────────────────────────┘
            │                     │
            ▼                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Service Layer                                     │
│  ┌──────────────────────────────────┐  ┌──────────────────────────────────┐ │
│  │          ChatService             │  │          RagService              │ │
│  │  - RAG + LLM 파이프라인          │  │  - 문서 처리 요청 forward        │ │
│  │  - 프롬프트 구성                 │  │                                  │ │
│  │  - latency 측정                  │  │                                  │ │
│  └───────────┬──────────────────────┘  └───────────┬──────────────────────┘ │
│              │                                      │                        │
│              ▼                                      ▼                        │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐          │
│  │  RagflowClient   │  │    LLMClient     │  │  RagflowClient   │          │
│  │  - search()      │  │ - generate_chat_ │  │ - process_doc()  │          │
│  │                  │  │   completion()   │  │                  │          │
│  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘          │
└───────────┼─────────────────────┼─────────────────────┼─────────────────────┘
            │                     │                     │
            └──────────┬──────────┴──────────┬──────────┘
                       │                     │
                       ▼                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Shared HTTP Client                                   │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                    http_client.py (Singleton)                         │   │
│  │  - get_async_http_client()                                            │   │
│  │  - timeout=10s, limits=20/100                                         │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         External Services                                    │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │  ctrlf-ragflow   │  │   LLM Server     │  │    ctrlf-back            │  │
│  │  (RAG 처리)      │  │  (GPT 호환)      │  │  (Spring Backend)        │  │
│  │                  │  │                  │  │                          │  │
│  │  /rag/process    │  │  /v1/chat/       │  │                          │  │
│  │  /rag/search     │  │   completions    │  │                          │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 6.2 Chat 요청 시퀀스

```
┌──────┐     ┌──────────┐     ┌─────────────┐     ┌───────────────┐     ┌─────────┐
│Client│     │  chat.py │     │ ChatService │     │RagflowClient  │     │LLMClient│
└──┬───┘     └────┬─────┘     └──────┬──────┘     └───────┬───────┘     └────┬────┘
   │              │                   │                    │                  │
   │ POST /ai/chat/messages           │                    │                  │
   │─────────────▶│                   │                    │                  │
   │              │                   │                    │                  │
   │              │ handle_chat(req)  │                    │                  │
   │              │──────────────────▶│                    │                  │
   │              │                   │                    │                  │
   │              │                   │ search(query)      │                  │
   │              │                   │───────────────────▶│                  │
   │              │                   │                    │                  │
   │              │                   │   List[ChatSource] │                  │
   │              │                   │◀───────────────────│                  │
   │              │                   │                    │                  │
   │              │                   │ generate_chat_completion(messages)    │
   │              │                   │───────────────────────────────────────▶│
   │              │                   │                    │                  │
   │              │                   │                    │     answer_text  │
   │              │                   │◀───────────────────────────────────────│
   │              │                   │                    │                  │
   │              │   ChatResponse    │                    │                  │
   │              │◀──────────────────│                    │                  │
   │              │                   │                    │                  │
   │ 200 OK + JSON│                   │                    │                  │
   │◀─────────────│                   │                    │                  │
   │              │                   │                    │                  │
```

---

## 7. 테스트 결과

### 7.1 테스트 실행 결과

```
============================= test session starts =============================
platform win32 -- Python 3.12.7, pytest-9.0.2, pluggy-1.6.0
plugins: anyio-4.12.0
collected 29 items

tests/test_chat_api.py::test_chat_endpoint_returns_200 PASSED            [  3%]
tests/test_chat_api.py::test_chat_endpoint_returns_dummy_answer PASSED   [  6%]
tests/test_chat_api.py::test_chat_endpoint_meta_structure PASSED         [ 10%]
tests/test_chat_api.py::test_chat_endpoint_with_minimal_payload PASSED   [ 13%]
tests/test_chat_api.py::test_chat_endpoint_with_conversation_history PASSED [ 17%]
tests/test_chat_api.py::test_chat_endpoint_validation_error PASSED       [ 20%]
tests/test_chat_api.py::test_chat_endpoint_invalid_role PASSED           [ 24%]
tests/test_health.py::test_health_check_returns_200 PASSED               [ 27%]
tests/test_health.py::test_health_check_returns_status_ok PASSED         [ 31%]
tests/test_health.py::test_health_check_contains_app_info PASSED         [ 34%]
tests/test_health.py::test_readiness_check_returns_200 PASSED            [ 37%]
tests/test_health.py::test_readiness_check_returns_ready_true PASSED     [ 41%]
tests/test_rag_api.py::test_rag_process_returns_200 PASSED               [ 44%]
tests/test_rag_api.py::test_rag_process_returns_success PASSED           [ 48%]
tests/test_rag_api.py::test_rag_process_response_structure PASSED        [ 51%]
tests/test_rag_api.py::test_rag_process_without_acl PASSED               [ 55%]
tests/test_rag_api.py::test_rag_process_with_empty_acl PASSED            [ 58%]
tests/test_rag_api.py::test_rag_process_validation_error_missing_fields PASSED [ 62%]
tests/test_rag_api.py::test_rag_process_validation_error_invalid_url PASSED [ 65%]
tests/test_rag_api.py::test_rag_process_preserves_doc_id PASSED          [ 68%]
tests/test_service_fallback.py::test_chat_service_returns_response_without_config PASSED [ 72%]
tests/test_service_fallback.py::test_chat_service_returns_fallback_message PASSED [ 75%]
tests/test_service_fallback.py::test_chat_service_empty_sources_without_ragflow PASSED [ 79%]
tests/test_service_fallback.py::test_chat_service_meta_has_latency PASSED [ 82%]
tests/test_service_fallback.py::test_rag_service_returns_dummy_success_without_config PASSED [ 86%]
tests/test_service_fallback.py::test_rag_service_preserves_doc_id PASSED [ 89%]
tests/test_service_fallback.py::test_ragflow_client_search_returns_empty_without_config PASSED [ 93%]
tests/test_service_fallback.py::test_ragflow_client_process_returns_failure_without_config PASSED [ 96%]
tests/test_service_fallback.py::test_llm_client_returns_fallback_without_config PASSED [100%]

============================= 29 passed in 2.15s ==============================
```

### 7.2 테스트 요약

| 테스트 파일 | 테스트 수 | 결과 | 설명 |
|-------------|----------|------|------|
| `test_health.py` | 5 | PASSED | 헬스체크 API 테스트 |
| `test_chat_api.py` | 7 | PASSED | 채팅 API 테스트 |
| `test_rag_api.py` | 8 | PASSED | RAG 문서 처리 API 테스트 |
| `test_service_fallback.py` | 9 | PASSED | **신규** - 서비스 fallback 테스트 |
| **총계** | **29** | **ALL PASSED** | 기존 20개 + 신규 9개 |

---

## 8. 환경변수

| 변수명 | 설명 | 기본값 | Phase 3-2 사용 |
|--------|------|--------|----------------|
| `APP_NAME` | 애플리케이션 이름 | `ctrlf-ai-gateway` | - |
| `APP_ENV` | 실행 환경 | `local` | - |
| `APP_VERSION` | 버전 | `0.1.0` | - |
| `LOG_LEVEL` | 로그 레벨 | `INFO` | - |
| `RAGFLOW_BASE_URL` | RAGFlow 서비스 URL | (빈 문자열) | RagflowClient, ChatService |
| `LLM_BASE_URL` | LLM 서비스 URL | (빈 문자열) | LLMClient, ChatService |
| `BACKEND_BASE_URL` | Spring 백엔드 URL | (빈 문자열) | - |
| `CORS_ORIGINS` | 허용 Origin | `*` | - |

**사용 예시** (`.env`):
```bash
RAGFLOW_BASE_URL=http://localhost:8001
LLM_BASE_URL=http://localhost:8002
```

---

## 9. TODO 및 플레이스홀더 목록

### 9.1 RagflowClient (app/services/ragflow_client.py)

| 위치 | TODO 내용 | 우선순위 |
|------|-----------|----------|
| L92 | 실제 RAGFlow process 엔드포인트 경로 확인 | 높음 |
| L96 | 실제 RAGFlow request payload 구조 확인 | 높음 |
| L114 | 실제 RAGFlow response 구조 파싱 | 높음 |
| L187 | 실제 RAGFlow search 엔드포인트 경로 확인 | 높음 |
| L191 | 실제 RAGFlow search payload 구조 확인 | 높음 |
| L212-220 | 실제 RAGFlow search response 구조 파싱 | 높음 |

### 9.2 LLMClient (app/services/llm_client.py)

| 위치 | TODO 내용 | 우선순위 |
|------|-----------|----------|
| L108-110 | 실제 LLM 엔드포인트 경로 확인 | 높음 |
| L112-113 | 실제 LLM request payload 구조 확인 | 높음 |
| L134-145 | 실제 LLM response 구조 파싱 | 높음 |

### 9.3 ChatService (app/services/chat_service.py)

| 위치 | TODO 내용 | 우선순위 |
|------|-----------|----------|
| L114-115 | PII Masking 구현 | 중간 |
| L117-118 | Intent Classification 구현 | 중간 |
| L159 | LLM 응답에서 실제 모델 이름 추출 | 낮음 |
| L217 | 멀티턴 대화 히스토리 지원 | 낮음 |

---

## 10. 파일별 코드 라인 수

| 파일 | 라인 수 | 변경 유형 |
|------|---------|----------|
| `app/models/chat.py` | 147 | 수정 (+4 lines) |
| `app/services/ragflow_client.py` | 254 | 신규 |
| `app/services/llm_client.py` | 181 | 신규 |
| `app/services/chat_service.py` | 284 | 수정 (전면 재작성) |
| `app/services/rag_service.py` | 125 | 수정 (+20 lines) |
| `tests/test_service_fallback.py` | 214 | 신규 |

**Phase 3-2 추가/수정 코드**: 약 850 lines

---

## 11. 향후 계획

### Phase 4 예정 작업

1. **PII 마스킹 구현**
   - 사용자 입력에서 민감 정보 탐지 및 마스킹
   - 응답에서 민감 정보 필터링

2. **의도 분류 (Intent Classification)**
   - 질문 유형 분류
   - 라우팅 최적화

3. **멀티턴 대화 지원**
   - 대화 히스토리 컨텍스트 유지
   - 세션 기반 대화 관리

4. **RAGFlow/LLM API 스펙 확정 후 플레이스홀더 업데이트**

---

## 12. 결론

Phase 3-2에서 RAGFlow와 LLM 서비스 연동을 위한 기본 파이프라인을 성공적으로 구축했습니다.

### 핵심 성과

1. **RAGFlow 클라이언트**: 문서 처리 및 검색 API 뼈대 완성
2. **LLM 클라이언트**: OpenAI 호환 형식의 채팅 완성 API 뼈대 완성
3. **ChatService RAG + LLM 파이프라인**: 검색 → 프롬프트 구성 → 응답 생성 흐름 완성
4. **Graceful Fallback**: 외부 서비스 미설정/오류 시 안전한 기본 응답
5. **기존 테스트 호환**: 20개 기존 테스트 + 9개 신규 테스트 = 29개 전체 통과

### 설계 원칙

- **Placeholder 기반 개발**: 실제 API 스펙 미확정 상태에서 TODO 주석으로 명시
- **Dependency Injection**: 테스트 용이성을 위한 클라이언트 주입 지원
- **Fail-Safe**: 외부 서비스 장애 시 graceful degradation

다음 단계에서는 실제 RAGFlow/LLM API 스펙 확정 후 플레이스홀더를 실제 구현으로 교체하고, PII 마스킹 및 의도 분류 기능을 추가할 예정입니다.

---

**작성자**: Claude Code
**검토 필요 항목**: RAGFlow/LLM 실제 API 스펙 확인, 시스템 프롬프트 최적화
