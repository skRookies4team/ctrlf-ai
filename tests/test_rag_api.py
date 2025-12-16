"""
RAG API Test Module

Tests for the /ai/rag/process endpoint.
Verifies that the RAG API returns expected response structure.

Phase 21+: Mock 모드 테스트 추가
- AI_ENV=mock 설정 시 RAGFlow 없이 Mock 응답 반환
"""

import os
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.core.config import clear_settings_cache


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


@pytest.fixture
async def client() -> AsyncClient:
    """
    Create async HTTP client for testing.

    Returns an AsyncClient that can send requests directly to the FastAPI app
    without starting an actual HTTP server.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.anyio
async def test_rag_process_returns_200(client: AsyncClient) -> None:
    """
    Test that /ai/rag/process returns 200 OK.
    """
    payload = {
        "doc_id": "HR-001",
        "file_url": "https://example.com/docs/hr-001.pdf",
        "domain": "POLICY",
        "acl": {
            "roles": ["EMPLOYEE", "MANAGER"],
            "departments": ["ALL"],
        },
    }

    response = await client.post("/ai/rag/process", json=payload)
    assert response.status_code == 200


@pytest.mark.anyio
async def test_rag_process_returns_success(client: AsyncClient) -> None:
    """
    Test that /ai/rag/process returns success=True for dummy response.
    """
    payload = {
        "doc_id": "HR-001",
        "file_url": "https://example.com/docs/hr-001.pdf",
        "domain": "POLICY",
        "acl": {
            "roles": ["EMPLOYEE", "MANAGER"],
            "departments": ["ALL"],
        },
    }

    response = await client.post("/ai/rag/process", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert data["doc_id"] == "HR-001"
    assert data["success"] is True


@pytest.mark.anyio
async def test_rag_process_response_structure(client: AsyncClient) -> None:
    """
    Test that /ai/rag/process returns correct response structure.
    """
    payload = {
        "doc_id": "DOC-123",
        "file_url": "https://example.com/docs/doc-123.pdf",
        "domain": "INCIDENT",
    }

    response = await client.post("/ai/rag/process", json=payload)
    data = response.json()

    # Check required fields
    assert "doc_id" in data
    assert "success" in data
    assert "message" in data

    # Check types
    assert isinstance(data["doc_id"], str)
    assert isinstance(data["success"], bool)
    assert data["message"] is None or isinstance(data["message"], str)


@pytest.mark.anyio
async def test_rag_process_without_acl(client: AsyncClient) -> None:
    """
    Test that /ai/rag/process works without ACL (optional field).
    """
    payload = {
        "doc_id": "HR-002",
        "file_url": "https://example.com/docs/hr-002.pdf",
        "domain": "EDUCATION",
    }

    response = await client.post("/ai/rag/process", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert data["doc_id"] == "HR-002"
    assert data["success"] is True


@pytest.mark.anyio
async def test_rag_process_with_empty_acl(client: AsyncClient) -> None:
    """
    Test that /ai/rag/process works with empty ACL lists.
    """
    payload = {
        "doc_id": "HR-003",
        "file_url": "https://example.com/docs/hr-003.pdf",
        "domain": "POLICY",
        "acl": {
            "roles": [],
            "departments": [],
        },
    }

    response = await client.post("/ai/rag/process", json=payload)
    assert response.status_code == 200


@pytest.mark.anyio
async def test_rag_process_validation_error_missing_fields(client: AsyncClient) -> None:
    """
    Test that /ai/rag/process returns 422 for missing required fields.
    """
    # Missing required fields
    payload = {
        "doc_id": "HR-001",
        # Missing file_url and domain
    }

    response = await client.post("/ai/rag/process", json=payload)
    assert response.status_code == 422


@pytest.mark.anyio
async def test_rag_process_validation_error_invalid_url(client: AsyncClient) -> None:
    """
    Test that /ai/rag/process returns 422 for invalid URL format.
    """
    payload = {
        "doc_id": "HR-001",
        "file_url": "not-a-valid-url",
        "domain": "POLICY",
    }

    response = await client.post("/ai/rag/process", json=payload)
    assert response.status_code == 422


@pytest.mark.anyio
async def test_rag_process_preserves_doc_id(client: AsyncClient) -> None:
    """
    Test that /ai/rag/process returns the same doc_id as request.
    """
    doc_id = "CUSTOM-DOC-ID-12345"
    payload = {
        "doc_id": doc_id,
        "file_url": "https://example.com/docs/custom.pdf",
        "domain": "POLICY",
    }

    response = await client.post("/ai/rag/process", json=payload)
    data = response.json()

    assert data["doc_id"] == doc_id


# =============================================================================
# Mock 모드 테스트 (Phase 21+)
# =============================================================================


@pytest.fixture
def mock_mode():
    """Mock 모드 활성화 fixture."""
    original = os.environ.get("AI_ENV")
    os.environ["AI_ENV"] = "mock"
    clear_settings_cache()
    yield
    if original is not None:
        os.environ["AI_ENV"] = original
    else:
        os.environ.pop("AI_ENV", None)
    clear_settings_cache()


@pytest.mark.anyio
async def test_rag_process_mock_mode_returns_mock_response(mock_mode) -> None:
    """
    Test that mock mode returns [MOCK] prefixed response.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "doc_id": "MOCK-DOC-001",
            "file_url": "https://example.com/docs/mock.pdf",
            "domain": "POLICY",
        }

        response = await client.post("/ai/rag/process", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert data["doc_id"] == "MOCK-DOC-001"
        assert data["success"] is True
        assert "[MOCK]" in data["message"]


@pytest.mark.anyio
async def test_rag_process_mock_mode_includes_domain(mock_mode) -> None:
    """
    Test that mock mode response includes domain information.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "doc_id": "MOCK-DOC-002",
            "file_url": "https://example.com/docs/mock.pdf",
            "domain": "EDUCATION",
        }

        response = await client.post("/ai/rag/process", json=payload)
        data = response.json()

        assert "EDUCATION" in data["message"]


@pytest.mark.anyio
async def test_rag_process_real_mode_no_ragflow_returns_dummy() -> None:
    """
    Test that real mode without RAGFlow returns dummy response (not mock).

    unittest.mock을 사용하여 settings를 직접 패치합니다.
    """
    from unittest.mock import patch, MagicMock
    from app.services.rag_service import RagService
    from app.models.rag import RagProcessRequest

    # Mock settings 생성
    mock_settings = MagicMock()
    mock_settings.is_mock_mode = False  # Real mode
    mock_settings.ragflow_base_url = None  # RAGFlow 미설정

    with patch("app.services.rag_service.get_settings", return_value=mock_settings):
        service = RagService()

        request = RagProcessRequest(
            doc_id="REAL-DOC-001",
            file_url="https://example.com/docs/real.pdf",
            domain="POLICY",
        )

        result = await service.process_document(request)

        assert result.doc_id == "REAL-DOC-001"
        assert result.success is True
        # Real mode without RAGFlow should NOT have [MOCK] prefix
        assert "[MOCK]" not in result.message
        assert "not configured" in result.message.lower()
