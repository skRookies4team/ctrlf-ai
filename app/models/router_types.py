"""
Phase 21: Intent Router 타입 정의 (Router Types)

Tier-0 Intent, Domain, RouteType enum 및 RouterResult 스키마를 정의합니다.
prompt.txt 스펙에 따라 LLM Router의 JSON 출력 스키마와 일치합니다.

Tier-0 Intent (6개 고정):
- POLICY_QA: 사규/규정/정책 관련 Q&A
- EDUCATION_QA: 교육 내용/규정 관련 질문
- BACKEND_STATUS: HR/근태/복지/연차/급여/교육현황 등 개인화 조회
- GENERAL_CHAT: 일반 잡담, Small talk
- SYSTEM_HELP: 시스템 사용법, 메뉴/화면 설명
- UNKNOWN: 분류 불가

Domain (5개):
- POLICY: 사규/보안 정책
- EDU: 4대 교육/직무 교육
- HR: 인사/근태/복지/연차/급여
- QUIZ: 퀴즈/시험 관련
- GENERAL: 일반

RouteType (5개):
- RAG_INTERNAL: 내부 RAG + 내부 LLM
- BACKEND_API: 백엔드 API(통계/이력 등) 위주
- LLM_ONLY: RAG 없이 LLM만 사용
- ROUTE_SYSTEM_HELP: 시스템 도움말 경로
- ROUTE_UNKNOWN: 분류 불가 경로
"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Tier-0 Intent Enum (라우팅용 표준 인텐트 - 6개 고정)
# =============================================================================


class Tier0Intent(str, Enum):
    """Tier-0 의도 유형 (라우팅용 표준 인텐트).

    prompt.txt에 정의된 6개의 표준 인텐트입니다.
    개인화(HR/근태/복지/연차/급여 등)는 BACKEND_STATUS로 통합합니다.

    Attributes:
        POLICY_QA: 사규/규정/정책 관련 Q&A
        EDUCATION_QA: 교육 내용/규정 관련 질문
        BACKEND_STATUS: HR/근태/복지/연차/급여/교육현황 등 개인화 조회
        GENERAL_CHAT: 일반 잡담, Small talk
        SYSTEM_HELP: 시스템 사용법, 메뉴/화면 설명
        UNKNOWN: 분류 불가
    """

    POLICY_QA = "POLICY_QA"
    EDUCATION_QA = "EDUCATION_QA"
    BACKEND_STATUS = "BACKEND_STATUS"
    GENERAL_CHAT = "GENERAL_CHAT"
    SYSTEM_HELP = "SYSTEM_HELP"
    UNKNOWN = "UNKNOWN"


# =============================================================================
# Domain Enum (Phase 21 확장)
# =============================================================================


class RouterDomain(str, Enum):
    """질문 도메인 유형 (Phase 21 확장).

    prompt.txt에 정의된 5개의 도메인입니다.

    Attributes:
        POLICY: 사규/보안 정책
        EDU: 4대 교육/직무 교육
        HR: 인사/근태/복지/연차/급여
        QUIZ: 퀴즈/시험 관련
        GENERAL: 일반
    """

    POLICY = "POLICY"
    EDU = "EDU"
    HR = "HR"
    QUIZ = "QUIZ"
    GENERAL = "GENERAL"


# =============================================================================
# RouteType Enum (Phase 21 확장)
# =============================================================================


class RouterRouteType(str, Enum):
    """처리 경로 유형 (Phase 21).

    prompt.txt에 정의된 5개의 라우트 타입입니다.

    Attributes:
        RAG_INTERNAL: 내부 RAG + 내부 LLM (POLICY_QA, EDUCATION_QA)
        BACKEND_API: 백엔드 API 위주 (BACKEND_STATUS)
        LLM_ONLY: RAG 없이 LLM만 사용 (GENERAL_CHAT)
        ROUTE_SYSTEM_HELP: 시스템 도움말 경로 (SYSTEM_HELP)
        ROUTE_UNKNOWN: 분류 불가 경로 (UNKNOWN)
    """

    RAG_INTERNAL = "RAG_INTERNAL"
    BACKEND_API = "BACKEND_API"
    LLM_ONLY = "LLM_ONLY"
    ROUTE_SYSTEM_HELP = "ROUTE_SYSTEM_HELP"
    ROUTE_UNKNOWN = "ROUTE_UNKNOWN"


# =============================================================================
# Sub-Intent IDs (치명 액션 식별용)
# =============================================================================


class SubIntentId(str, Enum):
    """세부 의도 식별자.

    치명 오분류 대상인 퀴즈 3종을 포함합니다.

    Attributes:
        QUIZ_START: 퀴즈 세션 생성/시작
        QUIZ_SUBMIT: 퀴즈 제출/채점/기록 저장
        QUIZ_GENERATION: 퀴즈 문항 생성/저장/배포
        EDU_STATUS_CHECK: 교육 이수현황/진도 조회
        HR_LEAVE_CHECK: 연차/휴가 잔여 조회
        HR_ATTENDANCE_CHECK: 근태 현황 조회
        HR_WELFARE_CHECK: 복지 포인트/혜택 조회
    """

    # 치명 액션 (확인 게이트 필요)
    QUIZ_START = "QUIZ_START"
    QUIZ_SUBMIT = "QUIZ_SUBMIT"
    QUIZ_GENERATION = "QUIZ_GENERATION"

    # 개인화 조회 (조회성이므로 확인 불필요)
    EDU_STATUS_CHECK = "EDU_STATUS_CHECK"
    HR_LEAVE_CHECK = "HR_LEAVE_CHECK"
    HR_ATTENDANCE_CHECK = "HR_ATTENDANCE_CHECK"
    HR_WELFARE_CHECK = "HR_WELFARE_CHECK"


# 치명 액션 목록 (requires_confirmation=true 대상)
CRITICAL_ACTION_SUB_INTENTS = frozenset([
    SubIntentId.QUIZ_START.value,
    SubIntentId.QUIZ_SUBMIT.value,
    SubIntentId.QUIZ_GENERATION.value,
])


# =============================================================================
# Debug Info Schema
# =============================================================================


class RouterDebugInfo(BaseModel):
    """라우터 디버그 정보.

    LLM Router의 debug 필드에 포함되는 정보입니다.

    Attributes:
        rule_hits: 매칭된 규칙 목록
        keywords: 검출된 키워드 목록
    """

    rule_hits: List[str] = Field(default_factory=list, description="매칭된 규칙 목록")
    keywords: List[str] = Field(default_factory=list, description="검출된 키워드 목록")


# =============================================================================
# RouterResult Schema (LLM Router 출력 JSON 스키마)
# =============================================================================


class RouterResult(BaseModel):
    """라우터 결과 스키마.

    prompt.txt에 정의된 LLM Router 출력 JSON 스키마와 일치합니다.
    rule_router와 llm_router 모두 이 스키마를 출력합니다.

    Attributes:
        tier0_intent: Tier-0 의도 분류 결과
        domain: 도메인 분류 결과
        route_type: 라우팅 경로
        sub_intent_id: 세부 의도 식별자 (선택)
        confidence: 분류 신뢰도 (0.0 ~ 1.0)
        needs_clarify: 되묻기 필요 여부
        clarify_question: 되묻기 질문 텍스트
        requires_confirmation: 확인 게이트 필요 여부
        confirmation_prompt: 확인 프롬프트 텍스트
        debug: 디버그 정보
    """

    tier0_intent: Tier0Intent = Field(
        default=Tier0Intent.UNKNOWN,
        description="Tier-0 의도 분류 결과",
    )
    domain: RouterDomain = Field(
        default=RouterDomain.GENERAL,
        description="도메인 분류 결과",
    )
    route_type: RouterRouteType = Field(
        default=RouterRouteType.ROUTE_UNKNOWN,
        description="라우팅 경로",
    )
    sub_intent_id: str = Field(
        default="",
        description="세부 의도 식별자",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="분류 신뢰도 (0.0 ~ 1.0)",
    )

    # 되묻기 관련
    needs_clarify: bool = Field(
        default=False,
        description="되묻기 필요 여부",
    )
    clarify_question: str = Field(
        default="",
        description="되묻기 질문 텍스트",
    )

    # 확인 게이트 관련
    requires_confirmation: bool = Field(
        default=False,
        description="확인 게이트 필요 여부 (치명 액션)",
    )
    confirmation_prompt: str = Field(
        default="",
        description="확인 프롬프트 텍스트",
    )

    # 디버그 정보
    debug: RouterDebugInfo = Field(
        default_factory=RouterDebugInfo,
        description="디버그 정보",
    )


# =============================================================================
# Clarification Templates (되묻기 템플릿)
# =============================================================================


class ClarifyTemplates:
    """되묻기 질문 템플릿.

    prompt.txt에 정의된 두 가지 경계에 대한 템플릿입니다.
    """

    # 경계 A: 교육 내용 설명 vs 내 이수현황/진도(개인화)
    EDUCATION_CONTENT_VS_STATUS = [
        "교육 내용 설명이 필요하신가요, 아니면 내 이수현황/진도 조회가 필요하신가요?",
        "교육을 요약/설명해드릴까요, 아니면 내 기록(수료/시청률/점수)을 조회해드릴까요?",
        "교육 안내가 필요하신가요, 아니면 내가 어디까지 했는지 확인이 필요하신가요?",
    ]

    # 경계 B: 규정 질문 vs HR/근태/복지 개인화(내 정보 조회)
    POLICY_VS_HR_PERSONAL = [
        "회사 규정(정책) 설명을 원하시나요, 아니면 내 HR 정보(연차/근태/복지) 조회를 원하시나요?",
        "규정의 기준/원칙을 찾을까요, 아니면 내 잔여/내역 같은 개인 데이터를 볼까요?",
        "질문이 정책(허용/금지/절차) 쪽인가요, 아니면 내 연차/급여/포인트 같은 개인화 쪽인가요?",
    ]

    # BACKEND_STATUS인데 sub_intent_id가 비어있을 때
    BACKEND_STATUS_CLARIFY = [
        "어떤 정보를 조회하시겠어요? (예: 연차 잔여, 교육 이수현황, 근태 현황, 복지 포인트 등)",
        "무엇을 확인해 드릴까요? 연차, 교육, 근태, 복지 중 선택해 주세요.",
        "조회하실 항목을 알려주세요. (연차/교육현황/근태/복지포인트 등)",
    ]


# =============================================================================
# Confirmation Templates (확인 프롬프트 템플릿)
# =============================================================================


class ConfirmationTemplates:
    """확인 프롬프트 템플릿.

    치명 액션(퀴즈 3종)에 대한 확인 프롬프트입니다.
    """

    QUIZ_START = "퀴즈를 지금 시작할까요? (예/아니오)"
    QUIZ_SUBMIT = "답안을 제출하고 채점할까요? (예/아니오)"
    QUIZ_GENERATION = "문항을 생성해서 저장할까요? (예/아니오)"


# =============================================================================
# Routing Policy (라우팅 정책)
# =============================================================================


# Tier0Intent → (RouteType, Domain) 기본 매핑
TIER0_ROUTING_POLICY = {
    Tier0Intent.POLICY_QA: (RouterRouteType.RAG_INTERNAL, RouterDomain.POLICY),
    Tier0Intent.EDUCATION_QA: (RouterRouteType.RAG_INTERNAL, RouterDomain.EDU),
    Tier0Intent.BACKEND_STATUS: (RouterRouteType.BACKEND_API, RouterDomain.HR),  # domain은 context에 따라 HR/QUIZ/EDU
    Tier0Intent.GENERAL_CHAT: (RouterRouteType.LLM_ONLY, RouterDomain.GENERAL),
    Tier0Intent.SYSTEM_HELP: (RouterRouteType.ROUTE_SYSTEM_HELP, RouterDomain.GENERAL),
    Tier0Intent.UNKNOWN: (RouterRouteType.ROUTE_UNKNOWN, RouterDomain.GENERAL),
}


def get_default_route_for_intent(intent: Tier0Intent) -> tuple[RouterRouteType, RouterDomain]:
    """Tier0Intent에 대한 기본 라우팅 정책을 반환합니다.

    Args:
        intent: Tier-0 의도

    Returns:
        tuple[RouterRouteType, RouterDomain]: (라우트 타입, 도메인)
    """
    return TIER0_ROUTING_POLICY.get(
        intent,
        (RouterRouteType.ROUTE_UNKNOWN, RouterDomain.GENERAL),
    )
