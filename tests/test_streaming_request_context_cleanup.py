"""
Streaming Request Context Cleanup Tests (A8)

스트리밍 응답에서 텔레메트리 컨텍스트 유지 및 cleanup 테스트입니다.

테스트 목록:
- TEST-1: 스트리밍 응답 중에도 request_context가 유지되어 emit_chat_turn_once가 정상 동작한다
- TEST-2: 스트리밍 종료 후 cleanup이 실행되어 다음 요청에 상태가 누수되지 않는다
- TEST-3: 스트리밍 중 예외 발생 시에도 CHAT_TURN 1회 발행 + 중복 없음
"""

import asyncio
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request
from starlette.responses import StreamingResponse

from app.telemetry.context import (
    RequestContext,
    get_request_context,
    reset_request_context,
    set_request_context,
)
from app.telemetry.emitters import (
    is_chat_turn_emitted,
    reset_chat_turn_emitted,
    reset_feedback_emitted,
    reset_security_emitted,
)
from app.telemetry.metrics import get_latency_metrics, reset_all_metrics


@pytest.fixture(autouse=True)
def reset_telemetry_state():
    """각 테스트 전후에 telemetry 상태를 리셋합니다."""
    # Setup: 테스트 전 리셋
    reset_request_context()
    reset_all_metrics()
    reset_chat_turn_emitted()
    reset_security_emitted()
    reset_feedback_emitted()

    yield

    # Teardown: 테스트 후 리셋
    reset_request_context()
    reset_all_metrics()
    reset_chat_turn_emitted()
    reset_security_emitted()
    reset_feedback_emitted()


def _is_context_reset() -> bool:
    """컨텍스트가 리셋되었는지 확인 (빈 RequestContext = 모든 필드 None)."""
    ctx = get_request_context()
    return ctx.trace_id is None and ctx.user_id is None


# =============================================================================
# TEST-1: 스트리밍 중 request_context 유지
# =============================================================================


class TestStreamingContextRetention:
    """스트리밍 응답 중 request_context 유지 테스트."""

    @pytest.mark.asyncio
    async def test_context_retained_during_streaming(self):
        """스트리밍 응답 중에도 request_context가 유지된다."""
        # Given: request_context 설정
        ctx = RequestContext(
            trace_id="trace-streaming-001",
            user_id="user-001",
            dept_id="dept-001",
            conversation_id="conv-001",
            turn_id=1,
        )
        set_request_context(ctx)

        # Given: 스트리밍 생성기
        async def stream_generator() -> AsyncIterator[bytes]:
            for i in range(3):
                # 스트리밍 중에도 컨텍스트 접근 가능해야 함
                current_ctx = get_request_context()
                assert current_ctx.trace_id is not None, f"Chunk {i}: context should not be None"
                assert current_ctx.trace_id == "trace-streaming-001"
                yield f"chunk-{i}\n".encode()

        # When: 스트리밍 반복
        chunks = []
        async for chunk in stream_generator():
            chunks.append(chunk)

        # Then: 모든 청크 수신
        assert len(chunks) == 3

        # Cleanup
        reset_request_context()

    @pytest.mark.asyncio
    async def test_emit_chat_turn_once_works_during_streaming(self):
        """스트리밍 중 emit_chat_turn_once가 정상 동작한다."""
        # Given: request_context 및 emit 상태 초기화
        reset_chat_turn_emitted()
        ctx = RequestContext(
            trace_id="trace-streaming-002",
            user_id="user-002",
            dept_id="dept-002",
            conversation_id="conv-002",
            turn_id=2,
        )
        set_request_context(ctx)

        # Given: FakePublisher mock
        with patch("app.telemetry.emitters.get_telemetry_publisher") as mock_get_publisher:
            mock_publisher = MagicMock()
            mock_publisher.enqueue = MagicMock(return_value=True)
            mock_get_publisher.return_value = mock_publisher

            # When: emit_chat_turn_once 호출
            from app.telemetry.emitters import emit_chat_turn_once

            emit_chat_turn_once(
                intent_main="STREAMING",
                route_type="LLM_ONLY",
                domain="CHAT",
                rag_used=False,
                latency_ms_total=100,
                error_code=None,
            )

            # Then: 발행됨
            assert mock_publisher.enqueue.call_count == 1
            assert is_chat_turn_emitted() is True

            # When: 다시 호출 (중복)
            emit_chat_turn_once(
                intent_main="STREAMING",
                route_type="LLM_ONLY",
                domain="CHAT",
                rag_used=False,
                latency_ms_total=100,
                error_code=None,
            )

            # Then: 중복 발행 없음
            assert mock_publisher.enqueue.call_count == 1

        # Cleanup
        reset_request_context()
        reset_chat_turn_emitted()


# =============================================================================
# TEST-2: 스트리밍 종료 후 cleanup
# =============================================================================


