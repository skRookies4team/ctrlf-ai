"""
Phase 21: 규칙 기반 라우터 (Rule Router)

키워드 기반 1차 분기를 수행합니다.
강한 룰(개인화/현황/규정/교육 키워드)로 명확한 의도를 빠르게 분류하고,
애매한 경계에서는 needs_clarify=true를 설정합니다.

주요 기능:
1. 키워드 기반 Tier0Intent 분류
2. 애매한 경계 감지 및 되묻기 설정
3. 치명 액션(퀴즈 3종) 확인 게이트 설정
4. 높은 신뢰도(confidence=0.9+) 케이스에서 LLM Router 스킵 가능

경계 A: 교육 내용 설명 vs 내 이수현황/진도(개인화)
경계 B: 규정 질문 vs HR/근태/복지 개인화(내 정보 조회)
"""

import random
from typing import Optional, Tuple

from app.core.logging import get_logger
from app.models.router_types import (
    ClarifyTemplates,
    ConfirmationTemplates,
    CRITICAL_ACTION_SUB_INTENTS,
    RouterDebugInfo,
    RouterDomain,
    RouterResult,
    RouterRouteType,
    SubIntentId,
    Tier0Intent,
    get_default_route_for_intent,
)

logger = get_logger(__name__)


# =============================================================================
# 키워드 정의
# =============================================================================

# 정책/규정 관련 키워드 (POLICY_QA)
POLICY_KEYWORDS = frozenset([
    "규정", "사규", "정책", "규칙", "지침", "매뉴얼", "가이드",
    "절차", "프로세스", "승인", "결재", "보안정책", "개인정보보호",
    "허용", "금지", "위반", "제재", "징계",
    "정보보호", "보안규정", "내부규정",
])

# 교육 내용/규정 관련 키워드 (EDUCATION_QA)
EDU_CONTENT_KEYWORDS = frozenset([
    "교육내용", "교육자료", "교육규정", "학습내용",
    "강의내용", "교육과정", "커리큘럼",
    "4대교육", "법정교육", "의무교육",
    "정보보호교육", "보안교육", "컴플라이언스교육",
    "교육이란", "교육이 뭐", "교육 설명",
    "무슨 교육", "어떤 교육",
])

# 교육 현황/개인화 키워드 (BACKEND_STATUS - EDU)
EDU_STATUS_KEYWORDS = frozenset([
    "수료", "이수", "미이수", "미수료", "수료율", "이수율",
    "진도", "진행률", "시청률", "완료율",
    "내 교육", "나의 교육", "내가 들은", "내가 수강",
    "교육현황", "수강현황", "학습현황",
    "언제까지", "기한", "마감",
    "어디까지", "몇 퍼센트", "얼마나 했",
])

# HR/근태/복지/연차 개인화 키워드 (BACKEND_STATUS - HR)
HR_PERSONAL_KEYWORDS = frozenset([
    "내 연차", "나의 연차", "연차 잔여", "연차 남은",
    "휴가 잔여", "휴가 남은", "내 휴가",
    "급여", "월급", "봉급", "내 급여", "급여명세",
    "근태", "출근", "퇴근", "내 근태", "근태현황",
    "복지", "복지포인트", "포인트 잔액", "내 포인트",
    "내 정보", "나의 정보", "내 현황", "나의 현황",
    "내가 얼마", "내 잔여", "나 몇 개",
])

# 연차/휴가 규정 키워드 (POLICY_QA - 규정 설명 요청)
LEAVE_POLICY_KEYWORDS = frozenset([
    "연차규정", "휴가규정", "연차제도", "휴가제도",
    "연차 이월", "휴가 이월", "연차 기준",
    "연차가 뭐", "휴가가 뭐", "연차란", "휴가란",
    "연차 어떻게 계산", "휴가 어떻게 계산",
    "연차 정책", "휴가 정책",
])

