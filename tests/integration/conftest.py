"""
pytest conftest.py - Integration Test Configuration

Integration tests run WITH real external services (LLM, RAGFlow, Milvus).
Environment variables are NOT modified - real service URLs are expected.

동작 방식:
- 로컬 (CI 환경 아닐 때): 필수 환경변수 없으면 skip (편의성)
- CI (GITHUB_ACTIONS=true): 필수 환경변수 없으면 즉시 fail (strict)
"""

import os
import pytest
import asyncio


# =============================================================================
# CI Environment Detection
# =============================================================================

IS_CI = os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("CI") == "true"


# =============================================================================
# Required Environment Variables for Integration Tests
# =============================================================================

REQUIRED_ENV_VARS = {
    "LLM_BASE_URL": "LLM 서비스 URL",
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
# CI Strict Fail / Local Skip Logic
# =============================================================================

missing_vars = get_missing_env_vars()

if IS_CI and missing_vars:
    # CI에서는 필수 환경변수 없으면 즉시 fail (테스트 수집 단계에서)
    raise AssertionError(
        f"[CI STRICT] Integration tests require env vars: {', '.join(missing_vars)}\n"
        "CI 환경에서는 필수 환경변수가 반드시 설정되어야 합니다."
    )

# 로컬에서만 skip 로직 적용
SKIP_INTEGRATION = len(missing_vars) > 0 and not IS_CI
SKIP_REASON = f"Integration test skipped (local): missing env vars: {', '.join(missing_vars)}"


def requires_integration_env(func):
    """통합 테스트에 필요한 환경변수가 없으면 skip하는 데코레이터 (로컬 전용)."""
    return pytest.mark.skipif(SKIP_INTEGRATION, reason=SKIP_REASON)(func)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def integration_env_check():
    """통합 테스트 환경 확인 fixture.

    세션 시작 시 환경변수 상태를 출력합니다.
    CI에서는 이미 conftest 로딩 시점에 AssertionError가 발생하므로
    이 fixture에 도달하면 환경변수가 모두 설정된 상태입니다.
    """
    print("\n" + "=" * 60)
    print(f"Integration Test Environment Check (CI={IS_CI})")
    print("=" * 60)

    for var, desc in {**REQUIRED_ENV_VARS, **OPTIONAL_ENV_VARS}.items():
        value = os.environ.get(var, "")
        status = "OK" if value else "MISSING"
        # URL은 앞부분만 표시 (보안)
        display_value = value[:30] + "..." if len(value) > 30 else value
        print(f"  {var}: {status} ({display_value or 'N/A'})")

    print("=" * 60 + "\n")

    # 로컬에서만 skip (CI는 이미 위에서 fail)
    if SKIP_INTEGRATION:
        pytest.skip(SKIP_REASON)


@pytest.fixture(autouse=True)
def reset_singletons():
    """각 테스트 후 싱글톤 인스턴스를 정리합니다."""
    yield
    from app.clients.llm_client import clear_llm_client
    from app.services.pii_service import clear_pii_service

    clear_llm_client()
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

