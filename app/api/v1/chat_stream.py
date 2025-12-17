"""
Chat Stream API Router Module

HTTP 청크 스트리밍으로 AI 응답을 전송하는 엔드포인트입니다.
백엔드(Spring)가 NDJSON을 줄 단위로 읽어서 SSE로 변환합니다.

Endpoints:
    - POST /ai/chat/stream: 스트리밍 채팅 응답 생성

NDJSON 규칙:
    - 한 줄 = 한 JSON
    - JSON 사이에 반드시 \\n (개행)
    - flush가 즉시 되도록 StreamingResponse 사용
"""

import asyncio
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.core.logging import get_logger
from app.models.chat_stream import (
    ChatStreamRequest,
    StreamErrorCode,
    StreamErrorEvent,
)
from app.services.chat_stream_service import ChatStreamService

logger = get_logger(__name__)

router = APIRouter(tags=["Chat Stream"])


def get_chat_stream_service() -> ChatStreamService:
    """ChatStreamService 인스턴스 반환."""
    return ChatStreamService()


async def stream_with_disconnect_check(
    request: Request,
    stream_generator: AsyncGenerator[str, None],
    request_id: str,
) -> AsyncGenerator[bytes, None]:
    """
    스트리밍 중 클라이언트 연결 끊김을 감지합니다.

    연결이 끊기면 LLM 생성을 중단하여 자원 낭비를 방지합니다.
    """
    try:
        async for chunk in stream_generator:
            # 연결 끊김 체크
            if await request.is_disconnected():
                logger.info(f"Client disconnected during stream: {request_id}")
                break
            yield chunk.encode("utf-8")
    except asyncio.CancelledError:
        logger.info(f"Stream cancelled: {request_id}")
        raise
    except Exception as e:
        logger.exception(f"Stream error: {request_id}")
        # 에러 이벤트 전송
        error_event = StreamErrorEvent(
            code=StreamErrorCode.INTERNAL_ERROR.value,
            message=str(e),
            request_id=request_id,
        )
        yield error_event.to_ndjson().encode("utf-8")


@router.post(
    "/ai/chat/stream",
    summary="스트리밍 채팅 응답 생성",
    description="""
    HTTP 청크 스트리밍으로 AI 응답을 전송합니다.

    ## 요청 형식 (ChatRequest와 동일 + request_id)

    ChatRequest와 동일한 필드 구조를 사용하며, 스트리밍 전용 `request_id`가 추가됩니다.

    ## 응답 형식: NDJSON (Newline Delimited JSON)

    한 줄에 하나의 JSON 객체가 전송됩니다.

    ### 이벤트 타입

    1. **meta** (시작 시 1회)
    ```json
    {"type":"meta","request_id":"...","model":"...","timestamp":"..."}
    ```

    2. **token** (토큰 스트림, 여러 번)
    ```json
    {"type":"token","text":"안"}
    {"type":"token","text":"녕"}
    ```

    3. **done** (정상 종료 시 1회)
    ```json
    {"type":"done","finish_reason":"stop","total_tokens":123,"elapsed_ms":4567}
    ```

    4. **error** (에러 시 1회, 스트림 종료)
    ```json
    {"type":"error","code":"LLM_TIMEOUT","message":"...","request_id":"..."}
    ```

    ## 중복 방지

    동일한 `request_id`로 요청이 이미 처리 중이면 `DUPLICATE_INFLIGHT` 에러를 반환합니다.

    ## 에러 코드

    - `LLM_TIMEOUT`: LLM 응답 시간 초과
    - `LLM_ERROR`: LLM 서비스 오류
    - `DUPLICATE_INFLIGHT`: 중복 요청 (이미 처리 중)
    - `INVALID_REQUEST`: 잘못된 요청
    - `INTERNAL_ERROR`: 내부 서버 오류
    - `CLIENT_DISCONNECTED`: 클라이언트 연결 끊김
    """,
    responses={
        200: {
            "description": "스트리밍 응답 (NDJSON)",
            "content": {
                "application/x-ndjson": {
                    "example": '{"type":"meta","request_id":"req-001","model":"gpt-4","timestamp":"2025-01-01T00:00:00"}\n{"type":"token","text":"안"}\n{"type":"token","text":"녕"}\n{"type":"done","finish_reason":"stop","total_tokens":2,"elapsed_ms":100}\n'
                }
            },
        },
        422: {"description": "유효성 검증 실패"},
    },
)
async def stream_chat(
    request: Request,
    body: ChatStreamRequest,
) -> StreamingResponse:
    """
    스트리밍 채팅 응답을 생성합니다.

    **Request Body (ChatRequest와 동일 + request_id):**
    - `request_id`: 중복 방지 / 재시도용 고유 키 (필수, 스트리밍 전용)
    - `session_id`: 채팅 세션 ID (필수)
    - `user_id`: 사용자 ID (필수)
    - `user_role`: 사용자 역할 - EMPLOYEE, MANAGER, ADMIN 등 (필수)
    - `department`: 사용자 부서 (선택)
    - `domain`: 질의 도메인 - POLICY, INCIDENT, EDUCATION 등 (선택)
    - `channel`: 요청 채널 - WEB, MOBILE 등 (기본: WEB)
    - `messages`: 대화 히스토리 (필수, 마지막 요소가 최신 메시지)

    **Response:**
    - Content-Type: application/x-ndjson
    - Transfer-Encoding: chunked
    - 한 줄 = 한 JSON (줄바꿈으로 구분)

    Args:
        request: FastAPI Request (연결 끊김 감지용)
        body: 스트리밍 채팅 요청

    Returns:
        StreamingResponse: NDJSON 스트리밍 응답
    """
    logger.info(
        f"Stream chat request: request_id={body.request_id}, "
        f"user_id={body.user_id}, user_role={body.user_role}, session_id={body.session_id}"
    )

    service = get_chat_stream_service()
    stream_generator = service.stream_chat(body)

    return StreamingResponse(
        stream_with_disconnect_check(request, stream_generator, body.request_id),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx 버퍼링 비활성화
        },
    )
