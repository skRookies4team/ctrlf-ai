"""
pytest conftest.py - Shared Test Configuration (Root)

공통 fixture만 포함합니다. env 조작은 하지 않습니다.

- tests/unit/conftest.py: env 비움 (외부 서비스 차단)
- tests/integration/conftest.py: env 유지 (실제 서비스 사용)

주의: 이 파일에서 os.environ을 수정하면 integration 테스트까지 영향받습니다.
"""

import asyncio
import pytest


# =============================================================================
# Singleton cleanup fixtures (공통)
# =============================================================================


@pytest.fixture(autouse=True)
def reset_singletons():
    """각 테스트 후 싱글톤 인스턴스를 정리합니다.

    테스트 격리를 위해 모든 테스트 후 자동으로 실행됩니다.
    환경변수를 변경하는 테스트에서도 다음 테스트가 깨끗한 상태로 시작합니다.
    """
    yield
    # 테스트 후 정리
    from app.clients.llm_client import clear_llm_client
    from app.services.pii_service import clear_pii_service

    clear_llm_client()
    clear_pii_service()


@pytest.fixture(scope="session", autouse=True)
def cleanup_http_client():
    """테스트 세션 종료 시 공용 HTTP 클라이언트를 정리합니다.

    FastAPI lifespan 외부에서 실행되는 테스트를 위해
    공용 HTTP 클라이언트를 명시적으로 close합니다.
    """
    yield
    # 세션 종료 시 1회 close
    from app.clients.http_client import close_async_http_client

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(close_async_http_client())
        else:
            loop.run_until_complete(close_async_http_client())
    except Exception:
        pass  # 이미 닫혔거나 루프가 없는 경우 무시
