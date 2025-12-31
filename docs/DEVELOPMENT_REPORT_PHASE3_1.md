# CTRL+F AI Gateway 개발 보고서 - Phase 3-1

## 개요

**프로젝트명**: ctrlf-ai-gateway
**Phase**: 3-1 (HTTP 클라이언트 유틸리티 및 클라이언트 계층 스켈레톤)
**작성일**: 2025-12-08
**버전**: 0.1.0

---

## 1. Phase 3-1 목표

Phase 1(기본 스켈레톤)과 Phase 2(AI API 더미 구현)를 기반으로, 외부 서비스와 통신하기 위한 클라이언트 계층의 기반을 구축합니다.

### 주요 목표
1. **공용 HTTP 클라이언트 싱글턴**: `httpx.AsyncClient` 인스턴스를 애플리케이션 전역에서 재사용
2. **RAGFlow 클라이언트 스켈레톤**: ctrlf-ragflow 서비스 연동 클라이언트 뼈대 구현
3. **LLM 클라이언트 스켈레톤**: LLM 서비스 연동 클라이언트 뼈대 구현
4. **Readiness 체크 확장**: `/health/ready`에 외부 서비스 의존성 체크 구조 추가
5. **기존 테스트 호환성 유지**: 20개 테스트 모두 통과

---

## 2. 디렉터리 구조 변경

### 변경 전
```
ctrlf-ai/
├── app/
│   ├── api/v1/
│   ├── core/
│   ├── models/
│   └── services/
└── tests/
```

### 변경 후
```
ctrlf-ai/
├── app/
│   ├── api/v1/
│   │   └── health.py          # 수정: Readiness 체크 확장
│   ├── clients/               # 신규: 클라이언트 계층
│   │   ├── __init__.py
│   │   ├── http_client.py     # 공용 AsyncClient 싱글턴
│   │   ├── ragflow_client.py  # RAGFlow 클라이언트
│   │   └── llm_client.py      # LLM 클라이언트
│   ├── core/
│   ├── models/
│   └── services/
├── main.py                    # 수정: lifespan에 HTTP 클라이언트 정리 추가
└── tests/
```

---

## 3. 신규 파일 상세

### 3.1 app/clients/__init__.py (25 lines)

클라이언트 모듈의 진입점으로, 외부에서 사용할 클래스와 함수를 export합니다.

```python
"""
HTTP 클라이언트 모듈 (Clients Module)

외부 서비스와 통신하기 위한 클라이언트 계층입니다.

구성:
    - http_client: 공용 httpx.AsyncClient 싱글턴 관리
    - ragflow_client: ctrlf-ragflow 서비스 연동 클라이언트
    - llm_client: LLM 서비스 연동 클라이언트
"""

from app.clients.http_client import (
    close_async_http_client,
    get_async_http_client,
)
from app.clients.llm_client import LLMClient
from app.clients.ragflow_client import RagflowClient

__all__ = [
    "get_async_http_client",
    "close_async_http_client",
    "RagflowClient",
    "LLMClient",
]
```

**Export 항목**:
| 이름 | 타입 | 설명 |
|------|------|------|
| `get_async_http_client` | Function | 공용 AsyncClient 싱글턴 반환 |
| `close_async_http_client` | Async Function | AsyncClient 정리 |
| `RagflowClient` | Class | RAGFlow 서비스 클라이언트 |
| `LLMClient` | Class | LLM 서비스 클라이언트 |

---

### 3.2 app/clients/http_client.py (64 lines)

애플리케이션 전역에서 재사용할 `httpx.AsyncClient` 싱글턴을 관리합니다.

