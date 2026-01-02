# ctrlf-ai-gateway 개발 진행 보고서 (Phase 2)

**작성일**: 2025년 12월 8일
**프로젝트명**: CTRL+F AI Gateway Service
**버전**: 0.1.0
**단계**: Phase 2 - AI API 레이어 구축
**작성자**: AI 개발 어시스턴트

---

## 1. 개요

### 1.1 이번 단계 목표
기존 스켈레톤(Phase 1)을 기반으로 **RAG 기반 사규 챗봇**을 지원하기 위한 AI API 레이어를 추가합니다. 이번 단계에서는 **비즈니스 로직 없이 구조와 인터페이스만 잡힌 상태**의 더미 구현을 완성합니다.

### 1.2 Phase 1 기존 구조 (유지)
- `app/core/config.py`: Settings, get_settings()
- `app/core/logging.py`: setup_logging(settings)
- `app/main.py`: FastAPI 앱, health 라우터
- `app/api/v1/health.py`: /health, /health/ready 엔드포인트
- `tests/test_health.py`: 헬스체크 테스트 (5개)
- `requirements.txt`, `Dockerfile`, `README.md`

### 1.3 Phase 2 추가 구조
- **Models 계층**: Pydantic 모델 (Request/Response 스키마)
- **Services 계층**: 비즈니스 로직 클래스 (현재 더미 구현)
- **API 라우터 계층**: Chat, RAG 엔드포인트
- **테스트**: Chat API, RAG API 테스트

---

## 2. 아키텍처 설계

### 2.1 레이어드 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                      API Layer                               │
│  app/api/v1/chat.py    app/api/v1/rag.py                   │
│  POST /ai/chat/messages    POST /ai/rag/process            │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Service Layer                             │
│  app/services/chat_service.py    app/services/rag_service.py│
│  ChatService.handle_chat()       RagService.process_document()│
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Models Layer                              │
│  app/models/chat.py              app/models/rag.py          │
│  ChatRequest, ChatResponse       RagProcessRequest, etc.    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              External Services (TODO)                        │
│  ctrlf-ragflow (RAG)         LLM Service (OpenAI 등)        │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 디렉터리 구조 (Phase 2 완료 후)

```
ctrlf-ai/
├── app/
│   ├── __init__.py
│   ├── main.py                      # FastAPI 앱 진입점 (수정됨)
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py          # (수정됨)
│   │       ├── health.py            # 헬스체크 (기존)
│   │       ├── chat.py              # [NEW] AI 채팅 API
│   │       └── rag.py               # [NEW] RAG 문서 처리 API
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py                # 설정 관리 (기존)
│   │   └── logging.py               # 로깅 설정 (기존)
│   ├── models/                      # [NEW] Pydantic 모델
│   │   ├── __init__.py
│   │   ├── chat.py                  # 채팅 관련 모델
│   │   └── rag.py                   # RAG 관련 모델
│   └── services/                    # [NEW] 비즈니스 로직
│       ├── __init__.py
│       ├── chat_service.py          # 채팅 서비스
│       └── rag_service.py           # RAG 서비스
├── tests/
│   ├── __init__.py
│   ├── test_health.py               # 헬스체크 테스트 (기존, 5개)
│   ├── test_chat_api.py             # [NEW] 채팅 API 테스트 (7개)
│   └── test_rag_api.py              # [NEW] RAG API 테스트 (8개)
├── .env.example
├── Dockerfile
├── README.md                        # (수정됨)
└── requirements.txt
```

---

## 3. Models 계층 상세

### 3.1 Chat Models (`app/models/chat.py`)

**파일 정보**: 143줄, 5개 Pydantic 모델 정의

#### 3.1.1 ChatMessage
```python
class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]  # 메시지 발화 주체
    content: str                                   # 메시지 텍스트
```
- 대화 히스토리의 한 턴을 표현
- `role`은 Literal 타입으로 제한하여 유효성 검증

