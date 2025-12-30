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

사용법:
    from fastapi import FastAPI
    from app.telemetry.middleware import RequestContextMiddleware

    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
"""

from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.telemetry.context import RequestContext, set_request_context, reset_request_context
from app.telemetry.emitters import (
    reset_chat_turn_emitted,
    reset_security_emitted,
    reset_feedback_emitted,
)
from app.telemetry.metrics import reset_all_metrics


class RequestContextMiddleware(BaseHTTPMiddleware):
    """요청 컨텍스트 미들웨어.

    요청 헤더에서 텔레메트리 관련 정보를 추출하여
    RequestContext에 저장합니다.

    A7: 요청 단위로 모든 telemetry contextvars를 리셋하여
    요청 간 상태 누적을 방지합니다.

    헤더 파싱 실패 시에도 예외를 발생시키지 않고
    해당 값을 None으로 처리합니다.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """요청 처리 전 헤더를 파싱하여 컨텍스트에 저장합니다.

        A7: 요청 시작 시 모든 contextvars 리셋, 종료 시 다시 리셋 (방어적).

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

        try:
            # 다음 핸들러 호출
            response = await call_next(request)
            return response
        finally:
            # A7: 요청 종료 시 모든 contextvars 리셋 (방어적)
            reset_request_context()
            reset_all_metrics()
            reset_chat_turn_emitted()
            reset_security_emitted()
            reset_feedback_emitted()

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
