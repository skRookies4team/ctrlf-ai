# Phase 9 구현 보고서: Mock/Real 모드 전환 기능

## 1. 개요

### 1.1 목적
AI Gateway가 **Mock 서비스**(Docker Compose 통합 테스트용)와 **Real 서비스**(실제 RAGFlow/LLM/Backend)를 환경변수 하나로 전환할 수 있는 설정 구조 구현

### 1.2 구현 범위
- `AI_ENV` 환경변수 기반 mock/real 모드 전환
- Docker Compose 프로필 (`--profile mock`, `--profile real`)
- REAL 모드용 Smoke Test 스켈레톤
- 기존 단위 테스트 호환성 유지

---

## 2. 아키텍처

### 2.1 URL 선택 우선순위

```
1. *_BASE_URL (직접 지정) → 최우선
2. AI_ENV=real → *_BASE_URL_REAL 사용
3. AI_ENV=mock (기본값) → *_BASE_URL_MOCK 사용
```

### 2.2 모드별 동작

| 모드 | AI_ENV | 대상 서비스 | 용도 |
|------|--------|-------------|------|
| Mock | `mock` | Docker Compose 내부 Mock 서버 | 통합 테스트, 개발 |
| Real | `real` | 실제 RAGFlow/LLM/Backend | 스테이징, 프로덕션 |

---

## 3. 구현 상세

### 3.1 Settings 클래스 변경 (`app/core/config.py`)

```python
class Settings(BaseSettings):
    # Phase 9: AI 환경 모드 설정
    AI_ENV: str = "mock"  # "mock" or "real"

    # Mock 모드 URL (Docker Compose 내부 네트워크)
    RAGFLOW_BASE_URL_MOCK: str = "http://ragflow:8080"
    LLM_BASE_URL_MOCK: str = "http://llm-internal:8001"
    BACKEND_BASE_URL_MOCK: str = "http://backend-mock:8081"

    # Real 모드 URL (배포 시 설정)
    RAGFLOW_BASE_URL_REAL: Optional[str] = None
    LLM_BASE_URL_REAL: Optional[str] = None
    BACKEND_BASE_URL_REAL: Optional[str] = None

    @property
    def ragflow_base_url(self) -> Optional[str]:
        """AI_ENV에 따라 자동으로 URL 선택"""
        if self.RAGFLOW_BASE_URL:
            return str(self.RAGFLOW_BASE_URL)
        if self.AI_ENV == "real":
            return self.RAGFLOW_BASE_URL_REAL
        if not self.RAGFLOW_BASE_URL_MOCK:
            return None
        return self.RAGFLOW_BASE_URL_MOCK
```

### 3.2 Docker Compose 프로필 (`docker-compose.yml`)

```yaml
services:
  # 기본 AI Gateway 정의
  ai-gateway:
    build: .
    environment:
      - AI_ENV=${AI_ENV:-mock}
      - RAGFLOW_BASE_URL_MOCK=http://ragflow:8080
      - RAGFLOW_BASE_URL_REAL=${RAGFLOW_BASE_URL_REAL:-}

  # Mock 프로필: Mock 서비스 의존성 포함
  ai-gateway-mock:
    extends: ai-gateway
    profiles: [mock]
    depends_on:
      ragflow: { condition: service_healthy }
      llm-internal: { condition: service_healthy }
      backend-mock: { condition: service_healthy }

  # Real 프로필: 외부 서비스 직접 연결
  ai-gateway-real:
    extends: ai-gateway
    profiles: [real]
    environment:
      - AI_ENV=real
      - RAGFLOW_BASE_URL_REAL=${RAGFLOW_BASE_URL_REAL:?Required}

  # Mock 서비스들 (mock 프로필 전용)
  ragflow:
    profiles: [mock]
  llm-internal:
    profiles: [mock]
  backend-mock:
    profiles: [mock]
```

### 3.3 클라이언트 변경

모든 클라이언트가 새 프로퍼티 사용:

```python
# RagflowClient
self._base_url = base_url if base_url is not None else settings.ragflow_base_url

# LLMClient
self._base_url = base_url if base_url is not None else settings.llm_base_url

# BackendClient
self._base_url = base_url or settings.backend_base_url
```

### 3.4 REAL 모드 Smoke Test (`tests/integration/test_real_smoke.py`)

```python
@pytest.mark.real_integration
def test_real_ai_gateway_health(http_client, check_real_mode_configured):
    """AI Gateway가 REAL 모드에서 정상 동작하는지 확인"""
    response = http_client.get(f"{AI_GATEWAY_URL}/health")
    assert response.status_code == 200

@pytest.mark.real_integration
def test_real_simple_chat_request(http_client, check_real_mode_configured):
    """실제 서비스를 통한 채팅 요청 테스트"""
    # 실제 LLM API 호출 (비용 발생 가능)
    ...
```

**테스트 목록:**
1. `test_real_ai_gateway_health` - 헬스체크
2. `test_real_readiness_check` - Readiness (서비스 연결 확인)
3. `test_real_simple_chat_request` - 간단한 채팅 E2E
4. `test_real_rag_llm_integration` - RAG + LLM 통합
5. `test_real_pii_masking` - PII 마스킹 동작
6. `test_real_invalid_request_handling` - 에러 처리
7. `test_real_concurrent_requests` - 동시 요청 처리

### 3.5 테스트 환경 설정 (`tests/conftest.py`)

