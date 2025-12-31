"""
Telemetry Models - v1 Telemetry Contract Schema

v1 Telemetry 계약에 따른 이벤트 스키마를 정의합니다.
백엔드로 전송되는 JSON은 camelCase 키를 사용합니다.

주요 모델:
- TelemetryEnvelope: 배치 전송용 래퍼
- TelemetryEvent: 개별 이벤트
- ChatTurnPayload: 채팅 턴 페이로드
- FeedbackPayload: 피드백 페이로드
- SecurityPayload: 보안 이벤트 페이로드

직렬화 규칙:
- model_dump(by_alias=True, exclude_none=True)로 v1 계약 JSON 생성
- 모든 필드는 snake_case, serialization_alias로 camelCase 출력
"""

from datetime import datetime
from typing import List, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# =============================================================================
# 공통 타입 / Enum (Literal 사용)
# =============================================================================

EventType = Literal["CHAT_TURN", "FEEDBACK", "SECURITY"]
FeedbackValue = Literal["like", "dislike"]
SecurityBlockType = Literal["PII_BLOCK", "EXTERNAL_DOMAIN_BLOCK"]


# =============================================================================
# RAG 서브모델
# =============================================================================


class RagSource(BaseModel):
    """RAG 검색 결과 소스 정보."""

    model_config = ConfigDict(
        populate_by_name=True,
    )

    doc_id: str = Field(
        description="문서 ID",
        serialization_alias="docId",
    )
    chunk_id: int = Field(
        description="청크 ID",
        serialization_alias="chunkId",
    )
    score: float = Field(
        description="유사도 점수",
    )


class RagInfo(BaseModel):
    """RAG 검색 정보."""

    model_config = ConfigDict(
        populate_by_name=True,
    )

    retriever: str = Field(
        description="검색 백엔드 (예: milvus)",
    )
    top_k: int = Field(
        description="검색 TopK 설정",
        serialization_alias="topK",
    )
    min_score: float = Field(
        description="최소 유사도 점수",
        serialization_alias="minScore",
    )
    max_score: float = Field(
        description="최대 유사도 점수",
        serialization_alias="maxScore",
    )
    avg_score: float = Field(
        description="평균 유사도 점수",
        serialization_alias="avgScore",
    )
    sources: List[RagSource] = Field(
        default_factory=list,
        description="검색 결과 소스 목록",
    )
    context_excerpt: Optional[str] = Field(
        default=None,
        description="컨텍스트 발췌 (최대 300자)",
        serialization_alias="contextExcerpt",
    )

    @field_validator("context_excerpt")
    @classmethod
    def validate_context_excerpt_length(cls, v: Optional[str]) -> Optional[str]:
        """contextExcerpt 길이 제한 (최대 300자)."""
        if v is not None and len(v) > 300:
            raise ValueError("contextExcerpt must be at most 300 characters")
        return v


# =============================================================================
# Payload 3종
# =============================================================================


class ChatTurnPayload(BaseModel):
    """채팅 턴 이벤트 페이로드."""

    model_config = ConfigDict(
        populate_by_name=True,
    )

    intent_main: str = Field(
        description="주요 의도 (예: POLICY_QA)",
        serialization_alias="intentMain",
    )
    intent_sub: Optional[str] = Field(
        default=None,
        description="세부 의도 (예: PARENTAL_LEAVE)",
        serialization_alias="intentSub",
    )
    route_type: str = Field(
        description="라우팅 타입 (예: RAG, EDU_API, GENERAL)",
        serialization_alias="routeType",
    )
    domain: str = Field(
        description="도메인 (예: POLICY, FAQ, EDUCATION)",
    )
    model: Optional[str] = Field(
        default=None,
        description="사용된 LLM 모델명",
    )
    rag_used: bool = Field(
        description="RAG 사용 여부",
        serialization_alias="ragUsed",
    )
    latency_ms_total: int = Field(
        description="총 응답 시간 (ms)",
        serialization_alias="latencyMsTotal",
    )
    latency_ms_llm: Optional[int] = Field(
        default=None,
        description="LLM 응답 시간 (ms)",
        serialization_alias="latencyMsLlm",
    )
    latency_ms_retrieval: Optional[int] = Field(
        default=None,
        description="검색/재랭크 시간 (ms)",
        serialization_alias="latencyMsRetrieval",
    )
    error_code: Optional[str] = Field(
        default=None,
        description="에러 코드 (없으면 null)",
        serialization_alias="errorCode",
    )
    pii_detected_input: bool = Field(
        description="입력에서 PII 검출 여부",
        serialization_alias="piiDetectedInput",
    )
    pii_detected_output: bool = Field(
        description="출력에서 PII 검출 여부",
        serialization_alias="piiDetectedOutput",
    )
    oos: bool = Field(
        default=False,
        description="Out-of-scope 여부",
    )
    rag: Optional[RagInfo] = Field(
        default=None,
        description="RAG 검색 상세 정보",
    )


