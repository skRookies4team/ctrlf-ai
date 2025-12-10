"""
의도 분류 서비스 (Intent Classification Service)

규칙 기반 의도 분류 및 라우팅 서비스입니다.
사용자의 질문을 분석하여 적절한 처리 경로(RAG, LLM only, Backend API 등)를
결정합니다.

Phase 10 업데이트:
- 역할(UserRole) × 도메인(Domain) × 라우트(RouteType) 정책 구현
- UserRole 기반 라우팅 분기 추가
- INCIDENT_QA vs INCIDENT_REPORT 구분
- EDU_STATUS (교육 현황 조회) 추가

현재는 규칙 기반(키워드 매칭)이며, 나중에 ML/LLM 기반 Intent Classifier로
교체될 수 있도록 인터페이스를 분리해 두었습니다.

룰 정의 (Phase 10):
===================

직원(EMPLOYEE):
- 질문이 정책/규정 관련 → Domain.POLICY, RouteType.RAG_INTERNAL
- 질문이 사고/위반 "문의" → Domain.INCIDENT, RouteType.RAG_INTERNAL
- 질문이 사고/위반 "신고 시작" → Domain.INCIDENT, RouteType.BACKEND_API
- 질문이 교육 일정/수료/기한 → Domain.EDU, RouteType.BACKEND_API
- 질문이 교육 내용/규정 → Domain.EDU, RouteType.RAG_INTERNAL

관리자(ADMIN):
- POLICY/INCIDENT/EDU 모든 도메인 접근 가능
- INCIDENT/EDU는 주로 "통계/현황/정책 해석" 중심 → MIXED_BACKEND_RAG

신고관리자(INCIDENT_MANAGER):
- INCIDENT 도메인 우선, RouteType.MIXED_BACKEND_RAG
- POLICY/EDU는 INCIDENT와 연계된 케이스에서만 접근, 단순 RAG_INTERNAL로 처리
"""

from typing import Optional

from app.core.logging import get_logger
from app.models.chat import ChatRequest
from app.models.intent import (
    Domain,
    IntentResult,
    IntentType,
    RouteType,
    UserRole,
)

logger = get_logger(__name__)


# =============================================================================
# 키워드 기반 의도 분류 규칙 정의
# =============================================================================

# 사고 "신고" 관련 키워드 (INCIDENT_REPORT) - 신고 행위를 나타내는 표현
INCIDENT_REPORT_KEYWORDS = frozenset([
    "신고", "신고하", "신고할", "신고합니다", "신고해야",
    "보고", "보고하", "보고할", "보고합니다",
    "접수", "등록", "제보",
])

# 사고 "문의" 관련 키워드 (INCIDENT_QA) - 사고 관련 일반 질문
INCIDENT_QA_KEYWORDS = frozenset([
    "사고", "유출", "침해", "해킹", "재발",
    "보안사고", "정보유출", "개인정보", "랜섬웨어", "악성코드",
    "피싱", "스팸", "분실", "도난", "위반",
    "사건", "사례", "유형", "패턴", "통계",
])

# 교육 "현황/일정" 관련 키워드 (EDU_STATUS) - 백엔드 API 호출 필요
EDU_STATUS_KEYWORDS = frozenset([
    "수료", "이수", "미이수", "미수료", "수료율",
    "일정", "기한", "마감", "대상", "대상자",
    "언제까지", "며칠", "몇일", "얼마나", "완료",
    "교육현황", "진도", "진행률",
])

# 교육 "내용" 관련 키워드 (EDUCATION_QA) - RAG 검색 필요
EDU_CONTENT_KEYWORDS = frozenset([
    "교육", "훈련", "퀴즈", "시험", "문제", "영상",
    "학습", "강의", "온라인교육", "보안교육",
    "정보보호교육", "컴플라이언스", "인증시험",
    "내용", "규정", "방법", "절차",
])

# 정책/규정 관련 키워드 (POLICY_QA)
POLICY_KEYWORDS = frozenset([
    "연차", "휴가", "규정", "사규", "정책", "규칙",
    "지침", "매뉴얼", "가이드", "절차", "프로세스",
    "승인", "결재", "보안", "개인정보보호",
])

