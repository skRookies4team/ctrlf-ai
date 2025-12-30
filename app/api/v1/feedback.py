"""
Feedback 내부 API (A6)

Backend -> AI 피드백 전달 엔드포인트입니다.

엔드포인트:
POST /internal/ai/feedback : Backend -> AI 피드백 수신

흐름:
1. Backend -> AI: POST /internal/ai/feedback (피드백 데이터 전달)
2. AI: FEEDBACK 이벤트 TelemetryEvent 생성 및 enqueue
3. AI -> Backend: /internal/telemetry/events 배치 전송 (TelemetryPublisher가 비동기 처리)

인증:
- X-Internal-Token 헤더 필수
"""

from typing import Literal, Optional

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.logging import get_logger
from app.telemetry.context import (
    RequestContext,
    get_request_context,
    reset_request_context,
    set_request_context,
)
from app.telemetry.emitters import (
    emit_feedback_event_once,
    reset_feedback_emitted,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/internal/ai", tags=["Feedback"])


# =============================================================================
# Request/Response Models
# =============================================================================


class FeedbackRequest(BaseModel):
    """Backend -> AI 피드백 요청.

    FeedbackPayload에 필요한 최소 필드만 포함합니다.
    """

    feedback: Literal["like", "dislike"] = Field(
        ...,
        description="피드백 값 (like/dislike)",
    )
    targetConversationId: str = Field(
        ...,
        description="대상 대화 ID",
    )
    targetTurnId: int = Field(
        ...,
        description="대상 턴 ID",
    )
    # 컨텍스트 필드 (헤더가 없는 경우 요청 바디에서 보완)
    traceId: Optional[str] = Field(
        None,
        description="추적 ID (X-Trace-Id 헤더가 없으면 이 값 사용)",
    )
    userId: Optional[str] = Field(
        None,
        description="사용자 ID (X-User-Id 헤더가 없으면 이 값 사용)",
    )
    deptId: Optional[str] = Field(
        None,
        description="부서 ID (X-Dept-Id 헤더가 없으면 이 값 사용)",
    )
    conversationId: Optional[str] = Field(
        None,
        description="대화 ID (X-Conversation-Id 헤더가 없으면 이 값 사용)",
    )
    turnId: Optional[int] = Field(
        None,
        description="턴 ID (X-Turn-Id 헤더가 없으면 이 값 사용)",
    )


class FeedbackResponse(BaseModel):
    """Backend -> AI 피드백 응답.

    telemetry는 fire-and-forget이므로 항상 accepted 형태로 응답합니다.
    """

    accepted: bool = True
    message: str = "Feedback received"


class ErrorResponse(BaseModel):
    """에러 응답."""

    error: str
    message: str


# =============================================================================
# Helper Functions
# =============================================================================


def _error_response(
    status_code: int,
    error: str,
    message: str,
) -> JSONResponse:
    """에러 응답을 생성합니다."""
    content = {
        "error": error,
        "message": message,
    }
    return JSONResponse(status_code=status_code, content=content)


# =============================================================================
# Routes
# =============================================================================


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    summary="피드백 수신 (Backend -> AI)",
    description="""
Backend에서 호출하여 사용자 피드백을 AI로 전달합니다.

**URL**: POST /internal/ai/feedback

**호출 주체**: Spring 백엔드

**인증**: X-Internal-Token 헤더 필수

**처리 흐름**:
1. 피드백 데이터 수신
2. FEEDBACK 이벤트 생성 및 TelemetryPublisher에 enqueue
3. 202 Accepted 반환 (fire-and-forget)

**컨텍스트 전파**:
- RequestContextMiddleware가 적용되지 않는 경우 요청 바디에서 컨텍스트 보완
- 헤더 > 요청 바디 순으로 우선순위 적용

**제약사항**:
- payload에 질문/답변 원문(PII 가능) 절대 포함 금지
""",
    responses={
        202: {"description": "피드백 접수됨 (비동기 처리)", "model": FeedbackResponse},
        401: {"description": "인증 실패", "model": ErrorResponse},
    },
)
async def receive_feedback(
    request: FeedbackRequest,
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
    x_trace_id: Optional[str] = Header(None, alias="X-Trace-Id"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_dept_id: Optional[str] = Header(None, alias="X-Dept-Id"),
    x_conversation_id: Optional[str] = Header(None, alias="X-Conversation-Id"),
    x_turn_id: Optional[str] = Header(None, alias="X-Turn-Id"),
):
    """피드백을 수신하고 FEEDBACK 이벤트를 발행합니다."""
    # 인증 검증
    settings = get_settings()
    expected_token = settings.BACKEND_INTERNAL_TOKEN

    if expected_token:  # 토큰이 설정된 경우만 검증
        if not x_internal_token:
            return _error_response(
                status_code=401,
                error="UNAUTHORIZED",
                message="X-Internal-Token 헤더가 필요합니다.",
            )
        if x_internal_token != expected_token:
            return _error_response(
                status_code=401,
                error="UNAUTHORIZED",
                message="유효하지 않은 인증 토큰입니다.",
            )
    else:
        logger.warning("BACKEND_INTERNAL_TOKEN not configured, skipping auth")

    logger.info(
        f"Received feedback: feedback={request.feedback}, "
        f"target_conversation_id={request.targetConversationId}, "
        f"target_turn_id={request.targetTurnId}"
    )

    # 컨텍스트 설정 (헤더 > 요청 바디 우선순위)
    trace_id = x_trace_id or request.traceId
    user_id = x_user_id or request.userId
    dept_id = x_dept_id or request.deptId
    conversation_id = x_conversation_id or request.conversationId
    turn_id_str = x_turn_id
    turn_id = int(turn_id_str) if turn_id_str else request.turnId

    # RequestContext 설정 (미들웨어가 적용되지 않는 경우 보완)
    current_ctx = get_request_context()
    needs_context_setup = (
        not current_ctx.trace_id
        or not current_ctx.user_id
        or not current_ctx.dept_id
        or not current_ctx.conversation_id
        or current_ctx.turn_id is None
    )

    if needs_context_setup:
        ctx = RequestContext(
            trace_id=trace_id,
            user_id=user_id,
            dept_id=dept_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        set_request_context(ctx)
        reset_feedback_emitted()  # 새 컨텍스트이므로 dedup 상태 초기화

    try:
        # FEEDBACK 이벤트 발행
        result = emit_feedback_event_once(
            feedback=request.feedback,
            target_conversation_id=request.targetConversationId,
            target_turn_id=request.targetTurnId,
        )

        if result:
            logger.debug(
                f"FEEDBACK event enqueued: target_conversation_id={request.targetConversationId}, "
                f"target_turn_id={request.targetTurnId}"
            )
        else:
            logger.debug(
                f"FEEDBACK event not enqueued (duplicate or missing context): "
                f"target_conversation_id={request.targetConversationId}, "
                f"target_turn_id={request.targetTurnId}"
            )

        # fire-and-forget: enqueue 결과와 관계없이 accepted 반환
        return JSONResponse(
            status_code=202,
            content=FeedbackResponse(
                accepted=True,
                message="Feedback received",
            ).model_dump(),
        )

    finally:
        # 컨텍스트를 직접 설정한 경우만 초기화
        if needs_context_setup:
            reset_request_context()