```python
"""
공용 HTTP 클라이언트 모듈 (Shared HTTP Client Module)

애플리케이션 전역에서 재사용할 httpx.AsyncClient 싱글턴을 관리합니다.
연결 풀을 효율적으로 사용하고, 애플리케이션 종료 시 리소스를 정리합니다.
"""

from typing import Optional
import httpx
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
_settings = get_settings()

# 모듈 전역 싱글턴 인스턴스
_async_client: Optional[httpx.AsyncClient] = None


def get_async_http_client() -> httpx.AsyncClient:
    """
    애플리케이션 전체에서 재사용할 httpx.AsyncClient 싱글턴을 반환합니다.
    lazy-init 방식으로 첫 호출 시 생성합니다.
    """
    global _async_client
    if _async_client is None:
        timeout = httpx.Timeout(10.0, connect=5.0)
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
        _async_client = httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
        )
        logger.info("Created shared AsyncClient (timeout=10s, limits=20/100)")
    return _async_client


async def close_async_http_client() -> None:
    """애플리케이션 종료 시 싱글턴 AsyncClient를 정리합니다."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
        logger.info("Closed shared AsyncClient")
```

**연결 풀 설정**:
| 설정 | 값 | 설명 |
|------|-----|------|
| `timeout` | 10초 | 전체 요청 타임아웃 |
| `connect timeout` | 5초 | 연결 타임아웃 |
| `max_keepalive_connections` | 20 | 유지할 최대 연결 수 |
| `max_connections` | 100 | 최대 동시 연결 수 |

**설계 결정**:
- **Lazy Initialization**: 첫 호출 시 생성하여 불필요한 리소스 사용 방지
- **싱글턴 패턴**: 연결 풀을 효율적으로 재사용
- **명시적 정리**: `close_async_http_client()`로 리소스 누수 방지

---

### 3.3 app/clients/ragflow_client.py (176 lines)

ctrlf-ragflow 서비스와 통신하는 클라이언트입니다.

```python
class RagflowClient:
    """
    ctrlf-ragflow 서비스와 통신하는 클라이언트.
    실제 엔드포인트 경로는 팀의 ctrlf-ragflow API 명세에 맞게
    나중에 수정해야 합니다.
    """

    def __init__(self) -> None:
        self._client = get_async_http_client()
        self._base_url = settings.RAGFLOW_BASE_URL

    async def health_check(self) -> bool:
        """RAGFlow 헬스체크를 수행합니다."""
        # BASE_URL이 없으면 False 반환 (점검 대상 아님으로 처리 가능)
        ...

    async def process_document(
        self, *, doc_id: str, file_url: str, domain: str,
        acl: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """RAG 문서 처리 요청을 RAGFlow에 위임합니다."""
        ...

    async def search_documents(
        self, *, query: str, domain: Optional[str] = None,
        user_role: Optional[str] = None, department: Optional[str] = None,
        top_k: int = 5
    ) -> Dict[str, Any]:
        """RAG 문서 검색을 요청합니다."""
        ...
```

**메서드 목록**:
| 메서드 | 설명 | TODO |
|--------|------|------|
| `health_check()` | RAGFlow 서비스 헬스체크 | 실제 health 엔드포인트 경로 확인 필요 |
| `process_document()` | 문서 처리 요청 | 실제 API 스펙에 맞게 수정 필요 |
| `search_documents()` | 문서 검색 요청 | 실제 검색 API 스펙에 맞게 수정 필요 |

**안전 설계**:
- `_ensure_base_url()`: BASE_URL 미설정 시 `RuntimeError` 발생
- `health_check()`: BASE_URL 미설정 시 네트워크 호출 없이 `False` 반환

---

### 3.4 app/clients/llm_client.py (162 lines)

LLM 서비스(OpenAI 호환 또는 내부 서버)와 통신하는 클라이언트입니다.

```python
class LLMClient:
    """
    LLM 서비스(OpenAI 호환 또는 내부 LLM 서버)와 통신하는 클라이언트.
    실제 API 스펙은 이후 팀에서 확정 후 TODO 부분을 수정해야 합니다.
    """

    def __init__(self) -> None:
        self._client = get_async_http_client()
        self._base_url = settings.LLM_BASE_URL

    async def health_check(self) -> bool:
        """LLM 서비스 헬스체크를 수행합니다."""
        ...

    async def generate_chat_completion(
        self, messages: List[Dict[str, str]], *,
        model: Optional[str] = None, temperature: float = 0.2,
        max_tokens: int = 512
    ) -> Dict[str, Any]:
        """ChatCompletion 스타일의 응답을 요청합니다."""
        ...

    async def generate_embedding(
        self, text: str, *, model: Optional[str] = None
    ) -> Dict[str, Any]:
        """텍스트 임베딩을 생성합니다."""
        ...
```

