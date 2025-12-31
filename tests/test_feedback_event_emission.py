"""
FEEDBACK Event Emission Tests - A6 요청 단위 Dedup 보장

emit_feedback_event_once의 "요청 단위 중복 방지"를 검증합니다.

테스트 목록:
- TEST-1: 동일 요청에서 동일 key로 2회 호출해도 enqueue 1번
- TEST-2: target_turn_id 또는 feedback가 다르면 각각 enqueue
- TEST-3: 필수 헤더 없으면 drop
- TEST-4: publisher=None이면 drop
- TEST-5: 내부 엔드포인트 호출 시 FEEDBACK 이벤트 enqueue + payload에 텍스트 없음 검증
- (옵션) test_sample_event_dump: model_dump(by_alias=True) 샘플 출력
"""

from typing import List
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.telemetry.context import (
    RequestContext,
    set_request_context,
    reset_request_context,
)
from app.telemetry.emitters import (
    emit_feedback_event_once,
    reset_feedback_emitted,
)
from app.telemetry.models import TelemetryEvent
from app.telemetry.publisher import (
    set_telemetry_publisher,
)


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


# =============================================================================
# Mini Test Middleware (헤더 → 컨텍스트 전파)
# =============================================================================


class FeedbackTestContextMiddleware(BaseHTTPMiddleware):
    """테스트용 컨텍스트 미들웨어."""

    async def dispatch(self, request: Request, call_next):
        # 헤더에서 컨텍스트 추출
        trace_id = request.headers.get("X-Trace-Id")
        user_id = request.headers.get("X-User-Id")
        dept_id = request.headers.get("X-Dept-Id")
        conversation_id = request.headers.get("X-Conversation-Id")
        turn_id_str = request.headers.get("X-Turn-Id")
        turn_id = int(turn_id_str) if turn_id_str else None

        # 컨텍스트 설정
        ctx = RequestContext(
            trace_id=trace_id,
            user_id=user_id,
            dept_id=dept_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        set_request_context(ctx)

        # 발행 상태 초기화 (새 요청마다)
        reset_feedback_emitted()

        try:
            response = await call_next(request)
            return response
        finally:
            reset_request_context()


# =============================================================================
# Mini FastAPI App
# =============================================================================


def create_feedback_test_app(stub_publisher: StubPublisher) -> FastAPI:
    """테스트용 미니 앱 생성."""
    app = FastAPI()
    app.add_middleware(FeedbackTestContextMiddleware)

    # Publisher 주입
    set_telemetry_publisher(stub_publisher)

    @app.get("/feedback-duplicate")
    def endpoint_feedback_duplicate():
        """emit_feedback_event_once를 같은 인자로 2번 호출하는 엔드포인트."""
        # 첫 번째 호출
        result1 = emit_feedback_event_once(
            feedback="like",
            target_conversation_id="C-TEST-001",
            target_turn_id=1,
        )
        # 두 번째 호출 (중복, drop되어야 함)
        result2 = emit_feedback_event_once(
            feedback="like",
            target_conversation_id="C-TEST-001",
            target_turn_id=1,
        )
        return {"result1": result1, "result2": result2}

    @app.get("/feedback-different-turns")
    def endpoint_feedback_different_turns():
        """다른 turn_id로 emit."""
        result1 = emit_feedback_event_once(
            feedback="like",
            target_conversation_id="C-TEST-001",
            target_turn_id=1,
        )
        result2 = emit_feedback_event_once(
            feedback="like",
            target_conversation_id="C-TEST-001",
            target_turn_id=2,
        )
        return {"result1": result1, "result2": result2}

    @app.get("/feedback-different-values")
    def endpoint_feedback_different_values():
        """다른 feedback 값으로 emit."""
        result1 = emit_feedback_event_once(
            feedback="like",
            target_conversation_id="C-TEST-001",
            target_turn_id=1,
        )
        result2 = emit_feedback_event_once(
            feedback="dislike",
            target_conversation_id="C-TEST-001",
            target_turn_id=1,
        )
        return {"result1": result1, "result2": result2}

    @app.get("/feedback-noheaders")
    def endpoint_noheaders():
        """헤더 없이 호출 -> drop."""
        result = emit_feedback_event_once(
            feedback="like",
            target_conversation_id="C-TEST-001",
            target_turn_id=1,
        )
        return {"result": result}

    return app


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def stub_publisher():
    """StubPublisher 인스턴스 생성."""
    return StubPublisher()


@pytest.fixture
def feedback_test_client(stub_publisher):
    """테스트 클라이언트 생성."""
    app = create_feedback_test_app(stub_publisher)
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
# TEST-1: 동일 요청에서 동일 key로 2회 호출해도 enqueue 1번
# =============================================================================


def test_duplicate_feedback_emission_blocked(
    feedback_test_client, stub_publisher, valid_headers
):
    """동일 요청에서 동일 key로 2회 호출해도 enqueue 1번."""
    # When: /feedback-duplicate 호출 (내부에서 emit_feedback_event_once 2번 호출)
    response = feedback_test_client.get("/feedback-duplicate", headers=valid_headers)

    # Then: 응답 정상
    assert response.status_code == 200
    data = response.json()
    assert data["result1"] is True  # 첫 번째 성공
    assert data["result2"] is False  # 두 번째 중복 방지

    # Then: 이벤트는 정확히 1개만 enqueue됨
    assert len(stub_publisher.events) == 1

    # Then: eventType == "FEEDBACK"
    event = stub_publisher.events[0]
    assert event.event_type == "FEEDBACK"

    # Then: payload 확인
    assert event.payload.feedback == "like"
    assert event.payload.target_conversation_id == "C-TEST-001"
    assert event.payload.target_turn_id == 1


# =============================================================================
# TEST-2: target_turn_id 또는 feedback이 다르면 각각 enqueue
# =============================================================================


def test_different_turn_ids_emit_separately(
    feedback_test_client, stub_publisher, valid_headers
):
    """target_turn_id가 다른 경우는 각각 1개씩 발행 가능."""
    # When: /feedback-different-turns 호출
    response = feedback_test_client.get(
        "/feedback-different-turns", headers=valid_headers
    )

    # Then: 응답 정상
    assert response.status_code == 200
    data = response.json()
    assert data["result1"] is True
    assert data["result2"] is True

    # Then: 이벤트 2개 enqueue됨
    assert len(stub_publisher.events) == 2

    # Then: 각각 다른 target_turn_id
    turn_ids = {e.payload.target_turn_id for e in stub_publisher.events}
    assert turn_ids == {1, 2}


def test_different_feedback_values_emit_separately(
    feedback_test_client, stub_publisher, valid_headers
):
    """feedback 값이 다른 경우는 각각 1개씩 발행 가능."""
    # When: /feedback-different-values 호출
    response = feedback_test_client.get(
        "/feedback-different-values", headers=valid_headers
    )

    # Then: 응답 정상
    assert response.status_code == 200
    data = response.json()
    assert data["result1"] is True
    assert data["result2"] is True

    # Then: 이벤트 2개 enqueue됨
    assert len(stub_publisher.events) == 2

    # Then: 각각 다른 feedback 값
    feedbacks = {e.payload.feedback for e in stub_publisher.events}
    assert feedbacks == {"like", "dislike"}


# =============================================================================
# TEST-3: 필수 헤더 없으면 drop
# =============================================================================


def test_missing_headers_drops(feedback_test_client, stub_publisher):
    """필수 헤더가 없으면 drop된다."""
    # When: 헤더 없이 호출
    response = feedback_test_client.get("/feedback-duplicate")

    # Then: 응답은 정상이지만 이벤트 없음
    assert response.status_code == 200
    assert len(stub_publisher.events) == 0


def test_missing_trace_id_drops(feedback_test_client, stub_publisher, valid_headers):
    """trace_id가 없으면 drop된다."""
    headers = valid_headers.copy()
    del headers["X-Trace-Id"]

    response = feedback_test_client.get("/feedback-duplicate", headers=headers)

    assert response.status_code == 200
    assert len(stub_publisher.events) == 0


def test_missing_user_id_drops(feedback_test_client, stub_publisher, valid_headers):
    """user_id가 없으면 drop된다."""
    headers = valid_headers.copy()
    del headers["X-User-Id"]

    response = feedback_test_client.get("/feedback-duplicate", headers=headers)

    assert response.status_code == 200
    assert len(stub_publisher.events) == 0


def test_missing_dept_id_drops(feedback_test_client, stub_publisher, valid_headers):
    """dept_id가 없으면 drop된다."""
    headers = valid_headers.copy()
    del headers["X-Dept-Id"]

    response = feedback_test_client.get("/feedback-duplicate", headers=headers)

    assert response.status_code == 200
    assert len(stub_publisher.events) == 0


# =============================================================================
# TEST-4: publisher=None이면 drop
# =============================================================================


def test_null_publisher_drops(valid_headers):
    """publisher가 None이면 drop된다."""
    # Given: publisher를 None으로 설정
    set_telemetry_publisher(None)

    # 컨텍스트 수동 설정
    ctx = RequestContext(
        trace_id="trace-123",
        user_id="U-001",
        dept_id="D-TEST",
        conversation_id="C-001",
        turn_id=1,
    )
    set_request_context(ctx)
    reset_feedback_emitted()

    try:
        # When
        result = emit_feedback_event_once(
            feedback="like",
            target_conversation_id="C-TEST-001",
            target_turn_id=1,
        )

        # Then: False 반환 (drop)
        assert result is False

    finally:
        reset_request_context()


# =============================================================================
# TEST-5: 내부 엔드포인트 호출 시 FEEDBACK 이벤트 enqueue + payload에 텍스트 없음 검증
# =============================================================================


def test_internal_endpoint_enqueues_feedback_event(stub_publisher, valid_headers):
    """내부 엔드포인트를 호출하면 FEEDBACK 이벤트가 enqueue된다."""
    from fastapi.testclient import TestClient
    from app.main import app

    # Publisher 설정
    set_telemetry_publisher(stub_publisher)
    stub_publisher.clear()

    client = TestClient(app)

    # 요청 바디에 컨텍스트 정보 포함
    request_body = {
        "feedback": "like",
        "targetConversationId": "C-ENDPOINT-001",
        "targetTurnId": 5,
        "traceId": "trace-endpoint-test",
        "userId": "U-ENDPOINT-001",
        "deptId": "D-ENDPOINT",
        "conversationId": "C-ENDPOINT-001",
        "turnId": 5,
    }

    # When: POST /internal/ai/feedback 호출
    response = client.post(
        "/internal/ai/feedback",
        json=request_body,
    )

    # Then: 202 Accepted
    assert response.status_code == 202
    data = response.json()
    assert data["accepted"] is True

    # Then: FEEDBACK 이벤트가 enqueue됨
    assert len(stub_publisher.events) == 1
    event = stub_publisher.events[0]
    assert event.event_type == "FEEDBACK"

    # Then: payload 확인
    assert event.payload.feedback == "like"
    assert event.payload.target_conversation_id == "C-ENDPOINT-001"
    assert event.payload.target_turn_id == 5


def test_feedback_payload_has_no_text_fields(stub_publisher, valid_headers):
    """payload에 질문/답변 텍스트 필드가 없다."""
    from fastapi.testclient import TestClient
    from app.main import app

    # Publisher 설정
    set_telemetry_publisher(stub_publisher)
    stub_publisher.clear()

    client = TestClient(app)

    request_body = {
        "feedback": "dislike",
        "targetConversationId": "C-TEXT-CHECK",
        "targetTurnId": 1,
        "traceId": "trace-text-check",
        "userId": "U-TEXT-001",
        "deptId": "D-TEXT",
        "conversationId": "C-TEXT-CHECK",
        "turnId": 1,
    }

    # When
    response = client.post("/internal/ai/feedback", json=request_body)

    # Then
    assert response.status_code == 202
    assert len(stub_publisher.events) == 1
    event = stub_publisher.events[0]
    payload = event.payload

    # 텍스트 관련 필드가 없어야 함
    payload_dict = payload.model_dump()
    forbidden_keys = ["question", "answer", "query", "response", "message", "text"]
    for key in forbidden_keys:
        assert key not in payload_dict, f"payload should not contain '{key}'"


# =============================================================================
# 추가 검증: 이벤트 구조 확인
# =============================================================================


def test_event_has_required_ids(feedback_test_client, stub_publisher, valid_headers):
    """이벤트에 필수 ID들이 포함되어 있다."""
    # When
    feedback_test_client.get("/feedback-duplicate", headers=valid_headers)

    # Then
    assert len(stub_publisher.events) == 1
    event = stub_publisher.events[0]

    # 필수 필드 존재
    assert event.event_id is not None
    assert event.trace_id is not None
    assert event.user_id is not None
    assert event.dept_id is not None
    assert event.conversation_id is not None
    assert event.turn_id is not None
    assert event.occurred_at is not None


def test_payload_camel_case_serialization(
    feedback_test_client, stub_publisher, valid_headers
):
    """payload가 camelCase로 직렬화된다."""
    # When
    feedback_test_client.get("/feedback-duplicate", headers=valid_headers)

    # Then
    event = stub_publisher.events[0]

    # model_dump(by_alias=True)로 camelCase 확인
    event_dict = event.model_dump(by_alias=True, mode="json")

    # 필드명이 camelCase
    assert "eventId" in event_dict
    assert "eventType" in event_dict
    assert "traceId" in event_dict
    assert "conversationId" in event_dict
    assert "turnId" in event_dict
    assert "userId" in event_dict
    assert "deptId" in event_dict
    assert "occurredAt" in event_dict

    # payload 내부도 camelCase
    payload = event_dict["payload"]
    assert "feedback" in payload
    assert "targetConversationId" in payload
    assert "targetTurnId" in payload


# =============================================================================
# 샘플 이벤트 출력 (디버깅/확인용)
# =============================================================================


def test_sample_feedback_event_dump(
    feedback_test_client, stub_publisher, valid_headers, capsys
):
    """샘플 FEEDBACK 이벤트를 출력한다."""
    # When
    feedback_test_client.get("/feedback-duplicate", headers=valid_headers)

    # Then
    assert len(stub_publisher.events) == 1
    event = stub_publisher.events[0]

    # model_dump 출력
    import json

    event_dict = event.model_dump(by_alias=True, mode="json")
    print("\n=== Sample FEEDBACK Event (by_alias=True) ===")
    print(json.dumps(event_dict, indent=2, ensure_ascii=False, default=str))

    # 캡처된 출력 확인
    captured = capsys.readouterr()
    assert "eventId" in captured.out
    assert "FEEDBACK" in captured.out
    assert "targetConversationId" in captured.out
    assert "targetTurnId" in captured.out
