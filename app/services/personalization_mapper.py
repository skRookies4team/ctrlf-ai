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
    # HR 관련 - 명확한 sub_intent_id가 있는 경우
    "HR_WELFARE_CHECK": "Q14",     # 복지/식대 포인트
    "HR_ATTENDANCE_CHECK": "Q10",  # 내 근태 현황
    "HR_TODO_CHECK": "Q20",        # 올해 HR 할 일 (미완료)
    # HR_LEAVE_CHECK은 query 기반으로 세분화 (아래 _classify_hr_leave 함수)

    # EDU 관련 - 개인화 조회
    "EDU_RESUME_CHECK": "Q4",      # 특정 교육 진도율/시청률 (이어보기)

    # QUIZ 관련 - 개인화 조회
    "QUIZ_PENDING_CHECK": "Q7",    # 미완료/재응시 퀴즈 조회
    # QUIZ_SCORE_CHECK는 query 기반으로 세분화 (아래 _classify_quiz_score 함수)
}

# HR_LEAVE_CHECK 세분화용 키워드 (RuleRouter가 모든 HR을 HR_LEAVE_CHECK으로 분류하므로)
# Q15: 복지 포인트 사용 내역 조회용 키워드 (Q14보다 우선 체크)
# 주의: "복지" 또는 "포인트"가 명시적으로 포함된 키워드만 사용
HR_WELFARE_HISTORY_KEYWORDS = frozenset([
    "포인트 사용", "포인트사용", "포인트 내역", "포인트내역", "복지 내역", "복지내역",
    "복지 사용", "복지사용", "복지 이력", "복지이력",
    "포인트 이력", "포인트이력", "포인트 썼", "복지 썼", "복지포인트 썼",
    "포인트 어디", "복지 어디", "포인트 뭐에", "복지 뭐에",
])
# Q14: 복지/식대 포인트 잔액 조회용 키워드
HR_WELFARE_KEYWORDS = frozenset([
    "복지", "복지포인트", "복지 포인트", "포인트 잔액", "식대", "선택복지",
    "잔액", "얼마 남", "얼마남", "남은 포인트",
])
HR_ATTENDANCE_KEYWORDS = frozenset([
    "근태", "출근", "퇴근", "근태현황", "근태 현황",
])
# Q12: 연차 사용 이력 조회용 키워드
HR_LEAVE_HISTORY_KEYWORDS = frozenset([
    "연차 사용", "연차사용", "연차 이력", "연차이력", "연차 내역", "연차내역",
    "휴가 이력", "휴가이력", "휴가 내역", "휴가내역", "휴가 사용", "휴가사용",
    "언제 썼", "언제썼", "언제 사용", "언제사용", "사용 내역", "사용내역",
    "쓴 연차", "썼던", "사용한 연차", "사용한 휴가",
    "연차 썼", "연차썼", "휴가 썼", "휴가썼",  # 추가: "언제 연차 썼어" 등 패턴
])

# Q13: 급여 명세서 요약 조회용 키워드
HR_SALARY_KEYWORDS = frozenset([
    "급여", "월급", "명세서", "급여명세", "급여 명세", "월급명세", "월급 명세",
    "봉급", "급료", "페이", "pay", "salary", "이번 달 급여", "이번달 급여",
    "급여 내역", "급여내역", "실수령", "실수령액", "세후", "세전",
])

# Q16: 내 인사 정보 조회용 키워드
HR_PERSONAL_INFO_KEYWORDS = frozenset([
    "인사 정보", "인사정보", "내 정보", "내정보", "프로필", "사원정보", "사원 정보",
    "입사일", "입사 일", "입사 언제", "언제 입사", "사원번호", "사원 번호",
    "직급", "직책", "부서", "내 부서", "나의 부서", "이메일", "연락처", "전화번호",
])

# Q17: 내 팀/부서 정보 조회용 키워드
HR_TEAM_INFO_KEYWORDS = frozenset([
    "팀 정보", "팀정보", "부서 정보", "부서정보", "우리 팀", "우리팀",
    "우리 부서", "우리부서", "팀 인원", "팀인원", "부서 인원", "부서인원",
    "팀 구성", "팀구성", "팀원", "부서원", "몇 명", "몇명",
    "팀장", "부서장",
])

# EDU_STATUS_CHECK 세분화용 키워드 매핑
EDU_STATUS_KEYWORDS: dict[str, list[str]] = {
    "Q1": ["미이수", "안 들은", "안들은", "필수 미이수", "필수교육 미이수", "안한 교육", "안 한 교육"],
    "Q3": ["데드라인", "마감", "이번 달", "이번달", "이달", "이달 내", "곧 마감"],
    "Q9": ["이번 주", "이번주", "할 일", "해야 할", "해야할", "이주", "이주 내", "금주"],
}