**메서드 목록**:
| 메서드 | 설명 | 예상 엔드포인트 |
|--------|------|----------------|
| `health_check()` | LLM 서비스 헬스체크 | `/health` |
| `generate_chat_completion()` | 채팅 완성 요청 | `/v1/chat/completions` |
| `generate_embedding()` | 텍스트 임베딩 생성 | `/v1/embeddings` |

**ChatCompletion 파라미터**:
| 파라미터 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `messages` | List[Dict] | 필수 | 대화 히스토리 |
| `model` | str | None | 사용할 모델 이름 |
| `temperature` | float | 0.2 | 응답 다양성 (0.0~1.0) |
| `max_tokens` | int | 512 | 최대 토큰 수 |

---

## 4. 수정된 파일 상세

### 4.1 app/main.py 변경사항

**변경 내용**: lifespan에 HTTP 클라이언트 정리 로직 추가

```python
# 추가된 import
from app.clients.http_client import close_async_http_client

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # 시작 시
    setup_logging(settings)
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"Environment: {settings.APP_ENV}")

    try:
        yield  # 애플리케이션 실행
    finally:
        # 종료 시: HTTP 클라이언트 정리
        await close_async_http_client()
        logger.info(f"Shutting down {settings.APP_NAME}")
```

**변경 이유**:
- `httpx.AsyncClient`는 명시적으로 닫아야 리소스 누수 방지
- `try/finally` 블록으로 예외 발생 시에도 정리 보장
- lazy-init이므로 사용하지 않은 경우 정리 작업 skip

---

### 4.2 app/api/v1/health.py 변경사항

**변경 내용**: Readiness 체크에 외부 서비스 의존성 체크 추가

```python
# 추가된 import
from app.clients.http_client import get_async_http_client
from app.clients.llm_client import LLMClient
from app.clients.ragflow_client import RagflowClient
from app.core.logging import get_logger

logger = get_logger(__name__)

@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness_check(
    settings: Settings = Depends(get_settings),
) -> ReadinessResponse:
    checks: Dict[str, bool] = {}

    # RAGFlow 헬스체크 (설정된 경우에만)
    if settings.RAGFLOW_BASE_URL:
        ragflow_client = RagflowClient()
        rag_ok = await ragflow_client.health_check()
        checks["ragflow"] = rag_ok

    # LLM 헬스체크 (설정된 경우에만)
    if settings.LLM_BASE_URL:
        llm_client = LLMClient()
        llm_ok = await llm_client.health_check()
        checks["llm"] = llm_ok

    # Backend 헬스체크 (설정된 경우에만)
    if settings.BACKEND_BASE_URL:
        client = get_async_http_client()
        try:
            resp = await client.get(f"{settings.BACKEND_BASE_URL}/health")
            backend_ok = resp.status_code == 200
        except Exception as e:
            logger.exception("Backend health check error: %s", e)
            backend_ok = False
        checks["backend"] = backend_ok

    # ready 상태 결정
    if checks:
        ready = all(checks.values())
    else:
        ready = True  # 의존성이 설정되지 않은 경우 기본 동작 유지

    return ReadinessResponse(ready=ready, checks=checks)
```

**Readiness 로직**:
| 상황 | checks | ready |
|------|--------|-------|
| BASE_URL 모두 미설정 | `{}` | `True` |
| 모든 서비스 정상 | `{"ragflow": True, "llm": True}` | `True` |
| 일부 서비스 비정상 | `{"ragflow": True, "llm": False}` | `False` |
| 모든 서비스 비정상 | `{"ragflow": False, "llm": False}` | `False` |

**호환성 보장**:
- 기존 테스트는 BASE_URL이 설정되지 않은 환경에서 실행
- 이 경우 `checks={}`이고 `ready=True` 반환
- 기존 테스트 20개 모두 통과

---

## 5. 아키텍처 다이어그램

### 5.1 클라이언트 계층 구조

