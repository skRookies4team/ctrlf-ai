"""
Chat API Test Module

Tests for the /ai/chat/messages endpoint.
Verifies that the chat API returns expected response structure.
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
async def test_chat_endpoint_returns_200(client: AsyncClient) -> None:
    """
    Test that /ai/chat/messages returns 200 OK.
    """
    payload = {
        "session_id": "test-session",
        "user_id": "user-123",
        "user_role": "EMPLOYEE",
        "department": "HR",
        "domain": "POLICY",
        "channel": "WEB",
        "messages": [{"role": "user", "content": "What is the annual leave policy?"}],
    }

    response = await client.post("/ai/chat/messages", json=payload)
    assert response.status_code == 200


@pytest.mark.anyio
async def test_chat_endpoint_returns_dummy_answer(client: AsyncClient) -> None:
    """
    Test that /ai/chat/messages returns a dummy answer with correct structure.
    """
    payload = {
        "session_id": "test-session",
        "user_id": "user-123",
        "user_role": "EMPLOYEE",
        "department": "HR",
        "domain": "POLICY",
        "channel": "WEB",
        "messages": [{"role": "user", "content": "What is the annual leave policy?"}],
    }

    response = await client.post("/ai/chat/messages", json=payload)
    assert response.status_code == 200

    data = response.json()

    # Check required fields exist
    assert "answer" in data
    assert isinstance(data["answer"], str)
    assert len(data["answer"]) > 0

    assert "sources" in data
    assert isinstance(data["sources"], list)

    assert "meta" in data
    assert isinstance(data["meta"], dict)


@pytest.mark.anyio
async def test_chat_endpoint_meta_structure(client: AsyncClient) -> None:
    """
    Test that /ai/chat/messages returns correct meta structure.
    """
    payload = {
        "session_id": "test-session",
        "user_id": "user-123",
        "user_role": "EMPLOYEE",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    response = await client.post("/ai/chat/messages", json=payload)
    data = response.json()

    meta = data["meta"]
    # Meta fields should exist (can be null for dummy response)
    assert "used_model" in meta
    assert "route" in meta
    assert "masked" in meta
    assert "latency_ms" in meta


@pytest.mark.anyio
async def test_chat_endpoint_with_minimal_payload(client: AsyncClient) -> None:
    """
    Test that /ai/chat/messages works with minimal required fields.
    """
    payload = {
        "session_id": "test-session",
        "user_id": "user-123",
        "user_role": "EMPLOYEE",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    response = await client.post("/ai/chat/messages", json=payload)
    assert response.status_code == 200


@pytest.mark.anyio
async def test_chat_endpoint_with_conversation_history(client: AsyncClient) -> None:
    """
    Test that /ai/chat/messages handles conversation history.
    """
    payload = {
        "session_id": "test-session",
        "user_id": "user-123",
        "user_role": "EMPLOYEE",
        "messages": [
            {"role": "user", "content": "What is the leave policy?"},
            {"role": "assistant", "content": "The leave policy includes..."},
            {"role": "user", "content": "How many days can I carry over?"},
        ],
    }

    response = await client.post("/ai/chat/messages", json=payload)
    assert response.status_code == 200


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
