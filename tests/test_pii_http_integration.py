"""
PII HTTP Integration Test Module

Tests for PII service HTTP integration using mock transport.
Verifies that:
- PiiService correctly calls HTTP endpoint with stage parameter
- HTTP errors trigger fallback behavior
- ChatService integrates PII service when configured

These tests use httpx.MockTransport to simulate PII service responses
without requiring an actual external service.
"""

import json
from typing import Callable

import httpx
import pytest

from app.models.chat import ChatMessage, ChatRequest, ChatResponse
from app.models.intent import MaskingStage, PiiMaskResult
from app.clients.llm_client import LLMClient
from app.clients.ragflow_client import RagflowClient
from app.services.chat_service import ChatService
from app.services.intent_service import IntentService
from app.services.pii_service import PiiService


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


def create_mock_transport(
    handler: Callable[[httpx.Request], httpx.Response]
) -> httpx.MockTransport:
    """Create a mock transport with the given handler."""
    return httpx.MockTransport(handler)


def create_pii_success_response(
    original_text: str,
    masked_text: str,
    has_pii: bool,
    tags: list,
) -> dict:
    """Create a standard PII service success response."""
    return {
        "original_text": original_text,
        "masked_text": masked_text,
        "has_pii": has_pii,
        "tags": tags,
    }


# --- PiiService HTTP Integration Tests ---


@pytest.mark.anyio
async def test_pii_service_calls_http_mask_endpoint_with_input_stage() -> None:
    """
    Test that PiiService calls /mask endpoint with stage="input".
    """
    received_requests: list = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        # Capture request for verification
        body = json.loads(request.content)
        received_requests.append({
            "url": str(request.url),
            "body": body,
        })

        # Verify endpoint
        assert str(request.url).endswith("/mask")

        # Return mock response
        response_data = create_pii_success_response(
            original_text=body["text"],
            masked_text="***-****-****",
            has_pii=True,
            tags=[{"entity": "010-1234-5678", "label": "PHONE", "start": 0, "end": 13}],
        )
        return httpx.Response(200, json=response_data)

    # Create client with mock transport
    mock_client = httpx.AsyncClient(transport=create_mock_transport(mock_handler))

    # Create PiiService with mock client
    service = PiiService(
        base_url="http://pii-mock:8003",
        enabled=True,
        client=mock_client,
    )

    # Call detect_and_mask
    result = await service.detect_and_mask("010-1234-5678", MaskingStage.INPUT)

    # Verify request was made with correct stage
    assert len(received_requests) == 1
    assert received_requests[0]["body"]["stage"] == "input"
    assert received_requests[0]["body"]["text"] == "010-1234-5678"

    # Verify result
    assert result.masked_text == "***-****-****"
    assert result.has_pii is True
    assert len(result.tags) == 1
    assert result.tags[0].label == "PHONE"


@pytest.mark.anyio
async def test_pii_service_calls_http_mask_endpoint_with_output_stage() -> None:
    """
    Test that PiiService calls /mask endpoint with stage="output".
    """
    received_requests: list = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        received_requests.append(body)

        response_data = create_pii_success_response(
            original_text=body["text"],
            masked_text="[PERSON]님의 전화번호는 [PHONE]입니다.",
            has_pii=True,
            tags=[
                {"entity": "홍길동", "label": "PERSON", "start": 0, "end": 3},
                {"entity": "010-9876-5432", "label": "PHONE", "start": 10, "end": 23},
            ],
        )
        return httpx.Response(200, json=response_data)

    mock_client = httpx.AsyncClient(transport=create_mock_transport(mock_handler))
    service = PiiService(
        base_url="http://pii-mock:8003",
        enabled=True,
        client=mock_client,
    )

    result = await service.detect_and_mask(
        "홍길동님의 전화번호는 010-9876-5432입니다.",
        MaskingStage.OUTPUT,
    )

    # Verify stage parameter
    assert len(received_requests) == 1
    assert received_requests[0]["stage"] == "output"

    # Verify result
    assert result.has_pii is True
    assert len(result.tags) == 2


@pytest.mark.anyio
async def test_pii_service_calls_http_mask_endpoint_with_log_stage() -> None:
    """
    Test that PiiService calls /mask endpoint with stage="log".
    """
    received_requests: list = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        received_requests.append(body)

        response_data = create_pii_success_response(
            original_text=body["text"],
            masked_text="[REDACTED]",
            has_pii=True,
            tags=[{"entity": "sensitive-data", "label": "PII", "start": 0, "end": 14}],
        )
        return httpx.Response(200, json=response_data)

    mock_client = httpx.AsyncClient(transport=create_mock_transport(mock_handler))
    service = PiiService(
        base_url="http://pii-mock:8003",
        enabled=True,
        client=mock_client,
    )

    result = await service.detect_and_mask("sensitive-data", MaskingStage.LOG)

    # Verify stage parameter
    assert len(received_requests) == 1
    assert received_requests[0]["stage"] == "log"


