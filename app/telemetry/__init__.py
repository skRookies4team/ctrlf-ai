"""
Telemetry Package - v1 Telemetry Contract

v1 Telemetry 계약에 따른 이벤트 스키마를 정의합니다.
기존 AILogEntry와는 별개로, 새로운 계약 기반 텔레메트리 시스템입니다.

주요 구성요소:
- TelemetryEnvelope: 이벤트 배치 전송용 래퍼
- TelemetryEvent: 개별 이벤트 (eventId, traceId, payload 포함)
- ChatTurnPayload: 채팅 턴 이벤트 페이로드
- FeedbackPayload: 사용자 피드백 이벤트 페이로드
- SecurityPayload: 보안 이벤트 페이로드
- RequestContext: 요청별 컨텍스트 (trace_id, user_id 등)
- RequestContextMiddleware: 헤더 → 컨텍스트 전파 미들웨어
- TelemetryPublisher: 비동기 이벤트 전송 Publisher
- emit_chat_turn_once: CHAT_TURN 이벤트 단일 발행 (턴당 1회 보장)
- emit_security_event_once: SECURITY 이벤트 단일 발행 (요청 단위 dedup)
- LatencyMetrics / RagMetrics: 요청별 메트릭 수집 컨테이너

Usage:
    from app.telemetry import (
        TelemetryEnvelope,
        TelemetryEvent,
        ChatTurnPayload,
        FeedbackPayload,
        SecurityPayload,
        # Context propagation
        RequestContext,
        get_request_context,
        set_request_context,
        # Publisher
        TelemetryPublisher,
        get_telemetry_publisher,
        # Emission (A4)
        emit_chat_turn_once,
        # Metrics (A4)
        set_latency_metrics,
        set_rag_metrics,
    )
"""

from app.telemetry.models import (
    # Enums / Literals
    EventType,
    FeedbackValue,
    SecurityBlockType,
    # RAG sub-models
    RagSource,
    RagInfo,
    # Payloads
    ChatTurnPayload,
    FeedbackPayload,
    SecurityPayload,
    # Event & Envelope
    TelemetryEvent,
    TelemetryEnvelope,
)
from app.telemetry.context import (
    RequestContext,
    get_request_context,
    set_request_context,
    reset_request_context,
)
from app.telemetry.middleware import RequestContextMiddleware
from app.telemetry.publisher import (
    TelemetryPublisher,
    get_telemetry_publisher,
    set_telemetry_publisher,
)
from app.telemetry.emitters import (
    emit_chat_turn_once,
    mark_chat_turn_emitted,
    is_chat_turn_emitted,
    reset_chat_turn_emitted,
    # Security (A5)
    emit_security_event_once,
    mark_security_emitted,
    is_security_emitted,
    reset_security_emitted,
    # Feedback (A6)
    emit_feedback_event_once,
    mark_feedback_emitted,
    is_feedback_emitted,
    reset_feedback_emitted,
)
from app.telemetry.metrics import (
    LatencyMetrics,
    RagMetrics,
    set_latency_metrics,
    get_latency_metrics,
    reset_latency_metrics,
    set_rag_metrics,
    get_rag_metrics,
    reset_rag_metrics,
    reset_all_metrics,
    rag_metrics_to_rag_info,
)

__all__ = [
    # Enums / Literals
    "EventType",
    "FeedbackValue",
    "SecurityBlockType",
    # RAG sub-models
    "RagSource",
    "RagInfo",
    # Payloads
    "ChatTurnPayload",
    "FeedbackPayload",
    "SecurityPayload",
    # Event & Envelope
    "TelemetryEvent",
    "TelemetryEnvelope",
    # Context propagation
    "RequestContext",
    "get_request_context",
    "set_request_context",
    "reset_request_context",
    "RequestContextMiddleware",
    # Publisher
    "TelemetryPublisher",
    "get_telemetry_publisher",
    "set_telemetry_publisher",
    # Emitters (A4)
    "emit_chat_turn_once",
    "mark_chat_turn_emitted",
    "is_chat_turn_emitted",
    "reset_chat_turn_emitted",
    # Security Emitters (A5)
    "emit_security_event_once",
    "mark_security_emitted",
    "is_security_emitted",
    "reset_security_emitted",
    # Feedback Emitters (A6)
    "emit_feedback_event_once",
    "mark_feedback_emitted",
    "is_feedback_emitted",
    "reset_feedback_emitted",
    # Metrics (A4)
    "LatencyMetrics",
    "RagMetrics",
    "set_latency_metrics",
    "get_latency_metrics",
    "reset_latency_metrics",
    "set_rag_metrics",
    "get_rag_metrics",
    "reset_rag_metrics",
    "reset_all_metrics",
    "rag_metrics_to_rag_info",
]
