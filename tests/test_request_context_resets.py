"""
Request Context Reset Tests - A7 요청 단위 Clean Slate 보장

RequestContextMiddleware가 요청 단위로 모든 contextvars를 리셋하는지 검증합니다.

테스트 목록:
- TEST-1: 요청 A에서 emit한 상태가 요청 B에 누적되지 않음 (SECURITY)
- TEST-2: 요청 A에서 emit한 상태가 요청 B에 누적되지 않음 (CHAT_TURN)
- TEST-3: 요청 A에서 emit한 상태가 요청 B에 누적되지 않음 (FEEDBACK)
"""

from typing import List
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.telemetry.middleware import RequestContextMiddleware
from app.telemetry.emitters import (
    emit_security_event_once,
    emit_chat_turn_once,
    emit_feedback_event_once,
)
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
# Test App Factory
# =============================================================================


def create_test_app(stub_publisher: StubPublisher) -> FastAPI:
    """테스트용 앱 생성."""
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    # Publisher 설정
    set_telemetry_publisher(stub_publisher)

    @app.get("/emit-security")
    def emit_security_endpoint():
        """SECURITY 이벤트를 발행하는 엔드포인트."""
        result = emit_security_event_once(
            block_type="PII_BLOCK",
            blocked=True,
            rule_id="TEST-RULE-001",
        )
        return {"emitted": result}

    @app.get("/emit-chat-turn")
    def emit_chat_turn_endpoint():
        """CHAT_TURN 이벤트를 발행하는 엔드포인트."""
        result = emit_chat_turn_once(
            intent_main="POLICY_QA",
            route_type="RAG",
            domain="POLICY",
            rag_used=True,
            latency_ms_total=100,
            pii_detected_input=False,
            pii_detected_output=False,
        )
        return {"emitted": result}

    @app.get("/emit-feedback")
    def emit_feedback_endpoint():
        """FEEDBACK 이벤트를 발행하는 엔드포인트."""
        result = emit_feedback_event_once(
            feedback="like",
            target_conversation_id="C-TEST-001",
            target_turn_id=1,
        )
        return {"emitted": result}

    return app


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def stub_publisher():
    """StubPublisher 인스턴스 생성."""
    return StubPublisher()


@pytest.fixture
def test_client(stub_publisher):
    """테스트 클라이언트 생성."""
    app = create_test_app(stub_publisher)
    return TestClient(app)


@pytest.fixture
def valid_headers():
    """유효한 컨텍스트 헤더."""
    return {
        "X-Trace-Id": f"trace-{uuid4().hex[:8]}",
        "X-User-Id": "U-TEST-001",
        "X-Dept-Id": "D-TEST",
        "X-Conversation-Id": "C-TEST-001",
        "X-Turn-Id": "1",
    }


# =============================================================================
# TEST-1: SECURITY 이벤트 요청 간 상태 격리
# =============================================================================


def test_security_emit_state_isolated_between_requests(
    test_client, stub_publisher, valid_headers
):
    """요청 A와 요청 B에서 각각 SECURITY 이벤트가 발행된다 (상태 누적 없음)."""
    # Given: 이벤트 초기화
    stub_publisher.clear()

    # When: 요청 A - 동일한 rule_id로 emit
    headers_a = valid_headers.copy()
    headers_a["X-Trace-Id"] = "trace-request-A"
    response_a = test_client.get("/emit-security", headers=headers_a)
    assert response_a.status_code == 200
    assert response_a.json()["emitted"] is True

    # When: 요청 B - 동일한 rule_id로 다시 emit
    headers_b = valid_headers.copy()
    headers_b["X-Trace-Id"] = "trace-request-B"
    response_b = test_client.get("/emit-security", headers=headers_b)
    assert response_b.status_code == 200

    # Then: 요청 B에서도 emit이 성공해야 함 (요청 A의 dedup 상태가 누적되지 않음)
    assert response_b.json()["emitted"] is True

    # Then: 총 2개의 SECURITY 이벤트가 발행됨
    security_events = stub_publisher.get_events_by_type("SECURITY")
    assert len(security_events) == 2

    # Then: 각 요청의 trace_id가 다름
    trace_ids = {e.trace_id for e in security_events}
    assert "trace-request-A" in trace_ids
    assert "trace-request-B" in trace_ids


# =============================================================================
# TEST-2: CHAT_TURN 이벤트 요청 간 상태 격리
# =============================================================================