```
┌─────────────────────────────────────────────────────────────┐
│                      API Layer                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  chat.py    │  │   rag.py    │  │     health.py       │  │
│  │ /ai/chat/*  │  │ /ai/rag/*   │  │ /health, /health/*  │  │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
└─────────┼────────────────┼─────────────────────┼────────────┘
          │                │                     │
          ▼                ▼                     ▼
┌─────────────────────────────────────────────────────────────┐
│                    Service Layer                             │
│  ┌─────────────────┐  ┌─────────────────┐                   │
│  │  ChatService    │  │   RagService    │                   │
│  │ (dummy 응답)    │  │  (dummy 응답)   │                   │
│  └────────┬────────┘  └────────┬────────┘                   │
└───────────┼─────────────────────┼───────────────────────────┘
            │                     │
            ▼                     ▼
┌─────────────────────────────────────────────────────────────┐
│                    Clients Layer (신규)                      │
│  ┌─────────────────┐  ┌─────────────────┐                   │
│  │   LLMClient     │  │ RagflowClient   │                   │
│  │ - health_check  │  │ - health_check  │                   │
│  │ - chat_complete │  │ - process_doc   │                   │
│  │ - embedding     │  │ - search_docs   │                   │
│  └────────┬────────┘  └────────┬────────┘                   │
│           │                    │                            │
│           └────────┬───────────┘                            │
│                    ▼                                        │
│           ┌─────────────────┐                               │
│           │ http_client.py  │                               │
│           │ (AsyncClient    │                               │
│           │  싱글턴)        │                               │
│           └────────┬────────┘                               │
└────────────────────┼────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│                  External Services                           │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────┐ │
│  │ ctrlf-ragflow│ │  LLM Server  │ │    ctrlf-back        │ │
│  │ (RAG 처리)   │ │ (GPT 등)     │ │  (Spring Backend)    │ │
│  └──────────────┘ └──────────────┘ └──────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 HTTP 클라이언트 생명주기

```
┌─────────────────────────────────────────────────────────────┐
│                  Application Lifecycle                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  [Startup]                                                   │
│     │                                                        │
│     ▼                                                        │
│  setup_logging()                                             │
│     │                                                        │
│     ▼                                                        │
│  logger.info("Starting...")                                  │
│     │                                                        │
│     ▼                                                        │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                    yield (Running)                    │   │
│  │                                                       │   │
│  │  첫 HTTP 요청 시:                                     │   │
│  │  get_async_http_client() ──▶ AsyncClient 생성        │   │
│  │                              (lazy initialization)    │   │
│  │                                                       │   │
│  │  이후 요청:                                           │   │
│  │  get_async_http_client() ──▶ 기존 인스턴스 반환      │   │
│  └──────────────────────────────────────────────────────┘   │
│     │                                                        │
│     ▼                                                        │
│  [Shutdown]                                                  │
│     │                                                        │
│     ▼                                                        │
│  close_async_http_client() ──▶ AsyncClient.aclose()         │
│     │                                                        │
│     ▼                                                        │
│  logger.info("Shutting down...")                             │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 6. 테스트 결과

### 6.1 테스트 실행

```bash
$ pytest -q
....................                                                     [100%]
20 passed in 2.49s
```

### 6.2 테스트 상세

| 테스트 파일 | 테스트 수 | 결과 |
|-------------|----------|------|
| test_health.py | 5 | PASSED |
| test_chat_api.py | 7 | PASSED |
| test_rag_api.py | 8 | PASSED |
| **총계** | **20** | **ALL PASSED** |

### 6.3 호환성 검증

기존 테스트가 모두 통과하는 이유:
1. **환경 독립성**: 테스트 환경에서 `RAGFLOW_BASE_URL`, `LLM_BASE_URL`, `BACKEND_BASE_URL`이 설정되지 않음
2. **조건부 체크**: `/health/ready`는 BASE_URL이 설정된 서비스만 체크
3. **기본 동작 유지**: 의존성이 없으면 `ready=True`, `checks={}` 반환

---

## 7. 환경변수 (변경 없음)

