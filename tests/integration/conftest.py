"""
pytest conftest.py - Integration Test Configuration

Integration tests run WITH real external services (LLM, RAGFlow, Milvus).
Environment variables are NOT modified - real service URLs are expected.

Local convenience: Skip tests if required env vars are missing.
CI: Env vars must be set, so tests will run (and fail if services are down).
"""

import os
import pytest
import asyncio


# =============================================================================
# Required Environment Variables for Integration Tests
# =============================================================================

REQUIRED_ENV_VARS = {
    "LLM_BASE_URL": "LLM 서비스 URL",
    "RAGFLOW_BASE_URL": "RAGFlow 서비스 URL",
}

OPTIONAL_ENV_VARS = {
    "MILVUS_HOST": "Milvus 호스트",
    "MILVUS_PORT": "Milvus 포트",
    "BACKEND_BASE_URL": "Backend 서비스 URL",
}


def get_missing_env_vars():
    """필수 환경변수 중 누락된 것을 반환합니다."""
    missing = []
    for var, desc in REQUIRED_ENV_VARS.items():
        if not os.environ.get(var):
            missing.append(f"{var} ({desc})")
    return missing


# =============================================================================
# Skip decorator for integration tests
# =============================================================================

missing_vars = get_missing_env_vars()
SKIP_INTEGRATION = len(missing_vars) > 0
SKIP_REASON = f"Integration test skipped: missing env vars: {', '.join(missing_vars)}"


def requires_integration_env(func):
    """통합 테스트에 필요한 환경변수가 없으면 skip하는 데코레이터."""
    return pytest.mark.skipif(SKIP_INTEGRATION, reason=SKIP_REASON)(func)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def integration_env_check():
    """통합 테스트 환경 확인 fixture.

    세션 시작 시 환경변수 상태를 출력합니다.
    """
    print("\n" + "=" * 60)
    print("Integration Test Environment Check")
    print("=" * 60)

    for var, desc in {**REQUIRED_ENV_VARS, **OPTIONAL_ENV_VARS}.items():
        value = os.environ.get(var, "")
        status = "OK" if value else "MISSING"
        # URL은 앞부분만 표시 (보안)
        display_value = value[:30] + "..." if len(value) > 30 else value
        print(f"  {var}: {status} ({display_value or 'N/A'})")

    print("=" * 60 + "\n")

    if SKIP_INTEGRATION:
        pytest.skip(SKIP_REASON)


@pytest.fixture(autouse=True)
def reset_singletons():
    """각 테스트 후 싱글톤 인스턴스를 정리합니다."""
    yield
    from app.clients.llm_client import clear_llm_client
    from app.clients.ragflow_client import clear_ragflow_client
    from app.services.pii_service import clear_pii_service

    clear_llm_client()
    clear_ragflow_client()
    clear_pii_service()


@pytest.fixture(scope="session", autouse=True)
def cleanup_http_client():
    """테스트 세션 종료 시 공용 HTTP 클라이언트를 정리합니다."""
    yield
    from app.clients.http_client import close_async_http_client

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(close_async_http_client())
        else:
            loop.run_until_complete(close_async_http_client())
    except Exception:
        pass


# =============================================================================
# Health Check Fixtures (for CI preflight)
# =============================================================================

@pytest.fixture(scope="session")
async def check_llm_health():
    """LLM 서비스 헬스체크."""
    import httpx

    base_url = os.environ.get("LLM_BASE_URL")
    if not base_url:
        pytest.skip("LLM_BASE_URL not set")

    async with httpx.AsyncClient() as client:
        try:
            # OpenAI compatible health check
            response = await client.get(f"{base_url}/health", timeout=10.0)
            if response.status_code >= 500:
                pytest.fail(f"LLM service unhealthy: {response.status_code}")
        except httpx.ConnectError as e:
            pytest.fail(f"Cannot connect to LLM service: {e}")

    return True


@pytest.fixture(scope="session")
async def check_ragflow_health():
    """RAGFlow 서비스 헬스체크."""
    import httpx

    base_url = os.environ.get("RAGFLOW_BASE_URL")
    if not base_url:
        pytest.skip("RAGFLOW_BASE_URL not set")

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{base_url}/api/health", timeout=10.0)
            if response.status_code >= 500:
                pytest.fail(f"RAGFlow service unhealthy: {response.status_code}")
        except httpx.ConnectError as e:
            pytest.fail(f"Cannot connect to RAGFlow service: {e}")

    return True
