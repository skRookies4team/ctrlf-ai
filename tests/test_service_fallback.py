"""
Service Fallback Test Module

Tests for service layer fallback behavior when external services
(RAGFlow, LLM) are not configured or unavailable.

These tests verify that:
- ChatService returns valid responses even without RAGFlow/LLM
- RagService returns appropriate failure responses when RAGFlow is unavailable
"""

import pytest

from app.models.chat import ChatMessage, ChatRequest, ChatResponse
from app.models.rag import RagProcessRequest, RagProcessResponse
from app.services.chat_service import ChatService
from app.services.llm_client import LLMClient
from app.services.rag_service import RagService
from app.services.ragflow_client import RagflowClient


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


# --- ChatService Fallback Tests ---


@pytest.mark.anyio
async def test_chat_service_returns_response_without_config() -> None:
    """
    Test that ChatService returns a valid ChatResponse even when
    RAGFlow and LLM are not configured (base_url is empty).
    """
    # Create clients with empty base_url (simulating unconfigured state)
    ragflow_client = RagflowClient(base_url="")
    llm_client = LLMClient(base_url="")

    service = ChatService(
        ragflow_client=ragflow_client,
        llm_client=llm_client,
    )

    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        department="HR",
        domain="POLICY",
        messages=[
            ChatMessage(role="user", content="What is the annual leave policy?")
        ],
    )

    response = await service.handle_chat(request)

    # Verify response is valid ChatResponse
    assert isinstance(response, ChatResponse)
    assert isinstance(response.answer, str)
    assert len(response.answer) > 0
    assert isinstance(response.sources, list)
    assert response.meta is not None


@pytest.mark.anyio
async def test_chat_service_returns_fallback_message() -> None:
    """
    Test that ChatService returns LLM fallback message when LLM is not configured.
    """
    ragflow_client = RagflowClient(base_url="")
    llm_client = LLMClient(base_url="")

    service = ChatService(
        ragflow_client=ragflow_client,
        llm_client=llm_client,
    )

    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="Hello")],
    )

    response = await service.handle_chat(request)

    # Should return LLM fallback message
    assert "LLM service" in response.answer or "not configured" in response.answer


@pytest.mark.anyio
async def test_chat_service_empty_sources_without_ragflow() -> None:
    """
    Test that ChatService returns empty sources when RAGFlow is not configured.
    """
    ragflow_client = RagflowClient(base_url="")
    llm_client = LLMClient(base_url="")

    service = ChatService(
        ragflow_client=ragflow_client,
        llm_client=llm_client,
    )

    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="Search test")],
    )

    response = await service.handle_chat(request)

    # Sources should be empty since RAGFlow is not configured
    assert response.sources == []


@pytest.mark.anyio
async def test_chat_service_meta_has_latency() -> None:
    """
    Test that ChatService response meta includes latency_ms.
    """
    ragflow_client = RagflowClient(base_url="")
    llm_client = LLMClient(base_url="")

    service = ChatService(
        ragflow_client=ragflow_client,
        llm_client=llm_client,
    )

    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="Test")],
    )

    response = await service.handle_chat(request)

    # Meta should have latency_ms set
    assert response.meta.latency_ms is not None
    assert response.meta.latency_ms >= 0


# --- RagService Fallback Tests ---


@pytest.mark.anyio
async def test_rag_service_returns_dummy_success_without_config() -> None:
    """
    Test that RagService returns dummy success when RAGFlow is not configured.

    Note: RagService returns success=True with dummy message when RAGFLOW_BASE_URL
    is not set, for backward compatibility with existing tests.
    """
    # RagService checks settings.RAGFLOW_BASE_URL internally
    service = RagService()

    request = RagProcessRequest(
        doc_id="TEST-001",
        file_url="https://example.com/test.pdf",
        domain="POLICY",
    )

    response = await service.process_document(request)

    # Should return dummy success when RAGFlow is not configured
    assert isinstance(response, RagProcessResponse)
    assert response.doc_id == "TEST-001"
    assert response.success is True  # Dummy success for compatibility
    assert response.message is not None
    assert "dummy" in response.message.lower() or "not configured" in response.message.lower()


@pytest.mark.anyio
async def test_rag_service_preserves_doc_id() -> None:
    """
    Test that RagService preserves doc_id in response.
    """
    service = RagService()

    doc_id = "CUSTOM-DOC-12345"
    request = RagProcessRequest(
        doc_id=doc_id,
        file_url="https://example.com/custom.pdf",
        domain="INCIDENT",
    )

    response = await service.process_document(request)

    # doc_id should be preserved
    assert response.doc_id == doc_id


# --- RagflowClient Direct Tests ---


@pytest.mark.anyio
async def test_ragflow_client_search_returns_empty_without_config() -> None:
    """
    Test that RagflowClient.search returns empty list when not configured.
    """
    client = RagflowClient(base_url="")

    result = await client.search(
        query="test query",
        domain="POLICY",
        user_role="EMPLOYEE",
        department="HR",
        top_k=5,
    )

    assert result == []


@pytest.mark.anyio
async def test_ragflow_client_process_returns_failure_without_config() -> None:
    """
    Test that RagflowClient.process_document returns failure when not configured.
    """
    client = RagflowClient(base_url="")

    request = RagProcessRequest(
        doc_id="TEST-002",
        file_url="https://example.com/test2.pdf",
        domain="EDUCATION",
    )

    result = await client.process_document(request)

    assert result.success is False
    assert result.doc_id == "TEST-002"


# --- LLMClient Direct Tests ---


@pytest.mark.anyio
async def test_llm_client_returns_fallback_without_config() -> None:
    """
    Test that LLMClient returns fallback message when not configured.
    """
    client = LLMClient(base_url="")

    result = await client.generate_chat_completion(
        messages=[{"role": "user", "content": "Hello"}]
    )

    assert isinstance(result, str)
    assert len(result) > 0
    # Should contain fallback message indicator
    assert "LLM service" in result or "not configured" in result or "fallback" in result.lower()
