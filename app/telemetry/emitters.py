"""
Telemetry Emitters - CHAT_TURN, SECURITY, FEEDBACK Event Emission Guards

CHAT_TURN 이벤트를 "턴당 정확히 1개"만 발행하도록 보장하고,
SECURITY 이벤트를 "요청 단위로 중복 없이" 발행하도록 보장하며,
FEEDBACK 이벤트를 "요청 단위로 중복 없이" 발행하도록 보장하는 모듈입니다.

핵심 원칙:
- 동일 요청에서 emit_chat_turn_once를 여러 번 호출해도 1회만 enqueue
- 동일 요청에서 같은 (blockType, ruleId) 조합의 SECURITY 이벤트는 1회만 enqueue
- 동일 요청에서 같은 (target_conversation_id, target_turn_id, feedback) 조합의 FEEDBACK 이벤트는 1회만 enqueue
- trace/user/dept/conversation/turn 필수값이 없으면 drop
- publisher가 없으면 drop
- enqueue 결과와 관계없이 중복 방지 가드 활성화

사용법:
    from app.telemetry.emitters import emit_chat_turn_once, emit_security_event_once, emit_feedback_event_once

    # 채팅 응답 생성 후 단일 지점에서 호출
    emit_chat_turn_once(
        intent_main="POLICY_QA",
        route_type="RAG",
        domain="POLICY",
        ...
    )

    # 보안 차단 발생 시 호출
    emit_security_event_once(
        block_type="PII_BLOCK",
        blocked=True,
        rule_id="PII-RULE-001",
    )

    # 피드백 수신 시 호출
    emit_feedback_event_once(
        feedback="like",
        target_conversation_id="conv-123",
        target_turn_id=1,
    )
"""

from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Literal, Optional, Set
from uuid import uuid4

from app.core.logging import get_logger
from app.telemetry.context import get_request_context
from app.telemetry.models import (
    ChatTurnPayload,
    FeedbackPayload,
    RagInfo,
    SecurityPayload,
    TelemetryEvent,
)
from app.telemetry.publisher import get_telemetry_publisher

logger = get_logger(__name__)

# =============================================================================
# Emission Guard - 턴당 1회 발행 보장
# =============================================================================

# 현재 요청에서 CHAT_TURN 이벤트가 이미 발행되었는지 추적
_chat_turn_emitted: ContextVar[bool] = ContextVar(
    "chat_turn_emitted",
    default=False,
)


def mark_chat_turn_emitted() -> None:
    """CHAT_TURN 이벤트가 발행되었음을 마킹합니다."""
    _chat_turn_emitted.set(True)


def is_chat_turn_emitted() -> bool:
    """현재 요청에서 CHAT_TURN 이벤트가 이미 발행되었는지 확인합니다."""
    return _chat_turn_emitted.get()


def reset_chat_turn_emitted() -> None:
    """CHAT_TURN 발행 상태를 초기화합니다.

    테스트 또는 새 요청 시작 시 호출합니다.
    일반적으로 contextvars 특성상 요청 단위로 자동 격리되므로
    명시적 호출이 필요하지 않습니다.
    """
    _chat_turn_emitted.set(False)


# =============================================================================
# emit_chat_turn_once - 단일 발행 함수
# =============================================================================


