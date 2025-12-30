"""
PII 입력 단계 Fail-Closed 테스트 - A7

PII detector가 입력 단계에서 실패할 때 Fail-Closed 동작을 검증합니다.

테스트 목록:
- TEST-1: PII detector 예외 시 안전한 fallback 메시지 반환
- TEST-2: SECURITY 이벤트 발행 (rule_id=PII_DETECTOR_UNAVAILABLE_INPUT)
- TEST-3: CHAT_TURN 이벤트 발행 (errorCode=PII_DETECTOR_UNAVAILABLE)
- TEST-4: 텔레메트리 이벤트에 질문 원문 미포함 검증
"""

import json
from typing import List
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.models.chat import ChatRequest, ChatMessage
from app.models.intent import MaskingStage
from app.services.chat_service import ChatService, PII_DETECTOR_UNAVAILABLE_MESSAGE
from app.services.pii_service import PiiDetectorUnavailableError
from app.telemetry.context import RequestContext, set_request_context, reset_request_context
from app.telemetry.emitters import (
    reset_chat_turn_emitted,
    reset_security_emitted,
    reset_feedback_emitted,
)
from app.telemetry.metrics import reset_all_metrics
from app.telemetry.models import TelemetryEvent
from app.telemetry.publisher import set_telemetry_publisher


# =============================================================================
# Stub Publisher
# =============================================================================


class StubPublisher:
    """테스트용 Publisher stub."""

    def __init__(self):
        self.events: List[TelemetryEvent] = []

    def enqueue(self, event: TelemetryEvent) -> bool:
        """이벤트를 리스트에 추가하고 True 반환."""
        self.events.append(event)
        return True

    def clear(self):
        """이벤트 리스트 초기화."""
        self.events.clear()

    def get_events_by_type(self, event_type: str) -> List[TelemetryEvent]:
        """특정 타입의 이벤트만 반환."""
        return [e for e in self.events if e.event_type == event_type]


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def stub_publisher():
    """StubPublisher 인스턴스 생성."""
    pub = StubPublisher()
    set_telemetry_publisher(pub)
    return pub


@pytest.fixture
def request_context():
    """테스트용 RequestContext 설정."""
    ctx = RequestContext(
        trace_id=f"trace-{uuid4().hex[:8]}",
        user_id="U-TEST-001",
        dept_id="D-TEST",
        conversation_id="C-TEST-001",
        turn_id=1,
    )
    set_request_context(ctx)
    reset_all_metrics()
    reset_chat_turn_emitted()
    reset_security_emitted()
    reset_feedback_emitted()

    yield ctx

    reset_request_context()
    reset_all_metrics()
    reset_chat_turn_emitted()
    reset_security_emitted()
    reset_feedback_emitted()


@pytest.fixture
def chat_request():
    """테스트용 ChatRequest 생성."""
    return ChatRequest(
        session_id="session-test-001",
        user_id="U-TEST-001",
        user_role="EMPLOYEE",
        messages=[
            ChatMessage(role="user", content="민감한 테스트 질문입니다 010-1234-5678")
        ],
    )


# =============================================================================
# TEST-1: PII detector 예외 시 안전한 fallback 메시지 반환
# =============================================================================


@pytest.mark.asyncio
async def test_pii_input_failure_returns_fallback_message(
    stub_publisher, request_context, chat_request
):
    """PII detector 실패 시 안전한 fallback 메시지를 반환한다."""
    # Given: PII detector가 예외를 발생시키도록 설정
    with patch(
        "app.services.chat_service.PiiService.detect_and_mask",
        new_callable=AsyncMock,
    ) as mock_pii:
        mock_pii.side_effect = PiiDetectorUnavailableError(
            stage=MaskingStage.INPUT,
            reason="timeout",
        )

        # ChatService 생성 (PII 서비스는 mock됨)
        service = ChatService()

        # When: handle_chat 호출
        response = await service.handle_chat(chat_request)

    # Then: 안전한 fallback 메시지 반환
    assert response.answer == PII_DETECTOR_UNAVAILABLE_MESSAGE
    assert response.sources == []


# =============================================================================
# TEST-2: SECURITY 이벤트 발행 검증
# =============================================================================


@pytest.mark.asyncio
async def test_pii_input_failure_emits_security_event(
    stub_publisher, request_context, chat_request
):
    """PII detector 실패 시 SECURITY 이벤트가 발행된다."""
    # Given
    stub_publisher.clear()

    with patch(
        "app.services.chat_service.PiiService.detect_and_mask",
        new_callable=AsyncMock,
    ) as mock_pii:
        mock_pii.side_effect = PiiDetectorUnavailableError(
            stage=MaskingStage.INPUT,
            reason="network error",
        )

        service = ChatService()

        # When
        await service.handle_chat(chat_request)

    # Then: SECURITY 이벤트 1개 발행
    security_events = stub_publisher.get_events_by_type("SECURITY")
    assert len(security_events) == 1

    event = security_events[0]
    assert event.event_type == "SECURITY"
    assert event.payload.block_type == "PII_BLOCK"
    assert event.payload.blocked is True
    assert event.payload.rule_id == "PII_DETECTOR_UNAVAILABLE_INPUT"


# =============================================================================
# TEST-3: CHAT_TURN 이벤트 발행 검증
# =============================================================================


