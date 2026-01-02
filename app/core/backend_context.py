# app/core/backend_context.py
"""
Step 3: Backend Context (2차 가드용 contextvars)

contextvars를 사용하여 요청 단위로 Backend API 차단 플래그를 관리합니다.

사용법:
    # ChatService에서 금지질문 판정 시 (skip_backend_api=True인 경우)
    from app.core.backend_context import set_backend_blocked, reset_backend_context

    set_backend_blocked(True, "FORBIDDEN_BACKEND:rule_001")

    # PersonalizationClient/BackendHandler에서 체크
    from app.core.backend_context import is_backend_blocked, get_backend_block_reason

    if is_backend_blocked():
        reason = get_backend_block_reason()
        raise BackendBlockedError(f"Backend API blocked: {reason}")

    # 요청 완료 후 컨텍스트 리셋
    reset_backend_context()
"""
from contextvars import ContextVar
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Context Variables (요청 단위로 격리됨)
# =============================================================================

# Backend API 차단 여부
_backend_blocked: ContextVar[bool] = ContextVar("backend_blocked", default=False)

# 차단 사유 (로깅/디버깅용)
_backend_block_reason: ContextVar[Optional[str]] = ContextVar("backend_block_reason", default=None)


# =============================================================================
# Custom Exception
# =============================================================================


class BackendBlockedError(Exception):
    """Backend API가 차단되었을 때 발생하는 예외.

    2차 가드에서 실수로 Backend API가 호출되려 할 때 발생합니다.
    """

    def __init__(self, reason: str = "Backend API blocked by context flag"):
        self.reason = reason
        super().__init__(reason)


# =============================================================================
# Context Management Functions
# =============================================================================


def set_backend_blocked(blocked: bool, reason: Optional[str] = None) -> None:
    """Backend API 차단 플래그를 설정합니다.

    Args:
        blocked: True면 Backend API 차단
        reason: 차단 사유 (예: "FORBIDDEN_BACKEND:rule_001")
    """
    _backend_blocked.set(blocked)
    _backend_block_reason.set(reason)

    if blocked:
        logger.debug(f"Backend context blocked: reason={reason}")


def is_backend_blocked() -> bool:
    """Backend API가 차단되었는지 확인합니다."""
    return _backend_blocked.get()


def get_backend_block_reason() -> Optional[str]:
    """Backend 차단 사유를 반환합니다."""
    return _backend_block_reason.get()


def reset_backend_context() -> None:
    """Backend 컨텍스트를 리셋합니다.

    요청 처리 완료 후 호출해야 합니다 (메모리 누수 방지).
    """
    _backend_blocked.set(False)
    _backend_block_reason.set(None)


# =============================================================================
# Context Manager (with 문 지원)
# =============================================================================


class backend_blocked_context:
    """Backend 차단 컨텍스트 매니저.

    with 문으로 사용하면 블록 종료 시 자동으로 리셋됩니다.

    Example:
        with backend_blocked_context("FORBIDDEN_BACKEND:rule_001"):
            # 이 블록 내에서는 Backend API가 차단됨
            pass
        # 블록 종료 후 자동 리셋
    """

    def __init__(self, reason: str):
        self.reason = reason
        self._previous_blocked: bool = False
        self._previous_reason: Optional[str] = None

    def __enter__(self):
        # 이전 상태 저장
        self._previous_blocked = _backend_blocked.get()
        self._previous_reason = _backend_block_reason.get()

        # 차단 설정
        set_backend_blocked(True, self.reason)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # 이전 상태 복원
        _backend_blocked.set(self._previous_blocked)
        _backend_block_reason.set(self._previous_reason)
        return False


# =============================================================================
# Guard Check Helper (PersonalizationClient/BackendHandler에서 사용)
# =============================================================================


def check_backend_allowed(component: str = "unknown") -> None:
    """Backend API 호출이 허용되는지 확인하고, 차단 시 예외를 발생시킵니다.

    Args:
        component: 호출 컴포넌트 이름 (로깅용)

    Raises:
        BackendBlockedError: Backend API가 차단된 경우
    """
    if is_backend_blocked():
        reason = get_backend_block_reason() or "unknown"
        logger.warning(
            f"Backend API blocked at {component}: reason={reason}"
        )
        raise BackendBlockedError(
            f"Backend API blocked at {component}: {reason}"
        )