# =============================================================================
# 토픽 기반 인텐트 세분화용 키워드 (Q8, Q18, Q19)
# =============================================================================

# 교육 토픽 키워드 (4대교육 + 직무교육)
EDUCATION_TOPIC_KEYWORDS = frozenset([
    # 성희롱 예방
    "성희롱", "성희롱예방", "성희롱 예방",
    # 직장내 괴롭힘
    "괴롭힘", "직장내괴롭힘", "직장 내 괴롭힘", "괴롭힘예방", "괴롭힘 예방",
    # 개인정보보호
    "개인정보", "개인정보보호", "개인정보 보호", "정보보안", "보안교육", "보안 교육",
    # 장애인 인식 개선
    "장애인", "장애인인식", "장애인 인식", "장애인인식개선", "장애인 인식 개선",
    # 직무교육
    "직무", "직무교육", "직무 교육",
])

# Q8: 특정 토픽 교육 시청 완료 여부 (영상만)
EDU_WATCH_COMPLETE_KEYWORDS = frozenset([
    "다 봤", "다봤", "시청 완료", "시청완료", "봤어", "봤나", "봤는지",
    "다 들었", "다들었", "끝까지 봤", "끝까지봤", "영상 봤", "영상봤",
])

# Q18: 보안교육/특정 토픽 전체 완료 여부 (영상 + 퀴즈)
EDU_FULL_COMPLETE_KEYWORDS = frozenset([
    "이수했", "이수 했", "완료했", "완료 했", "수료했", "수료 했",
    "다 했", "다했", "끝났", "끝냈", "이수", "수료", "완료",
])

# Q19: 특정 토픽 교육 마감일 조회
EDU_TOPIC_DEADLINE_KEYWORDS = frozenset([
    "언제까지", "마감일", "기한", "마감 언제", "언제 마감",
    "데드라인", "deadline", "까지야", "까지인지", "까지에요", "언제",
])

# QUIZ_SCORE_CHECK 세분화용 키워드 (Q5: 평균 점수, Q6: 낮은 점수 과목)
QUIZ_SCORE_Q6_KEYWORDS = frozenset([
    "낮은", "가장 낮", "제일 낮", "취약", "약한", "못한",
    "높은", "가장 높", "제일 높", "잘한",
])

# Q로 시작하는지 확인
def is_personalization_q(sub_intent_id: str) -> bool:
    if not sub_intent_id:
        return False
    if not sub_intent_id.startswith("Q"):
        return False
    rest = sub_intent_id[1:]
    return rest.isdigit() and 1 <= int(rest) <= 20


def to_personalization_q(
    sub_intent_id: str,
    query: str,
) -> Optional[str]:
    if not sub_intent_id:
        return None

    if is_personalization_q(sub_intent_id):
        logger.debug(f"Already personalization Q: {sub_intent_id}")
        return sub_intent_id

    if sub_intent_id in SUBINTENT_TO_Q:
        q = SUBINTENT_TO_Q[sub_intent_id]
        logger.debug(f"Mapped {sub_intent_id} -> {q}")
        return q

    if sub_intent_id == "HR_LEAVE_CHECK":
        q = _classify_hr_leave(query)
        logger.debug(f"HR_LEAVE_CHECK classified as {q}, query_len={len(query)}")
        return q

    if sub_intent_id == "EDU_STATUS_CHECK":
        q = _classify_edu_status(query)
        logger.debug(f"EDU_STATUS_CHECK classified as {q}, query_len={len(query)}")
        return q

    if sub_intent_id == "QUIZ_SCORE_CHECK":
        q = _classify_quiz_score(query)
        logger.debug(f"QUIZ_SCORE_CHECK classified as {q}, query_len={len(query)}")
        return q

    logger.debug(f"No personalization mapping for: {sub_intent_id}")
    return None


def _contains_any(text: str, keywords: frozenset) -> bool:
    return any(keyword in text for keyword in keywords)