def emit_chat_turn_once(
    *,
    intent_main: str,
    intent_sub: Optional[str] = None,
    route_type: str,
    domain: str,
    model_name: Optional[str] = None,
    rag_used: bool,
    latency_ms_total: int,
    latency_ms_llm: Optional[int] = None,
    latency_ms_retrieval: Optional[int] = None,
    error_code: Optional[str] = None,
    pii_detected_input: bool = False,
    pii_detected_output: bool = False,
    oos: bool = False,
    rag_info: Optional[RagInfo] = None,
) -> bool:
    """CHAT_TURN 이벤트를 발행합니다 (턴당 1회만).

    동작 규칙:
    1. 이미 발행된 경우 → False 반환 (중복 방지)
    2. 필수 컨텍스트(trace/user/dept/conversation/turn) 없으면 → drop (False)
    3. publisher 없으면 → drop (False)
    4. enqueue 결과와 관계없이 발행 마킹 (중복 방지 목적)

    Args:
        intent_main: 주요 의도 (예: POLICY_QA)
        intent_sub: 세부 의도 (선택)
        route_type: 라우팅 타입 (예: RAG, LLM_ONLY)
        domain: 도메인 (예: POLICY, EDUCATION)
        model_name: 사용된 LLM 모델명
        rag_used: RAG 사용 여부
        latency_ms_total: 총 응답 시간 (ms)
        latency_ms_llm: LLM 응답 시간 (ms, 선택)
        latency_ms_retrieval: 검색 시간 (ms, 선택)
        error_code: 에러 코드 (없으면 None)
        pii_detected_input: 입력 PII 검출 여부
        pii_detected_output: 출력 PII 검출 여부
        oos: Out-of-scope 여부
        rag_info: RAG 검색 상세 정보 (선택)

    Returns:
        True: 이벤트가 성공적으로 enqueue됨
        False: 중복, 컨텍스트 누락, publisher 없음, 또는 enqueue 실패
    """
    try:
        # 1. 중복 체크
        if is_chat_turn_emitted():
            logger.debug("CHAT_TURN already emitted for this request, skipping")
            return False

        # 2. 컨텍스트에서 필수값 가져오기
        ctx = get_request_context()

        # 필수값 검증: trace/user/dept/conversation/turn
        if not ctx.trace_id:
            logger.debug("Missing trace_id, dropping CHAT_TURN event")
            return False
        if not ctx.user_id:
            logger.debug("Missing user_id, dropping CHAT_TURN event")
            return False
        if not ctx.dept_id:
            logger.debug("Missing dept_id, dropping CHAT_TURN event")
            return False
        if not ctx.conversation_id:
            logger.debug("Missing conversation_id, dropping CHAT_TURN event")
            return False
        if ctx.turn_id is None:
            logger.debug("Missing turn_id, dropping CHAT_TURN event")
            return False

        # 3. Publisher 확인
        publisher = get_telemetry_publisher()
        if publisher is None:
            logger.debug("TelemetryPublisher not available, dropping CHAT_TURN event")
            return False

        # 4. Payload 구성
        payload = ChatTurnPayload(
            intent_main=intent_main,
            intent_sub=intent_sub,
            route_type=route_type,
            domain=domain,
            model=model_name,
            rag_used=rag_used,
            latency_ms_total=latency_ms_total,
            latency_ms_llm=latency_ms_llm,
            latency_ms_retrieval=latency_ms_retrieval,
            error_code=error_code,
            pii_detected_input=pii_detected_input,
            pii_detected_output=pii_detected_output,
            oos=oos,
            rag=rag_info,
        )

        # 5. Event 구성
        event = TelemetryEvent(
            event_id=uuid4(),
            event_type="CHAT_TURN",
            trace_id=ctx.trace_id,
            conversation_id=ctx.conversation_id,
            turn_id=ctx.turn_id,
            user_id=ctx.user_id,
            dept_id=ctx.dept_id,
            occurred_at=datetime.now(timezone.utc),
            payload=payload,
        )

        # 6. Enqueue (결과와 관계없이 마킹)
        enqueue_result = publisher.enqueue(event)

        # 7. 발행 마킹 (중복 방지)
        mark_chat_turn_emitted()

        if enqueue_result:
            logger.debug(
                "CHAT_TURN event enqueued",
                extra={
                    "trace_id": ctx.trace_id,
                    "intent_main": intent_main,
                    "route_type": route_type,
                },
            )
        else:
            logger.warning(
                "CHAT_TURN event enqueue failed (queue full or disabled)",
                extra={"trace_id": ctx.trace_id},
            )

        return enqueue_result

    except Exception as e:
        # 예외 발생 시에도 마킹 (중복 방지 목적)
        mark_chat_turn_emitted()
        logger.error(
            "Error emitting CHAT_TURN event",
            extra={"error": str(e)},
        )
        return False


# =============================================================================
# Security Emission Guard - 요청 단위 Dedup
# =============================================================================

# 현재 요청에서 발행된 SECURITY 이벤트 키 집합
# 키 형식: f"{block_type}:{rule_id or 'NONE'}"
_security_emitted_keys: ContextVar[Set[str]] = ContextVar(
    "security_emitted_keys",
    default=None,
)


def _get_security_emitted_set() -> Set[str]:
    """현재 요청의 SECURITY 발행 키 집합을 반환합니다."""
    keys = _security_emitted_keys.get()
    if keys is None:
        keys = set()
        _security_emitted_keys.set(keys)
    return keys


def mark_security_emitted(key: str) -> None:
    """SECURITY 이벤트가 발행되었음을 마킹합니다.

    Args:
        key: 발행 키 (예: "PII_BLOCK:RULE-001")
    """
    keys = _get_security_emitted_set()
    keys.add(key)


def is_security_emitted(key: str) -> bool:
    """현재 요청에서 해당 SECURITY 이벤트가 이미 발행되었는지 확인합니다.

    Args:
        key: 발행 키 (예: "PII_BLOCK:RULE-001")

    Returns:
        True: 이미 발행됨 (중복)
        False: 아직 발행되지 않음
    """
    keys = _get_security_emitted_set()
    return key in keys


