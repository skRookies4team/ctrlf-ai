"""
PII 출력 단계 Fail-Closed 테스트 - A7

PII detector가 출력 단계에서 실패할 때 Fail-Closed 동작을 검증합니다.

테스트 목록:
- TEST-1: 출력 PII detector 실패 시 원 답변이 아닌 fallback 반환
- TEST-2: SECURITY 이벤트 발행 (rule_id=PII_DETECTOR_UNAVAILABLE_OUTPUT)
- TEST-3: CHAT_TURN 이벤트 발행 (errorCode=PII_DETECTOR_UNAVAILABLE)
- TEST-4: 텔레메트리 이벤트에 LLM 응답 원문 미포함 검증
"""

import json
from typing import List
from unittest.mock import AsyncMock, patch, MagicMock, Mock
from uuid import uuid4

import pytest

from app.models.chat import ChatRequest, ChatMessage
from app.models.intent import MaskingStage, PiiMaskResult, IntentType, UserRole, RouteType
from app.services.intent_service import IntentResult
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
            ChatMessage(role="user", content="안녕하세요, 일반적인 질문입니다.")
        ],
    )


# =============================================================================
# Helper: Mock PII Service (입력은 성공, 출력은 실패)
# =============================================================================


def create_pii_mock_input_success_output_fail():
    """입력 PII는 성공, 출력 PII는 실패하는 mock을 생성."""
    call_count = 0

    async def mock_detect_and_mask(text, stage):
        nonlocal call_count
        call_count += 1

        if stage == MaskingStage.INPUT:
            # 입력 단계: 성공 (PII 없음)
            return PiiMaskResult(
                original_text=text,
                masked_text=text,
                has_pii=False,
                tags=[],
            )
        elif stage == MaskingStage.OUTPUT:
            # 출력 단계: 실패
            raise PiiDetectorUnavailableError(
                stage=stage,
                reason="timeout",
            )
        else:
            # LOG 등 다른 단계: 성공
            return PiiMaskResult(
                original_text=text,
                masked_text=text,
                has_pii=False,
                tags=[],
            )

    return mock_detect_and_mask


# =============================================================================
# TEST-1: 출력 PII detector 실패 시 원 답변이 아닌 fallback 반환
# =============================================================================


@pytest.mark.asyncio
async def test_pii_output_failure_returns_fallback_not_raw_answer(
    stub_publisher, request_context, chat_request
):
    """출력 PII detector 실패 시 원 답변이 아닌 fallback 메시지를 반환한다."""
    # Given: LLM 응답을 민감한 정보로 설정
    llm_raw_answer = "귀하의 계좌번호 123-456-789와 연락처 010-9999-8888을 확인했습니다."

    with patch(
        "app.services.chat_service.PiiService.detect_and_mask",
        new_callable=AsyncMock,
        side_effect=create_pii_mock_input_success_output_fail(),
    ), patch(
        "app.services.chat_service.LLMClient.generate_chat_completion",
        new_callable=AsyncMock,
        return_value=llm_raw_answer,
    ), patch(
        "app.services.chat_service.IntentService.classify",
        return_value=IntentResult(
            intent=IntentType.GENERAL_CHAT,
            user_role=UserRole.EMPLOYEE,
            domain="GENERAL",
            route=RouteType.LLM_ONLY,
        ),
    ), patch(
        "app.services.chat_service.ChatService._perform_rag_search_with_fallback",
        new_callable=AsyncMock,
        return_value=([], False, "RAGFLOW"),
    ):
        service = ChatService()

        # When
        response = await service.handle_chat(chat_request)

    # Then: 원 답변이 아닌 fallback 메시지 반환
    assert response.answer == PII_DETECTOR_UNAVAILABLE_MESSAGE
    assert llm_raw_answer not in response.answer
    assert "123-456-789" not in response.answer
    assert "010-9999-8888" not in response.answer


# =============================================================================
# TEST-2: SECURITY 이벤트 발행 검증
# =============================================================================


@pytest.mark.asyncio
async def test_pii_output_failure_emits_security_event(
    stub_publisher, request_context, chat_request
):
    """출력 PII detector 실패 시 SECURITY 이벤트가 발행된다."""
    # Given
    stub_publisher.clear()

    with patch(
        "app.services.chat_service.PiiService.detect_and_mask",
        new_callable=AsyncMock,
        side_effect=create_pii_mock_input_success_output_fail(),
    ), patch(
        "app.services.chat_service.LLMClient.generate_chat_completion",
        new_callable=AsyncMock,
        return_value="Test answer",
    ), patch(
        "app.services.chat_service.IntentService.classify",
        return_value=IntentResult(
            intent=IntentType.GENERAL_CHAT,
            user_role=UserRole.EMPLOYEE,
            domain="GENERAL",
            route=RouteType.LLM_ONLY,
        ),
    ), patch(
        "app.services.chat_service.ChatService._perform_rag_search_with_fallback",
        new_callable=AsyncMock,
        return_value=([], False, "RAGFLOW"),
    ):
        service = ChatService()

        # When
        await service.handle_chat(chat_request)

    # Then: SECURITY 이벤트 발행 (OUTPUT 단계 실패)
    security_events = stub_publisher.get_events_by_type("SECURITY")
    assert len(security_events) == 1

    event = security_events[0]
    assert event.event_type == "SECURITY"
    assert event.payload.block_type == "PII_BLOCK"
    assert event.payload.blocked is True
    assert event.payload.rule_id == "PII_DETECTOR_UNAVAILABLE_OUTPUT"


# =============================================================================
# TEST-3: CHAT_TURN 이벤트 발행 검증
# =============================================================================