#### 3.1.2 ChatRequest
```python
class ChatRequest(BaseModel):
    session_id: str          # 필수: 채팅 세션 ID
    user_id: str             # 필수: 사용자 ID (사번 등)
    user_role: str           # 필수: 사용자 역할 (EMPLOYEE, MANAGER, ADMIN)
    department: Optional[str] = None    # 선택: 소속 부서
    domain: Optional[str] = None        # 선택: 질문 도메인 (POLICY, INCIDENT 등)
    channel: str = "WEB"                # 선택: 요청 채널 (기본값: WEB)
    messages: List[ChatMessage]         # 필수: 대화 히스토리
```
- 백엔드(ctrlf-back) → AI 게이트웨이 요청 스펙
- 사용자 컨텍스트 정보와 대화 히스토리를 포함
- `domain`은 AI가 자동 판단할 수 있도록 선택 필드로 설계

#### 3.1.3 ChatSource
```python
class ChatSource(BaseModel):
    doc_id: str                    # 문서 ID
    title: str                     # 문서 제목
    page: Optional[int] = None     # 페이지 번호
    score: Optional[float] = None  # 검색 점수
```
- RAG 검색 결과로 반환되는 근거 문서 정보
- 답변의 신뢰성을 높이기 위한 출처 표시용

#### 3.1.4 ChatAnswerMeta
```python
class ChatAnswerMeta(BaseModel):
    used_model: Optional[str] = None     # 사용된 LLM 모델명
    route: Optional[str] = None          # 라우팅 경로 (ROUTE_RAG_INTERNAL 등)
    masked: Optional[bool] = None        # PII 마스킹 여부
    latency_ms: Optional[int] = None     # 응답 소요 시간(ms)
```
- AI 응답에 대한 메타데이터
- 디버깅, 모니터링, 감사 로그용

#### 3.1.5 ChatResponse
```python
class ChatResponse(BaseModel):
    answer: str                              # 최종 답변 텍스트
    sources: List[ChatSource] = []           # 참고 문서 목록
    meta: ChatAnswerMeta = ChatAnswerMeta()  # 응답 메타데이터
```
- AI 게이트웨이 → 백엔드 응답 스펙
- 답변, 출처, 메타정보를 포함한 완전한 응답 구조

---

### 3.2 RAG Models (`app/models/rag.py`)

**파일 정보**: 81줄, 3개 Pydantic 모델 정의

#### 3.2.1 RagAcl
```python
class RagAcl(BaseModel):
    roles: List[str] = []        # 열람 가능한 역할 목록
    departments: List[str] = []  # 열람 가능한 부서 목록
```
- 문서 접근 제어(Access Control List) 정보
- 역할 기반 및 부서 기반 접근 제어 지원

#### 3.2.2 RagProcessRequest
```python
class RagProcessRequest(BaseModel):
    doc_id: str              # 필수: 문서 ID
    file_url: HttpUrl        # 필수: 문서 파일 URL (유효한 URL 형식 검증)
    domain: str              # 필수: 문서 도메인 (POLICY, INCIDENT 등)
    acl: Optional[RagAcl] = None  # 선택: 접근 제어 설정
```
- 백엔드 → AI 게이트웨이 RAG 문서 처리 요청
- `HttpUrl` 타입으로 URL 형식 자동 검증
- ACL은 선택 필드로 유연한 접근 제어 지원

#### 3.2.3 RagProcessResponse
```python
class RagProcessResponse(BaseModel):
    doc_id: str                      # 요청과 동일한 문서 ID
    success: bool                    # 처리 성공 여부
    message: Optional[str] = None    # 추가 설명 또는 에러 메시지
```
- AI 게이트웨이 → 백엔드 처리 결과 응답
- 성공/실패 여부와 메시지를 포함

---

## 4. Services 계층 상세

### 4.1 ChatService (`app/services/chat_service.py`)

**파일 정보**: 127줄

#### 클래스 구조
```python
class ChatService:
    async def handle_chat(self, req: ChatRequest) -> ChatResponse:
        """채팅 요청 처리 및 응답 생성"""
        # 현재: 더미 응답 반환
        # TODO: 실제 구현 예정
```

#### 주요 메서드

**`handle_chat(req: ChatRequest) -> ChatResponse`**
- 채팅 요청을 처리하고 응답을 생성
- 현재는 더미 응답 반환
- 로깅을 통한 요청 추적 구현

