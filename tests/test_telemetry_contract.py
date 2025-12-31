"""
Telemetry Contract Tests - v1 Telemetry Schema Validation

v1 Telemetry 계약 스키마의 직렬화 및 검증 테스트입니다.
model_dump(by_alias=True, exclude_none=True) 결과가
v1 계약 JSON과 1:1로 일치하는지 검증합니다.

테스트 목록:
- TEST-1: CHAT_TURN 이벤트 직렬화 일치
- TEST-2: FEEDBACK 이벤트 직렬화 일치
- TEST-3: SECURITY 이벤트 직렬화 일치
- TEST-4: eventType/payload 불일치 ValidationError
- TEST-5: contextExcerpt 길이 제한 검증
"""

from datetime import datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.telemetry import (
    ChatTurnPayload,
    FeedbackPayload,
    RagInfo,
    RagSource,
    SecurityPayload,
    TelemetryEnvelope,
    TelemetryEvent,
)


# =============================================================================
# 테스트용 고정값
# =============================================================================

FIXED_EVENT_ID = UUID("b9b2f9f9-1f7a-4ad4-9db5-4a1cf2d6b3d1")
FIXED_TRACE_ID = "9de2d2d0-3c9c-4c60-bda6-1c0e1c2a4c55"
FIXED_CONVERSATION_ID = "C-20251230-000123"
FIXED_OCCURRED_AT = datetime(2025, 12, 30, 21, 10, 21)
FIXED_SENT_AT = datetime(2025, 12, 30, 21, 10, 22)


# =============================================================================
# TEST-1: CHAT_TURN 이벤트 직렬화 일치
# =============================================================================


def test_chat_turn_event_serialization():
    """CHAT_TURN 이벤트 샘플 직렬화가 v1 계약과 일치하는지 검증."""
    # Given: CHAT_TURN 페이로드 생성 (RAG 정보 포함)
    rag_source = RagSource(
        doc_id="POL-001",
        chunk_id=12,
        score=0.63,
    )
    rag_info = RagInfo(
        retriever="milvus",
        top_k=5,
        min_score=0.58,
        max_score=0.63,
        avg_score=0.60,
        sources=[rag_source],
        context_excerpt="연차는 입사일 기준으로 1년 근무 시 15일이 부여됩니다.",
    )
    chat_turn_payload = ChatTurnPayload(
        intent_main="POLICY_QA",
        intent_sub="PARENTAL_LEAVE",
        route_type="RAG",
        domain="POLICY",
        model="gpt-4o-mini",
        rag_used=True,
        latency_ms_total=415,
        latency_ms_llm=280,
        latency_ms_retrieval=90,
        error_code=None,
        pii_detected_input=False,
        pii_detected_output=False,
        oos=False,
        rag=rag_info,
    )

    # Given: TelemetryEvent 생성
    event = TelemetryEvent(
        event_id=FIXED_EVENT_ID,
        event_type="CHAT_TURN",
        trace_id=FIXED_TRACE_ID,
        conversation_id=FIXED_CONVERSATION_ID,
        turn_id=7,
        user_id="U-10293",
        dept_id="D-SALES",
        occurred_at=FIXED_OCCURRED_AT,
        payload=chat_turn_payload,
    )

    # Given: TelemetryEnvelope 생성
    envelope = TelemetryEnvelope(
        source="ai-gateway",
        sent_at=FIXED_SENT_AT,
        events=[event],
    )

    # When: 직렬화
    result = envelope.model_dump(by_alias=True, exclude_none=True, mode="json")

    # Then: 기대 JSON 구조와 일치
    expected = {
        "source": "ai-gateway",
        "sentAt": "2025-12-30T21:10:22",
        "events": [
            {
                "eventId": "b9b2f9f9-1f7a-4ad4-9db5-4a1cf2d6b3d1",
                "eventType": "CHAT_TURN",
                "traceId": "9de2d2d0-3c9c-4c60-bda6-1c0e1c2a4c55",
                "conversationId": "C-20251230-000123",
                "turnId": 7,
                "userId": "U-10293",
                "deptId": "D-SALES",
                "occurredAt": "2025-12-30T21:10:21",
                "payload": {
                    "intentMain": "POLICY_QA",
                    "intentSub": "PARENTAL_LEAVE",
                    "routeType": "RAG",
                    "domain": "POLICY",
                    "model": "gpt-4o-mini",
                    "ragUsed": True,
                    "latencyMsTotal": 415,
                    "latencyMsLlm": 280,
                    "latencyMsRetrieval": 90,
                    "piiDetectedInput": False,
                    "piiDetectedOutput": False,
                    "oos": False,
                    "rag": {
                        "retriever": "milvus",
                        "topK": 5,
                        "minScore": 0.58,
                        "maxScore": 0.63,
                        "avgScore": 0.60,
                        "sources": [
                            {
                                "docId": "POL-001",
                                "chunkId": 12,
                                "score": 0.63,
                            }
                        ],
                        "contextExcerpt": "연차는 입사일 기준으로 1년 근무 시 15일이 부여됩니다.",
                    },
                },
            }
        ],
    }

    assert result == expected


