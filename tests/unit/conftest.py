"""
pytest conftest.py - Unit Test Configuration

Unit tests run WITHOUT external services.
All service URLs are cleared to prevent accidental external calls.
"""

import os

# Set environment variables BEFORE importing any app modules
# This ensures Settings class uses these values

# Disable mock URLs for unit tests (no external services required)
os.environ["RAGFLOW_BASE_URL_MOCK"] = ""
os.environ["LLM_BASE_URL_MOCK"] = ""
os.environ["BACKEND_BASE_URL_MOCK"] = ""

# Disable Milvus for unit tests (use FakeRagflowClient instead)
os.environ["MILVUS_ENABLED"] = "false"
os.environ["RETRIEVAL_BACKEND"] = "ragflow"
os.environ["CHAT_RETRIEVER_BACKEND"] = "ragflow"

# Remove direct URL env vars (HttpUrl type doesn't accept empty string)
# So we need to unset them entirely
for key in ["RAGFLOW_BASE_URL", "LLM_BASE_URL", "BACKEND_BASE_URL"]:
    os.environ.pop(key, None)

# Set AI_ENV to mock mode (but with empty mock URLs = no external calls)
os.environ["AI_ENV"] = "mock"

# Now clear the settings cache so Settings reloads with new values
# Import AFTER setting env vars to ensure clean state
from app.core.config import clear_settings_cache
clear_settings_cache()


# =============================================================================
# Singleton cleanup fixtures
# =============================================================================

import pytest


@pytest.fixture(autouse=True)
def reset_singletons():
    """각 테스트 후 싱글톤 인스턴스를 정리합니다.

    테스트 격리를 위해 모든 테스트 후 자동으로 실행됩니다.
    환경변수를 변경하는 테스트에서도 다음 테스트가 깨끗한 상태로 시작합니다.
    """
    # 테스트 전: 환경변수 정리 (unit test에서는 외부 서비스 사용 안 함)
    for key in ["RAGFLOW_BASE_URL", "LLM_BASE_URL", "BACKEND_BASE_URL"]:
        os.environ.pop(key, None)
    clear_settings_cache()

    yield

    # 테스트 후 정리
    from app.clients.llm_client import clear_llm_client
    from app.clients.ragflow_client import clear_ragflow_client
    from app.services.pii_service import clear_pii_service

    clear_llm_client()
    clear_ragflow_client()
    clear_pii_service()
    clear_settings_cache()


@pytest.fixture(scope="session", autouse=True)
def cleanup_http_client():
    """테스트 세션 종료 시 공용 HTTP 클라이언트를 정리합니다.

    FastAPI lifespan 외부에서 실행되는 테스트를 위해
    공용 HTTP 클라이언트를 명시적으로 close합니다.
    """
    yield
    # 세션 종료 시 1회 close
    import asyncio
    from app.clients.http_client import close_async_http_client

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(close_async_http_client())
        else:
            loop.run_until_complete(close_async_http_client())
    except Exception:
        pass  # 이미 닫혔거나 루프가 없는 경우 무시