#### 현재 구현 (더미)
```python
async def handle_chat(self, req: ChatRequest) -> ChatResponse:
    logger.info(f"Processing chat request: session_id={req.session_id}, ...")

    dummy_answer = "This is a dummy response. RAG and LLM integration..."
    sources: list[ChatSource] = []
    meta = ChatAnswerMeta(used_model=None, route=None, masked=None, latency_ms=None)

    return ChatResponse(answer=dummy_answer, sources=sources, meta=meta)
```

#### TODO 메서드 (향후 구현 예정)
```python
# async def _mask_pii(self, text: str) -> str:
#     """PII 마스킹 적용"""

# async def _classify_intent(self, text: str, domain: Optional[str]) -> str:
#     """의도 분류"""

# async def _search_rag(self, query: str, user_role: str, department: Optional[str]) -> List[ChatSource]:
#     """RAGFlow를 통한 문서 검색"""

# async def _generate_response(self, query: str, documents: List[ChatSource], history: List[ChatMessage]) -> str:
#     """LLM을 통한 응답 생성"""
```

#### 향후 처리 파이프라인 (계획)
```
1. PII 마스킹 (_mask_pii)
     ↓
2. 의도 분류 (_classify_intent)
     ↓
3. RAG 검색 (_search_rag) - ctrlf-ragflow 연동
     ↓
4. LLM 응답 생성 (_generate_response)
     ↓
5. 후처리 및 반환
```

---

### 4.2 RagService (`app/services/rag_service.py`)

**파일 정보**: 108줄

#### 클래스 구조
```python
class RagService:
    async def process_document(self, req: RagProcessRequest) -> RagProcessResponse:
        """RAG 문서 처리 (전처리, 임베딩, 인덱싱)"""
        # 현재: 더미 응답 반환
        # TODO: RAGFlow 연동 예정
```

#### 현재 구현 (더미)
```python
async def process_document(self, req: RagProcessRequest) -> RagProcessResponse:
    logger.info(f"Processing RAG document: doc_id={req.doc_id}, domain={req.domain}, ...")

    if req.acl:
        logger.debug(f"Document ACL: roles={req.acl.roles}, departments={req.acl.departments}")

    return RagProcessResponse(
        doc_id=req.doc_id,
        success=True,
        message="RAG document processing dummy response. ..."
    )
```

#### TODO 메서드 (향후 구현 예정)
```python
# async def _download_document(self, file_url: HttpUrl) -> bytes:
#     """URL에서 문서 다운로드"""

# async def _preprocess_document(self, content: bytes, domain: str) -> List[str]:
#     """RAGFlow 전처리 및 청킹"""

# async def _generate_embeddings(self, chunks: List[str]) -> List[List[float]]:
#     """임베딩 생성"""

# async def _store_embeddings(self, doc_id: str, embeddings: List[List[float]], acl: Optional[RagAcl]) -> None:
#     """벡터 DB에 임베딩 저장 (ACL 메타데이터 포함)"""
```

#### 향후 처리 파이프라인 (계획)
```
1. 문서 다운로드 (_download_document)
     ↓
2. RAGFlow 전처리 (_preprocess_document) - ctrlf-ragflow 연동
     ↓
3. 임베딩 생성 (_generate_embeddings)
     ↓
4. 벡터 DB 저장 (_store_embeddings) - ACL 메타데이터 포함
     ↓
5. 결과 반환
```

---

## 5. API 라우터 계층 상세

### 5.1 Chat API Router (`app/api/v1/chat.py`)

**파일 정보**: 107줄

#### 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/ai/chat/messages` | AI 채팅 응답 생성 |

#### 의존성 주입 패턴
```python
def get_chat_service() -> ChatService:
    """DI 패턴으로 ChatService 인스턴스 제공"""
    return ChatService()

@router.post("/ai/chat/messages", response_model=ChatResponse)
async def create_chat_message(
    req: ChatRequest,
    service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    return await service.handle_chat(req)
```

#### OpenAPI 문서화
- `summary`: "Generate AI Chat Response"
- `description`: 상세 설명 포함
- `responses`: 200, 422 응답 예시 포함

