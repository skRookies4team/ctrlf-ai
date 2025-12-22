"""
헬스체크 API 테스트 모듈

/health 및 /health/ready 엔드포인트의 동작을 검증합니다.
pytest와 httpx.AsyncClient를 사용합니다.
"""

import os
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import clear_settings_cache
from app.main import app


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio 백엔드 설정"""
    return "asyncio"


@pytest.fixture(autouse=True)
def disable_service_urls():
    """
    Readiness 테스트를 위해 서비스 URL을 비활성화합니다.

    실제 서비스에 연결하지 않도록 모든 URL을 빈 문자열로 설정합니다.
    직접 URL과 Mock URL 모두 비활성화합니다.
    """
    # 원본 값 저장
    originals = {
        "RAGFLOW_BASE_URL": os.environ.get("RAGFLOW_BASE_URL"),
        "RAGFLOW_BASE_URL_MOCK": os.environ.get("RAGFLOW_BASE_URL_MOCK"),
        "LLM_BASE_URL": os.environ.get("LLM_BASE_URL"),
        "LLM_BASE_URL_MOCK": os.environ.get("LLM_BASE_URL_MOCK"),
        "BACKEND_BASE_URL": os.environ.get("BACKEND_BASE_URL"),
        "BACKEND_BASE_URL_MOCK": os.environ.get("BACKEND_BASE_URL_MOCK"),
    }

    # 모든 URL 비활성화
    os.environ["RAGFLOW_BASE_URL"] = ""
    os.environ["RAGFLOW_BASE_URL_MOCK"] = ""
    os.environ["LLM_BASE_URL"] = ""
    os.environ["LLM_BASE_URL_MOCK"] = ""
    os.environ["BACKEND_BASE_URL"] = ""
    os.environ["BACKEND_BASE_URL_MOCK"] = ""
    clear_settings_cache()

    yield

    # 원본 값 복원
    for key, value in originals.items():
        if value is not None:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)
    clear_settings_cache()


@pytest.fixture
async def client() -> AsyncClient:
    """
    테스트용 비동기 HTTP 클라이언트를 생성합니다.

    FastAPI 앱에 직접 요청을 보낼 수 있는 AsyncClient를 반환합니다.
    실제 HTTP 서버를 띄우지 않고 테스트할 수 있습니다.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.anyio
async def test_health_check_returns_200(client: AsyncClient) -> None:
    """
    /health 엔드포인트가 200 OK를 반환하는지 확인합니다.
    """
    response = await client.get("/health")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_health_check_returns_status_ok(client: AsyncClient) -> None:
    """
    /health 엔드포인트가 status="ok"를 포함한 JSON을 반환하는지 확인합니다.
    """
    response = await client.get("/health")
    data = response.json()

    assert data["status"] == "ok"
    assert "app" in data
    assert "version" in data
    assert "env" in data


@pytest.mark.anyio
async def test_health_check_contains_app_info(client: AsyncClient) -> None:
    """
    /health 응답에 올바른 앱 정보가 포함되어 있는지 확인합니다.
    """
    response = await client.get("/health")
    data = response.json()

    assert data["app"] == "ctrlf-ai-gateway"
    assert data["version"] == "0.1.0"
    # env는 환경에 따라 다를 수 있으므로 존재 여부만 확인
    assert data["env"] in ["local", "dev", "prod"]


@pytest.mark.anyio
async def test_readiness_check_returns_200(client: AsyncClient) -> None:
    """
    /health/ready 엔드포인트가 200 OK를 반환하는지 확인합니다.
    """
    response = await client.get("/health/ready")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_readiness_check_returns_ready_true(client: AsyncClient) -> None:
    """
    /health/ready 엔드포인트가 ready=True를 반환하는지 확인합니다.
    """
    response = await client.get("/health/ready")
    data = response.json()

    assert data["ready"] is True
    assert "checks" in data
