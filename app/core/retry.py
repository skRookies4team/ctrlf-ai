"""
재시도 헬퍼 모듈 (Retry Helper Module)

Phase 12: 외부 서비스 호출 재시도 로직
- 단순 지수 백오프 구현
- 향후 tenacity 등 라이브러리로 교체 가능한 구조

재시도 정책:
- LLM: 1회 재시도 (지수 백오프)
- RAGFlow: 0~1회 재시도 (검색은 실패해도 진행 가능)
- Backend: 0~1회 재시도 (데이터 없이도 진행 가능)

사용 방법:
    from app.core.retry import retry_async

    @retry_async(max_retries=1, base_delay=0.2)
    async def call_llm():
        ...
"""

import asyncio
import functools
from typing import Any, Callable, Optional, Tuple, Type, TypeVar

from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


# =============================================================================
# 타임아웃/재시도 설정 상수
# =============================================================================

# 기본 타임아웃 (초)
DEFAULT_RAGFLOW_TIMEOUT = 10.0
DEFAULT_LLM_TIMEOUT = 30.0  # LLM은 응답 생성에 시간이 걸림
DEFAULT_BACKEND_TIMEOUT = 5.0
DEFAULT_PII_TIMEOUT = 5.0

# 재시도 설정
DEFAULT_MAX_RETRIES = 1
DEFAULT_BASE_DELAY = 0.2  # 첫 번째 재시도 전 대기 시간 (초)
DEFAULT_MAX_DELAY = 2.0  # 최대 대기 시간 (초)
DEFAULT_EXPONENTIAL_BASE = 2  # 지수 백오프 기준


class RetryConfig:
    """
    재시도 설정.

    Attributes:
        max_retries: 최대 재시도 횟수
        base_delay: 첫 재시도 전 대기 시간 (초)
        max_delay: 최대 대기 시간 (초)
        exponential_base: 지수 백오프 기준
        retryable_exceptions: 재시도할 예외 타입 튜플
    """

    def __init__(
        self,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_delay: float = DEFAULT_BASE_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
        exponential_base: int = DEFAULT_EXPONENTIAL_BASE,
        retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
    ) -> None:
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.retryable_exceptions = retryable_exceptions


# 서비스별 기본 재시도 설정
RAGFLOW_RETRY_CONFIG = RetryConfig(
    max_retries=1,
    base_delay=0.2,
    max_delay=1.0,
)

LLM_RETRY_CONFIG = RetryConfig(
    max_retries=1,
    base_delay=0.5,
    max_delay=2.0,
)

BACKEND_RETRY_CONFIG = RetryConfig(
    max_retries=1,
    base_delay=0.2,
    max_delay=1.0,
)


def calculate_backoff_delay(
    attempt: int,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    exponential_base: int = DEFAULT_EXPONENTIAL_BASE,
) -> float:
    """
    지수 백오프 지연 시간을 계산합니다.

    Args:
        attempt: 현재 재시도 횟수 (0부터 시작)
        base_delay: 기본 대기 시간
        max_delay: 최대 대기 시간
        exponential_base: 지수 기준

    Returns:
        float: 대기 시간 (초)

    Example:
        - attempt=0: base_delay * 2^0 = 0.2초
        - attempt=1: base_delay * 2^1 = 0.4초
        - attempt=2: base_delay * 2^2 = 0.8초
    """
    delay = base_delay * (exponential_base ** attempt)
    return min(delay, max_delay)


async def retry_async_operation(
    operation: Callable[..., Any],
    *args: Any,
    config: Optional[RetryConfig] = None,
    operation_name: str = "operation",
    **kwargs: Any,
) -> Any:
    """
    비동기 작업을 재시도합니다.

    Args:
        operation: 실행할 비동기 함수
        *args: 함수 인자
        config: 재시도 설정. None이면 기본값 사용.
        operation_name: 로그용 작업 이름
        **kwargs: 함수 키워드 인자

    Returns:
        작업 결과

    Raises:
        Exception: 모든 재시도 실패 시 마지막 예외

    Example:
        result = await retry_async_operation(
            client.get,
            url,
            config=LLM_RETRY_CONFIG,
            operation_name="llm_chat_completion",
        )
    """
    if config is None:
        config = RetryConfig()

    last_exception: Optional[Exception] = None

    for attempt in range(config.max_retries + 1):
        try:
            return await operation(*args, **kwargs)

        except config.retryable_exceptions as e:
            last_exception = e

            if attempt < config.max_retries:
                delay = calculate_backoff_delay(
                    attempt,
                    config.base_delay,
                    config.max_delay,
                    config.exponential_base,
                )
                logger.warning(
                    f"{operation_name} failed (attempt {attempt + 1}/{config.max_retries + 1}), "
                    f"retrying in {delay:.2f}s: {type(e).__name__}: {e}"
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    f"{operation_name} failed after {config.max_retries + 1} attempts: "
                    f"{type(e).__name__}: {e}"
                )

    if last_exception:
        raise last_exception

    # 이 코드에 도달하면 안 됨
    raise RuntimeError(f"{operation_name} failed without exception")


def retry_async(
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
) -> Callable:
    """
    비동기 함수에 재시도 로직을 추가하는 데코레이터.

    Args:
        max_retries: 최대 재시도 횟수
        base_delay: 첫 재시도 전 대기 시간 (초)
        max_delay: 최대 대기 시간 (초)
        retryable_exceptions: 재시도할 예외 타입 튜플

    Returns:
        데코레이터 함수

    Example:
        @retry_async(max_retries=1, base_delay=0.2)
        async def call_external_api():
            response = await client.get(url)
            return response.json()
    """
    config = RetryConfig(
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        retryable_exceptions=retryable_exceptions,
    )

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await retry_async_operation(
                func,
                *args,
                config=config,
                operation_name=func.__name__,
                **kwargs,
            )
        return wrapper

    return decorator