# =============================================================================
# TEST-2: FEEDBACK 이벤트 직렬화 일치
# =============================================================================


def test_feedback_event_serialization():
    """FEEDBACK 이벤트 샘플 직렬화가 v1 계약과 일치하는지 검증."""
    # Given: FEEDBACK 페이로드 생성
    feedback_payload = FeedbackPayload(
        feedback="like",
        target_conversation_id="C-20251230-000123",
        target_turn_id=7,
    )

    # Given: TelemetryEvent 생성
    event = TelemetryEvent(
        event_id=FIXED_EVENT_ID,
        event_type="FEEDBACK",
        trace_id=FIXED_TRACE_ID,
        conversation_id=FIXED_CONVERSATION_ID,
        turn_id=8,  # 피드백은 다음 턴에서 발생
        user_id="U-10293",
        dept_id="D-SALES",
        occurred_at=FIXED_OCCURRED_AT,
        payload=feedback_payload,
    )

    # Given: TelemetryEnvelope 생성
    envelope = TelemetryEnvelope(
        source="ai-gateway",
        sent_at=FIXED_SENT_AT,
        events=[event],
    )

    # When: 직렬화
    result = envelope.model_dump(by_alias=True, exclude_none=True, mode="json")

    # Then: 기대 JSON 구조와 일치
    expected = {
        "source": "ai-gateway",
        "sentAt": "2025-12-30T21:10:22",
        "events": [
            {
                "eventId": "b9b2f9f9-1f7a-4ad4-9db5-4a1cf2d6b3d1",
                "eventType": "FEEDBACK",
                "traceId": "9de2d2d0-3c9c-4c60-bda6-1c0e1c2a4c55",
                "conversationId": "C-20251230-000123",
                "turnId": 8,
                "userId": "U-10293",
                "deptId": "D-SALES",
                "occurredAt": "2025-12-30T21:10:21",
                "payload": {
                    "feedback": "like",
                    "targetConversationId": "C-20251230-000123",
                    "targetTurnId": 7,
                },
            }
        ],
    }

    assert result == expected


# =============================================================================
# TEST-3: SECURITY 이벤트 직렬화 일치
# =============================================================================


def test_security_event_serialization():
    """SECURITY 이벤트 샘플 직렬화가 v1 계약과 일치하는지 검증."""
    # Given: SECURITY 페이로드 생성
    security_payload = SecurityPayload(
        block_type="PII_BLOCK",
        blocked=True,
        rule_id="PII-RULE-001",
    )

    # Given: TelemetryEvent 생성
    event = TelemetryEvent(
        event_id=FIXED_EVENT_ID,
        event_type="SECURITY",
        trace_id=FIXED_TRACE_ID,
        conversation_id=FIXED_CONVERSATION_ID,
        turn_id=7,
        user_id="U-10293",
        dept_id="D-SALES",
        occurred_at=FIXED_OCCURRED_AT,
        payload=security_payload,
    )

    # Given: TelemetryEnvelope 생성
    envelope = TelemetryEnvelope(
        source="ai-gateway",
        sent_at=FIXED_SENT_AT,
        events=[event],
    )

    # When: 직렬화
    result = envelope.model_dump(by_alias=True, exclude_none=True, mode="json")

    # Then: 기대 JSON 구조와 일치
    expected = {
        "source": "ai-gateway",
        "sentAt": "2025-12-30T21:10:22",
        "events": [
            {
                "eventId": "b9b2f9f9-1f7a-4ad4-9db5-4a1cf2d6b3d1",
                "eventType": "SECURITY",
                "traceId": "9de2d2d0-3c9c-4c60-bda6-1c0e1c2a4c55",
                "conversationId": "C-20251230-000123",
                "turnId": 7,
                "userId": "U-10293",
                "deptId": "D-SALES",
                "occurredAt": "2025-12-30T21:10:21",
                "payload": {
                    "blockType": "PII_BLOCK",
                    "blocked": True,
                    "ruleId": "PII-RULE-001",
                },
            }
        ],
    }

    assert result == expected


# =============================================================================
# TEST-4: eventType/payload 불일치 ValidationError
# =============================================================================


