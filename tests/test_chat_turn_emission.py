"""
CHAT_TURN Emission Tests - A4 턴당 1회 발행 보장

emit_chat_turn_once의 "턴당 정확히 1개" 발행을 검증합니다.

테스트 목록:
- TEST-1: 동일 요청에서 emit_chat_turn_once를 2번 불러도 enqueue는 1번만 된다
- TEST-2: trace/user/dept 헤더가 없으면 drop된다
- TEST-3: publisher가 None이면 drop된다
- TEST-4: 예외 상황에서도 finally에서 1개 발행된다
- 추가 검증: eventType == "CHAT_TURN", payload에 텍스트 필드 없음
"""

from datetime import datetime
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
    emit_chat_turn_once,
    reset_chat_turn_emitted,
)
from app.telemetry.models import TelemetryEvent
from app.telemetry.publisher import (
    set_telemetry_publisher,
    get_telemetry_publisher,
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


class TestContextMiddleware(BaseHTTPMiddleware):
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
        reset_chat_turn_emitted()

        try:
            response = await call_next(request)
            return response
        finally:
            reset_request_context()


# =============================================================================
# Mini FastAPI App
# =============================================================================


def create_test_app(stub_publisher: StubPublisher) -> FastAPI:
    """테스트용 미니 앱 생성."""
    app = FastAPI()
    app.add_middleware(TestContextMiddleware)

    # Publisher 주입
    set_telemetry_publisher(stub_publisher)

    @app.get("/ok")
    def endpoint_ok():
        """emit_chat_turn_once를 2번 호출하는 엔드포인트."""
        # 첫 번째 호출
        result1 = emit_chat_turn_once(
            intent_main="TEST_INTENT",
            route_type="RAG",
            domain="POLICY",
            rag_used=True,
            latency_ms_total=100,
        )
        # 두 번째 호출 (중복, drop되어야 함)
        result2 = emit_chat_turn_once(
            intent_main="TEST_INTENT_2",
            route_type="LLM_ONLY",
            domain="GENERAL",
            rag_used=False,
            latency_ms_total=200,
        )
        return {"result1": result1, "result2": result2}

    @app.get("/error")
    def endpoint_error():
        """예외 발생 시에도 finally에서 emit."""
        try:
            raise ValueError("Test error")
        except Exception:
            pass
        finally:
            emit_chat_turn_once(
                intent_main="ERROR_INTENT",
                route_type="ERROR",
                domain="ERROR",
                rag_used=False,
                latency_ms_total=50,
                error_code="TEST_ERROR",
            )
        return {"status": "handled"}

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
# TEST-1: 동일 요청에서 emit_chat_turn_once를 2번 불러도 enqueue는 1번만 된다
# =============================================================================


def test_duplicate_emission_blocked(test_client, stub_publisher, valid_headers):
    """동일 요청에서 emit_chat_turn_once를 2번 불러도 enqueue는 1번만 된다."""
    # When: /ok 호출 (내부에서 emit_chat_turn_once 2번 호출)
    response = test_client.get("/ok", headers=valid_headers)

    # Then: 응답 정상
    assert response.status_code == 200
    data = response.json()
    assert data["result1"] is True  # 첫 번째 성공
    assert data["result2"] is False  # 두 번째 중복 방지

    # Then: 이벤트는 정확히 1개만 enqueue됨
    assert len(stub_publisher.events) == 1

    # Then: 첫 번째 호출의 intent가 기록됨
    event = stub_publisher.events[0]
    assert event.payload.intent_main == "TEST_INTENT"


def test_multiple_requests_each_emit_once(test_client, stub_publisher, valid_headers):
    """여러 요청에서 각각 1번씩 emit된다."""
    # When: /ok 3번 호출
    for i in range(3):
        headers = valid_headers.copy()
        headers["X-Trace-Id"] = f"trace-{i}"
        headers["X-Turn-Id"] = str(i + 1)
        test_client.get("/ok", headers=headers)

    # Then: 요청 수만큼 이벤트 생성
    assert len(stub_publisher.events) == 3


# =============================================================================
# TEST-2: trace/user/dept 헤더가 없으면 drop된다
# =============================================================================


def test_missing_trace_id_drops(test_client, stub_publisher, valid_headers):
    """trace_id가 없으면 drop된다."""
    # Given: trace_id 없음
    headers = valid_headers.copy()
    del headers["X-Trace-Id"]

    # When
    response = test_client.get("/ok", headers=headers)

    # Then: 응답은 정상이지만 이벤트 없음
    assert response.status_code == 200
    assert len(stub_publisher.events) == 0


def test_missing_user_id_drops(test_client, stub_publisher, valid_headers):
    """user_id가 없으면 drop된다."""
    # Given: user_id 없음
    headers = valid_headers.copy()
    del headers["X-User-Id"]

    # When
    response = test_client.get("/ok", headers=headers)

    # Then: 이벤트 없음
    assert response.status_code == 200
    assert len(stub_publisher.events) == 0


def test_missing_dept_id_drops(test_client, stub_publisher, valid_headers):
    """dept_id가 없으면 drop된다."""
    # Given: dept_id 없음
    headers = valid_headers.copy()
    del headers["X-Dept-Id"]

    # When
    response = test_client.get("/ok", headers=headers)

    # Then: 이벤트 없음
    assert response.status_code == 200
    assert len(stub_publisher.events) == 0


def test_missing_conversation_id_drops(test_client, stub_publisher, valid_headers):
    """conversation_id가 없으면 drop된다."""
    # Given: conversation_id 없음
    headers = valid_headers.copy()
    del headers["X-Conversation-Id"]

    # When
    response = test_client.get("/ok", headers=headers)

    # Then: 이벤트 없음
    assert response.status_code == 200
    assert len(stub_publisher.events) == 0


def test_missing_turn_id_drops(test_client, stub_publisher, valid_headers):
    """turn_id가 없으면 drop된다."""
    # Given: turn_id 없음
    headers = valid_headers.copy()
    del headers["X-Turn-Id"]

    # When
    response = test_client.get("/ok", headers=headers)

    # Then: 이벤트 없음
    assert response.status_code == 200
    assert len(stub_publisher.events) == 0


def test_all_headers_missing_drops(test_client, stub_publisher):
    """모든 헤더가 없으면 drop된다."""
    # When: 헤더 없이 호출
    response = test_client.get("/ok")

    # Then: 이벤트 없음
    assert response.status_code == 200
    assert len(stub_publisher.events) == 0


# =============================================================================
# TEST-3: publisher가 None이면 drop된다
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
    reset_chat_turn_emitted()

    try:
        # When
        result = emit_chat_turn_once(
            intent_main="TEST",
            route_type="RAG",
            domain="POLICY",
            rag_used=True,
            latency_ms_total=100,
        )

        # Then: False 반환 (drop)
        assert result is False

    finally:
        reset_request_context()


# =============================================================================
# TEST-4: 예외 상황에서도 finally에서 1개 발행된다
# =============================================================================


def test_exception_emits_in_finally(test_client, stub_publisher, valid_headers):
    """예외 상황에서도 finally에서 1개 발행된다."""
    # When: /error 호출 (내부에서 예외 발생 후 finally에서 emit)
    response = test_client.get("/error", headers=valid_headers)

    # Then: 응답 정상
    assert response.status_code == 200

    # Then: 이벤트 1개 발행됨
    assert len(stub_publisher.events) == 1

    # Then: error_code가 포함됨
    event = stub_publisher.events[0]
    assert event.payload.error_code == "TEST_ERROR"
    assert event.payload.intent_main == "ERROR_INTENT"


# =============================================================================
# 추가 검증: eventType, payload 구조
# =============================================================================


def test_event_type_is_chat_turn(test_client, stub_publisher, valid_headers):
    """eventType이 CHAT_TURN이다."""
    # When
    test_client.get("/ok", headers=valid_headers)

    # Then
    assert len(stub_publisher.events) == 1
    event = stub_publisher.events[0]
    assert event.event_type == "CHAT_TURN"


def test_payload_has_no_text_fields(test_client, stub_publisher, valid_headers):
    """payload에 질문/답변 텍스트 필드가 없다."""
    # When
    test_client.get("/ok", headers=valid_headers)

    # Then
    event = stub_publisher.events[0]
    payload = event.payload

    # 질문/답변 관련 필드가 없어야 함
    payload_dict = payload.model_dump()
    forbidden_keys = ["question", "answer", "query", "response", "message", "text"]
    for key in forbidden_keys:
        assert key not in payload_dict, f"payload should not contain '{key}'"


def test_event_has_required_ids(test_client, stub_publisher, valid_headers):
    """이벤트에 필수 ID들이 포함되어 있다."""
    # When
    test_client.get("/ok", headers=valid_headers)

    # Then
    event = stub_publisher.events[0]

    # 필수 필드 존재
    assert event.event_id is not None
    assert event.trace_id is not None
    assert event.user_id is not None
    assert event.dept_id is not None
    assert event.conversation_id is not None
    assert event.turn_id is not None
    assert event.occurred_at is not None


def test_payload_camel_case_serialization(test_client, stub_publisher, valid_headers):
    """payload가 camelCase로 직렬화된다."""
    # When
    test_client.get("/ok", headers=valid_headers)

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
    assert "intentMain" in payload
    assert "routeType" in payload
    assert "ragUsed" in payload
    assert "latencyMsTotal" in payload
    assert "piiDetectedInput" in payload
    assert "piiDetectedOutput" in payload


# =============================================================================
# 샘플 이벤트 출력 (디버깅/확인용)
# =============================================================================


def test_sample_event_dump(test_client, stub_publisher, valid_headers, capsys):
    """샘플 TelemetryEvent를 출력한다."""
    # When
    test_client.get("/ok", headers=valid_headers)

    # Then
    assert len(stub_publisher.events) == 1
    event = stub_publisher.events[0]

    # model_dump 출력
    import json

    event_dict = event.model_dump(by_alias=True, mode="json")
    print("\n=== Sample TelemetryEvent (by_alias=True) ===")
    print(json.dumps(event_dict, indent=2, ensure_ascii=False, default=str))

    # 캡처된 출력 확인
    captured = capsys.readouterr()
    assert "eventId" in captured.out
    assert "CHAT_TURN" in captured.out