# 퀴즈 시작 키워드 (QUIZ_START)
QUIZ_START_KEYWORDS = frozenset([
    "퀴즈 시작", "퀴즈 시작해", "퀴즈 시작할", "퀴즈를 시작",
    "시험 시작", "테스트 시작", "퀴즈 풀",
    "퀴즈 치", "시험 치", "테스트 치",
])

# 퀴즈 제출 키워드 (QUIZ_SUBMIT)
QUIZ_SUBMIT_KEYWORDS = frozenset([
    "퀴즈 제출", "답안 제출", "정답 제출",
    "채점해", "채점 해", "점수 확인",
    "제출할게", "제출합니다", "완료",
])

# 퀴즈 생성 키워드 (QUIZ_GENERATION)
QUIZ_GENERATION_KEYWORDS = frozenset([
    "퀴즈 생성", "문제 생성", "문항 생성",
    "퀴즈 만들", "문제 만들", "시험 만들",
    "퀴즈 출제", "문제 출제",
])

# 일반 잡담 키워드 (GENERAL_CHAT)
GENERAL_CHAT_KEYWORDS = frozenset([
    "안녕", "ㅎㅎ", "ㅋㅋ", "날씨", "농담", "심심",
    "잘가", "반가워", "고마워", "감사", "수고",
    "뭐해", "머해", "하이", "헬로", "바이",
    "ㅇㅇ", "ㄴㄴ", "ㅎㅇ",
])

# 시스템 도움말 키워드 (SYSTEM_HELP)
SYSTEM_HELP_KEYWORDS = frozenset([
    "사용법", "메뉴", "화면", "버튼", "기능",
    "어떻게 사용", "어디서", "어디에", "찾기",
    "검색하는 방법", "사용방법", "이용방법",
    "도움말", "헬프", "help",
])

# 애매한 경계 감지용 키워드 조합

# 경계 A 감지: "교육" + 애매한 동사/표현
EDU_AMBIGUOUS_KEYWORDS = frozenset([
    "교육", "강의", "수강", "학습",
])
EDU_AMBIGUOUS_VERBS = frozenset([
    "알려", "알고", "확인", "조회", "보여",
    "뭐야", "뭔가요", "어떻게", "어때",
])

# 경계 B 감지: "연차/휴가" + 애매한 표현
LEAVE_AMBIGUOUS_KEYWORDS = frozenset([
    "연차", "휴가", "휴일", "쉬는날",
])
LEAVE_AMBIGUOUS_VERBS = frozenset([
    "알려", "알고", "확인", "조회", "보여",
    "뭐야", "뭔가요", "어떻게", "어때", "있",
])


# =============================================================================
# RuleRouter 클래스
# =============================================================================


