"""
SECURITY Event Emission Tests - A5 요청 단위 Dedup 보장

emit_security_event_once의 "요청 단위 중복 방지"를 검증합니다.

테스트 목록:
- TEST-1: 동일 요청에서 동일 (blockType, ruleId) 2회 호출해도 event는 1개
- TEST-2: ruleId가 다른 경우는 각각 1개씩 발행 가능
- TEST-3: 필수 헤더 없으면 drop
- TEST-4: publisher가 None이면 drop
- 추가 검증: eventType == "SECURITY", payload 구조 확인
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
    emit_security_event_once,
    reset_security_emitted,
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


class SecurityTestContextMiddleware(BaseHTTPMiddleware):
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
        reset_security_emitted()

        try:
            response = await call_next(request)
            return response
        finally:
            reset_request_context()


# =============================================================================
# Mini FastAPI App
# =============================================================================


def create_security_test_app(stub_publisher: StubPublisher) -> FastAPI:
    """테스트용 미니 앱 생성."""
    app = FastAPI()
    app.add_middleware(SecurityTestContextMiddleware)

    # Publisher 주입
    set_telemetry_publisher(stub_publisher)

    @app.get("/block")
    def endpoint_block():
        """emit_security_event_once를 같은 인자로 2번 호출하는 엔드포인트."""
        # 첫 번째 호출
        result1 = emit_security_event_once(
            block_type="PII_BLOCK",
            blocked=True,
            rule_id="RULE-001",
        )
        # 두 번째 호출 (중복, drop되어야 함)
        result2 = emit_security_event_once(
            block_type="PII_BLOCK",
            blocked=True,
            rule_id="RULE-001",
        )
        return {"result1": result1, "result2": result2}

    @app.get("/multi-rules")
    def endpoint_multi_rules():
        """다른 ruleId로 emit."""
        result1 = emit_security_event_once(
            block_type="PII_BLOCK",
            blocked=True,
            rule_id="R1",
        )
        result2 = emit_security_event_once(
            block_type="PII_BLOCK",
            blocked=True,
            rule_id="R2",
        )
        return {"result1": result1, "result2": result2}

    @app.get("/external-domain")
    def endpoint_external_domain():
        """EXTERNAL_DOMAIN_BLOCK 이벤트 발행."""
        result = emit_security_event_once(
            block_type="EXTERNAL_DOMAIN_BLOCK",
            blocked=True,
            rule_id="EXT-RULE-001",
        )
        return {"result": result}

    @app.get("/noheaders")
    def endpoint_noheaders():
        """헤더 없이 호출 -> drop."""
        result = emit_security_event_once(
            block_type="PII_BLOCK",
            blocked=True,
            rule_id="RULE-001",
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
def security_test_client(stub_publisher):
    """테스트 클라이언트 생성."""
    app = create_security_test_app(stub_publisher)
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
# TEST-1: 동일 요청에서 동일 (blockType, ruleId) 2회 호출해도 event는 1개
# =============================================================================


def test_duplicate_security_emission_blocked(
    security_test_client, stub_publisher, valid_headers
):
    """동일 요청에서 동일 (blockType, ruleId) 2회 호출해도 event는 1개."""
    # When: /block 호출 (내부에서 emit_security_event_once 2번 호출)
    response = security_test_client.get("/block", headers=valid_headers)

    # Then: 응답 정상
    assert response.status_code == 200
    data = response.json()
    assert data["result1"] is True  # 첫 번째 성공
    assert data["result2"] is False  # 두 번째 중복 방지

    # Then: 이벤트는 정확히 1개만 enqueue됨
    assert len(stub_publisher.events) == 1

    # Then: eventType == "SECURITY"
    event = stub_publisher.events[0]
    assert event.event_type == "SECURITY"

    # Then: payload 확인
    assert event.payload.block_type == "PII_BLOCK"
    assert event.payload.blocked is True
    assert event.payload.rule_id == "RULE-001"


# =============================================================================
# TEST-2: ruleId가 다른 경우는 각각 1개씩 발행 가능
# =============================================================================


def test_different_rule_ids_emit_separately(
    security_test_client, stub_publisher, valid_headers
):
    """ruleId가 다른 경우는 각각 1개씩 발행 가능."""
    # When: /multi-rules 호출
    response = security_test_client.get("/multi-rules", headers=valid_headers)

    # Then: 응답 정상
    assert response.status_code == 200
    data = response.json()
    assert data["result1"] is True
    assert data["result2"] is True

    # Then: 이벤트 2개 enqueue됨
    assert len(stub_publisher.events) == 2

    # Then: 각각 다른 rule_id
    rule_ids = {e.payload.rule_id for e in stub_publisher.events}
    assert rule_ids == {"R1", "R2"}


# =============================================================================
# TEST-3: 필수 헤더 없으면 drop
# =============================================================================


def test_missing_headers_drops(security_test_client, stub_publisher):
    """필수 헤더가 없으면 drop된다."""
    # When: 헤더 없이 호출
    response = security_test_client.get("/block")

    # Then: 응답은 정상이지만 이벤트 없음
    assert response.status_code == 200
    assert len(stub_publisher.events) == 0


def test_missing_trace_id_drops(security_test_client, stub_publisher, valid_headers):
    """trace_id가 없으면 drop된다."""
    headers = valid_headers.copy()
    del headers["X-Trace-Id"]

    response = security_test_client.get("/block", headers=headers)

    assert response.status_code == 200
    assert len(stub_publisher.events) == 0


def test_missing_user_id_drops(security_test_client, stub_publisher, valid_headers):
    """user_id가 없으면 drop된다."""
    headers = valid_headers.copy()
    del headers["X-User-Id"]

    response = security_test_client.get("/block", headers=headers)

    assert response.status_code == 200
    assert len(stub_publisher.events) == 0


def test_missing_dept_id_drops(security_test_client, stub_publisher, valid_headers):
    """dept_id가 없으면 drop된다."""
    headers = valid_headers.copy()
    del headers["X-Dept-Id"]

    response = security_test_client.get("/block", headers=headers)

    assert response.status_code == 200
    assert len(stub_publisher.events) == 0


# =============================================================================
# TEST-4: publisher가 None이면 drop
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
    reset_security_emitted()

    try:
        # When
        result = emit_security_event_once(
            block_type="PII_BLOCK",
            blocked=True,
            rule_id="RULE-001",
        )

        # Then: False 반환 (drop)
        assert result is False

    finally:
        reset_request_context()


# =============================================================================
# 추가 검증: EXTERNAL_DOMAIN_BLOCK 이벤트
# =============================================================================


def test_external_domain_block_event(
    security_test_client, stub_publisher, valid_headers
):
    """EXTERNAL_DOMAIN_BLOCK 이벤트 발행."""
    # When
    response = security_test_client.get("/external-domain", headers=valid_headers)

    # Then
    assert response.status_code == 200
    data = response.json()
    assert data["result"] is True

    # Then: 이벤트 1개
    assert len(stub_publisher.events) == 1
    event = stub_publisher.events[0]

    # Then: payload 확인
    assert event.event_type == "SECURITY"
    assert event.payload.block_type == "EXTERNAL_DOMAIN_BLOCK"
    assert event.payload.blocked is True
    assert event.payload.rule_id == "EXT-RULE-001"


# =============================================================================
# 추가 검증: payload 구조 / camelCase 직렬화
# =============================================================================


def test_event_has_required_ids(security_test_client, stub_publisher, valid_headers):
    """이벤트에 필수 ID들이 포함되어 있다."""
    # When
    security_test_client.get("/block", headers=valid_headers)

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
    security_test_client, stub_publisher, valid_headers
):
    """payload가 camelCase로 직렬화된다."""
    # When
    security_test_client.get("/block", headers=valid_headers)

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
    assert "blockType" in payload
    assert "blocked" in payload
    assert "ruleId" in payload


def test_payload_has_no_text_fields(security_test_client, stub_publisher, valid_headers):
    """payload에 질문/답변 텍스트 필드가 없다."""
    # When
    security_test_client.get("/block", headers=valid_headers)

    # Then
    event = stub_publisher.events[0]
    payload = event.payload

    # 텍스트 관련 필드가 없어야 함
    payload_dict = payload.model_dump()
    forbidden_keys = ["question", "answer", "query", "response", "message", "text"]
    for key in forbidden_keys:
        assert key not in payload_dict, f"payload should not contain '{key}'"


# =============================================================================
# 샘플 이벤트 출력 (디버깅/확인용)
# =============================================================================


def test_sample_security_event_dump(
    security_test_client, stub_publisher, valid_headers, capsys
):
    """샘플 SECURITY 이벤트를 출력한다."""
    # When
    security_test_client.get("/block", headers=valid_headers)

    # Then
    assert len(stub_publisher.events) == 1
    event = stub_publisher.events[0]

    # model_dump 출력
    import json

    event_dict = event.model_dump(by_alias=True, mode="json")
    print("\n=== Sample SECURITY Event (by_alias=True) ===")
    print(json.dumps(event_dict, indent=2, ensure_ascii=False, default=str))

    # 캡처된 출력 확인
    captured = capsys.readouterr()
    assert "eventId" in captured.out
    assert "SECURITY" in captured.out
    assert "blockType" in captured.out