@pytest.mark.asyncio
async def test_pii_output_failure_emits_chat_turn_event(
    stub_publisher, request_context, chat_request
):
    """출력 PII detector 실패 시 CHAT_TURN 이벤트가 발행된다."""
    # Given
    stub_publisher.clear()

    with patch(
        "app.services.chat_service.PiiService.detect_and_mask",
        new_callable=AsyncMock,
        side_effect=create_pii_mock_input_success_output_fail(),
    ), patch(
        "app.services.chat_service.LLMClient.generate_chat_completion",
        new_callable=AsyncMock,
        return_value="Test answer",
    ), patch(
        "app.services.chat_service.IntentService.classify",
        return_value=IntentResult(
            intent=IntentType.POLICY_QA,
            user_role=UserRole.EMPLOYEE,
            domain="POLICY",
            route=RouteType.RAG_INTERNAL,
        ),
    ), patch(
        "app.services.chat_service.ChatService._perform_rag_search_with_fallback",
        new_callable=AsyncMock,
        return_value=([], False, "RAGFLOW"),
    ):
        service = ChatService()

        # When
        await service.handle_chat(chat_request)

    # Then: CHAT_TURN 이벤트 발행
    chat_events = stub_publisher.get_events_by_type("CHAT_TURN")
    assert len(chat_events) == 1

    event = chat_events[0]
    assert event.event_type == "CHAT_TURN"
    assert event.payload.error_code == "PII_DETECTOR_UNAVAILABLE"
    # 출력 단계 실패이므로 intent/domain은 이미 결정된 값 사용
    assert event.payload.intent_main == "POLICY_QA"


# =============================================================================
# TEST-4: 텔레메트리 이벤트에 LLM 응답 원문 미포함 검증
# =============================================================================


@pytest.mark.asyncio
async def test_pii_output_failure_no_raw_answer_in_events(
    stub_publisher, request_context, chat_request
):
    """출력 PII detector 실패 시 텔레메트리 이벤트에 LLM 응답 원문이 포함되지 않는다."""
    # Given
    stub_publisher.clear()
    llm_raw_answer = "귀하의 비밀번호 ABC123XYZ와 카드번호 1234-5678-9012-3456입니다."

    with patch(
        "app.services.chat_service.PiiService.detect_and_mask",
        new_callable=AsyncMock,
        side_effect=create_pii_mock_input_success_output_fail(),
    ), patch(
        "app.services.chat_service.LLMClient.generate_chat_completion",
        new_callable=AsyncMock,
        return_value=llm_raw_answer,
    ), patch(
        "app.services.chat_service.IntentService.classify",
        return_value=IntentResult(
            intent=IntentType.GENERAL_CHAT,
            user_role=UserRole.EMPLOYEE,
            domain="GENERAL",
            route=RouteType.LLM_ONLY,
        ),
    ), patch(
        "app.services.chat_service.ChatService._perform_rag_search_with_fallback",
        new_callable=AsyncMock,
        return_value=([], False, "RAGFLOW"),
    ):
        service = ChatService()

        # When
        await service.handle_chat(chat_request)

    # Then: 모든 이벤트에 LLM 응답 원문이 포함되지 않음
    for event in stub_publisher.events:
        event_json = json.dumps(
            event.model_dump(by_alias=True, mode="json"),
            ensure_ascii=False,
            default=str,
        )

        # LLM 응답 원문의 일부가 포함되어 있으면 안 됨
        assert llm_raw_answer not in event_json
        assert "ABC123XYZ" not in event_json
        assert "1234-5678-9012-3456" not in event_json


# =============================================================================
# TEST-5: 샘플 이벤트 덤프 출력
# =============================================================================


@pytest.mark.asyncio
async def test_sample_pii_output_fail_closed_event_dump(
    stub_publisher, request_context, chat_request, capsys
):
    """PII 출력 Fail-Closed 시 발생하는 이벤트 샘플을 출력한다."""
    # Given
    stub_publisher.clear()

    with patch(
        "app.services.chat_service.PiiService.detect_and_mask",
        new_callable=AsyncMock,
        side_effect=create_pii_mock_input_success_output_fail(),
    ), patch(
        "app.services.chat_service.LLMClient.generate_chat_completion",
        new_callable=AsyncMock,
        return_value="Test answer",
    ), patch(
        "app.services.chat_service.IntentService.classify",
        return_value=IntentResult(
            intent=IntentType.GENERAL_CHAT,
            user_role=UserRole.EMPLOYEE,
            domain="GENERAL",
            route=RouteType.LLM_ONLY,
        ),
    ), patch(
        "app.services.chat_service.ChatService._perform_rag_search_with_fallback",
        new_callable=AsyncMock,
        return_value=([], False, "RAGFLOW"),
    ):
        service = ChatService()

        # When
        await service.handle_chat(chat_request)

    # Then: 이벤트 샘플 출력
    print("\n=== PII Output Fail-Closed: SECURITY Event ===")
    security_events = stub_publisher.get_events_by_type("SECURITY")
    if security_events:
        event_dict = security_events[0].model_dump(by_alias=True, mode="json")
        print(json.dumps(event_dict, indent=2, ensure_ascii=False, default=str))

    print("\n=== PII Output Fail-Closed: CHAT_TURN Event ===")
    chat_events = stub_publisher.get_events_by_type("CHAT_TURN")
    if chat_events:
        event_dict = chat_events[0].model_dump(by_alias=True, mode="json")
        print(json.dumps(event_dict, indent=2, ensure_ascii=False, default=str))

    # 캡처된 출력 확인
    captured = capsys.readouterr()
    assert "PII_DETECTOR_UNAVAILABLE_OUTPUT" in captured.out
    assert "PII_DETECTOR_UNAVAILABLE" in captured.out