@pytest.mark.anyio
async def test_pii_service_http_500_error_falls_back_to_original() -> None:
    """
    Test that PiiService returns original text on HTTP 500 error.
    """
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "Internal Server Error"})

    mock_client = httpx.AsyncClient(transport=create_mock_transport(mock_handler))
    service = PiiService(
        base_url="http://pii-mock:8003",
        enabled=True,
        client=mock_client,
    )

    original_text = "010-1234-5678"
    result = await service.detect_and_mask(original_text, MaskingStage.INPUT)

    # Verify fallback behavior
    assert result.original_text == original_text
    assert result.masked_text == original_text
    assert result.has_pii is False
    assert result.tags == []


@pytest.mark.anyio
async def test_pii_service_http_400_error_falls_back_to_original() -> None:
    """
    Test that PiiService returns original text on HTTP 400 error.
    """
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "Bad Request"})

    mock_client = httpx.AsyncClient(transport=create_mock_transport(mock_handler))
    service = PiiService(
        base_url="http://pii-mock:8003",
        enabled=True,
        client=mock_client,
    )

    original_text = "홍길동"
    result = await service.detect_and_mask(original_text, MaskingStage.INPUT)

    assert result.masked_text == original_text
    assert result.has_pii is False


@pytest.mark.anyio
async def test_pii_service_connection_error_falls_back_to_original() -> None:
    """
    Test that PiiService returns original text on connection error.
    """
    def mock_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    mock_client = httpx.AsyncClient(transport=create_mock_transport(mock_handler))
    service = PiiService(
        base_url="http://pii-mock:8003",
        enabled=True,
        client=mock_client,
    )

    original_text = "민감한 정보"
    result = await service.detect_and_mask(original_text, MaskingStage.INPUT)

    assert result.masked_text == original_text
    assert result.has_pii is False


@pytest.mark.anyio
async def test_pii_service_invalid_json_response_falls_back() -> None:
    """
    Test that PiiService returns original text on invalid JSON response.
    """
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not valid json")

    mock_client = httpx.AsyncClient(transport=create_mock_transport(mock_handler))
    service = PiiService(
        base_url="http://pii-mock:8003",
        enabled=True,
        client=mock_client,
    )

    original_text = "테스트 데이터"
    result = await service.detect_and_mask(original_text, MaskingStage.INPUT)

    assert result.masked_text == original_text
    assert result.has_pii is False


@pytest.mark.anyio
async def test_pii_service_no_pii_detected_response() -> None:
    """
    Test that PiiService correctly handles response with no PII detected.
    """
    def mock_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        response_data = create_pii_success_response(
            original_text=body["text"],
            masked_text=body["text"],
            has_pii=False,
            tags=[],
        )
        return httpx.Response(200, json=response_data)

    mock_client = httpx.AsyncClient(transport=create_mock_transport(mock_handler))
    service = PiiService(
        base_url="http://pii-mock:8003",
        enabled=True,
        client=mock_client,
    )

    result = await service.detect_and_mask("일반적인 텍스트입니다.", MaskingStage.INPUT)

    assert result.has_pii is False
    assert result.masked_text == "일반적인 텍스트입니다."
    assert result.tags == []


@pytest.mark.anyio
async def test_pii_service_empty_string_skips_http_call() -> None:
    """
    Test that PiiService does not call HTTP for empty strings.
    """
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={})

    mock_client = httpx.AsyncClient(transport=create_mock_transport(mock_handler))
    service = PiiService(
        base_url="http://pii-mock:8003",
        enabled=True,
        client=mock_client,
    )

    result = await service.detect_and_mask("", MaskingStage.INPUT)

    # Verify no HTTP call was made
    assert call_count == 0
    assert result.masked_text == ""
    assert result.has_pii is False


@pytest.mark.anyio
async def test_pii_service_whitespace_only_skips_http_call() -> None:
    """
    Test that PiiService does not call HTTP for whitespace-only strings.
    """
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={})

    mock_client = httpx.AsyncClient(transport=create_mock_transport(mock_handler))
    service = PiiService(
        base_url="http://pii-mock:8003",
        enabled=True,
        client=mock_client,
    )

    result = await service.detect_and_mask("   \t\n  ", MaskingStage.INPUT)

    assert call_count == 0
    assert result.masked_text == "   \t\n  "
    assert result.has_pii is False


# --- ChatService Integration with PII HTTP Service ---