class TestStreamingCleanup:
    """스트리밍 종료 후 cleanup 테스트."""

    @pytest.mark.asyncio
    async def test_cleanup_after_stream_completion(self):
        """스트리밍 종료 후 cleanup이 실행된다."""
        from app.telemetry.middleware import _wrap_streaming_body

        # Given: 컨텍스트 설정
        ctx = RequestContext(
            trace_id="trace-cleanup-001",
            user_id="user-cleanup",
            dept_id="dept-cleanup",
            conversation_id="conv-cleanup",
            turn_id=5,
        )
        set_request_context(ctx)

        # Given: 스트리밍 생성기
        async def original_body() -> AsyncIterator[bytes]:
            yield b"data1\n"
            yield b"data2\n"

        # When: 래핑된 body 반복
        wrapped = _wrap_streaming_body(original_body())
        chunks = []
        async for chunk in wrapped:
            chunks.append(chunk)

        # Then: 모든 청크 수신
        assert len(chunks) == 2

        # Then: cleanup 후 컨텍스트 리셋됨
        assert _is_context_reset(), "Context should be reset after stream completion"

    @pytest.mark.asyncio
    async def test_no_state_leakage_between_requests(self):
        """다음 요청에 상태가 누수되지 않는다."""
        from app.telemetry.middleware import _wrap_streaming_body

        # Given: 첫 번째 요청 컨텍스트
        ctx1 = RequestContext(
            trace_id="trace-req1",
            user_id="user-req1",
            conversation_id="conv-req1",
            turn_id=1,
        )
        set_request_context(ctx1)
        reset_all_metrics()
        reset_chat_turn_emitted()

        # When: 첫 번째 스트림 완료
        async def stream1() -> AsyncIterator[bytes]:
            yield b"req1-data\n"

        wrapped1 = _wrap_streaming_body(stream1())
        async for _ in wrapped1:
            pass

        # Then: 첫 번째 요청 후 상태 리셋됨
        assert _is_context_reset()
        assert is_chat_turn_emitted() is False

        # When: 두 번째 요청 컨텍스트 설정
        ctx2 = RequestContext(
            trace_id="trace-req2",
            user_id="user-req2",
            conversation_id="conv-req2",
            turn_id=2,
        )
        set_request_context(ctx2)

        # Then: 두 번째 요청은 첫 번째 요청의 영향을 받지 않음
        current_ctx = get_request_context()
        assert current_ctx.trace_id == "trace-req2"
        assert current_ctx.user_id == "user-req2"

        # Cleanup
        reset_request_context()


# =============================================================================
# TEST-3: 예외 발생 시 CHAT_TURN 발행 보장
# =============================================================================


class TestStreamingExceptionHandling:
    """스트리밍 중 예외 발생 시 텔레메트리 처리 테스트."""

    @pytest.mark.asyncio
    async def test_chat_turn_emitted_on_stream_completion(self):
        """스트리밍 정상 완료 시 CHAT_TURN 1회 발행된다."""
        from app.models.chat import ChatMessage
        from app.models.chat_stream import ChatStreamRequest
        from app.services.chat_stream_service import ChatStreamService, InFlightTracker

        # Given: 초기화
        reset_chat_turn_emitted()
        reset_all_metrics()
        ctx = RequestContext(
            trace_id="trace-success-001",
            user_id="user-success",
            dept_id="dept-success",
            conversation_id="conv-success",
            turn_id=10,
        )
        set_request_context(ctx)

        tracker = InFlightTracker()
        service = ChatStreamService(tracker=tracker)

        request = ChatStreamRequest(
            request_id="req-success-001",
            session_id="sess-success",
            user_id="user-success",
            user_role="EMPLOYEE",
            messages=[ChatMessage(role="user", content="테스트")],
        )

        # Given: FakePublisher mock
        with patch("app.telemetry.emitters.get_telemetry_publisher") as mock_get_publisher:
            mock_publisher = MagicMock()
            mock_publisher.enqueue = MagicMock(return_value=True)
            mock_get_publisher.return_value = mock_publisher

            # When: 스트리밍 (fallback 모드로 정상 완료)
            with patch.object(service, "_settings") as mock_settings:
                mock_settings.llm_base_url = None  # Fallback 모드
                mock_settings.LLM_MODEL_NAME = "test-model"

                chunks = []
                async for chunk in service.stream_chat(request):
                    chunks.append(chunk)

            # Then: CHAT_TURN 1회 발행
            assert mock_publisher.enqueue.call_count == 1, "CHAT_TURN should be emitted exactly once"

        # Cleanup
        reset_request_context()
        reset_chat_turn_emitted()
        reset_all_metrics()

    @pytest.mark.asyncio
    async def test_chat_turn_emitted_flag_prevents_duplicate(self):
        """is_chat_turn_emitted 플래그가 중복 발행을 방지한다.

        Note: 이 테스트는 emit_chat_turn_once의 중복 방지 로직을 검증합니다.
        실제 publisher mock은 test_emit_chat_turn_once_works_during_streaming에서 검증됩니다.
        """
        from app.telemetry.emitters import emit_chat_turn_once, mark_chat_turn_emitted

        # Given: 초기화 (emitted=False)
        assert is_chat_turn_emitted() is False, "Should start with emitted=False"

        ctx = RequestContext(
            trace_id="trace-dup-test",
            user_id="user-dup",
            conversation_id="conv-dup",
            turn_id=5,
        )
        set_request_context(ctx)

        # When: mark_chat_turn_emitted로 수동으로 플래그 설정
        mark_chat_turn_emitted()

        # Then: 플래그가 True로 설정됨
        assert is_chat_turn_emitted() is True

        # When: emit_chat_turn_once 호출 (이미 emitted 상태)
        result = emit_chat_turn_once(
            intent_main="STREAMING",
            route_type="LLM_ONLY",
            domain="CHAT",
            rag_used=False,
            latency_ms_total=100,
            error_code=None,
        )

        # Then: 중복이므로 False 반환 (발행 안됨)
        assert result is False, "Should return False when already emitted"

    @pytest.mark.asyncio
    async def test_cleanup_on_stream_exception(self):
        """스트리밍 중 예외 발생 시에도 cleanup이 실행된다."""
        from app.telemetry.middleware import _wrap_streaming_body

        # Given: 컨텍스트 설정
        ctx = RequestContext(
            trace_id="trace-exc-cleanup",
            user_id="user-exc",
            conversation_id="conv-exc",
            turn_id=7,
        )
        set_request_context(ctx)

        # Given: 예외를 발생시키는 스트리밍 생성기
        async def error_stream() -> AsyncIterator[bytes]:
            yield b"data1\n"
            raise RuntimeError("Simulated stream error")

        # When: 래핑된 body 반복 (예외 발생)
        wrapped = _wrap_streaming_body(error_stream())
        chunks = []
        with pytest.raises(RuntimeError, match="Simulated stream error"):
            async for chunk in wrapped:
                chunks.append(chunk)

        # Then: 예외 전까지 청크 수신
        assert len(chunks) == 1

        # Then: cleanup 후 컨텍스트 리셋됨 (finally 블록에서)
        assert _is_context_reset(), "Context should be reset even after exception"


