"""
Request Context Middleware Tests - A2 Context Propagation

RequestContextMiddleware가 헤더를 올바르게 파싱하여
contextvars에 저장하는지 검증하는 테스트입니다.

테스트 방식:
- 미니 FastAPI 앱을 생성하여 middleware만 붙이고 검증
- 실제 앱 전체를 import하지 않아 의존성 문제 방지

테스트 목록:
- TEST-1: 모든 헤더가 있을 때 컨텍스트가 정확히 저장된다
- TEST-2: 일부 헤더가 없을 때 None으로 처리된다
- TEST-3: X-Turn-Id가 숫자가 아닐 때 None 처리
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.telemetry.context import get_request_context, RequestContext
from app.telemetry.middleware import RequestContextMiddleware


def create_test_app() -> FastAPI:
    """테스트용 미니 FastAPI 앱 생성.

    RequestContextMiddleware만 등록하고,
    컨텍스트 값을 JSON으로 반환하는 엔드포인트를 제공합니다.
    """
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/_ctx")
    def get_context():
        """현재 요청 컨텍스트를 JSON으로 반환."""
        ctx = get_request_context()
        return {
            "trace_id": ctx.trace_id,
            "user_id": ctx.user_id,
            "dept_id": ctx.dept_id,
            "conversation_id": ctx.conversation_id,
            "turn_id": ctx.turn_id,
        }

    return app


# =============================================================================
# TEST-1: 모든 헤더가 있을 때 컨텍스트가 정확히 저장된다
# =============================================================================


def test_all_headers_present_context_stored_correctly():
    """모든 헤더가 있을 때 컨텍스트가 정확히 저장된다."""
    # Given: 테스트 앱 생성
    app = create_test_app()
    client = TestClient(app)

    # Given: 모든 헤더 설정
    headers = {
        "X-Trace-Id": "trace-abc-123",
        "X-User-Id": "U-10293",
        "X-Dept-Id": "D-SALES",
        "X-Conversation-Id": "C-20251230-000123",
        "X-Turn-Id": "7",
    }

    # When: 요청
    response = client.get("/_ctx", headers=headers)

    # Then: 성공 응답
    assert response.status_code == 200

    # Then: 컨텍스트 값 검증
    data = response.json()
    assert data["trace_id"] == "trace-abc-123"
    assert data["user_id"] == "U-10293"
    assert data["dept_id"] == "D-SALES"
    assert data["conversation_id"] == "C-20251230-000123"
    assert data["turn_id"] == 7  # int로 변환됨


def test_turn_id_is_integer():
    """X-Turn-Id가 정수로 변환되는지 검증."""
    # Given: 테스트 앱 생성
    app = create_test_app()
    client = TestClient(app)

    # Given: turn_id가 문자열 "42"
    headers = {
        "X-Trace-Id": "trace-xyz",
        "X-Turn-Id": "42",
    }

    # When: 요청
    response = client.get("/_ctx", headers=headers)

    # Then: turn_id가 정수 42
    data = response.json()
    assert data["turn_id"] == 42
    assert isinstance(data["turn_id"], int)


# =============================================================================
# TEST-2: 일부 헤더가 없을 때 None으로 처리된다
# =============================================================================


def test_missing_headers_are_none():
    """일부 헤더가 없을 때 None으로 처리된다."""
    # Given: 테스트 앱 생성
    app = create_test_app()
    client = TestClient(app)

    # Given: trace/user/dept 헤더 없이 호출
    headers = {
        "X-Conversation-Id": "C-20251230-000456",
        "X-Turn-Id": "3",
    }

    # When: 요청
    response = client.get("/_ctx", headers=headers)

    # Then: 성공 응답
    assert response.status_code == 200

    # Then: 없는 헤더는 null
    data = response.json()
    assert data["trace_id"] is None
    assert data["user_id"] is None
    assert data["dept_id"] is None
    # 있는 헤더는 정상
    assert data["conversation_id"] == "C-20251230-000456"
    assert data["turn_id"] == 3


def test_no_headers_all_none():
    """헤더가 하나도 없을 때 모든 값이 None."""
    # Given: 테스트 앱 생성
    app = create_test_app()
    client = TestClient(app)

    # When: 헤더 없이 요청
    response = client.get("/_ctx")

    # Then: 성공 응답
    assert response.status_code == 200

    # Then: 모든 값이 null
    data = response.json()
    assert data["trace_id"] is None
    assert data["user_id"] is None
    assert data["dept_id"] is None
    assert data["conversation_id"] is None
    assert data["turn_id"] is None


# =============================================================================
# TEST-3: X-Turn-Id가 숫자가 아닐 때 None 처리
# =============================================================================


def test_invalid_turn_id_is_none():
    """X-Turn-Id가 숫자가 아닐 때 None으로 처리된다."""
    # Given: 테스트 앱 생성
    app = create_test_app()
    client = TestClient(app)

    # Given: X-Turn-Id가 "abc" (숫자 아님)
    headers = {
        "X-Trace-Id": "trace-test",
        "X-Turn-Id": "abc",
    }

    # When: 요청
    response = client.get("/_ctx", headers=headers)

    # Then: 성공 응답 (예외 발생 안함)
    assert response.status_code == 200

    # Then: turn_id가 null
    data = response.json()
    assert data["trace_id"] == "trace-test"
    assert data["turn_id"] is None


def test_empty_turn_id_is_none():
    """X-Turn-Id가 빈 문자열일 때 None으로 처리된다."""
    # Given: 테스트 앱 생성
    app = create_test_app()
    client = TestClient(app)

    # Given: X-Turn-Id가 빈 문자열
    headers = {
        "X-Trace-Id": "trace-empty",
        "X-Turn-Id": "",
    }

    # When: 요청
    response = client.get("/_ctx", headers=headers)

    # Then: 성공 응답
    assert response.status_code == 200

    # Then: turn_id가 null
    data = response.json()
    assert data["turn_id"] is None


def test_float_turn_id_is_none():
    """X-Turn-Id가 소수일 때 None으로 처리된다."""
    # Given: 테스트 앱 생성
    app = create_test_app()
    client = TestClient(app)

    # Given: X-Turn-Id가 "3.14" (소수)
    headers = {
        "X-Turn-Id": "3.14",
    }

    # When: 요청
    response = client.get("/_ctx", headers=headers)

    # Then: 성공 응답
    assert response.status_code == 200

    # Then: turn_id가 null (int("3.14")은 ValueError)
    data = response.json()
    assert data["turn_id"] is None


# =============================================================================
# 추가 테스트: 컨텍스트 격리 검증
# =============================================================================


def test_context_isolation_between_requests():
    """요청 간 컨텍스트가 격리되는지 검증."""
    # Given: 테스트 앱 생성
    app = create_test_app()
    client = TestClient(app)

    # When: 첫 번째 요청 (헤더 있음)
    response1 = client.get("/_ctx", headers={"X-Trace-Id": "trace-1"})
    data1 = response1.json()

    # When: 두 번째 요청 (헤더 없음)
    response2 = client.get("/_ctx")
    data2 = response2.json()

    # Then: 첫 번째 요청의 컨텍스트
    assert data1["trace_id"] == "trace-1"

    # Then: 두 번째 요청은 독립적 (이전 요청의 값이 새지 않음)
    assert data2["trace_id"] is None