def reset_security_emitted() -> None:
    """SECURITY 발행 상태를 초기화합니다.

    테스트 또는 새 요청 시작 시 호출합니다.
    """
    _security_emitted_keys.set(None)


# =============================================================================
# emit_security_event_once - SECURITY 이벤트 단일 발행 함수
# =============================================================================


def emit_security_event_once(
    *,
    block_type: Literal["PII_BLOCK", "EXTERNAL_DOMAIN_BLOCK"],
    blocked: bool,
    rule_id: Optional[str] = None,
) -> bool:
    """SECURITY 이벤트를 발행합니다 (요청 단위 dedup).

    동작 규칙:
    1. 필수 컨텍스트(trace/user/dept/conversation/turn) 없으면 → drop (False)
    2. publisher 없으면 → drop (False)
    3. 같은 (block_type, rule_id) 조합이 이미 발행됐으면 → skip (False)
    4. enqueue 결과와 관계없이 발행 마킹 (중복 방지 목적)

    Args:
        block_type: 차단 유형 ("PII_BLOCK" 또는 "EXTERNAL_DOMAIN_BLOCK")
        blocked: 실제 차단 여부 (대부분 True)
        rule_id: 적용된 규칙 ID (선택)

    Returns:
        True: 이벤트가 성공적으로 enqueue됨
        False: 중복, 컨텍스트 누락, publisher 없음, 또는 enqueue 실패
    """
    try:
        # 1. 컨텍스트에서 필수값 가져오기
        ctx = get_request_context()

        # 필수값 검증: trace/user/dept/conversation/turn
        if not ctx.trace_id:
            logger.debug("Missing trace_id, dropping SECURITY event")
            return False
        if not ctx.user_id:
            logger.debug("Missing user_id, dropping SECURITY event")
            return False
        if not ctx.dept_id:
            logger.debug("Missing dept_id, dropping SECURITY event")
            return False
        if not ctx.conversation_id:
            logger.debug("Missing conversation_id, dropping SECURITY event")
            return False
        if ctx.turn_id is None:
            logger.debug("Missing turn_id, dropping SECURITY event")
            return False

        # 2. Publisher 확인
        publisher = get_telemetry_publisher()
        if publisher is None:
            logger.debug("TelemetryPublisher not available, dropping SECURITY event")
            return False

        # 3. Dedup 체크
        key = f"{block_type}:{rule_id or 'NONE'}"
        if is_security_emitted(key):
            logger.debug(
                "SECURITY event already emitted for this key, skipping",
                extra={"key": key},
            )
            return False

        # 4. Payload 구성
        payload = SecurityPayload(
            block_type=block_type,
            blocked=blocked,
            rule_id=rule_id,
        )

        # 5. Event 구성
        event = TelemetryEvent(
            event_id=uuid4(),
            event_type="SECURITY",
            trace_id=ctx.trace_id,
            conversation_id=ctx.conversation_id,
            turn_id=ctx.turn_id,
            user_id=ctx.user_id,
            dept_id=ctx.dept_id,
            occurred_at=datetime.now(timezone.utc),
            payload=payload,
        )

        # 6. Enqueue (결과와 관계없이 마킹)
        enqueue_result = publisher.enqueue(event)

        # 7. 발행 마킹 (중복 방지)
        mark_security_emitted(key)

        if enqueue_result:
            logger.debug(
                "SECURITY event enqueued",
                extra={
                    "trace_id": ctx.trace_id,
                    "block_type": block_type,
                    "rule_id": rule_id,
                },
            )
        else:
            logger.warning(
                "SECURITY event enqueue failed (queue full or disabled)",
                extra={"trace_id": ctx.trace_id},
            )

        return enqueue_result

    except Exception as e:
        # 예외 발생 시에도 마킹 시도 (중복 방지 목적)
        try:
            key = f"{block_type}:{rule_id or 'NONE'}"
            mark_security_emitted(key)
        except Exception:
            pass
        logger.error(
            "Error emitting SECURITY event",
            extra={"error": str(e)},
        )
        return False


# =============================================================================
# Feedback Emission Guard - 요청 단위 Dedup
# =============================================================================

# 현재 요청에서 발행된 FEEDBACK 이벤트 키 집합
# 키 형식: f"{target_conversation_id}:{target_turn_id}:{feedback}"
_feedback_emitted_keys: ContextVar[Set[str]] = ContextVar(
    "feedback_emitted_keys",
    default=None,
)


def _get_feedback_emitted_set() -> Set[str]:
    """현재 요청의 FEEDBACK 발행 키 집합을 반환합니다."""
    keys = _feedback_emitted_keys.get()
    if keys is None:
        keys = set()
        _feedback_emitted_keys.set(keys)
    return keys


