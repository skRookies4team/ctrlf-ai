"""
Telemetry Publisher Tests - A3 Async Queue + Batch Flush

TelemetryPublisher의 비동기 배치 전송 및 에러 핸들링을 검증합니다.

테스트 목록:
- TEST-1: enabled=False면 enqueue가 False 반환 + 전송 호출 0회
- TEST-2: batch_size 트리거로 배치 전송된다
- TEST-3: queue 포화 시 drop
- TEST-4: 전송 실패 시 retry_once=1회만 수행 후 drop
- TEST-5: 전송 성공 시 반환값/큐 drain 검증
"""

import json
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from app.telemetry.models import (
    ChatTurnPayload,
    TelemetryEvent,
)
from app.telemetry.publisher import TelemetryPublisher


def create_test_event(
    trace_id: str = "trace-123",
    user_id: str = "U-001",
    dept_id: str = "D-SALES",
) -> TelemetryEvent:
    """테스트용 TelemetryEvent 생성."""
    payload = ChatTurnPayload(
        intent_main="TEST_INTENT",
        route_type="RAG",
        domain="POLICY",
        rag_used=True,
        latency_ms_total=100,
        pii_detected_input=False,
        pii_detected_output=False,
    )
    return TelemetryEvent(
        event_id=uuid4(),
        event_type="CHAT_TURN",
        trace_id=trace_id,
        conversation_id="C-TEST-001",
        turn_id=1,
        user_id=user_id,
        dept_id=dept_id,
        occurred_at=datetime.now(),
        payload=payload,
    )


# =============================================================================
# TEST-1: enabled=False면 enqueue가 False 반환 + 전송 호출 0회
# =============================================================================


@pytest.mark.asyncio
async def test_disabled_publisher_enqueue_returns_false():
    """enabled=False면 enqueue가 False 반환."""
    # Given: disabled publisher
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock()

    publisher = TelemetryPublisher(
        backend_base_url="http://test.local",
        internal_token="test-token",
        enabled=False,  # 비활성화
        http_client=mock_client,
    )

    # When: enqueue 시도
    event = create_test_event()
    result = publisher.enqueue(event)

    # Then: False 반환
    assert result is False


@pytest.mark.asyncio
async def test_disabled_publisher_flush_no_calls():
    """enabled=False면 flush_now 호출해도 전송 0회."""
    # Given: disabled publisher
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=httpx.Response(200))

    publisher = TelemetryPublisher(
        backend_base_url="http://test.local",
        internal_token="test-token",
        enabled=False,
        http_client=mock_client,
    )
    await publisher.start()

    try:
        # When: enqueue + flush_now
        for _ in range(5):
            publisher.enqueue(create_test_event())

        flushed = await publisher.flush_now()

        # Then: 전송 0회
        assert flushed == 0
        mock_client.post.assert_not_called()

    finally:
        await publisher.stop()


# =============================================================================
# TEST-2: batch_size 트리거로 배치 전송된다
# =============================================================================


@pytest.mark.asyncio
async def test_batch_size_triggers_batch_send():
    """batch_size=3일 때 이벤트 3개 enqueue 후 flush_now하면 배치 전송."""
    # Given: batch_size=3인 publisher
    post_calls: list[dict[str, Any]] = []

    async def mock_post(url: str, json: dict, **kwargs) -> httpx.Response:
        post_calls.append({"url": url, "json": json})
        return httpx.Response(200)

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = mock_post

    publisher = TelemetryPublisher(
        backend_base_url="http://test.local",
        internal_token="test-token",
        enabled=True,
        batch_size=3,
        flush_sec=999,  # 주기 flush 방지
        http_client=mock_client,
    )
    await publisher.start()

    try:
        # When: 이벤트 3개 enqueue
        for i in range(3):
            result = publisher.enqueue(create_test_event(trace_id=f"trace-{i}"))
            assert result is True

        # When: flush_now 호출
        flushed = await publisher.flush_now()

        # Then: 3개 전송됨
        assert flushed == 3

        # Then: POST 호출 1회
        assert len(post_calls) == 1

        # Then: body에 events 길이가 3
        body = post_calls[0]["json"]
        assert "events" in body
        assert len(body["events"]) == 3

        # Then: envelope 구조 확인
        assert body["source"] == "ai-gateway"
        assert "sentAt" in body

        # Then: 각 이벤트의 키 확인 (camelCase alias)
        event0 = body["events"][0]
        assert "eventId" in event0
        assert "eventType" in event0
        assert "traceId" in event0
        assert "payload" in event0

        # 실제 전송 JSON 출력 (TEST-2 요구사항)
        print("\n=== TEST-2: 전송된 JSON payload ===")
        print(f"events 수: {len(body['events'])}")
        print(f"source: {body['source']}")
        print(f"첫 번째 이벤트 키: {list(event0.keys())}")
        print(f"payload 키: {list(event0['payload'].keys())}")

    finally:
        await publisher.stop()