#### 요청 예시
```json
{
  "session_id": "chat-session-123",
  "user_id": "employee-456",
  "user_role": "EMPLOYEE",
  "department": "HR",
  "domain": "POLICY",
  "channel": "WEB",
  "messages": [
    {"role": "user", "content": "연차 이월 규정이 어떻게 되나요?"}
  ]
}
```

#### 응답 예시 (더미)
```json
{
  "answer": "This is a dummy response. RAG and LLM integration will be implemented in the next phase. Your question has been received successfully.",
  "sources": [],
  "meta": {
    "used_model": null,
    "route": null,
    "masked": null,
    "latency_ms": null
  }
}
```

---

### 5.2 RAG API Router (`app/api/v1/rag.py`)

**파일 정보**: 94줄

#### 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/ai/rag/process` | RAG 문서 처리 요청 |

#### 의존성 주입 패턴
```python
def get_rag_service() -> RagService:
    """DI 패턴으로 RagService 인스턴스 제공"""
    return RagService()

@router.post("/ai/rag/process", response_model=RagProcessResponse)
async def process_rag_document(
    req: RagProcessRequest,
    service: RagService = Depends(get_rag_service),
) -> RagProcessResponse:
    return await service.process_document(req)
```

#### 요청 예시
```json
{
  "doc_id": "HR-001",
  "file_url": "https://storage.example.com/docs/hr-policy.pdf",
  "domain": "POLICY",
  "acl": {
    "roles": ["EMPLOYEE", "MANAGER"],
    "departments": ["ALL"]
  }
}
```

#### 응답 예시 (더미)
```json
{
  "doc_id": "HR-001",
  "success": true,
  "message": "RAG document processing dummy response. Actual RAGFlow integration will be implemented in the next phase."
}
```

---

## 6. main.py 수정 내용

### 6.1 변경 사항

**Import 추가**
```python
# 변경 전
from app.api.v1 import health

# 변경 후
from app.api.v1 import chat, health, rag
```

**라우터 등록 추가**
```python
# Health API (기존)
app.include_router(health.router, prefix="", tags=["Health"])

# AI API routers (신규)
app.include_router(chat.router, tags=["Chat"])
app.include_router(rag.router, tags=["RAG"])
```

### 6.2 API 경로 설계 결정

**현재 구조**:
- 헬스체크: `/health`, `/health/ready`
- 채팅 API: `/ai/chat/messages`
- RAG API: `/ai/rag/process`

**설계 이유**:
1. 헬스체크는 쿠버네티스 호환성을 위해 루트에 배치
2. AI API는 `/ai` prefix로 그룹화하여 명확한 구분
3. 향후 `/api/v1` prefix로 마이그레이션 가능하도록 주석 문서화

---

## 7. 테스트 상세

### 7.1 Chat API 테스트 (`tests/test_chat_api.py`)

**파일 정보**: 172줄, 7개 테스트 케이스

| 테스트 함수 | 검증 내용 | 결과 |
|-------------|----------|------|
| `test_chat_endpoint_returns_200` | 정상 요청 시 200 OK 반환 | PASSED |
| `test_chat_endpoint_returns_dummy_answer` | 응답 구조 (answer, sources, meta) 검증 | PASSED |
| `test_chat_endpoint_meta_structure` | meta 필드 구조 검증 | PASSED |
| `test_chat_endpoint_with_minimal_payload` | 최소 필수 필드만으로 요청 | PASSED |
| `test_chat_endpoint_with_conversation_history` | 대화 히스토리 포함 요청 | PASSED |
| `test_chat_endpoint_validation_error` | 필수 필드 누락 시 422 반환 | PASSED |
| `test_chat_endpoint_invalid_role` | 잘못된 role 값 시 422 반환 | PASSED |

#### 테스트 픽스처
```python
@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
```

#### 테스트 예시: 정상 요청
```python
@pytest.mark.anyio
async def test_chat_endpoint_returns_200(client: AsyncClient) -> None:
    payload = {
        "session_id": "test-session",
        "user_id": "user-123",
        "user_role": "EMPLOYEE",
        "department": "HR",
        "domain": "POLICY",
        "channel": "WEB",
        "messages": [{"role": "user", "content": "What is the annual leave policy?"}],
    }
    response = await client.post("/ai/chat/messages", json=payload)
    assert response.status_code == 200
```