def mark_feedback_emitted(key: str) -> None:
    """FEEDBACK 이벤트가 발행되었음을 마킹합니다.

    Args:
        key: 발행 키 (예: "conv-123:1:like")
    """
    keys = _get_feedback_emitted_set()
    keys.add(key)


def is_feedback_emitted(key: str) -> bool:
    """현재 요청에서 해당 FEEDBACK 이벤트가 이미 발행되었는지 확인합니다.

    Args:
        key: 발행 키 (예: "conv-123:1:like")

    Returns:
        True: 이미 발행됨 (중복)
        False: 아직 발행되지 않음
    """
    keys = _get_feedback_emitted_set()
    return key in keys


def reset_feedback_emitted() -> None:
    """FEEDBACK 발행 상태를 초기화합니다.

    테스트 또는 새 요청 시작 시 호출합니다.
    """
    _feedback_emitted_keys.set(None)


# =============================================================================
# emit_feedback_event_once - FEEDBACK 이벤트 단일 발행 함수
# =============================================================================


def emit_feedback_event_once(
    *,
    feedback: Literal["like", "dislike"],
    target_conversation_id: str,
    target_turn_id: int,
) -> bool:
    """FEEDBACK 이벤트를 발행합니다 (요청 단위 dedup).

    동작 규칙:
    1. 필수 컨텍스트(trace/user/dept/conversation/turn) 없으면 → drop (False)
    2. publisher 없으면 → drop (False)
    3. 같은 (target_conversation_id, target_turn_id, feedback) 조합이 이미 발행됐으면 → skip (False)
    4. enqueue 결과와 관계없이 발행 마킹 (중복 방지 목적)

    Args:
        feedback: 피드백 값 ("like" 또는 "dislike")
        target_conversation_id: 대상 대화 ID
        target_turn_id: 대상 턴 ID

    Returns:
        True: 이벤트가 성공적으로 enqueue됨
        False: 중복, 컨텍스트 누락, publisher 없음, 또는 enqueue 실패
    """
    try:
        # 1. 컨텍스트에서 필수값 가져오기
        ctx = get_request_context()

        # 필수값 검증: trace/user/dept/conversation/turn
        if not ctx.trace_id:
            logger.debug("Missing trace_id, dropping FEEDBACK event")
            return False
        if not ctx.user_id:
            logger.debug("Missing user_id, dropping FEEDBACK event")
            return False
        if not ctx.dept_id:
            logger.debug("Missing dept_id, dropping FEEDBACK event")
            return False
        if not ctx.conversation_id:
            logger.debug("Missing conversation_id, dropping FEEDBACK event")
            return False
        if ctx.turn_id is None:
            logger.debug("Missing turn_id, dropping FEEDBACK event")
            return False

        # 2. Publisher 확인
        publisher = get_telemetry_publisher()
        if publisher is None:
            logger.debug("TelemetryPublisher not available, dropping FEEDBACK event")
            return False

        # 3. Dedup 체크
        key = f"{target_conversation_id}:{target_turn_id}:{feedback}"
        if is_feedback_emitted(key):
            logger.debug(
                "FEEDBACK event already emitted for this key, skipping",
                extra={"key": key},
            )
            return False

        # 4. Payload 구성
        payload = FeedbackPayload(
            feedback=feedback,
            target_conversation_id=target_conversation_id,
            target_turn_id=target_turn_id,
        )

        # 5. Event 구성
        event = TelemetryEvent(
            event_id=uuid4(),
            event_type="FEEDBACK",
            trace_id=ctx.trace_id,
            conversation_id=ctx.conversation_id,
            turn_id=ctx.turn_id,
            user_id=ctx.user_id,
            dept_id=ctx.dept_id,
            occurred_at=datetime.now(timezone.utc),
            payload=payload,
        )

        # 6. Enqueue (결과와 관계없이 마킹)
        enqueue_result = publisher.enqueue(event)

        # 7. 발행 마킹 (중복 방지)
        mark_feedback_emitted(key)

        if enqueue_result:
            logger.debug(
                "FEEDBACK event enqueued",
                extra={
                    "trace_id": ctx.trace_id,
                    "feedback": feedback,
                    "target_conversation_id": target_conversation_id,
                    "target_turn_id": target_turn_id,
                },
            )
        else:
            logger.warning(
                "FEEDBACK event enqueue failed (queue full or disabled)",
                extra={"trace_id": ctx.trace_id},
            )

        return enqueue_result

    except Exception as e:
        # 예외 발생 시에도 마킹 시도 (중복 방지 목적)
        try:
            key = f"{target_conversation_id}:{target_turn_id}:{feedback}"
            mark_feedback_emitted(key)
        except Exception:
            pass
        logger.error(
            "Error emitting FEEDBACK event",
            extra={"error": str(e)},
        )
        return False
