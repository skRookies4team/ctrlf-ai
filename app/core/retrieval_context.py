# app/core/retrieval_context.py
"""
Phase 50: Retrieval Context (2차 가드용 contextvars)

contextvars를 사용하여 요청 단위로 retrieval 차단 플래그를 관리합니다.

사용법:
    # ChatService에서 금지질문 판정 시
    from app.core.retrieval_context import set_retrieval_blocked, reset_retrieval_context

    set_retrieval_blocked(True, "FORBIDDEN_QUERY:rule_001")

    # RagHandler/MilvusClient에서 체크
    from app.core.retrieval_context import is_retrieval_blocked, get_block_reason

    if is_retrieval_blocked():
        reason = get_block_reason()
        raise RetrievalBlockedError(f"Retrieval blocked: {reason}")

    # 요청 완료 후 컨텍스트 리셋
    reset_retrieval_context()
"""
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Context Variables (요청 단위로 격리됨)
# =============================================================================

# retrieval 차단 여부
_retrieval_blocked: ContextVar[bool] = ContextVar("retrieval_blocked", default=False)

# 차단 사유 (로깅/디버깅용)
_block_reason: ContextVar[Optional[str]] = ContextVar("block_reason", default=None)

# 룰셋 버전 (트레이싱용)
_ruleset_version: ContextVar[Optional[str]] = ContextVar("ruleset_version", default=None)


# =============================================================================
# Custom Exception
# =============================================================================


class RetrievalBlockedError(Exception):
    """Retrieval이 차단되었을 때 발생하는 예외.

    2차 가드에서 실수로 Milvus/RAG가 호출되려 할 때 발생합니다.
    """

    def __init__(self, reason: str = "Retrieval blocked by context flag"):
        self.reason = reason
        super().__init__(reason)


# =============================================================================
# Context Management Functions
# =============================================================================


def set_retrieval_blocked(blocked: bool, reason: Optional[str] = None) -> None:
    """Retrieval 차단 플래그를 설정합니다.

    Args:
        blocked: True면 retrieval 차단
        reason: 차단 사유 (예: "FORBIDDEN_QUERY:rule_001")
    """
    _retrieval_blocked.set(blocked)
    _block_reason.set(reason)

    if blocked:
        logger.debug(f"Retrieval context blocked: reason={reason}")


def is_retrieval_blocked() -> bool:
    """Retrieval이 차단되었는지 확인합니다."""
    return _retrieval_blocked.get()


def get_block_reason() -> Optional[str]:
    """차단 사유를 반환합니다."""
    return _block_reason.get()


def set_ruleset_version(version: str) -> None:
    """룰셋 버전을 컨텍스트에 저장합니다."""
    _ruleset_version.set(version)


def get_ruleset_version() -> Optional[str]:
    """룰셋 버전을 반환합니다."""
    return _ruleset_version.get()


def reset_retrieval_context() -> None:
    """Retrieval 컨텍스트를 리셋합니다.

    요청 처리 완료 후 호출해야 합니다 (메모리 누수 방지).
    """
    _retrieval_blocked.set(False)
    _block_reason.set(None)
    _ruleset_version.set(None)


# =============================================================================
# Context Manager (with 문 지원)
# =============================================================================


class retrieval_blocked_context:
    """Retrieval 차단 컨텍스트 매니저.

    with 문으로 사용하면 블록 종료 시 자동으로 리셋됩니다.

    Example:
        with retrieval_blocked_context("FORBIDDEN_QUERY:rule_001"):
            # 이 블록 내에서는 retrieval이 차단됨
            pass
        # 블록 종료 후 자동 리셋
    """

    def __init__(self, reason: str):
        self.reason = reason
        self._previous_blocked: bool = False
        self._previous_reason: Optional[str] = None

    def __enter__(self):
        # 이전 상태 저장
        self._previous_blocked = _retrieval_blocked.get()
        self._previous_reason = _block_reason.get()

        # 차단 설정
        set_retrieval_blocked(True, self.reason)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # 이전 상태 복원
        _retrieval_blocked.set(self._previous_blocked)
        _block_reason.set(self._previous_reason)
        return False


# =============================================================================
# Guard Check Helper (RagHandler/MilvusClient에서 사용)
# =============================================================================


def check_retrieval_allowed(component: str = "unknown") -> None:
    """Retrieval이 허용되는지 확인하고, 차단 시 예외를 발생시킵니다.

    Args:
        component: 호출 컴포넌트 이름 (로깅용)

    Raises:
        RetrievalBlockedError: retrieval이 차단된 경우
    """
    if is_retrieval_blocked():
        reason = get_block_reason() or "unknown"
        logger.warning(
            f"Retrieval blocked at {component}: reason={reason}"
        )
        raise RetrievalBlockedError(
            f"Retrieval blocked at {component}: {reason}"
        )
