"""
Chat API Test Module

Tests for the /ai/chat/messages endpoint.
Verifies that the chat API returns expected response structure.

Note: RAGFlow가 제거되어 RAG_INTERNAL 라우트 테스트는 삭제되었습니다.
MILVUS_ENABLED=True 환경에서만 RAG 검색이 가능합니다.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


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
async def test_chat_endpoint_validation_error(client: AsyncClient) -> None:
    """
    Test that /ai/chat/messages returns 422 for invalid payload.
    """
    # Missing required fields
    payload = {
        "session_id": "test-session",
        # Missing user_id, user_role, messages
    }

    response = await client.post("/ai/chat/messages", json=payload)
    assert response.status_code == 422


@pytest.mark.anyio
async def test_chat_endpoint_invalid_role(client: AsyncClient) -> None:
    """
    Test that /ai/chat/messages validates message role.
    """
    payload = {
        "session_id": "test-session",
        "user_id": "user-123",
        "user_role": "EMPLOYEE",
        "messages": [{"role": "invalid_role", "content": "Hello"}],
    }

    response = await client.post("/ai/chat/messages", json=payload)
    assert response.status_code == 422
