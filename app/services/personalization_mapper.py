"""
Personalization Mapper (개인화 매퍼)

SubIntentId (HR_LEAVE_CHECK 등) -> PersonalizationSubIntentId (Q1-Q20)로 변환하는 매핑 레이어.

rule_router가 Q를 직접 반환하지 않는 현재 구조에서,
ChatService에서 한 번 보정하여 개인화 처리로 연결합니다.

Usage:
    from app.services.personalization_mapper import to_personalization_q

    q = to_personalization_q(sub_intent_id="HR_LEAVE_CHECK", query="내 연차 며칠?")
    # q = "Q11"
"""

from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# SubIntentId -> PersonalizationSubIntentId 직접 매핑
# =============================================================================

# 단일 SubIntentId -> Q 매핑 (1:1 대응)
SUBINTENT_TO_Q: dict[str, str] = {
    # HR 관련
    "HR_LEAVE_CHECK": "Q11",       # 남은 연차 일수
    "HR_WELFARE_CHECK": "Q14",     # 복지/식대 포인트
    "HR_ATTENDANCE_CHECK": "Q20",  # 올해 HR 할 일 (근태 포함)
}

# EDU_STATUS_CHECK 세분화용 키워드 매핑
EDU_STATUS_KEYWORDS: dict[str, list[str]] = {
    "Q1": ["미이수", "안 들은", "안들은", "필수 미이수", "필수교육 미이수", "안한 교육", "안 한 교육"],
    "Q3": ["데드라인", "마감", "이번 달", "이번달", "이달", "이달 내", "곧 마감"],
    "Q9": ["이번 주", "이번주", "할 일", "해야 할", "해야할", "이주", "이주 내", "금주"],
}

# Q로 시작하는지 확인
def is_personalization_q(sub_intent_id: str) -> bool:
    """sub_intent_id가 이미 Q1-Q20 형식인지 확인합니다.

    Args:
        sub_intent_id: 확인할 sub_intent_id

    Returns:
        bool: Q1-Q20 형식이면 True
    """
    if not sub_intent_id:
        return False
    if not sub_intent_id.startswith("Q"):
        return False
    # Q 다음이 숫자인지 확인
    rest = sub_intent_id[1:]
    return rest.isdigit() and 1 <= int(rest) <= 20


def to_personalization_q(
    sub_intent_id: str,
    query: str,
) -> Optional[str]:
    """SubIntentId를 PersonalizationSubIntentId(Q1-Q20)로 변환합니다.

    Args:
        sub_intent_id: rule_router에서 반환된 sub_intent_id
                      (예: "HR_LEAVE_CHECK", "EDU_STATUS_CHECK", 또는 이미 "Q11")
        query: 사용자 질문 (EDU_STATUS_CHECK 세분화에 사용)

    Returns:
        Optional[str]: 변환된 Q ID (예: "Q11", "Q1") 또는 None (개인화 대상 아님)

    Examples:
        >>> to_personalization_q("HR_LEAVE_CHECK", "내 연차 며칠?")
        "Q11"

        >>> to_personalization_q("EDU_STATUS_CHECK", "미이수 교육 알려줘")
        "Q1"

        >>> to_personalization_q("EDU_STATUS_CHECK", "이번 주 할 일 뭐야?")
        "Q9"

        >>> to_personalization_q("QUIZ_START", "퀴즈 시작")
        None  # 개인화 대상 아님 (액션)
    """
    if not sub_intent_id:
        return None

    # 이미 Q1-Q20 형식이면 그대로 반환
    if is_personalization_q(sub_intent_id):
        logger.debug(f"Already personalization Q: {sub_intent_id}")
        return sub_intent_id

    # 직접 매핑 확인
    if sub_intent_id in SUBINTENT_TO_Q:
        q = SUBINTENT_TO_Q[sub_intent_id]
        logger.debug(f"Mapped {sub_intent_id} -> {q}")
        return q

    # EDU_STATUS_CHECK 세분화
    if sub_intent_id == "EDU_STATUS_CHECK":
        q = _classify_edu_status(query)
        logger.debug(f"EDU_STATUS_CHECK classified as {q} for query: {query[:50]}...")
        return q

    # 매핑되지 않음 (개인화 대상 아님)
    logger.debug(f"No personalization mapping for: {sub_intent_id}")
    return None


def _classify_edu_status(query: str) -> str:
    """EDU_STATUS_CHECK를 query 키워드로 Q1/Q2/Q3/Q9 중 하나로 세분화합니다.

    Args:
        query: 사용자 질문

    Returns:
        str: Q1 (미이수), Q3 (마감 임박), Q9 (이번 주 할 일), Q2 (기본: 수료현황)
    """
    q_lower = query.lower()

    # Q1: 미이수 필수 교육
    for keyword in EDU_STATUS_KEYWORDS["Q1"]:
        if keyword in q_lower:
            return "Q1"

    # Q3: 이번 달 데드라인
    for keyword in EDU_STATUS_KEYWORDS["Q3"]:
        if keyword in q_lower:
            return "Q3"

    # Q9: 이번 주 할 일
    for keyword in EDU_STATUS_KEYWORDS["Q9"]:
        if keyword in q_lower:
            return "Q9"

    # 기본: Q2 (수료현황/진도)
    return "Q2"


# =============================================================================
# 개인화 대상 여부 판단
# =============================================================================

# 개인화 조회 대상 SubIntentId 집합
PERSONALIZATION_SUBINTENTS = frozenset([
    # 직접 매핑되는 SubIntentId
    "HR_LEAVE_CHECK",
    "HR_WELFARE_CHECK",
    "HR_ATTENDANCE_CHECK",
    "EDU_STATUS_CHECK",
    # 이미 Q 형식인 경우도 포함
    *[f"Q{i}" for i in range(1, 21)],
])


def is_personalization_request(
    sub_intent_id: str,
) -> bool:
    """개인화 조회 대상인지 확인합니다.

    Args:
        sub_intent_id: 확인할 sub_intent_id

    Returns:
        bool: 개인화 조회 대상이면 True
    """
    if not sub_intent_id:
        return False

    # Q1-Q20 형식
    if is_personalization_q(sub_intent_id):
        return True

    # 매핑 가능한 SubIntentId
    return sub_intent_id in PERSONALIZATION_SUBINTENTS
