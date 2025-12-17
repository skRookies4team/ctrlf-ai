"""
Chat Stream Models (스트리밍 채팅 모델)

HTTP 청크 스트리밍 API를 위한 요청/응답 모델입니다.
백엔드(Spring)가 NDJSON으로 파싱하여 SSE로 변환합니다.

NDJSON 규칙:
- 한 줄 = 한 JSON
- JSON 사이에 반드시 \n (개행)
- 타입: meta, token, done, error

Phase 23: ChatRequest와 필드 일치 (session_id, user_id, user_role, messages 등)
"""

from datetime import datetime
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from app.models.chat import ChatMessage


# =============================================================================
# Request Models
# =============================================================================


class ChatStreamRequest(BaseModel):
    """
    스트리밍 채팅 요청 모델.

    ChatRequest와 동일한 필드 구조 + 스트리밍 전용 request_id.

    Attributes:
        request_id: 중복 방지 / 재시도용 고유 키 (필수, 스트리밍 전용)
        session_id: 채팅 세션 ID (필수)
        user_id: 사용자 ID (필수)
        user_role: 사용자 역할 - EMPLOYEE, MANAGER, ADMIN 등 (필수)
        department: 사용자 부서 (선택)
        domain: 질의 도메인 - POLICY, INCIDENT, EDUCATION 등 (선택)
        channel: 요청 채널 - WEB, MOBILE 등 (기본: WEB)
        messages: 대화 히스토리 (마지막 요소가 최신 메시지)
    """

    request_id: str = Field(
        ...,
        min_length=1,
        description="중복 방지 / 재시도용 고유 키 (Idempotency Key, 스트리밍 전용)",
    )
    session_id: str = Field(
        ...,
        min_length=1,
        description="채팅 세션 ID (백엔드 관리)",
    )
    user_id: str = Field(
        ...,
        min_length=1,
        description="사용자 ID (사번 등)",
    )
    user_role: str = Field(
        ...,
        min_length=1,
        description="사용자 역할 (EMPLOYEE, MANAGER, ADMIN 등)",
    )
    department: Optional[str] = Field(
        default=None,
        description="사용자 부서 (선택)",
    )
    domain: Optional[str] = Field(
        default=None,
        description="질의 도메인 (POLICY, INCIDENT, EDUCATION 등). 미지정 시 AI가 판단.",
    )
    channel: str = Field(
        default="WEB",
        description="요청 채널 (WEB, MOBILE 등)",
    )
    messages: List[ChatMessage] = Field(
        ...,
        min_length=1,
        description="대화 히스토리 (마지막 요소가 최신 메시지)",
    )


# =============================================================================
# Stream Event Types (NDJSON Response)
# =============================================================================


class StreamEventType(str, Enum):
    """스트리밍 이벤트 타입."""

    META = "meta"  # 시작 시 1회
    TOKEN = "token"  # 토큰 스트림 (여러 번)
    DONE = "done"  # 정상 종료 시 1회
    ERROR = "error"  # 에러 시 1회


class StreamMetaEvent(BaseModel):
    """
    스트리밍 시작 이벤트 (연결 직후 1회).

    목적: 백엔드/ALB/프록시에서 침묵 시간을 없애고 연결 상태를 확정.
    """

    type: Literal["meta"] = "meta"
    request_id: str = Field(..., description="요청 ID")
    model: str = Field(..., description="사용 모델명")
    timestamp: str = Field(..., description="시작 시간 (ISO 8601)")

    def to_ndjson(self) -> str:
        """NDJSON 문자열로 변환 (줄바꿈 포함)."""
        return self.model_dump_json() + "\n"


class StreamTokenEvent(BaseModel):
    """
    토큰 스트림 이벤트 (여러 번 전송).

    text는 누적이 아닌 증분(delta)으로 전송.
    """

    type: Literal["token"] = "token"
    text: str = Field(..., description="토큰 텍스트 (증분)")

    def to_ndjson(self) -> str:
        """NDJSON 문자열로 변환 (줄바꿈 포함)."""
        return self.model_dump_json() + "\n"


class StreamDoneEvent(BaseModel):
    """
    스트리밍 정상 종료 이벤트 (1회).
    """

    type: Literal["done"] = "done"
    finish_reason: str = Field(default="stop", description="종료 사유")
    total_tokens: Optional[int] = Field(default=None, description="총 토큰 수")
    elapsed_ms: int = Field(..., description="총 소요 시간 (ms)")
    ttfb_ms: Optional[int] = Field(default=None, description="첫 토큰까지 시간 (ms)")

    def to_ndjson(self) -> str:
        """NDJSON 문자열로 변환 (줄바꿈 포함)."""
        return self.model_dump_json() + "\n"


class StreamErrorCode(str, Enum):
    """스트리밍 에러 코드."""

    LLM_TIMEOUT = "LLM_TIMEOUT"  # LLM 타임아웃
    LLM_ERROR = "LLM_ERROR"  # LLM 에러
    DUPLICATE_INFLIGHT = "DUPLICATE_INFLIGHT"  # 중복 요청 (이미 처리 중)
    INVALID_REQUEST = "INVALID_REQUEST"  # 잘못된 요청
    INTERNAL_ERROR = "INTERNAL_ERROR"  # 내부 에러
    CLIENT_DISCONNECTED = "CLIENT_DISCONNECTED"  # 클라이언트 연결 끊김


class StreamErrorEvent(BaseModel):
    """
    스트리밍 에러 이벤트 (1회, 에러 후 즉시 스트림 종료).
    """

    type: Literal["error"] = "error"
    code: str = Field(..., description="에러 코드")
    message: str = Field(..., description="에러 메시지")
    request_id: str = Field(..., description="요청 ID")

    def to_ndjson(self) -> str:
        """NDJSON 문자열로 변환 (줄바꿈 포함)."""
        return self.model_dump_json() + "\n"


# =============================================================================
# Internal Models (서비스 내부용)
# =============================================================================


class InFlightRequest(BaseModel):
    """
    진행 중인 요청 정보 (중복 방지용).

    Attributes:
        request_id: 요청 ID
        started_at: 시작 시간
        completed: 완료 여부
        final_response: 완료된 응답 (캐시용)
    """

    request_id: str
    started_at: datetime
    completed: bool = False
    final_response: Optional[str] = None  # 완료된 전체 응답 (캐시용)


class StreamMetrics(BaseModel):
    """
    스트리밍 메트릭 정보.

    로그에 기록되는 메트릭입니다.
    """

    request_id: str
    model: str
    ttfb_ms: Optional[int] = None  # Time To First Byte
    total_elapsed_ms: int = 0
    total_tokens: int = 0
    error_code: Optional[str] = None
    completed: bool = False
