# Phase 12: AI Gateway 안정성/품질 Hardening 개발 보고서

## 개요

Phase 12에서는 AI Gateway의 안정성과 품질을 강화하기 위해 다음 기능들을 구현했습니다:

1. **에러 타입 표준화**: ErrorType, ServiceType Enum 및 UpstreamServiceError 커스텀 예외
2. **타임아웃/재시도 로직**: 각 외부 서비스별 타임아웃 설정 및 지수 백오프 재시도
3. **Fallback 전략**: RAG/LLM/Backend 실패 시 graceful degradation
4. **모니터링/지표 수집**: 에러 카운터, latency 통계, 요청 카운터
5. **개별 서비스 latency 측정**: ChatAnswerMeta에 rag_latency_ms, llm_latency_ms, backend_latency_ms 추가

## 구현 내용

### Step 1: 에러 타입 및 응답 스펙 정의

**새 파일: `app/core/exceptions.py`**

```python
class ErrorType(str, Enum):
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"  # 외부 서비스 타임아웃
    UPSTREAM_ERROR = "UPSTREAM_ERROR"      # 외부 서비스 HTTP 에러
    BAD_REQUEST = "BAD_REQUEST"            # 입력 검증 실패
    INTERNAL_ERROR = "INTERNAL_ERROR"      # 내부 서비스 에러
    UNKNOWN = "UNKNOWN"                    # 알 수 없는 에러

class ServiceType(str, Enum):
    RAGFLOW = "RAGFLOW"
    LLM = "LLM"
    BACKEND = "BACKEND"
    PII = "PII"

class UpstreamServiceError(Exception):
    """외부 서비스 에러를 래핑하는 커스텀 예외"""
    def __init__(self, service, error_type, message, status_code=None, is_timeout=False, original_error=None):
        ...
```

**ChatAnswerMeta 확장 필드:**
- `error_type`: 에러 타입 코드
- `error_message`: 요약된 에러 메시지
- `fallback_reason`: fallback 발생 원인 (RAG_FAIL, LLM_FAIL, BACKEND_FAIL)
- `rag_latency_ms`: RAG 검색 소요 시간
- `llm_latency_ms`: LLM 응답 생성 소요 시간
- `backend_latency_ms`: Backend API 소요 시간

### Step 2: 클라이언트 타임아웃/재시도/에러 래핑

**새 파일: `app/core/retry.py`**

타임아웃 상수:
- `DEFAULT_RAGFLOW_TIMEOUT = 10.0s`
- `DEFAULT_LLM_TIMEOUT = 30.0s`
- `DEFAULT_BACKEND_TIMEOUT = 5.0s`

재시도 설정:
```python
LLM_RETRY_CONFIG = RetryConfig(max_retries=1, base_delay=0.5, max_delay=2.0)
RAGFLOW_RETRY_CONFIG = RetryConfig(max_retries=1, base_delay=0.2, max_delay=1.0)
BACKEND_RETRY_CONFIG = RetryConfig(max_retries=1, base_delay=0.2, max_delay=1.0)
```

지수 백오프:
```python
def calculate_backoff_delay(attempt, base_delay=0.2, max_delay=2.0):
    delay = base_delay * (2 ** attempt)
    return min(delay, max_delay)
```

**LLMClient 에러 래핑 예시:**
```python
try:
    response = await retry_async_operation(
        self._client.post, url, json=payload, timeout=self._timeout,
        config=LLM_RETRY_CONFIG, operation_name="llm_chat_completion",
    )
except httpx.TimeoutException as e:
    raise UpstreamServiceError(
        service=ServiceType.LLM,
        error_type=ErrorType.UPSTREAM_TIMEOUT,
        message="LLM timeout",
        is_timeout=True,
        original_error=e,
    )
```

### Step 3: ChatService Fallback 로직

**Fallback 메시지:**
```python
LLM_FALLBACK_MESSAGE = "죄송합니다. 현재 AI 서비스에 일시적인 문제가 발생했습니다..."
BACKEND_FALLBACK_MESSAGE = "현재 시스템에서 정보를 가져오는 데 문제가 발생했습니다..."
RAG_FAIL_NOTICE = "\n\n※ 참고: 관련 문서 검색에 문제가 있어..."
MIXED_BACKEND_FAIL_NOTICE = "\n\n※ 참고: 실제 현황 데이터를 조회하지 못했습니다..."
```

**Fallback 전략:**

| 라우트 | 실패 조건 | Fallback 동작 |
|--------|-----------|---------------|
| RAG_INTERNAL | RAG 실패 | LLM-only로 진행 + RAG_FAIL_NOTICE 추가 |
| BACKEND_API | Backend 실패 | BACKEND_FALLBACK_MESSAGE 반환 |
| MIXED_BACKEND_RAG | RAG만 실패 | Backend 데이터만 사용 + RAG_FAIL_NOTICE |
| MIXED_BACKEND_RAG | Backend만 실패 | RAG 데이터만 사용 + MIXED_BACKEND_FAIL_NOTICE |
| 모든 라우트 | LLM 실패 | LLM_FALLBACK_MESSAGE + route=ERROR |