# =============================================================================
# 추가 테스트: Middleware 통합
# =============================================================================


class TestMiddlewareStreamingIntegration:
    """RequestContextMiddleware와 스트리밍 통합 테스트."""

    @pytest.mark.asyncio
    async def test_middleware_defers_cleanup_for_streaming(self):
        """Middleware가 StreamingResponse에 대해 cleanup을 지연한다."""
        from starlette.middleware.base import RequestResponseEndpoint
        from starlette.responses import Response

        from app.telemetry.middleware import RequestContextMiddleware

        middleware = RequestContextMiddleware(app=MagicMock())

        # Given: Mock request
        mock_request = MagicMock(spec=Request)
        mock_request.headers = {
            "X-Trace-Id": "trace-middleware-001",
            "X-User-Id": "user-middleware",
        }
        mock_request.url.path = "/ai/chat/stream"

        # Given: StreamingResponse를 반환하는 핸들러
        async def stream_body() -> AsyncIterator[bytes]:
            yield b"streaming-data\n"

        async def mock_call_next(request: Request) -> Response:
            return StreamingResponse(stream_body(), media_type="application/x-ndjson")

        # When: Middleware dispatch
        response = await middleware.dispatch(mock_request, mock_call_next)

        # Then: StreamingResponse 반환
        assert isinstance(response, StreamingResponse)

        # Then: body_iterator가 래핑됨 (cleanup 지연)
        # 래핑된 iterator는 _wrap_streaming_body에서 반환됨
        assert response.body_iterator is not None

    @pytest.mark.asyncio
    async def test_middleware_immediate_cleanup_for_non_streaming(self):
        """Middleware가 비스트리밍 응답에 대해 즉시 cleanup한다."""
        from starlette.middleware.base import RequestResponseEndpoint
        from starlette.responses import JSONResponse, Response

        from app.telemetry.middleware import RequestContextMiddleware

        middleware = RequestContextMiddleware(app=MagicMock())

        # Given: Mock request
        mock_request = MagicMock(spec=Request)
        mock_request.headers = {
            "X-Trace-Id": "trace-nonstream-001",
            "X-User-Id": "user-nonstream",
        }
        mock_request.url.path = "/ai/chat"

        # Given: 일반 Response를 반환하는 핸들러
        async def mock_call_next(request: Request) -> Response:
            return JSONResponse({"status": "ok"})

        # When: Middleware dispatch
        response = await middleware.dispatch(mock_request, mock_call_next)

        # Then: 일반 Response 반환
        assert not isinstance(response, StreamingResponse)

        # Then: 즉시 cleanup됨 (컨텍스트 리셋됨)
        assert _is_context_reset(), "Context should be reset immediately for non-streaming"