class RuleRouter:
    """규칙 기반 라우터.

    키워드 기반 1차 분기를 수행하여 명확한 의도를 빠르게 분류합니다.
    애매한 경계에서는 needs_clarify=true를 설정합니다.

    Usage:
        router = RuleRouter()
        result = router.route(user_query="연차 며칠 남았어?")
        if result.confidence >= 0.9:
            # LLM Router 스킵 가능
            return result
        else:
            # LLM Router로 추가 분류 필요
            llm_result = await llm_router.route(user_query)
    """

    def __init__(self) -> None:
        """RuleRouter 초기화."""
        pass

    def route(self, user_query: str) -> RouterResult:
        """사용자 질문을 규칙 기반으로 분류합니다.

        Args:
            user_query: 사용자 질문 텍스트

        Returns:
            RouterResult: 라우팅 결과

        Note:
            - confidence >= 0.9: 높은 신뢰도, LLM Router 스킵 권장
            - confidence < 0.9: LLM Router로 추가 분류 권장
            - needs_clarify=True: 되묻기 필요
        """
        query_lower = user_query.lower()
        debug_info = RouterDebugInfo()

        # Step 1: 애매한 경계 체크 (최우선)
        clarify_result = self._check_ambiguous_boundaries(query_lower, debug_info)
        if clarify_result:
            logger.info(
                f"RuleRouter: Ambiguous boundary detected, needs_clarify=True, "
                f"query={user_query[:50]}..."
            )
            return clarify_result

        # Step 2: 치명 액션(퀴즈 3종) 체크
        critical_result = self._check_critical_actions(query_lower, debug_info)
        if critical_result:
            logger.info(
                f"RuleRouter: Critical action detected, "
                f"sub_intent_id={critical_result.sub_intent_id}, "
                f"query={user_query[:50]}..."
            )
            return critical_result

        # Step 3: 명확한 키워드 매칭
        intent_result = self._classify_by_keywords(query_lower, debug_info)

        logger.info(
            f"RuleRouter: intent={intent_result.tier0_intent.value}, "
            f"domain={intent_result.domain.value}, "
            f"confidence={intent_result.confidence}, "
            f"query={user_query[:50]}..."
        )

        return intent_result

    def _check_ambiguous_boundaries(
        self,
        query_lower: str,
        debug_info: RouterDebugInfo,
    ) -> Optional[RouterResult]:
        """애매한 경계를 체크하고 되묻기 결과를 반환합니다.

        경계 A: 교육 내용 설명 vs 내 이수현황/진도
        경계 B: 규정 질문 vs HR/근태/복지 개인화

        Args:
            query_lower: 소문자로 변환된 질문
            debug_info: 디버그 정보 객체

        Returns:
            Optional[RouterResult]: 되묻기가 필요하면 RouterResult, 아니면 None
        """
        # 경계 A: 교육 관련 애매함 체크
        if self._is_boundary_a_ambiguous(query_lower):
            debug_info.rule_hits.append("BOUNDARY_A_AMBIGUOUS")
            return RouterResult(
                tier0_intent=Tier0Intent.UNKNOWN,
                domain=RouterDomain.EDU,
                route_type=RouterRouteType.ROUTE_UNKNOWN,
                confidence=0.3,
                needs_clarify=True,
                clarify_question=random.choice(ClarifyTemplates.EDUCATION_CONTENT_VS_STATUS),
                debug=debug_info,
            )

        # 경계 B: 연차/휴가 관련 애매함 체크
        if self._is_boundary_b_ambiguous(query_lower):
            debug_info.rule_hits.append("BOUNDARY_B_AMBIGUOUS")
            return RouterResult(
                tier0_intent=Tier0Intent.UNKNOWN,
                domain=RouterDomain.HR,
                route_type=RouterRouteType.ROUTE_UNKNOWN,
                confidence=0.3,
                needs_clarify=True,
                clarify_question=random.choice(ClarifyTemplates.POLICY_VS_HR_PERSONAL),
                debug=debug_info,
            )

        return None

    def _is_boundary_a_ambiguous(self, query_lower: str) -> bool:
        """경계 A (교육 내용 vs 이수현황) 애매함을 체크합니다.

        애매한 패턴 예시:
        - "교육 알려줘" (내용? 현황?)
        - "교육 확인해줘" (내용? 진도?)
        - "교육 어떻게 되어있어?" (규정? 내 현황?)

        명확하지 않은 패턴:
        - 교육 키워드 + 애매한 동사
        - 단, EDU_CONTENT_KEYWORDS나 EDU_STATUS_KEYWORDS에 명확히 해당하면 제외
        """
        # 먼저 명확한 키워드가 있는지 체크
        if self._contains_any(query_lower, EDU_CONTENT_KEYWORDS):
            return False  # 명확히 교육 내용 질문
        if self._contains_any(query_lower, EDU_STATUS_KEYWORDS):
            return False  # 명확히 교육 현황 질문

        # 교육 키워드 + 애매한 동사 조합 체크
        has_edu_keyword = self._contains_any(query_lower, EDU_AMBIGUOUS_KEYWORDS)
        has_ambiguous_verb = self._contains_any(query_lower, EDU_AMBIGUOUS_VERBS)

        return has_edu_keyword and has_ambiguous_verb

    def _is_boundary_b_ambiguous(self, query_lower: str) -> bool:
        """경계 B (규정 질문 vs HR 개인화) 애매함을 체크합니다.

        애매한 패턴 예시:
        - "연차 알려줘" (규정? 내 잔여?)
        - "휴가 확인해줘" (정책? 내 휴가?)
        - "연차 어떻게 되어있어?" (규정? 내 현황?)

        명확하지 않은 패턴:
        - 연차/휴가 키워드 + 애매한 동사
        - 단, LEAVE_POLICY_KEYWORDS나 HR_PERSONAL_KEYWORDS에 명확히 해당하면 제외
        """
        # 먼저 명확한 키워드가 있는지 체크
        if self._contains_any(query_lower, LEAVE_POLICY_KEYWORDS):
            return False  # 명확히 정책 질문
        if self._contains_any(query_lower, HR_PERSONAL_KEYWORDS):
            return False  # 명확히 개인화 질문

        # 연차/휴가 키워드 + 애매한 동사 조합 체크
        has_leave_keyword = self._contains_any(query_lower, LEAVE_AMBIGUOUS_KEYWORDS)
        has_ambiguous_verb = self._contains_any(query_lower, LEAVE_AMBIGUOUS_VERBS)

        return has_leave_keyword and has_ambiguous_verb

    def _check_critical_actions(
        self,
        query_lower: str,
        debug_info: RouterDebugInfo,
    ) -> Optional[RouterResult]:
        """치명 액션(퀴즈 3종)을 체크하고 확인 게이트를 설정합니다.

        Args:
            query_lower: 소문자로 변환된 질문
            debug_info: 디버그 정보 객체

        Returns:
            Optional[RouterResult]: 치명 액션이면 RouterResult, 아니면 None
        """
        # QUIZ_START 체크
        if self._contains_any(query_lower, QUIZ_START_KEYWORDS):
            debug_info.rule_hits.append("QUIZ_START")
            debug_info.keywords.extend(
                [kw for kw in QUIZ_START_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                domain=RouterDomain.QUIZ,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id=SubIntentId.QUIZ_START.value,
                confidence=0.95,
                requires_confirmation=True,
                confirmation_prompt=ConfirmationTemplates.QUIZ_START,
                debug=debug_info,
            )

        # QUIZ_SUBMIT 체크
        if self._contains_any(query_lower, QUIZ_SUBMIT_KEYWORDS):
            debug_info.rule_hits.append("QUIZ_SUBMIT")
            debug_info.keywords.extend(
                [kw for kw in QUIZ_SUBMIT_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                domain=RouterDomain.QUIZ,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id=SubIntentId.QUIZ_SUBMIT.value,
                confidence=0.95,
                requires_confirmation=True,
                confirmation_prompt=ConfirmationTemplates.QUIZ_SUBMIT,
                debug=debug_info,
            )

        # QUIZ_GENERATION 체크
        if self._contains_any(query_lower, QUIZ_GENERATION_KEYWORDS):
            debug_info.rule_hits.append("QUIZ_GENERATION")
            debug_info.keywords.extend(
                [kw for kw in QUIZ_GENERATION_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                domain=RouterDomain.QUIZ,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id=SubIntentId.QUIZ_GENERATION.value,
                confidence=0.95,
                requires_confirmation=True,
                confirmation_prompt=ConfirmationTemplates.QUIZ_GENERATION,
                debug=debug_info,
            )

        return None

    def _classify_by_keywords(
        self,
        query_lower: str,
        debug_info: RouterDebugInfo,
    ) -> RouterResult:
        """키워드 기반으로 의도를 분류합니다.

        Args:
            query_lower: 소문자로 변환된 질문
            debug_info: 디버그 정보 객체

        Returns:
            RouterResult: 분류 결과
        """
        # 우선순위 순서대로 체크

        # 1. HR 개인화 (가장 명확한 개인화 패턴)
        if self._contains_any(query_lower, HR_PERSONAL_KEYWORDS):
            debug_info.rule_hits.append("HR_PERSONAL")
            debug_info.keywords.extend(
                [kw for kw in HR_PERSONAL_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                domain=RouterDomain.HR,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id=SubIntentId.HR_LEAVE_CHECK.value,
                confidence=0.9,
                debug=debug_info,
            )

        # 2. 교육 현황 조회
        if self._contains_any(query_lower, EDU_STATUS_KEYWORDS):
            debug_info.rule_hits.append("EDU_STATUS")
            debug_info.keywords.extend(
                [kw for kw in EDU_STATUS_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                domain=RouterDomain.EDU,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id=SubIntentId.EDU_STATUS_CHECK.value,
                confidence=0.9,
                debug=debug_info,
            )

        # 3. 교육 내용 질문
        if self._contains_any(query_lower, EDU_CONTENT_KEYWORDS):
            debug_info.rule_hits.append("EDU_CONTENT")
            debug_info.keywords.extend(
                [kw for kw in EDU_CONTENT_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.EDUCATION_QA,
                domain=RouterDomain.EDU,
                route_type=RouterRouteType.RAG_INTERNAL,
                confidence=0.85,
                debug=debug_info,
            )

        # 4. 정책/규정 질문
        if self._contains_any(query_lower, POLICY_KEYWORDS):
            debug_info.rule_hits.append("POLICY")
            debug_info.keywords.extend(
                [kw for kw in POLICY_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.POLICY_QA,
                domain=RouterDomain.POLICY,
                route_type=RouterRouteType.RAG_INTERNAL,
                confidence=0.85,
                debug=debug_info,
            )

        # 5. 연차/휴가 규정 질문
        if self._contains_any(query_lower, LEAVE_POLICY_KEYWORDS):
            debug_info.rule_hits.append("LEAVE_POLICY")
            debug_info.keywords.extend(
                [kw for kw in LEAVE_POLICY_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.POLICY_QA,
                domain=RouterDomain.POLICY,
                route_type=RouterRouteType.RAG_INTERNAL,
                confidence=0.85,
                debug=debug_info,
            )

        # 6. 시스템 도움말
        if self._contains_any(query_lower, SYSTEM_HELP_KEYWORDS):
            debug_info.rule_hits.append("SYSTEM_HELP")
            debug_info.keywords.extend(
                [kw for kw in SYSTEM_HELP_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.SYSTEM_HELP,
                domain=RouterDomain.GENERAL,
                route_type=RouterRouteType.ROUTE_SYSTEM_HELP,
                confidence=0.9,
                debug=debug_info,
            )

        # 7. 일반 잡담
        if self._contains_any(query_lower, GENERAL_CHAT_KEYWORDS):
            debug_info.rule_hits.append("GENERAL_CHAT")
            debug_info.keywords.extend(
                [kw for kw in GENERAL_CHAT_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.GENERAL_CHAT,
                domain=RouterDomain.GENERAL,
                route_type=RouterRouteType.LLM_ONLY,
                confidence=0.8,
                debug=debug_info,
            )

        # 8. 기본값: UNKNOWN (LLM Router로 추가 분류 필요)
        debug_info.rule_hits.append("UNKNOWN_DEFAULT")
        return RouterResult(
            tier0_intent=Tier0Intent.UNKNOWN,
            domain=RouterDomain.GENERAL,
            route_type=RouterRouteType.ROUTE_UNKNOWN,
            confidence=0.3,
            debug=debug_info,
        )

    def _contains_any(self, text: str, keywords: frozenset) -> bool:
        """텍스트에 키워드 중 하나라도 포함되어 있는지 확인합니다.

        Args:
            text: 검사할 텍스트 (소문자로 변환된 상태)
            keywords: 검사할 키워드 집합

        Returns:
            bool: 키워드 포함 여부
        """
        return any(keyword in text for keyword in keywords)