@pytest.mark.asyncio
async def test_pii_input_failure_emits_chat_turn_event(
    stub_publisher, request_context, chat_request
):
    """PII detector 실패 시 CHAT_TURN 이벤트가 발행된다."""
    # Given
    stub_publisher.clear()

    with patch(
        "app.services.chat_service.PiiService.detect_and_mask",
        new_callable=AsyncMock,
    ) as mock_pii:
        mock_pii.side_effect = PiiDetectorUnavailableError(
            stage=MaskingStage.INPUT,
            reason="HTTP 500",
        )

        service = ChatService()

        # When
        await service.handle_chat(chat_request)

    # Then: CHAT_TURN 이벤트 1개 발행
    chat_events = stub_publisher.get_events_by_type("CHAT_TURN")
    assert len(chat_events) == 1

    event = chat_events[0]
    assert event.event_type == "CHAT_TURN"
    assert event.payload.error_code == "PII_DETECTOR_UNAVAILABLE"
    # 입력 단계 실패이므로 intent/domain은 결정되지 않음
    assert event.payload.intent_main == "UNKNOWN"
    assert event.payload.domain == "UNKNOWN"


# =============================================================================
# TEST-4: 텔레메트리 이벤트에 질문 원문 미포함 검증
# =============================================================================


@pytest.mark.asyncio
async def test_pii_input_failure_no_raw_text_in_events(
    stub_publisher, request_context, chat_request
):
    """PII detector 실패 시 텔레메트리 이벤트에 질문 원문이 포함되지 않는다."""
    # Given
    stub_publisher.clear()
    test_question = "민감한 테스트 질문입니다 010-1234-5678"

    with patch(
        "app.services.chat_service.PiiService.detect_and_mask",
        new_callable=AsyncMock,
    ) as mock_pii:
        mock_pii.side_effect = PiiDetectorUnavailableError(
            stage=MaskingStage.INPUT,
            reason="timeout",
        )

        service = ChatService()

        # When
        await service.handle_chat(chat_request)

    # Then: 모든 이벤트에 질문 원문이 포함되지 않음
    for event in stub_publisher.events:
        event_json = json.dumps(
            event.model_dump(by_alias=True, mode="json"),
            ensure_ascii=False,
            default=str,
        )

        # 질문 원문의 일부가 포함되어 있으면 안 됨
        assert test_question not in event_json
        assert "010-1234-5678" not in event_json
        assert "민감한" not in event_json


# =============================================================================
# TEST-5: 다양한 예외 유형 처리
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    [
        "timeout",
        "HTTP 500",
        "network error: ConnectionError",
        "JSON parsing error",
        "unexpected error: RuntimeError",
    ],
)
async def test_pii_input_failure_handles_various_errors(
    stub_publisher, request_context, chat_request, reason
):
    """다양한 PII detector 실패 원인에 대해 일관된 Fail-Closed 동작."""
    # Given
    stub_publisher.clear()

    with patch(
        "app.services.chat_service.PiiService.detect_and_mask",
        new_callable=AsyncMock,
    ) as mock_pii:
        mock_pii.side_effect = PiiDetectorUnavailableError(
            stage=MaskingStage.INPUT,
            reason=reason,
        )

        service = ChatService()

        # When
        response = await service.handle_chat(chat_request)

    # Then: 항상 안전한 fallback 반환
    assert response.answer == PII_DETECTOR_UNAVAILABLE_MESSAGE

    # Then: SECURITY 이벤트 발행
    security_events = stub_publisher.get_events_by_type("SECURITY")
    assert len(security_events) == 1
    assert security_events[0].payload.rule_id == "PII_DETECTOR_UNAVAILABLE_INPUT"

    # Then: CHAT_TURN 이벤트 발행
    chat_events = stub_publisher.get_events_by_type("CHAT_TURN")
    assert len(chat_events) == 1
    assert chat_events[0].payload.error_code == "PII_DETECTOR_UNAVAILABLE"


# =============================================================================
# TEST-6: 샘플 이벤트 덤프 출력
# =============================================================================


@pytest.mark.asyncio
async def test_sample_pii_input_fail_closed_event_dump(
    stub_publisher, request_context, chat_request, capsys
):
    """PII 입력 Fail-Closed 시 발생하는 이벤트 샘플을 출력한다."""
    # Given
    stub_publisher.clear()

    with patch(
        "app.services.chat_service.PiiService.detect_and_mask",
        new_callable=AsyncMock,
    ) as mock_pii:
        mock_pii.side_effect = PiiDetectorUnavailableError(
            stage=MaskingStage.INPUT,
            reason="timeout",
        )

        service = ChatService()

        # When
        await service.handle_chat(chat_request)

    # Then: 이벤트 샘플 출력
    print("\n=== PII Input Fail-Closed: SECURITY Event ===")
    security_events = stub_publisher.get_events_by_type("SECURITY")
    if security_events:
        event_dict = security_events[0].model_dump(by_alias=True, mode="json")
        print(json.dumps(event_dict, indent=2, ensure_ascii=False, default=str))

    print("\n=== PII Input Fail-Closed: CHAT_TURN Event ===")
    chat_events = stub_publisher.get_events_by_type("CHAT_TURN")
    if chat_events:
        event_dict = chat_events[0].model_dump(by_alias=True, mode="json")
        print(json.dumps(event_dict, indent=2, ensure_ascii=False, default=str))

    # 캡처된 출력 확인
    captured = capsys.readouterr()
    assert "PII_DETECTOR_UNAVAILABLE_INPUT" in captured.out
    assert "PII_DETECTOR_UNAVAILABLE" in captured.out