| 변수명 | 설명 | 기본값 | Phase 3-1 사용 |
|--------|------|--------|----------------|
| `APP_NAME` | 애플리케이션 이름 | `ctrlf-ai-gateway` | - |
| `APP_ENV` | 실행 환경 | `local` | - |
| `APP_VERSION` | 버전 | `0.1.0` | - |
| `LOG_LEVEL` | 로그 레벨 | `INFO` | - |
| `RAGFLOW_BASE_URL` | RAGFlow 서비스 URL | (빈 문자열) | RagflowClient |
| `LLM_BASE_URL` | LLM 서비스 URL | (빈 문자열) | LLMClient |
| `BACKEND_BASE_URL` | Spring 백엔드 URL | (빈 문자열) | Backend health |
| `CORS_ORIGINS` | 허용 Origin | `*` | - |

---

## 8. TODO 항목 (향후 작업)

### 8.1 클라이언트 계층

| 파일 | TODO 내용 | 우선순위 |
|------|-----------|----------|
| `ragflow_client.py` | 실제 RAGFlow health 엔드포인트 경로 확인 | 높음 |
| `ragflow_client.py` | 실제 문서 처리 API 스펙 반영 | 높음 |
| `ragflow_client.py` | 실제 검색 API 스펙 반영 | 높음 |
| `llm_client.py` | 실제 LLM health 엔드포인트 확인 | 높음 |
| `llm_client.py` | 실제 ChatCompletion API 스펙 반영 | 높음 |
| `llm_client.py` | 실제 Embedding API 스펙 반영 | 중간 |

### 8.2 Readiness 체크

| 파일 | TODO 내용 | 우선순위 |
|------|-----------|----------|
| `health.py` | 백엔드 health 엔드포인트 경로 확인 (`/actuator/health` 등) | 높음 |

### 8.3 서비스 계층 연동 (다음 Phase)

| 작업 | 설명 |
|------|------|
| `ChatService` + `LLMClient` 연동 | 더미 응답 → 실제 LLM 호출 |
| `ChatService` + `RagflowClient` 연동 | RAG 검색 결과 활용 |
| `RagService` + `RagflowClient` 연동 | 더미 응답 → 실제 RAGFlow 호출 |

---

## 9. 파일별 코드 라인 수

| 파일 | 라인 수 | 비고 |
|------|---------|------|
| `app/clients/__init__.py` | 25 | 신규 |
| `app/clients/http_client.py` | 64 | 신규 |
| `app/clients/ragflow_client.py` | 176 | 신규 |
| `app/clients/llm_client.py` | 162 | 신규 |
| `app/main.py` | 140 | 수정 (+3 lines) |
| `app/api/v1/health.py` | 146 | 수정 (+15 lines) |

**Phase 3-1 추가 코드**: 약 430 lines

---

## 10. 다음 Phase 예고

### Phase 3-2: 서비스 계층 연동 (예정)

1. **ChatService 실제 구현**
   - LLMClient를 사용한 채팅 응답 생성
   - RagflowClient를 사용한 문서 검색 연동

2. **RagService 실제 구현**
   - RagflowClient를 사용한 문서 처리 연동

3. **에러 핸들링 강화**
   - 외부 서비스 타임아웃/에러 처리
   - 재시도 로직 (optional)

4. **클라이언트 테스트 추가**
   - Mock 서버를 사용한 클라이언트 단위 테스트

---

## 11. 결론

Phase 3-1에서 외부 서비스와 통신하기 위한 클라이언트 계층의 기반을 성공적으로 구축했습니다.

### 핵심 성과
- **공용 HTTP 클라이언트**: 연결 풀을 효율적으로 관리하는 싱글턴 구현
- **클라이언트 스켈레톤**: RAGFlow, LLM 클라이언트의 뼈대 구현
- **Readiness 확장**: 외부 서비스 의존성 체크 구조 추가
- **하위 호환성**: 기존 20개 테스트 모두 통과

### 설계 원칙
- **Lazy Initialization**: 필요할 때만 리소스 생성
- **안전한 기본값**: BASE_URL 미설정 시 네트워크 호출 방지
- **명시적 TODO**: 팀원이 나중에 수정해야 할 부분 명확히 표시

다음 단계에서는 서비스 계층과 클라이언트 계층을 연동하여 실제 외부 서비스 호출을 구현할 예정입니다.

---

**작성자**: Claude Code
**검토 필요 항목**: RAGFlow/LLM/Backend 실제 API 스펙 확인