def test_event_type_payload_mismatch_raises_validation_error():
    """eventType과 payload 타입이 불일치하면 ValidationError 발생."""
    # Given: CHAT_TURN eventType인데 FeedbackPayload 사용
    feedback_payload = FeedbackPayload(
        feedback="like",
        target_conversation_id="C-20251230-000123",
        target_turn_id=7,
    )

    # When/Then: ValidationError 발생
    with pytest.raises(ValidationError) as exc_info:
        TelemetryEvent(
            event_id=FIXED_EVENT_ID,
            event_type="CHAT_TURN",  # CHAT_TURN인데
            trace_id=FIXED_TRACE_ID,
            conversation_id=FIXED_CONVERSATION_ID,
            turn_id=7,
            user_id="U-10293",
            dept_id="D-SALES",
            occurred_at=FIXED_OCCURRED_AT,
            payload=feedback_payload,  # FeedbackPayload 사용
        )

    # Then: 에러 메시지 확인
    assert "CHAT_TURN" in str(exc_info.value)
    assert "FeedbackPayload" in str(exc_info.value)


def test_feedback_event_type_with_chat_turn_payload_raises_error():
    """FEEDBACK eventType에 ChatTurnPayload 사용 시 ValidationError 발생."""
    # Given: ChatTurnPayload 생성
    chat_payload = ChatTurnPayload(
        intent_main="POLICY_QA",
        route_type="RAG",
        domain="POLICY",
        rag_used=True,
        latency_ms_total=415,
        pii_detected_input=False,
        pii_detected_output=False,
    )

    # When/Then: ValidationError 발생
    with pytest.raises(ValidationError) as exc_info:
        TelemetryEvent(
            event_id=FIXED_EVENT_ID,
            event_type="FEEDBACK",  # FEEDBACK인데
            trace_id=FIXED_TRACE_ID,
            conversation_id=FIXED_CONVERSATION_ID,
            turn_id=7,
            user_id="U-10293",
            dept_id="D-SALES",
            occurred_at=FIXED_OCCURRED_AT,
            payload=chat_payload,  # ChatTurnPayload 사용
        )

    assert "FEEDBACK" in str(exc_info.value)
    assert "ChatTurnPayload" in str(exc_info.value)


def test_security_event_type_with_feedback_payload_raises_error():
    """SECURITY eventType에 FeedbackPayload 사용 시 ValidationError 발생."""
    # Given: FeedbackPayload 생성
    feedback_payload = FeedbackPayload(
        feedback="dislike",
        target_conversation_id="C-20251230-000123",
        target_turn_id=7,
    )

    # When/Then: ValidationError 발생
    with pytest.raises(ValidationError) as exc_info:
        TelemetryEvent(
            event_id=FIXED_EVENT_ID,
            event_type="SECURITY",  # SECURITY인데
            trace_id=FIXED_TRACE_ID,
            conversation_id=FIXED_CONVERSATION_ID,
            turn_id=7,
            user_id="U-10293",
            dept_id="D-SALES",
            occurred_at=FIXED_OCCURRED_AT,
            payload=feedback_payload,  # FeedbackPayload 사용
        )

    assert "SECURITY" in str(exc_info.value)
    assert "FeedbackPayload" in str(exc_info.value)


# =============================================================================
# TEST-5: contextExcerpt 길이 제한 검증
# =============================================================================


def test_context_excerpt_max_300_chars_passes():
    """contextExcerpt 300자는 통과."""
    # Given: 정확히 300자
    text_300 = "가" * 300

    # When/Then: 정상 생성
    rag_info = RagInfo(
        retriever="milvus",
        top_k=5,
        min_score=0.5,
        max_score=0.9,
        avg_score=0.7,
        sources=[],
        context_excerpt=text_300,
    )

    assert rag_info.context_excerpt == text_300
    assert len(rag_info.context_excerpt) == 300


def test_context_excerpt_301_chars_raises_validation_error():
    """contextExcerpt 301자는 ValidationError 발생."""
    # Given: 301자
    text_301 = "가" * 301

    # When/Then: ValidationError 발생
    with pytest.raises(ValidationError) as exc_info:
        RagInfo(
            retriever="milvus",
            top_k=5,
            min_score=0.5,
            max_score=0.9,
            avg_score=0.7,
            sources=[],
            context_excerpt=text_301,
        )

    assert "300 characters" in str(exc_info.value)


def test_context_excerpt_none_is_allowed():
    """contextExcerpt None은 허용."""
    # When/Then: 정상 생성
    rag_info = RagInfo(
        retriever="milvus",
        top_k=5,
        min_score=0.5,
        max_score=0.9,
        avg_score=0.7,
        sources=[],
        context_excerpt=None,
    )

    assert rag_info.context_excerpt is None


# =============================================================================
# 추가 테스트: 빈 events 리스트 금지
# =============================================================================


def test_empty_events_list_raises_validation_error():
    """TelemetryEnvelope의 events는 최소 1개 필요."""
    # When/Then: ValidationError 발생
    with pytest.raises(ValidationError):
        TelemetryEnvelope(
            source="ai-gateway",
            sent_at=FIXED_SENT_AT,
            events=[],  # 빈 리스트
        )