#### 테스트 예시: 유효성 검증
```python
@pytest.mark.anyio
async def test_chat_endpoint_invalid_role(client: AsyncClient) -> None:
    payload = {
        "session_id": "test-session",
        "user_id": "user-123",
        "user_role": "EMPLOYEE",
        "messages": [{"role": "invalid_role", "content": "Hello"}],  # 잘못된 role
    }
    response = await client.post("/ai/chat/messages", json=payload)
    assert response.status_code == 422  # Validation Error
```

---

### 7.2 RAG API 테스트 (`tests/test_rag_api.py`)

**파일 정보**: 184줄, 8개 테스트 케이스

| 테스트 함수 | 검증 내용 | 결과 |
|-------------|----------|------|
| `test_rag_process_returns_200` | 정상 요청 시 200 OK 반환 | PASSED |
| `test_rag_process_returns_success` | success=True 반환 검증 | PASSED |
| `test_rag_process_response_structure` | 응답 구조 (doc_id, success, message) 검증 | PASSED |
| `test_rag_process_without_acl` | ACL 없이 요청 가능 여부 | PASSED |
| `test_rag_process_with_empty_acl` | 빈 ACL로 요청 가능 여부 | PASSED |
| `test_rag_process_validation_error_missing_fields` | 필수 필드 누락 시 422 반환 | PASSED |
| `test_rag_process_validation_error_invalid_url` | 잘못된 URL 형식 시 422 반환 | PASSED |
| `test_rag_process_preserves_doc_id` | 요청 doc_id가 응답에 그대로 반환 | PASSED |

#### 테스트 예시: URL 유효성 검증
```python
@pytest.mark.anyio
async def test_rag_process_validation_error_invalid_url(client: AsyncClient) -> None:
    payload = {
        "doc_id": "HR-001",
        "file_url": "not-a-valid-url",  # 잘못된 URL
        "domain": "POLICY",
    }
    response = await client.post("/ai/rag/process", json=payload)
    assert response.status_code == 422  # Validation Error
```

---

### 7.3 전체 테스트 결과

```
============================= test session starts =============================
platform win32 -- Python 3.12.7, pytest-9.0.2, pluggy-1.6.0
plugins: anyio-4.12.0
collected 20 items

tests/test_chat_api.py::test_chat_endpoint_returns_200 PASSED            [  5%]
tests/test_chat_api.py::test_chat_endpoint_returns_dummy_answer PASSED   [ 10%]
tests/test_chat_api.py::test_chat_endpoint_meta_structure PASSED         [ 15%]
tests/test_chat_api.py::test_chat_endpoint_with_minimal_payload PASSED   [ 20%]
tests/test_chat_api.py::test_chat_endpoint_with_conversation_history PASSED [ 25%]
tests/test_chat_api.py::test_chat_endpoint_validation_error PASSED       [ 30%]
tests/test_chat_api.py::test_chat_endpoint_invalid_role PASSED           [ 35%]
tests/test_health.py::test_health_check_returns_200 PASSED               [ 40%]
tests/test_health.py::test_health_check_returns_status_ok PASSED         [ 45%]
tests/test_health.py::test_health_check_contains_app_info PASSED         [ 50%]
tests/test_health.py::test_readiness_check_returns_200 PASSED            [ 55%]
tests/test_health.py::test_readiness_check_returns_ready_true PASSED     [ 60%]
tests/test_rag_api.py::test_rag_process_returns_200 PASSED               [ 65%]
tests/test_rag_api.py::test_rag_process_returns_success PASSED           [ 70%]
tests/test_rag_api.py::test_rag_process_response_structure PASSED        [ 75%]
tests/test_rag_api.py::test_rag_process_without_acl PASSED               [ 80%]
tests/test_rag_api.py::test_rag_process_with_empty_acl PASSED            [ 85%]
tests/test_rag_api.py::test_rag_process_validation_error_missing_fields PASSED [ 90%]
tests/test_rag_api.py::test_rag_process_validation_error_invalid_url PASSED [ 95%]
tests/test_rag_api.py::test_rag_process_preserves_doc_id PASSED          [100%]

============================== 20 passed in 4.35s ==============================
```

