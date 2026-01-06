"""
타임아웃 정책 모듈 (Timeout Policy)

라우트/질문 난이도에 따른 차등 타임아웃을 결정합니다.

사용 예시:
    from app.services.chat.timeout_policy import (
        pick_llm_timeout,
        pick_rag_timeout,
        pick_backend_timeout,
        TimeoutContext,
    )

    ctx = TimeoutContext.from_intent(intent_main, sub_intent)
    llm_timeout = pick_llm_timeout(settings, ctx)
    rag_timeout = pick_rag_timeout(settings, ctx)
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Set

if TYPE_CHECKING:
    from app.core.config import Settings


# =============================================================================
# 장문 생성 / 복잡 쿼리 판별 상수
# =============================================================================

# 장문 생성이 필요한 인텐트 (체크리스트, 가이드, 요약 등)
LONGFORM_INTENTS: Set[str] = {
    "Q13",  # 교육 자료 요약
    "Q14",  # 체크리스트 생성
    "CHECKLIST",
    "GUIDE",
    "SUMMARY",
    "REPORT",
    "SCRIPT_GENERATION",
    "VIDEO_SCRIPT",
}

# 복잡한 쿼리가 예상되는 인텐트 (여러 문서 참조, 통계 분석 등)
COMPLEX_INTENTS: Set[str] = {
    "Q05",  # 부서별 교육 현황 통계
    "Q06",  # 사고 통계
    "Q11",  # 복합 질문
    "Q12",  # 연차 사용 이력
    "Q15",  # 복지 포인트 사용 내역
    "STATISTICS",
    "ANALYSIS",
    "COMPARISON",
    "MULTI_DOCUMENT",
}

# 단순 조회 인텐트 (빠른 응답 기대)
SIMPLE_INTENTS: Set[str] = {
    "Q01",  # 단순 정책 조회
    "Q02",  # 절차 안내
    "Q03",  # 연락처/담당자
    "Q04",  # 개인 정보 조회
    "FAQ",
    "GREETING",
    "SIMPLE_QUERY",
}


@dataclass
class TimeoutContext:
    """
    타임아웃 결정에 필요한 컨텍스트.

    Attributes:
        is_longform: 장문 생성 여부
        is_complex: 복잡한 쿼리 여부
        is_simple: 단순 조회 여부
        query_length: 쿼리 길이 (글자 수)
    """

    is_longform: bool = False
    is_complex: bool = False
    is_simple: bool = False
    query_length: int = 0

    @classmethod
    def from_intent(
        cls,
        intent_main: Optional[str],
        sub_intent: Optional[str] = None,
        query: Optional[str] = None,
    ) -> "TimeoutContext":
        """
        인텐트와 쿼리로부터 타임아웃 컨텍스트를 생성합니다.

        Args:
            intent_main: 메인 인텐트 (예: "Q01", "FAQ")
            sub_intent: 서브 인텐트 (선택)
            query: 사용자 쿼리 (선택)

        Returns:
            TimeoutContext: 타임아웃 결정 컨텍스트
        """
        intents_to_check = {intent_main, sub_intent} - {None}

        is_longform = bool(intents_to_check & LONGFORM_INTENTS)
        is_complex = bool(intents_to_check & COMPLEX_INTENTS)
        is_simple = bool(intents_to_check & SIMPLE_INTENTS)

        query_length = len(query) if query else 0

        # 쿼리가 길면 복잡한 쿼리로 간주 (500자 이상)
        if query_length > 500 and not is_longform:
            is_complex = True

        return cls(
            is_longform=is_longform,
            is_complex=is_complex,
            is_simple=is_simple,
            query_length=query_length,
        )


def pick_llm_timeout(settings: "Settings", ctx: TimeoutContext) -> float:
    """
    LLM 호출 타임아웃을 결정합니다.

    Args:
        settings: 설정 객체
        ctx: 타임아웃 컨텍스트

    Returns:
        float: LLM 타임아웃 (초)
    """
    if ctx.is_longform:
        return settings.TIMEOUT_LLM_LONGFORM_SEC  # 120초
    if ctx.is_complex:
        return settings.TIMEOUT_LLM_COMPLEX_SEC   # 60초
    return settings.TIMEOUT_LLM_SIMPLE_SEC        # 30초


def pick_rag_timeout(settings: "Settings", ctx: TimeoutContext) -> float:
    """
    RAG 검색 타임아웃을 결정합니다.

    Args:
        settings: 설정 객체
        ctx: 타임아웃 컨텍스트

    Returns:
        float: RAG 타임아웃 (초)
    """
    if ctx.is_longform:
        return settings.TIMEOUT_RAG_LONGFORM_SEC  # 30초
    if ctx.is_complex:
        return settings.TIMEOUT_RAG_COMPLEX_SEC   # 20초
    return settings.TIMEOUT_RAG_SIMPLE_SEC        # 10초


def pick_backend_timeout(settings: "Settings", ctx: TimeoutContext) -> float:
    """
    Backend API 타임아웃을 결정합니다.

    Args:
        settings: 설정 객체
        ctx: 타임아웃 컨텍스트

    Returns:
        float: Backend 타임아웃 (초)
    """
    if ctx.is_complex:
        return settings.TIMEOUT_BACKEND_SLOW_SEC   # 30초 (통계/집계)
    if ctx.is_simple:
        return settings.TIMEOUT_BACKEND_FAST_SEC   # 5초 (단순 조회)
    return settings.TIMEOUT_BACKEND_NORMAL_SEC     # 15초 (일반)


def get_all_timeouts(
    settings: "Settings",
    ctx: TimeoutContext,
) -> dict:
    """
    모든 서비스의 타임아웃을 한 번에 반환합니다.

    Args:
        settings: 설정 객체
        ctx: 타임아웃 컨텍스트

    Returns:
        dict: 서비스별 타임아웃
            {
                "llm": float,
                "rag": float,
                "backend": float,
            }
    """
    return {
        "llm": pick_llm_timeout(settings, ctx),
        "rag": pick_rag_timeout(settings, ctx),
        "backend": pick_backend_timeout(settings, ctx),
    }