# =============================================================================
# TEST-3: queue 포화 시 drop
# =============================================================================


@pytest.mark.asyncio
async def test_queue_overflow_drops_event():
    """max_queue_size=2일 때 3번째 enqueue는 drop."""
    # Given: max_queue_size=2인 publisher
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=httpx.Response(200))

    publisher = TelemetryPublisher(
        backend_base_url="http://test.local",
        internal_token="test-token",
        enabled=True,
        max_queue_size=2,
        http_client=mock_client,
    )

    # When: 이벤트 3개 enqueue
    result1 = publisher.enqueue(create_test_event(trace_id="trace-1"))
    result2 = publisher.enqueue(create_test_event(trace_id="trace-2"))
    result3 = publisher.enqueue(create_test_event(trace_id="trace-3"))

    # Then: 처음 2개는 성공, 3번째는 drop
    assert result1 is True
    assert result2 is True
    assert result3 is False  # drop


@pytest.mark.asyncio
async def test_queue_overflow_no_exception():
    """큐 포화 시에도 예외 발생 없음."""
    # Given: max_queue_size=1인 publisher
    mock_client = MagicMock(spec=httpx.AsyncClient)

    publisher = TelemetryPublisher(
        backend_base_url="http://test.local",
        internal_token="test-token",
        enabled=True,
        max_queue_size=1,
        http_client=mock_client,
    )

    # When/Then: 예외 없이 동작
    try:
        for i in range(100):  # 대량 enqueue
            publisher.enqueue(create_test_event(trace_id=f"trace-{i}"))
    except Exception as e:
        pytest.fail(f"Exception should not be raised: {e}")


# =============================================================================
# TEST-4: 전송 실패 시 retry_once=1회만 수행 후 drop
# =============================================================================


@pytest.mark.asyncio
async def test_retry_once_on_failure():
    """전송 실패 시 retry_once=True면 정확히 2회 호출 후 drop."""
    # Given: 항상 500 응답 반환하는 mock
    call_count = 0

    async def mock_post_fail(url: str, json: dict, **kwargs) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(500)

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = mock_post_fail

    publisher = TelemetryPublisher(
        backend_base_url="http://test.local",
        internal_token="test-token",
        enabled=True,
        retry_once=True,
        http_client=mock_client,
    )
    await publisher.start()

    try:
        # When: 이벤트 enqueue + flush
        publisher.enqueue(create_test_event())
        await publisher.flush_now()

        # Then: 정확히 2회 호출 (원본 + 재시도)
        assert call_count == 2

    finally:
        await publisher.stop()


@pytest.mark.asyncio
async def test_retry_disabled_single_call():
    """retry_once=False면 실패 시 1회만 호출."""
    # Given: 항상 500 응답 반환하는 mock
    call_count = 0

    async def mock_post_fail(url: str, json: dict, **kwargs) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(500)

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = mock_post_fail

    publisher = TelemetryPublisher(
        backend_base_url="http://test.local",
        internal_token="test-token",
        enabled=True,
        retry_once=False,  # 재시도 비활성화
        http_client=mock_client,
    )
    await publisher.start()

    try:
        # When: 이벤트 enqueue + flush
        publisher.enqueue(create_test_event())
        await publisher.flush_now()

        # Then: 정확히 1회만 호출
        assert call_count == 1

    finally:
        await publisher.stop()


@pytest.mark.asyncio
async def test_failure_does_not_raise_exception():
    """전송 실패 시 예외가 밖으로 전파되지 않음."""
    # Given: 항상 500 응답 반환하는 mock
    async def mock_post_fail(url: str, json: dict, **kwargs) -> httpx.Response:
        return httpx.Response(500)

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = mock_post_fail

    publisher = TelemetryPublisher(
        backend_base_url="http://test.local",
        internal_token="test-token",
        enabled=True,
        retry_once=True,
        http_client=mock_client,
    )
    await publisher.start()

    try:
        # When/Then: 예외 없이 실행
        publisher.enqueue(create_test_event())
        await publisher.flush_now()  # 예외 없어야 함

    except Exception as e:
        pytest.fail(f"Exception should not be raised: {e}")

    finally:
        await publisher.stop()