**테스트 요약**:
- 전체: 20개 테스트
- Health API: 5개 (기존)
- Chat API: 7개 (신규)
- RAG API: 8개 (신규)
- 모두 통과 (100%)

---

## 8. 코드 통계

### 8.1 신규 파일

| 파일 | 줄 수 | 설명 |
|------|-------|------|
| `app/models/__init__.py` | 32 | 모델 패키지 초기화 |
| `app/models/chat.py` | 143 | 채팅 모델 정의 (5개 클래스) |
| `app/models/rag.py` | 81 | RAG 모델 정의 (3개 클래스) |
| `app/services/__init__.py` | 15 | 서비스 패키지 초기화 |
| `app/services/chat_service.py` | 127 | 채팅 서비스 로직 |
| `app/services/rag_service.py` | 108 | RAG 서비스 로직 |
| `app/api/v1/chat.py` | 107 | 채팅 API 라우터 |
| `app/api/v1/rag.py` | 94 | RAG API 라우터 |
| `tests/test_chat_api.py` | 172 | 채팅 API 테스트 (7개) |
| `tests/test_rag_api.py` | 184 | RAG API 테스트 (8개) |
| **합계** | **1,063** | |

### 8.2 수정 파일

| 파일 | 변경 내용 |
|------|----------|
| `app/main.py` | chat, rag 라우터 import 및 등록 |
| `app/api/v1/__init__.py` | chat, rag 모듈 export |
| `README.md` | AI API 문서 추가 |

### 8.3 전체 프로젝트 규모

| 항목 | 수량 |
|------|------|
| Python 파일 | 17개 |
| 테스트 케이스 | 20개 |
| API 엔드포인트 | 4개 |
| Pydantic 모델 | 8개 |
| 서비스 클래스 | 2개 |

---

## 9. API 엔드포인트 요약

| 메서드 | 경로 | 설명 | 상태 |
|--------|------|------|------|
| GET | `/health` | Liveness 체크 | 구현완료 |
| GET | `/health/ready` | Readiness 체크 | 구현완료 |
| POST | `/ai/chat/messages` | AI 채팅 응답 생성 | 더미 구현 |
| POST | `/ai/rag/process` | RAG 문서 처리 | 더미 구현 |

---

## 10. 다음 단계 (Phase 3) 계획

### 10.1 RAGFlow 연동
- [ ] `ctrlf-ragflow` 서비스 연동
- [ ] 문서 전처리 API 호출
- [ ] 임베딩 생성 및 저장
- [ ] 문서 검색 API 호출

### 10.2 LLM 연동
- [ ] OpenAI API 또는 자체 LLM 서버 연동
- [ ] 프롬프트 템플릿 설계
- [ ] 응답 생성 로직 구현

### 10.3 비즈니스 로직 구현
- [ ] PII 마스킹 로직
- [ ] 의도 분류 (Intent Classification)
- [ ] 응답 후처리
- [ ] 에러 핸들링 강화

### 10.4 인프라
- [ ] `httpx.AsyncClient` 싱글턴 관리
- [ ] 연결 풀링 설정
- [ ] 타임아웃 및 재시도 로직

---

## 11. 결론

Phase 2에서는 AI API 레이어의 **구조와 인터페이스**를 완성했습니다.

**완료 항목**:
- Models 계층: 8개 Pydantic 모델
- Services 계층: 2개 서비스 클래스 (더미 구현)
- API 라우터: 2개 엔드포인트
- 테스트: 15개 신규 테스트 (총 20개)

**현재 상태**:
- API 엔드포인트는 정상 동작하며 더미 응답 반환
- 요청/응답 스키마 검증 완료
- 모든 테스트 통과 (20/20)

**다음 단계**:
- `ctrlf-ragflow` 서비스 연동
- LLM 서비스 연동
- 실제 비즈니스 로직 구현

---

**보고서 끝**