# 일반 잡담 키워드 (GENERAL_CHAT)
GENERAL_CHAT_KEYWORDS = frozenset([
    "안녕", "ㅎㅎ", "ㅋㅋ", "날씨", "농담", "심심",
    "잘가", "반가워", "고마워", "감사", "수고",
    "뭐해", "머해", "하이", "헬로", "바이",
])

# 시스템 도움말 관련 키워드 (SYSTEM_HELP)
SYSTEM_HELP_KEYWORDS = frozenset([
    "사용법", "메뉴", "화면", "버튼", "기능",
    "어떻게 사용", "어디서", "어디에", "찾기", "검색하는 방법",
])


# =============================================================================
# IntentService 클래스
# =============================================================================


class IntentService:
    """규칙 기반 의도 분류 및 라우팅 서비스.

    Phase 10에서 역할(UserRole) × 도메인(Domain) 기반 라우팅을 구현합니다.
    현재는 매우 단순한 규칙 기반(키워드 매칭) 구현이며,
    이후 ML/LLM 기반 모델로 교체될 수 있도록 인터페이스를 분리해 둔다.

    Usage:
        service = IntentService()
        result = service.classify(req=chat_request, user_query="연차 이월 규정 알려줘")
        print(result.user_role)  # UserRole.EMPLOYEE
        print(result.intent)     # IntentType.POLICY_QA
        print(result.domain)     # "POLICY"
        print(result.route)      # RouteType.RAG_INTERNAL
    """

    def classify(
        self,
        *,
        req: ChatRequest,
        user_query: str,
    ) -> IntentResult:
        """사용자 질문을 분류하고 라우팅 경로를 결정합니다.

        Phase 10: 역할(UserRole) × 도메인(Domain) 기반 라우팅

        Args:
            req: ChatRequest 객체 (도메인, 역할 정보 등 포함)
            user_query: 마스킹된 사용자 질문 텍스트

        Returns:
            IntentResult: 의도 분류 및 라우팅 결과 (user_role 포함)

        Note:
            - 현재는 규칙 기반이며, 나중에 ML/LLM 기반 Intent Classifier로 교체 예정
            - 키워드는 프로젝트 도메인에 맞게 추후 보완 필요
        """
        # Step 1: UserRole 파싱 (문자열 → Enum)
        user_role = self._parse_user_role(req.user_role)

        # Step 2: 도메인 힌트 확인 (프론트/백엔드에서 넘겨준 경우)
        domain_hint = self._parse_domain_hint(req.domain)

        # Step 3: 키워드 기반 의도 및 도메인 분류
        intent, domain = self._classify_intent_and_domain(
            user_query=user_query,
            domain_hint=domain_hint,
        )

        # Step 4: 역할×도메인×의도 기반 라우팅 결정
        route = self._determine_route(
            user_role=user_role,
            domain=domain,
            intent=intent,
        )

        logger.info(
            f"Intent classification result: "
            f"user_role={user_role.value}, "
            f"intent={intent.value}, "
            f"domain={domain}, "
            f"route={route.value}"
        )

        return IntentResult(
            user_role=user_role,
            intent=intent,
            domain=domain,
            route=route,
        )

    def _parse_user_role(self, role_str: str) -> UserRole:
        """문자열 역할을 UserRole Enum으로 변환합니다.

        Args:
            role_str: 역할 문자열 (예: "EMPLOYEE", "ADMIN", "INCIDENT_MANAGER")

        Returns:
            UserRole: 매칭되는 Enum 값, 없으면 EMPLOYEE (기본값)
        """
        role_upper = role_str.upper() if role_str else "EMPLOYEE"

        try:
            return UserRole(role_upper)
        except ValueError:
            # 매핑되지 않는 역할은 EMPLOYEE로 처리
            # 예: "MANAGER" → EMPLOYEE, "HR" → ADMIN 등 필요시 매핑 추가
            if role_upper in ("MANAGER", "HR", "SECURITY"):
                return UserRole.ADMIN
            logger.warning(f"Unknown user role '{role_str}', defaulting to EMPLOYEE")
            return UserRole.EMPLOYEE

    def _parse_domain_hint(self, domain_str: Optional[str]) -> Optional[str]:
        """도메인 힌트 문자열을 정규화합니다.

        Args:
            domain_str: 도메인 문자열 (예: "POLICY", "INCIDENT", "EDU", "EDUCATION")

        Returns:
            정규화된 도메인 문자열 또는 None
        """
        if not domain_str:
            return None

        domain_upper = domain_str.upper()

        # EDUCATION → EDU 정규화
        if domain_upper in ("EDUCATION", "TRAINING", "LEARN"):
            return Domain.EDU.value

        # 유효한 Domain Enum 값이면 그대로 반환
        try:
            return Domain(domain_upper).value
        except ValueError:
            # 레거시 문자열 처리
            if "POLICY" in domain_upper or "규정" in domain_str or "사규" in domain_str:
                return Domain.POLICY.value
            if "INCIDENT" in domain_upper or "사고" in domain_str:
                return Domain.INCIDENT.value
            return domain_upper

    def _classify_intent_and_domain(
        self,
        user_query: str,
        domain_hint: Optional[str],
    ) -> tuple[IntentType, str]:
        """키워드 기반으로 의도와 도메인을 분류합니다.

        우선순위:
        1. 사고 신고 (INCIDENT_REPORT)
        2. 사고 문의 (INCIDENT_QA)
        3. 교육 현황 (EDU_STATUS)
        4. 교육 내용 (EDUCATION_QA)
        5. 시스템 도움말 (SYSTEM_HELP)
        6. 일반 잡담 (GENERAL_CHAT)
        7. 도메인 힌트가 있으면 해당 도메인의 QA
        8. 정책 관련 키워드 (POLICY_QA)
        9. 기본값: POLICY_QA

        Args:
            user_query: 사용자 질문 텍스트
            domain_hint: 프론트/백엔드에서 전달한 도메인 힌트

        Returns:
            tuple[IntentType, str]: (의도, 도메인)
        """
        query_lower = user_query.lower()

        # 1. 사고 "신고" 의도 체크 (신고 키워드 + 사고 키워드 조합)
        if self._contains_any(query_lower, INCIDENT_REPORT_KEYWORDS):
            if self._contains_any(query_lower, INCIDENT_QA_KEYWORDS):
                logger.debug(f"Intent: INCIDENT_REPORT - query={user_query[:50]}...")
                return IntentType.INCIDENT_REPORT, Domain.INCIDENT.value

        # 2. 사고 "문의" 의도 체크 (사고 관련 키워드만)
        if self._contains_any(query_lower, INCIDENT_QA_KEYWORDS):
            logger.debug(f"Intent: INCIDENT_QA - query={user_query[:50]}...")
            return IntentType.INCIDENT_QA, Domain.INCIDENT.value

        # 3. 교육 "현황/일정" 조회 체크
        if self._contains_any(query_lower, EDU_STATUS_KEYWORDS):
            logger.debug(f"Intent: EDU_STATUS - query={user_query[:50]}...")
            return IntentType.EDU_STATUS, Domain.EDU.value

        # 4. 교육 "내용" 질문 체크
        if self._contains_any(query_lower, EDU_CONTENT_KEYWORDS):
            logger.debug(f"Intent: EDUCATION_QA - query={user_query[:50]}...")
            return IntentType.EDUCATION_QA, Domain.EDU.value

        # 5. 시스템 도움말 체크
        if self._contains_any(query_lower, SYSTEM_HELP_KEYWORDS):
            logger.debug(f"Intent: SYSTEM_HELP - query={user_query[:50]}...")
            return IntentType.SYSTEM_HELP, Domain.POLICY.value

        # 6. 일반 잡담 체크
        if self._contains_any(query_lower, GENERAL_CHAT_KEYWORDS):
            logger.debug(f"Intent: GENERAL_CHAT - query={user_query[:50]}...")
            return IntentType.GENERAL_CHAT, Domain.POLICY.value

        # 7. 도메인 힌트가 있으면 해당 도메인의 기본 QA
        if domain_hint:
            if domain_hint == Domain.INCIDENT.value:
                return IntentType.INCIDENT_QA, Domain.INCIDENT.value
            elif domain_hint == Domain.EDU.value:
                return IntentType.EDUCATION_QA, Domain.EDU.value
            else:
                return IntentType.POLICY_QA, domain_hint

        # 8. 정책 관련 키워드 체크
        if self._contains_any(query_lower, POLICY_KEYWORDS):
            logger.debug(f"Intent: POLICY_QA (keyword) - query={user_query[:50]}...")
            return IntentType.POLICY_QA, Domain.POLICY.value

        # 9. 기본값: POLICY_QA
        logger.debug(f"Intent: POLICY_QA (default) - query={user_query[:50]}...")
        return IntentType.POLICY_QA, Domain.POLICY.value

    def _determine_route(
        self,
        user_role: UserRole,
        domain: str,
        intent: IntentType,
    ) -> RouteType:
        """역할×도메인×의도 기반으로 라우팅 경로를 결정합니다.

        Phase 10 룰:
        - EMPLOYEE: 대부분 RAG_INTERNAL, 신고/교육현황은 BACKEND_API
        - ADMIN: INCIDENT/EDU 통계는 MIXED_BACKEND_RAG
        - INCIDENT_MANAGER: INCIDENT는 MIXED_BACKEND_RAG

        Args:
            user_role: 사용자 역할
            domain: 분류된 도메인
            intent: 분류된 의도

        Returns:
            RouteType: 라우팅 경로
        """
        # === 일반적인 의도별 라우팅 (역할 무관) ===

        # 일반 잡담, 시스템 도움말 → LLM_ONLY
        if intent in {IntentType.GENERAL_CHAT, IntentType.SYSTEM_HELP}:
            return RouteType.LLM_ONLY

        # === 역할별 라우팅 ===

        if user_role == UserRole.EMPLOYEE:
            return self._route_for_employee(domain, intent)

        elif user_role == UserRole.ADMIN:
            return self._route_for_admin(domain, intent)

        elif user_role == UserRole.INCIDENT_MANAGER:
            return self._route_for_incident_manager(domain, intent)

        # Fallback: RAG_INTERNAL
        return RouteType.RAG_INTERNAL

    def _route_for_employee(self, domain: str, intent: IntentType) -> RouteType:
        """직원(EMPLOYEE) 역할에 대한 라우팅 결정.

        - 정책/규정 질문 → RAG_INTERNAL
        - 사고 문의 → RAG_INTERNAL
        - 사고 신고 시작 → BACKEND_API (신고 플로우 안내)
        - 교육 내용 질문 → RAG_INTERNAL
        - 교육 일정/수료 조회 → BACKEND_API
        """
        if intent == IntentType.INCIDENT_REPORT:
            # 사고 신고 → 백엔드 API로 신고 플로우 안내
            return RouteType.BACKEND_API

        if intent == IntentType.EDU_STATUS:
            # 교육 현황/일정 조회 → 백엔드 API
            return RouteType.BACKEND_API

        # 나머지는 RAG + LLM
        return RouteType.RAG_INTERNAL

    def _route_for_admin(self, domain: str, intent: IntentType) -> RouteType:
        """관리자(ADMIN) 역할에 대한 라우팅 결정.

        - INCIDENT/EDU 도메인: 통계/현황 중심 → MIXED_BACKEND_RAG
        - POLICY 도메인: RAG_INTERNAL
        """
        if domain == Domain.INCIDENT.value:
            # INCIDENT는 사건 로그 + RAG 조합
            return RouteType.MIXED_BACKEND_RAG

        if domain == Domain.EDU.value:
            # EDU 통계/현황은 백엔드 데이터 + RAG
            if intent == IntentType.EDU_STATUS:
                return RouteType.MIXED_BACKEND_RAG
            # 교육 내용 질문은 RAG만
            return RouteType.RAG_INTERNAL

        # POLICY → RAG_INTERNAL
        return RouteType.RAG_INTERNAL

    def _route_for_incident_manager(self, domain: str, intent: IntentType) -> RouteType:
        """신고관리자(INCIDENT_MANAGER) 역할에 대한 라우팅 결정.

        - INCIDENT 도메인 우선 → MIXED_BACKEND_RAG (사건 로그 + POLICY RAG)
        - POLICY/EDU는 INCIDENT 연계 시 RAG_INTERNAL
        """
        if domain == Domain.INCIDENT.value:
            # INCIDENT는 사건 로그 + RAG 조합
            return RouteType.MIXED_BACKEND_RAG

        # POLICY/EDU → 단순 RAG_INTERNAL
        return RouteType.RAG_INTERNAL

    def _contains_any(self, text: str, keywords: frozenset) -> bool:
        """텍스트에 키워드 중 하나라도 포함되어 있는지 확인합니다.

        Args:
            text: 검사할 텍스트 (소문자로 변환된 상태)
            keywords: 검사할 키워드 집합

        Returns:
            bool: 키워드 포함 여부
        """
        return any(keyword in text for keyword in keywords)