class FeedbackPayload(BaseModel):
    """사용자 피드백 이벤트 페이로드."""

    model_config = ConfigDict(
        populate_by_name=True,
    )

    feedback: FeedbackValue = Field(
        description="피드백 값 (like/dislike)",
    )
    target_conversation_id: str = Field(
        description="대상 대화 ID",
        serialization_alias="targetConversationId",
    )
    target_turn_id: int = Field(
        description="대상 턴 ID",
        serialization_alias="targetTurnId",
    )


class SecurityPayload(BaseModel):
    """보안 이벤트 페이로드."""

    model_config = ConfigDict(
        populate_by_name=True,
    )

    block_type: SecurityBlockType = Field(
        description="차단 유형 (PII_BLOCK, EXTERNAL_DOMAIN_BLOCK)",
        serialization_alias="blockType",
    )
    blocked: bool = Field(
        description="실제 차단 여부",
    )
    rule_id: Optional[str] = Field(
        default=None,
        description="적용된 규칙 ID",
        serialization_alias="ruleId",
    )


# =============================================================================
# TelemetryEvent / Envelope
# =============================================================================

# Payload Union 타입
PayloadType = Union[ChatTurnPayload, FeedbackPayload, SecurityPayload]


class TelemetryEvent(BaseModel):
    """텔레메트리 이벤트.

    eventType과 payload 타입이 일치해야 합니다:
    - CHAT_TURN -> ChatTurnPayload
    - FEEDBACK -> FeedbackPayload
    - SECURITY -> SecurityPayload
    """

    model_config = ConfigDict(
        populate_by_name=True,
    )

    event_id: UUID = Field(
        description="이벤트 고유 ID (UUID)",
        serialization_alias="eventId",
    )
    event_type: EventType = Field(
        description="이벤트 타입",
        serialization_alias="eventType",
    )
    trace_id: str = Field(
        description="요청 추적 ID",
        serialization_alias="traceId",
    )
    conversation_id: str = Field(
        description="대화 세션 ID",
        serialization_alias="conversationId",
    )
    turn_id: int = Field(
        description="턴 ID",
        serialization_alias="turnId",
    )
    user_id: str = Field(
        description="사용자 ID",
        serialization_alias="userId",
    )
    dept_id: str = Field(
        description="부서 ID",
        serialization_alias="deptId",
    )
    occurred_at: datetime = Field(
        description="이벤트 발생 시각",
        serialization_alias="occurredAt",
    )
    payload: PayloadType = Field(
        description="이벤트 페이로드",
    )

    @model_validator(mode="after")
    def validate_event_type_payload_match(self) -> "TelemetryEvent":
        """eventType과 payload 타입 일치 검증."""
        event_type = self.event_type
        payload = self.payload

        if event_type == "CHAT_TURN" and not isinstance(payload, ChatTurnPayload):
            raise ValueError(
                f"eventType 'CHAT_TURN' requires ChatTurnPayload, "
                f"got {type(payload).__name__}"
            )
        elif event_type == "FEEDBACK" and not isinstance(payload, FeedbackPayload):
            raise ValueError(
                f"eventType 'FEEDBACK' requires FeedbackPayload, "
                f"got {type(payload).__name__}"
            )
        elif event_type == "SECURITY" and not isinstance(payload, SecurityPayload):
            raise ValueError(
                f"eventType 'SECURITY' requires SecurityPayload, "
                f"got {type(payload).__name__}"
            )

        return self


class TelemetryEnvelope(BaseModel):
    """텔레메트리 이벤트 배치 전송용 래퍼.

    POST /internal/telemetry/events로 전송되는 최상위 구조입니다.
    """

    model_config = ConfigDict(
        populate_by_name=True,
    )

    source: str = Field(
        description="이벤트 발행 소스 (예: ai-gateway)",
    )
    sent_at: datetime = Field(
        description="전송 시각",
        serialization_alias="sentAt",
    )
    events: List[TelemetryEvent] = Field(
        description="이벤트 목록 (최소 1개)",
        min_length=1,
    )