def _classify_edu_status(query: str) -> str:
    q_lower = query.lower()

    # 토픽 기반 인텐트 우선 체크 (Q8, Q18, Q19)
    has_topic = _contains_any(q_lower, EDUCATION_TOPIC_KEYWORDS)

    if has_topic:
        # Q19: 토픽 + 마감일
        if _contains_any(q_lower, EDU_TOPIC_DEADLINE_KEYWORDS):
            logger.debug(f"Topic-based Q19 detected (deadline)")
            return "Q19"

        # Q8: 토픽 + 시청 완료 (영상만)
        if _contains_any(q_lower, EDU_WATCH_COMPLETE_KEYWORDS):
            logger.debug(f"Topic-based Q8 detected (video watch)")
            return "Q8"

        # Q18: 토픽 + 이수/완료 (영상 + 퀴즈)
        if _contains_any(q_lower, EDU_FULL_COMPLETE_KEYWORDS):
            logger.debug(f"Topic-based Q18 detected (full completion)")
            return "Q18"

    # Q1: 미이수 필수 교육
    for keyword in EDU_STATUS_KEYWORDS["Q1"]:
        if keyword in q_lower:
            return "Q1"

    # Q3: 이번 달 데드라인 (토픽 없는 경우)
    if not has_topic:
        for keyword in EDU_STATUS_KEYWORDS["Q3"]:
            if keyword in q_lower:
                return "Q3"

    # Q9: 이번 주 할 일
    for keyword in EDU_STATUS_KEYWORDS["Q9"]:
        if keyword in q_lower:
            return "Q9"

    # 기본: Q2 (수료현황/진도)
    return "Q2"


def _classify_quiz_score(query: str) -> str:
    q_lower = query.lower()

    for keyword in QUIZ_SCORE_Q6_KEYWORDS:
        if keyword in q_lower:
            return "Q6"

    return "Q5"


def _classify_hr_leave(query: str) -> str:
    """HR_LEAVE_CHECK를 Q10, Q11, Q12, Q13, Q14, Q15, Q16, Q17로 세분화합니다.

    우선순위:
    1. Q17: 팀/부서 정보 조회
    2. Q16: 인사 정보 조회
    3. Q13: 급여 명세서 요약
    4. Q15: 복지 포인트 사용 내역 (내역/이력/사용 키워드)
    5. Q14: 복지/식대 포인트 잔액
    6. Q10: 근태 현황
    7. Q12: 연차 사용 이력 (이력/내역/사용 키워드)
    8. Q11: 남은 연차 일수 (기본값)
    """
    q_lower = query.lower()

    # Q17: 팀/부서 정보 조회 (우선순위 높음)
    for keyword in HR_TEAM_INFO_KEYWORDS:
        if keyword in q_lower:
            return "Q17"

    # Q16: 인사 정보 조회
    for keyword in HR_PERSONAL_INFO_KEYWORDS:
        if keyword in q_lower:
            return "Q16"

    # Q13: 급여 명세서 요약
    for keyword in HR_SALARY_KEYWORDS:
        if keyword in q_lower:
            return "Q13"

    # Q15: 복지 포인트 사용 내역 (Q14보다 우선 체크)
    for keyword in HR_WELFARE_HISTORY_KEYWORDS:
        if keyword in q_lower:
            return "Q15"

    # Q14: 복지/식대 포인트 잔액
    for keyword in HR_WELFARE_KEYWORDS:
        if keyword in q_lower:
            return "Q14"

    # Q10: 근태 현황
    for keyword in HR_ATTENDANCE_KEYWORDS:
        if keyword in q_lower:
            return "Q10"

    # Q12: 연차 사용 이력 (이력/내역/사용 관련 키워드 우선 체크)
    for keyword in HR_LEAVE_HISTORY_KEYWORDS:
        if keyword in q_lower:
            return "Q12"

    # Q11: 남은 연차 일수 (기본값)
    return "Q11"


# =============================================================================
# 개인화 대상 여부 판단
# =============================================================================

PERSONALIZATION_SUBINTENTS = frozenset([
    "HR_LEAVE_CHECK",
    "HR_WELFARE_CHECK",
    "HR_ATTENDANCE_CHECK",
    "HR_TODO_CHECK",
    "EDU_STATUS_CHECK",
    "EDU_RESUME_CHECK",
    "QUIZ_PENDING_CHECK",
    "QUIZ_SCORE_CHECK",
    *[f"Q{i}" for i in range(1, 21)],
])


def is_personalization_request(sub_intent_id: str) -> bool:
    if not sub_intent_id:
        return False
    if is_personalization_q(sub_intent_id):
        return True
    return sub_intent_id in PERSONALIZATION_SUBINTENTS


# =============================================================================
# 기간(Period) 파싱
# =============================================================================

PERIOD_KEYWORDS: dict[str, str] = {
    "이번 주": "this-week",
    "이번주": "this-week",
    "금주": "this-week",
    "이주": "this-week",
    "이번 달": "this-month",
    "이번달": "this-month",
    "이달": "this-month",
    "금월": "this-month",
    "3개월": "3m",
    "삼개월": "3m",
    "최근 3개월": "3m",
    "올해": "this-year",
    "금년": "this-year",
    "이번 년도": "this-year",
    "이번년도": "this-year",
}


def extract_period_from_query(query: str) -> Optional[str]:
    q_lower = query.lower()

    for keyword, period in PERIOD_KEYWORDS.items():
        if keyword in q_lower:
            logger.debug(f"Period extracted: '{keyword}' -> {period}")
            return period

    return None