**_perform_rag_search_with_fallback 메서드:**
```python
async def _perform_rag_search_with_fallback(self, query, domain, req):
    """RAG 검색 수행 후 (결과, 실패여부) 반환"""
    try:
        sources = await self._ragflow.search_as_sources(...)
        return sources, False  # 성공 (0건도 정상)
    except UpstreamServiceError:
        metrics.increment_error(LOG_TAG_RAG_ERROR)
        metrics.increment_error(LOG_TAG_RAG_FALLBACK)
        return [], True  # 실패
```

### Step 4: 모니터링 및 지표 수집

**새 파일: `app/core/metrics.py`**

```python
class MetricsCollector:
    """스레드 안전한 in-memory 지표 수집기"""
    error_counts: Dict[str, int]      # 에러 타입별 카운터
    retry_counts: Dict[str, int]      # 서비스별 재시도 카운터
    latency_stats: Dict[str, LatencyStats]  # 서비스별 latency 통계
    request_counts: Dict[str, int]    # 라우트별 요청 카운터

    def increment_error(self, error_tag: str) -> None: ...
    def increment_retry(self, service: str) -> None: ...
    def record_latency(self, service: str, latency_ms: int) -> None: ...
    def increment_request(self, route: str) -> None: ...
    def get_stats(self) -> Dict: ...
```

**로그 태그 상수:**
```python
LOG_TAG_RAG_TIMEOUT = "RAG_TIMEOUT"
LOG_TAG_RAG_ERROR = "RAG_ERROR"
LOG_TAG_LLM_TIMEOUT = "LLM_TIMEOUT"
LOG_TAG_LLM_ERROR = "LLM_ERROR"
LOG_TAG_LLM_RETRY = "LLM_RETRY"
LOG_TAG_RAG_FALLBACK = "RAG_FALLBACK"
LOG_TAG_LLM_FALLBACK = "LLM_FALLBACK"
```

**ChatService에서 메트릭 기록:**
```python
# LLM 에러 시
if e.is_timeout:
    metrics.increment_error(LOG_TAG_LLM_TIMEOUT)
else:
    metrics.increment_error(LOG_TAG_LLM_ERROR)
metrics.increment_error(LOG_TAG_LLM_FALLBACK)

# 응답 완료 시
metrics.increment_request(final_route.value)
metrics.record_latency("llm", llm_latency_ms)
metrics.record_latency("ragflow", rag_latency_ms)
metrics.record_latency("backend", backend_latency_ms)
```

### Step 5: 테스트 추가

**새 파일: `tests/test_phase12_hardening.py`**

총 41개 테스트:

1. **ErrorType/ServiceType Enum 테스트** (2개)
2. **UpstreamServiceError 예외 테스트** (7개)
3. **RetryConfig 및 retry_async_operation 테스트** (6개)
4. **calculate_backoff_delay 테스트** (4개)
5. **MetricsCollector 테스트** (7개)
6. **ChatAnswerMeta Phase 12 필드 테스트** (2개)
7. **LLMClient 에러 래핑 테스트** (5개)
8. **ChatService fallback 테스트** (5개)
9. **상수 테스트** (3개)

## 테스트 결과

```
tests/test_phase12_hardening.py: 41 passed
전체 테스트: 224 passed, 12 deselected
```

## 파일 변경 요약

### 새로 생성된 파일
- `app/core/exceptions.py`: 에러 타입 및 커스텀 예외 클래스
- `app/core/retry.py`: 재시도 로직 및 타임아웃 상수
- `app/core/metrics.py`: 모니터링 지표 수집기
- `tests/test_phase12_hardening.py`: Phase 12 테스트 (41개)

### 수정된 파일
- `app/models/chat.py`: ChatAnswerMeta에 Phase 12 필드 추가
- `app/clients/llm_client.py`: 타임아웃, 재시도, UpstreamServiceError 래핑
- `app/clients/ragflow_client.py`: 재시도 로직 추가
- `app/clients/backend_data_client.py`: 재시도 및 latency 추적
- `app/services/chat_service.py`: Fallback 로직, 메트릭 기록, latency 추적

## 향후 확장

1. **Prometheus/Grafana 연동**: MetricsCollector를 Prometheus exporter로 확장
2. **알림 시스템**: 특정 에러 임계치 초과 시 알림 발송
3. **Circuit Breaker**: 연속 실패 시 자동 차단 및 복구
4. **성능 최적화**: 병렬 처리 및 캐싱 전략 고도화

## 결론

Phase 12에서 AI Gateway의 안정성과 품질을 크게 강화했습니다:

- **표준화된 에러 처리**: 모든 외부 서비스 에러가 UpstreamServiceError로 일관되게 래핑됨
- **Graceful Degradation**: 부분 실패 시에도 가능한 범위 내에서 서비스 제공
- **투명한 모니터링**: 에러, 재시도, latency 지표가 수집되어 운영 가시성 확보
- **안정적인 코드베이스**: 224개 테스트 통과, Phase 12 전용 41개 테스트 추가
