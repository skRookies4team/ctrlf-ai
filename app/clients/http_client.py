"""
공용 HTTP 클라이언트 모듈 (Shared HTTP Client Module)

애플리케이션 전역에서 재사용할 httpx.AsyncClient 싱글턴을 관리합니다.
연결 풀을 효율적으로 사용하고, 애플리케이션 종료 시 리소스를 정리합니다.

사용 방법:
    from app.clients.http_client import get_async_http_client

    client = get_async_http_client()
    response = await client.get("https://example.com")
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
    연결 풀 설정:
        - timeout: 10초 (connect: 5초)
        - max_keepalive_connections: 20
        - max_connections: 100

    Returns:
        httpx.AsyncClient: 공용 HTTP 클라이언트 인스턴스
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
    """
    애플리케이션 종료 시 싱글턴 AsyncClient를 정리합니다.

    FastAPI lifespan의 shutdown 단계에서 호출되어야 합니다.
    """
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
        logger.info("Closed shared AsyncClient")