@pytest.mark.anyio
async def test_chat_service_uses_pii_http_service_when_configured() -> None:
    """
    Test that ChatService correctly uses PII HTTP service when configured.
    Verifies that meta.masked is True when PII is detected.
    """
    pii_calls: list = []

    def mock_pii_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        pii_calls.append(body)

        # Simulate PII detection in input
        if body["stage"] == "input":
            return httpx.Response(200, json={
                "original_text": body["text"],
                "masked_text": "[PHONE] 번호로 연락주세요",
                "has_pii": True,
                "tags": [{"entity": "010-1234-5678", "label": "PHONE", "start": 0, "end": 13}],
            })
        else:
            # No PII in output
            return httpx.Response(200, json={
                "original_text": body["text"],
                "masked_text": body["text"],
                "has_pii": False,
                "tags": [],
            })

    pii_mock_client = httpx.AsyncClient(transport=create_mock_transport(mock_pii_handler))
    pii_service = PiiService(
        base_url="http://pii-mock:8003",
        enabled=True,
        client=pii_mock_client,
    )

    # Create other services (fallback mode)
    ragflow_client = RagflowClient(base_url="")
    llm_client = LLMClient(base_url="")
    intent_service = IntentService()

    chat_service = ChatService(
        ragflow_client=ragflow_client,
        llm_client=llm_client,
        pii_service=pii_service,
        intent_service=intent_service,
    )

    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="010-1234-5678 번호로 연락주세요")],
    )

    response = await chat_service.handle_chat(request)

    # Verify PII service was called for INPUT, OUTPUT, and LOG stages
    # INPUT (1) + OUTPUT (1) + LOG for question and answer (2) = 4 calls
    assert len(pii_calls) == 4
    stages = [call["stage"] for call in pii_calls]
    assert "input" in stages
    assert "output" in stages
    assert stages.count("log") == 2  # LOG stage called twice (question + answer)

    # Verify response
    assert isinstance(response, ChatResponse)
    assert response.meta.masked is True  # PII was detected in input


@pytest.mark.anyio
async def test_chat_service_pii_masked_false_when_no_pii() -> None:
    """
    Test that ChatService sets meta.masked=False when no PII is detected.
    """
    def mock_pii_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json={
            "original_text": body["text"],
            "masked_text": body["text"],
            "has_pii": False,
            "tags": [],
        })

    pii_mock_client = httpx.AsyncClient(transport=create_mock_transport(mock_pii_handler))
    pii_service = PiiService(
        base_url="http://pii-mock:8003",
        enabled=True,
        client=pii_mock_client,
    )

    chat_service = ChatService(
        ragflow_client=RagflowClient(base_url=""),
        llm_client=LLMClient(base_url=""),
        pii_service=pii_service,
        intent_service=IntentService(),
    )

    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="회사 정책에 대해 알려주세요")],
    )

    response = await chat_service.handle_chat(request)

    assert response.meta.masked is False


@pytest.mark.anyio
async def test_chat_service_continues_when_pii_service_fails() -> None:
    """
    Test that ChatService continues processing when PII service fails.
    """
    def mock_pii_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "PII service down"})

    pii_mock_client = httpx.AsyncClient(transport=create_mock_transport(mock_pii_handler))
    pii_service = PiiService(
        base_url="http://pii-mock:8003",
        enabled=True,
        client=pii_mock_client,
    )

    chat_service = ChatService(
        ragflow_client=RagflowClient(base_url=""),
        llm_client=LLMClient(base_url=""),
        pii_service=pii_service,
        intent_service=IntentService(),
    )

    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="테스트 질문")],
    )

    # Should not raise exception
    response = await chat_service.handle_chat(request)

    assert isinstance(response, ChatResponse)
    assert response.answer is not None
    # masked should be False due to fallback
    assert response.meta.masked is False


@pytest.mark.anyio
async def test_pii_service_multiple_entities_detected() -> None:
    """
    Test that PiiService correctly handles multiple PII entities.
    """
    def mock_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json={
            "original_text": body["text"],
            "masked_text": "[PERSON]의 주민번호는 [RRN]이고 전화번호는 [PHONE]입니다.",
            "has_pii": True,
            "tags": [
                {"entity": "홍길동", "label": "PERSON", "start": 0, "end": 3},
                {"entity": "901010-1234567", "label": "RRN", "start": 9, "end": 23},
                {"entity": "010-1234-5678", "label": "PHONE", "start": 30, "end": 43},
            ],
        })

    mock_client = httpx.AsyncClient(transport=create_mock_transport(mock_handler))
    service = PiiService(
        base_url="http://pii-mock:8003",
        enabled=True,
        client=mock_client,
    )

    result = await service.detect_and_mask(
        "홍길동의 주민번호는 901010-1234567이고 전화번호는 010-1234-5678입니다.",
        MaskingStage.INPUT,
    )

    assert result.has_pii is True
    assert len(result.tags) == 3
    labels = [tag.label for tag in result.tags]
    assert "PERSON" in labels
    assert "RRN" in labels
    assert "PHONE" in labels