def test_chat_turn_emit_state_isolated_between_requests(
    test_client, stub_publisher, valid_headers
):
    """요청 A와 요청 B에서 각각 CHAT_TURN 이벤트가 발행된다 (상태 누적 없음)."""
    # Given: 이벤트 초기화
    stub_publisher.clear()

    # When: 요청 A
    headers_a = valid_headers.copy()
    headers_a["X-Trace-Id"] = "trace-chat-A"
    response_a = test_client.get("/emit-chat-turn", headers=headers_a)
    assert response_a.status_code == 200
    assert response_a.json()["emitted"] is True

    # When: 요청 B
    headers_b = valid_headers.copy()
    headers_b["X-Trace-Id"] = "trace-chat-B"
    response_b = test_client.get("/emit-chat-turn", headers=headers_b)
    assert response_b.status_code == 200

    # Then: 요청 B에서도 emit이 성공해야 함
    assert response_b.json()["emitted"] is True

    # Then: 총 2개의 CHAT_TURN 이벤트가 발행됨
    chat_events = stub_publisher.get_events_by_type("CHAT_TURN")
    assert len(chat_events) == 2

    # Then: 각 요청의 trace_id가 다름
    trace_ids = {e.trace_id for e in chat_events}
    assert "trace-chat-A" in trace_ids
    assert "trace-chat-B" in trace_ids


# =============================================================================
# TEST-3: FEEDBACK 이벤트 요청 간 상태 격리
# =============================================================================


def test_feedback_emit_state_isolated_between_requests(
    test_client, stub_publisher, valid_headers
):
    """요청 A와 요청 B에서 각각 FEEDBACK 이벤트가 발행된다 (상태 누적 없음)."""
    # Given: 이벤트 초기화
    stub_publisher.clear()

    # When: 요청 A
    headers_a = valid_headers.copy()
    headers_a["X-Trace-Id"] = "trace-feedback-A"
    response_a = test_client.get("/emit-feedback", headers=headers_a)
    assert response_a.status_code == 200
    assert response_a.json()["emitted"] is True

    # When: 요청 B
    headers_b = valid_headers.copy()
    headers_b["X-Trace-Id"] = "trace-feedback-B"
    response_b = test_client.get("/emit-feedback", headers=headers_b)
    assert response_b.status_code == 200

    # Then: 요청 B에서도 emit이 성공해야 함
    assert response_b.json()["emitted"] is True

    # Then: 총 2개의 FEEDBACK 이벤트가 발행됨
    feedback_events = stub_publisher.get_events_by_type("FEEDBACK")
    assert len(feedback_events) == 2

    # Then: 각 요청의 trace_id가 다름
    trace_ids = {e.trace_id for e in feedback_events}
    assert "trace-feedback-A" in trace_ids
    assert "trace-feedback-B" in trace_ids


# =============================================================================
# TEST-4: 혼합 이벤트 요청 간 상태 격리
# =============================================================================


def test_mixed_events_state_isolated_between_requests(
    test_client, stub_publisher, valid_headers
):
    """다양한 이벤트 타입이 요청 간에 격리된다."""
    # Given: 이벤트 초기화
    stub_publisher.clear()

    # When: 요청 A - 모든 타입 emit
    headers_a = valid_headers.copy()
    headers_a["X-Trace-Id"] = "trace-mixed-A"
    test_client.get("/emit-security", headers=headers_a)
    test_client.get("/emit-chat-turn", headers=headers_a)
    test_client.get("/emit-feedback", headers=headers_a)

    # 요청 A 결과 확인
    events_after_a = len(stub_publisher.events)
    assert events_after_a == 3  # SECURITY, CHAT_TURN, FEEDBACK 각 1개

    # When: 요청 B - 동일하게 모든 타입 emit
    headers_b = valid_headers.copy()
    headers_b["X-Trace-Id"] = "trace-mixed-B"
    resp_sec = test_client.get("/emit-security", headers=headers_b)
    resp_chat = test_client.get("/emit-chat-turn", headers=headers_b)
    resp_feed = test_client.get("/emit-feedback", headers=headers_b)

    # Then: 요청 B에서도 모두 emit 성공
    assert resp_sec.json()["emitted"] is True
    assert resp_chat.json()["emitted"] is True
    assert resp_feed.json()["emitted"] is True

    # Then: 총 6개 이벤트 (요청 A 3개 + 요청 B 3개)
    assert len(stub_publisher.events) == 6
