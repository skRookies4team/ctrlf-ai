"""
의도 분류 서비스 (Intent Classification Service)

규칙 기반 의도 분류 및 라우팅 서비스입니다.
사용자의 질문을 분석하여 적절한 처리 경로(RAG, LLM only, Incident 등)를
결정합니다.

현재는 규칙 기반(키워드 매칭)이며, 나중에 ML/LLM 기반 Intent Classifier로
교체될 수 있도록 인터페이스를 분리해 두었습니다.

키워드는 프로젝트 도메인에 맞게 추후 보완이 필요합니다.
"""

from app.core.logging import get_logger
from app.models.chat import ChatRequest
from app.models.intent import IntentResult, IntentType, RouteType

logger = get_logger(__name__)

# 키워드 기반 의도 분류 규칙 정의
# 우선순위: INCIDENT > EDUCATION > GENERAL_CHAT > POLICY (기본값)

# 사고/신고 관련 키워드 (INCIDENT_REPORT)
INCIDENT_KEYWORDS = frozenset([
    "사고", "유출", "침해", "해킹", "재발", "신고",
    "보안사고", "정보유출", "개인정보", "랜섬웨어", "악성코드",
    "피싱", "스팸", "분실", "도난", "위반",
])

# 교육/훈련 관련 키워드 (EDUCATION_QA)
EDUCATION_KEYWORDS = frozenset([
    "교육", "훈련", "퀴즈", "시험", "문제", "영상",
    "수료", "이수", "학습", "강의", "온라인교육", "보안교육",
    "정보보호교육", "컴플라이언스", "인증시험",
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


class IntentService:
    """규칙 기반 의도 분류 및 라우팅 서비스.

    현재는 매우 단순한 규칙 기반(키워드 매칭) 구현이며,
    이후 ML/LLM 기반 모델로 교체될 수 있도록 인터페이스를 분리해 둔다.

    Usage:
        service = IntentService()
        result = service.classify(req=chat_request, user_query="연차 이월 규정 알려줘")
        print(result.intent)  # IntentType.POLICY_QA
        print(result.route)   # RouteType.ROUTE_RAG_INTERNAL
    """

    def classify(
        self,
        *,
        req: ChatRequest,
        user_query: str,
    ) -> IntentResult:
        """사용자 질문을 분류하고 라우팅 경로를 결정합니다.

        규칙 기반 키워드 매칭으로 의도를 분류합니다.
        우선순위: INCIDENT > EDUCATION > SYSTEM_HELP > GENERAL_CHAT > POLICY (기본값)

        Args:
            req: ChatRequest 객체 (도메인 정보 등 포함)
            user_query: 마스킹된 사용자 질문 텍스트

        Returns:
            IntentResult: 의도 분류 및 라우팅 결과

        Note:
            - 현재는 규칙 기반이며, 나중에 ML/LLM 기반 Intent Classifier로 교체 예정
            - 키워드는 프로젝트 도메인에 맞게 추후 보완 필요
        """
        query_lower = user_query.lower()

        # 우선순위 1: 사고/신고 관련 키워드 체크
        if self._contains_any(query_lower, INCIDENT_KEYWORDS):
            intent = IntentType.INCIDENT_REPORT
            route = RouteType.ROUTE_INCIDENT
            domain = "INCIDENT"
            logger.debug(f"Intent classified as INCIDENT_REPORT: query={user_query[:50]}...")

        # 우선순위 2: 교육/훈련 관련 키워드 체크
        elif self._contains_any(query_lower, EDUCATION_KEYWORDS):
            intent = IntentType.EDUCATION_QA
            route = RouteType.ROUTE_TRAINING
            domain = "EDUCATION"
            logger.debug(f"Intent classified as EDUCATION_QA: query={user_query[:50]}...")

        # 우선순위 3: 시스템 도움말 키워드 체크
        elif self._contains_any(query_lower, SYSTEM_HELP_KEYWORDS):
            intent = IntentType.SYSTEM_HELP
            route = RouteType.ROUTE_LLM_ONLY
            domain = None
            logger.debug(f"Intent classified as SYSTEM_HELP: query={user_query[:50]}...")

        # 우선순위 4: 일반 잡담 키워드 체크
        elif self._contains_any(query_lower, GENERAL_CHAT_KEYWORDS):
            intent = IntentType.GENERAL_CHAT
            route = RouteType.ROUTE_LLM_ONLY
            domain = None
            logger.debug(f"Intent classified as GENERAL_CHAT: query={user_query[:50]}...")

        # 우선순위 5: req.domain이 "POLICY" 관련인 경우
        elif req.domain and req.domain.upper() in ("POLICY", "REGULATION", "RULE", "사규", "정책"):
            intent = IntentType.POLICY_QA
            route = RouteType.ROUTE_RAG_INTERNAL
            domain = req.domain
            logger.debug(f"Intent classified as POLICY_QA (from domain): query={user_query[:50]}...")

        # 기본값: POLICY_QA + RAG
        else:
            intent = IntentType.POLICY_QA
            route = RouteType.ROUTE_RAG_INTERNAL
            domain = "POLICY"
            logger.debug(f"Intent classified as POLICY_QA (default): query={user_query[:50]}...")

        # domain 결정: req.domain이 있으면 그대로 사용
        final_domain = req.domain if req.domain else domain

        logger.info(
            f"Intent classification result: intent={intent.value}, "
            f"route={route.value}, domain={final_domain}"
        )

        return IntentResult(
            intent=intent,
            domain=final_domain,
            route=route,
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
