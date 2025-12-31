"""
Request Context Module - v1 Telemetry Context Propagation

요청별 컨텍스트를 저장하고 조회하는 모듈입니다.
백엔드에서 전달하는 헤더(X-Trace-Id, X-User-Id 등)를 저장하여
텔레메트리 이벤트 생성 시 어디서든 참조할 수 있게 합니다.

사용법:
    from app.telemetry.context import get_request_context, set_request_context

    # Middleware에서 설정
    ctx = RequestContext(trace_id="...", user_id="...", ...)
    set_request_context(ctx)

    # 어디서든 조회
    ctx = get_request_context()
    print(ctx.trace_id)
"""

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional


@dataclass
class RequestContext:
    """요청별 컨텍스트 정보.

    백엔드에서 전달하는 헤더 값을 저장합니다.
    헤더가 없는 경우 각 필드는 None이 됩니다.

    Attributes:
        trace_id: 요청 추적 ID (X-Trace-Id)
        user_id: 사용자 ID (X-User-Id)
        dept_id: 부서 ID (X-Dept-Id)
        conversation_id: 대화 세션 ID (X-Conversation-Id)
        turn_id: 턴 ID (X-Turn-Id, 정수)
    """

    trace_id: Optional[str] = None
    user_id: Optional[str] = None
    dept_id: Optional[str] = None
    conversation_id: Optional[str] = None
    turn_id: Optional[int] = None


# 전역 ContextVar - 요청별로 독립적인 컨텍스트 제공
_request_context_var: ContextVar[Optional[RequestContext]] = ContextVar(
    "request_context",
    default=None,
)


def set_request_context(ctx: RequestContext) -> None:
    """요청 컨텍스트를 설정합니다.

    일반적으로 Middleware에서 요청 시작 시 호출합니다.

    Args:
        ctx: 설정할 RequestContext 객체
    """
    _request_context_var.set(ctx)


def get_request_context() -> RequestContext:
    """현재 요청의 컨텍스트를 반환합니다.

    컨텍스트가 설정되지 않은 경우 모든 필드가 None인
    빈 RequestContext를 반환합니다. (예외를 발생시키지 않음)

    Returns:
        현재 요청의 RequestContext 객체
    """
    ctx = _request_context_var.get()
    if ctx is None:
        return RequestContext()
    return ctx


def reset_request_context() -> None:
    """요청 컨텍스트를 초기화합니다.

    테스트 환경에서 컨텍스트를 명시적으로 리셋할 때 사용합니다.
    일반적인 요청 처리에서는 contextvars의 특성상
    요청 단위로 자동 격리되므로 호출할 필요가 없습니다.
    """
    _request_context_var.set(None)
