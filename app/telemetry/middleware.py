"""
Request Context Middleware - Header Extraction & Context Propagation

FastAPI 요청 헤더에서 텔레메트리 관련 정보를 추출하여
contextvars에 저장하는 미들웨어입니다.

지원 헤더:
    - X-Trace-Id: 요청 추적 ID
    - X-User-Id: 사용자 ID
    - X-Dept-Id: 부서 ID
    - X-Conversation-Id: 대화 세션 ID
    - X-Turn-Id: 턴 ID (정수)

A7 업데이트:
    - 요청 시작 시 모든 telemetry contextvars 리셋 (clean slate 보장)
    - 요청 종료 시(finally) 모든 contextvars 리셋 (방어적)

A8 업데이트:
    - StreamingResponse의 경우 스트림 완료 후 cleanup 실행
    - body_iterator 래핑으로 스트림 전송 중 contextvars 유지 보장

사용법:
    from fastapi import FastAPI
    from app.telemetry.middleware import RequestContextMiddleware

    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
"""

from typing import AsyncIterator, Optional

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from app.telemetry.context import RequestContext, set_request_context, reset_request_context
from app.telemetry.emitters import (
    reset_chat_turn_emitted,
    reset_security_emitted,
    reset_feedback_emitted,
)
from app.telemetry.metrics import reset_all_metrics

from app.core.logging import get_logger

logger = get_logger(__name__)


def _cleanup_telemetry_context() -> None:
    """모든 telemetry contextvars를 리셋합니다."""
    reset_request_context()
    reset_all_metrics()
    reset_chat_turn_emitted()
    reset_security_emitted()
    reset_feedback_emitted()


async def _wrap_streaming_body(
    body_iterator: AsyncIterator[bytes],
) -> AsyncIterator[bytes]:
    """
    StreamingResponse의 body_iterator를 래핑하여 스트림 완료 후 cleanup을 실행합니다.

    A8: 스트림 전송 중에는 contextvars가 유지되고,
    스트림이 완료(정상/예외)된 후에 cleanup이 실행됩니다.

    Args:
        body_iterator: 원본 body iterator

    Yields:
        bytes: 스트리밍 청크
    """
    try:
        async for chunk in body_iterator:
            yield chunk
    finally:
        # 스트림 완료 후 cleanup
        logger.debug("Streaming body completed, cleaning up telemetry context")
        _cleanup_telemetry_context()


class RequestContextMiddleware(BaseHTTPMiddleware):
    """요청 컨텍스트 미들웨어.

    요청 헤더에서 텔레메트리 관련 정보를 추출하여
    RequestContext에 저장합니다.

    A7: 요청 단위로 모든 telemetry contextvars를 리셋하여
    요청 간 상태 누적을 방지합니다.

    A8: StreamingResponse의 경우 스트림 완료 후 cleanup을 실행하여
    스트림 전송 중 contextvars가 유지됩니다.

    헤더 파싱 실패 시에도 예외를 발생시키지 않고
    해당 값을 None으로 처리합니다.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """요청 처리 전 헤더를 파싱하여 컨텍스트에 저장합니다.

        A7: 요청 시작 시 모든 contextvars 리셋.
        A8: StreamingResponse면 스트림 완료 후 cleanup, 아니면 즉시 cleanup.

        Args:
            request: FastAPI/Starlette 요청 객체
            call_next: 다음 미들웨어/라우터 핸들러

        Returns:
            Response 객체
        """
        # 헤더에서 값 추출 (없으면 None)
        trace_id = request.headers.get("X-Trace-Id")
        user_id = request.headers.get("X-User-Id")
        dept_id = request.headers.get("X-Dept-Id")
        conversation_id = request.headers.get("X-Conversation-Id")
        turn_id = self._parse_turn_id(request.headers.get("X-Turn-Id"))

        # RequestContext 생성 및 저장
        ctx = RequestContext(
            trace_id=trace_id,
            user_id=user_id,
            dept_id=dept_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        set_request_context(ctx)

        # A7: 요청 시작 시 모든 telemetry contextvars 리셋 (clean slate 보장)
        reset_all_metrics()
        reset_chat_turn_emitted()
        reset_security_emitted()
        reset_feedback_emitted()

        is_streaming = False

        try:
            # 다음 핸들러 호출
            response = await call_next(request)

            # A8: StreamingResponse 여부 확인
            if isinstance(response, StreamingResponse):
                is_streaming = True
                # body_iterator를 래핑하여 스트림 완료 후 cleanup 실행
                response.body_iterator = _wrap_streaming_body(response.body_iterator)
                logger.debug(f"Streaming response detected, deferring cleanup: path={request.url.path}")

            return response

        except Exception:
            # 예외 발생 시에는 즉시 cleanup
            _cleanup_telemetry_context()
            raise

        finally:
            # A8: StreamingResponse가 아닌 경우에만 즉시 cleanup
            if not is_streaming:
                _cleanup_telemetry_context()

    @staticmethod
    def _parse_turn_id(value: Optional[str]) -> Optional[int]:
        """X-Turn-Id 헤더를 정수로 파싱합니다.

        Args:
            value: 헤더 값 (문자열 또는 None)

        Returns:
            파싱된 정수 또는 None (파싱 실패 시)
        """
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