```python
import os

# 단위 테스트: Mock URL을 빈 문자열로 설정하여 외부 호출 방지
os.environ["RAGFLOW_BASE_URL_MOCK"] = ""
os.environ["LLM_BASE_URL_MOCK"] = ""
os.environ["BACKEND_BASE_URL_MOCK"] = ""

# HttpUrl 타입은 빈 문자열 불허 → 환경변수 삭제
for key in ["RAGFLOW_BASE_URL", "LLM_BASE_URL", "BACKEND_BASE_URL"]:
    os.environ.pop(key, None)

os.environ["AI_ENV"] = "mock"

from app.core.config import clear_settings_cache
clear_settings_cache()
```

---

## 4. 환경변수 정리

| 변수명 | 설명 | 기본값 |
|--------|------|--------|
| `AI_ENV` | 서비스 모드 (`mock`/`real`) | `mock` |
| `RAGFLOW_BASE_URL` | RAGFlow URL (직접 지정, 최우선) | - |
| `RAGFLOW_BASE_URL_MOCK` | Mock 모드용 RAGFlow URL | `http://ragflow:8080` |
| `RAGFLOW_BASE_URL_REAL` | Real 모드용 RAGFlow URL | - |
| `LLM_BASE_URL` | LLM URL (직접 지정, 최우선) | - |
| `LLM_BASE_URL_MOCK` | Mock 모드용 LLM URL | `http://llm-internal:8001` |
| `LLM_BASE_URL_REAL` | Real 모드용 LLM URL | - |
| `BACKEND_BASE_URL` | Backend URL (직접 지정, 최우선) | - |
| `BACKEND_BASE_URL_MOCK` | Mock 모드용 Backend URL | `http://backend-mock:8081` |
| `BACKEND_BASE_URL_REAL` | Real 모드용 Backend URL | - |

---

## 5. 사용 방법

### 5.1 Mock 모드 (통합 테스트)

```bash
# Mock 서비스와 함께 시작
docker compose --profile mock up -d

# 통합 테스트 실행
pytest -m integration -v

# 종료
docker compose --profile mock down
```

### 5.2 Real 모드 (실제 서비스 연동)

```bash
# 환경변수 설정 (필수)
export RAGFLOW_BASE_URL_REAL=http://your-ragflow:8080
export LLM_BASE_URL_REAL=http://your-llm:8001
export BACKEND_BASE_URL_REAL=http://your-backend:8080

# Real 모드로 시작 (Mock 서비스 없음)
docker compose --profile real up -d

# Smoke 테스트 실행
pytest -m real_integration -v

# 종료
docker compose --profile real down
```

### 5.3 단위 테스트 (외부 서비스 없음)

```bash
# 기본 실행 (integration, real_integration 제외)
pytest -v

# 결과: 107 passed
```

---

## 6. 변경된 파일 목록

| 파일 | 변경 내용 |
|------|----------|
| `app/core/config.py` | AI_ENV, mock/real URL 필드, 프로퍼티, clear_settings_cache() |
| `app/clients/ragflow_client.py` | `settings.ragflow_base_url` 프로퍼티 사용 |
| `app/clients/llm_client.py` | `settings.llm_base_url` 프로퍼티 사용 |
| `app/clients/backend_client.py` | `settings.backend_base_url` 프로퍼티 사용 |
| `app/services/rag_service.py` | `process_document_request()` 메서드 호출로 수정 |
| `app/services/ai_log_service.py` | `settings.backend_base_url` 프로퍼티 사용 |
| `app/api/v1/health.py` | 프로퍼티 기반 헬스체크 |
| `docker-compose.yml` | mock/real 프로필 구조 |
| `pytest.ini` | `real_integration` 마커 추가 |
| `tests/conftest.py` | 테스트 환경 설정 (신규) |
| `tests/integration/test_real_smoke.py` | Real 모드 smoke test (신규) |
| `README.md` | 환경변수 및 사용법 업데이트 |

---

## 7. 테스트 결과

```
==================== test session starts ====================
collected 119 items / 12 deselected / 107 selected

tests/test_ai_log.py .................. [ 16%]
tests/test_chat_api.py .......        [ 23%]
tests/test_chat_http_e2e.py .....     [ 28%]
tests/test_chat_rag_integration.py .. [ 36%]
tests/test_health.py .....            [ 41%]
tests/test_intent_and_pii.py ........ [ 57%]
tests/test_pii_http_integration.py .. [ 71%]
tests/test_rag_api.py ........        [ 79%]
tests/test_ragflow_retrieval_test.py  [ 91%]
tests/test_service_fallback.py ...... [100%]

================ 107 passed, 12 deselected in 5.16s ================
```

- **통과**: 107개 (단위 테스트)
- **제외**: 12개 (integration + real_integration)

---

## 8. 향후 작업

1. **Real URL 확정**: 실제 RAGFlow/LLM/Backend 서비스 URL 확정 후 `REAL` 환경변수 기본값 설정
2. **CI/CD 통합**: GitHub Actions에서 `real_integration` 테스트를 스테이징 환경에서 실행
3. **모니터링**: Real 모드 Smoke Test 결과를 Slack/Teams로 알림

---

## 9. 결론

Phase 9 구현을 통해 AI Gateway는 단일 환경변수(`AI_ENV`)로 Mock/Real 모드를 전환할 수 있게 되었습니다. 이를 통해:

- **개발/테스트**: Mock 서비스로 빠른 피드백 루프
- **스테이징/프로덕션**: Real 서비스 연동으로 실제 동작 검증
- **기존 호환성**: 모든 107개 단위 테스트 통과