# =============================================================================
# TEST-5: 전송 성공 시 반환값/큐 drain 검증
# =============================================================================


@pytest.mark.asyncio
async def test_success_returns_event_count():
    """전송 성공 시 flush_now 반환값이 이벤트 수와 일치."""
    # Given: 200 응답 반환하는 mock
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=httpx.Response(200))

    publisher = TelemetryPublisher(
        backend_base_url="http://test.local",
        internal_token="test-token",
        enabled=True,
        batch_size=10,  # 배치보다 적게 enqueue
        http_client=mock_client,
    )
    await publisher.start()

    try:
        # When: 이벤트 5개 enqueue
        for i in range(5):
            publisher.enqueue(create_test_event(trace_id=f"trace-{i}"))

        # When: flush_now 호출
        flushed = await publisher.flush_now()

        # Then: 반환값 = 5
        assert flushed == 5

    finally:
        await publisher.stop()


@pytest.mark.asyncio
async def test_queue_drained_after_flush():
    """flush 후 큐가 비어있음."""
    # Given: 200 응답 반환하는 mock
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=httpx.Response(200))

    publisher = TelemetryPublisher(
        backend_base_url="http://test.local",
        internal_token="test-token",
        enabled=True,
        batch_size=10,
        http_client=mock_client,
    )
    await publisher.start()

    try:
        # When: 이벤트 enqueue + flush
        for i in range(3):
            publisher.enqueue(create_test_event(trace_id=f"trace-{i}"))

        first_flush = await publisher.flush_now()
        assert first_flush == 3

        # When: 재호출
        second_flush = await publisher.flush_now()

        # Then: 큐 비어있음 → 0 반환
        assert second_flush == 0

    finally:
        await publisher.stop()


@pytest.mark.asyncio
async def test_correct_url_and_headers():
    """전송 시 올바른 URL과 헤더 사용."""
    # Given
    captured_calls: list[dict] = []

    async def mock_post(url: str, json: dict, headers: dict, **kwargs) -> httpx.Response:
        captured_calls.append({"url": url, "headers": headers, "json": json})
        return httpx.Response(200)

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = mock_post

    publisher = TelemetryPublisher(
        backend_base_url="http://backend.test",
        internal_token="secret-token-123",
        enabled=True,
        http_client=mock_client,
    )
    await publisher.start()

    try:
        # When
        publisher.enqueue(create_test_event())
        await publisher.flush_now()

        # Then: URL 확인
        assert len(captured_calls) == 1
        assert captured_calls[0]["url"] == "http://backend.test/internal/telemetry/events"

        # Then: 헤더 확인
        headers = captured_calls[0]["headers"]
        assert headers["X-Internal-Token"] == "secret-token-123"
        assert headers["Content-Type"] == "application/json"

    finally:
        await publisher.stop()


# =============================================================================
# 추가 테스트: start/stop lifecycle
# =============================================================================


@pytest.mark.asyncio
async def test_stop_flushes_remaining_events():
    """stop 시 남은 이벤트를 best-effort로 flush 시도."""
    # Given
    flushed_events: list[dict] = []

    async def mock_post(url: str, json: dict, **kwargs) -> httpx.Response:
        flushed_events.append(json)
        return httpx.Response(200)

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.post = mock_post

    publisher = TelemetryPublisher(
        backend_base_url="http://test.local",
        internal_token="test-token",
        enabled=True,
        batch_size=100,  # 배치 사이즈보다 적게 enqueue
        flush_sec=999,  # 자동 flush 방지
        http_client=mock_client,
    )
    await publisher.start()

    # When: 이벤트 enqueue (flush 안 함)
    for i in range(3):
        publisher.enqueue(create_test_event(trace_id=f"trace-{i}"))

    # When: stop 호출 (남은 이벤트 drain)
    await publisher.stop()

    # Then: stop 시 남은 이벤트 전송됨
    assert len(flushed_events) == 1
    assert len(flushed_events[0]["events"]) == 3
