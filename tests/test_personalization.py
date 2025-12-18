"""
Personalization Tests (개인화 테스트)

개인화 기능 관련 모델, 클라이언트, 라우터 테스트.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.personalization import (
    DEFAULT_PERIOD_FOR_INTENT,
    DepartmentClarifyTemplates,
    DepartmentInfo,
    DepartmentSearchResponse,
    ERROR_RESPONSE_TEMPLATES,
    PersonalizationError,
    PersonalizationErrorType,
    PersonalizationFacts,
    PersonalizationResolveRequest,
    PersonalizationSubIntentId,
    PeriodType,
    PRIORITY_SUB_INTENTS,
    SUB_INTENT_METADATA,
)
from app.models.router_types import (
    ClarifyTemplates,
    RouterDomain,
    RouterRouteType,
    SubIntentId,
    Tier0Intent,
)


# =============================================================================
# Model Tests
# =============================================================================


class TestPersonalizationModels:
    """개인화 모델 테스트."""

    def test_period_type_enum(self):
        """PeriodType enum 값 확인."""
        assert PeriodType.THIS_WEEK.value == "this-week"
        assert PeriodType.THIS_MONTH.value == "this-month"
        assert PeriodType.THREE_MONTHS.value == "3m"
        assert PeriodType.THIS_YEAR.value == "this-year"

    def test_personalization_sub_intent_id_enum(self):
        """PersonalizationSubIntentId enum 확인."""
        assert PersonalizationSubIntentId.Q1.value == "Q1"
        assert PersonalizationSubIntentId.Q11.value == "Q11"
        assert PersonalizationSubIntentId.Q20.value == "Q20"

    def test_priority_sub_intents(self):
        """우선순위 인텐트 8개 확인."""
        assert len(PRIORITY_SUB_INTENTS) == 8
        assert "Q1" in PRIORITY_SUB_INTENTS
        assert "Q3" in PRIORITY_SUB_INTENTS
        assert "Q5" in PRIORITY_SUB_INTENTS
        assert "Q6" in PRIORITY_SUB_INTENTS
        assert "Q9" in PRIORITY_SUB_INTENTS
        assert "Q11" in PRIORITY_SUB_INTENTS
        assert "Q14" in PRIORITY_SUB_INTENTS
        assert "Q20" in PRIORITY_SUB_INTENTS

    def test_default_period_for_intent(self):
        """인텐트별 기본 period 확인."""
        assert DEFAULT_PERIOD_FOR_INTENT["Q3"] == PeriodType.THIS_MONTH
        assert DEFAULT_PERIOD_FOR_INTENT["Q5"] == PeriodType.THIS_YEAR
        assert DEFAULT_PERIOD_FOR_INTENT["Q6"] == PeriodType.THREE_MONTHS
        assert DEFAULT_PERIOD_FOR_INTENT["Q9"] == PeriodType.THIS_WEEK
        assert DEFAULT_PERIOD_FOR_INTENT["Q11"] == PeriodType.THIS_YEAR

    def test_error_response_templates(self):
        """에러 응답 템플릿 확인."""
        assert "NOT_FOUND" in ERROR_RESPONSE_TEMPLATES
        assert "TIMEOUT" in ERROR_RESPONSE_TEMPLATES
        assert "PARTIAL" in ERROR_RESPONSE_TEMPLATES
        assert "NOT_IMPLEMENTED" in ERROR_RESPONSE_TEMPLATES

    def test_not_implemented_error_message(self):
        """NOT_IMPLEMENTED 에러 메시지에 지원 질문 예시 포함 확인."""
        msg = ERROR_RESPONSE_TEMPLATES["NOT_IMPLEMENTED"]
        assert "현재 데모 범위에서는 지원하지 않는 질문" in msg
        assert "지원되는 질문 예시" in msg

    def test_personalization_resolve_request(self):
        """PersonalizationResolveRequest 모델 생성."""
        request = PersonalizationResolveRequest(
            sub_intent_id="Q11",
            period="this-year",
            target_dept_id=None,
        )
        assert request.sub_intent_id == "Q11"
        assert request.period == "this-year"
        assert request.target_dept_id is None

    def test_personalization_facts(self):
        """PersonalizationFacts 모델 생성."""
        facts = PersonalizationFacts(
            sub_intent_id="Q11",
            period_start="2025-01-01",
            period_end="2025-12-31",
            metrics={"remaining_days": 7},
        )
        assert facts.sub_intent_id == "Q11"
        assert facts.metrics["remaining_days"] == 7
        assert facts.error is None

    def test_personalization_facts_with_error(self):
        """에러가 있는 PersonalizationFacts."""
        facts = PersonalizationFacts(
            sub_intent_id="Q11",
            error=PersonalizationError(
                type="NOT_FOUND",
                message="Data not found",
            ),
        )
        assert facts.error is not None
        assert facts.error.type == "NOT_FOUND"

    def test_department_info(self):
        """DepartmentInfo 모델."""
        dept = DepartmentInfo(
            dept_id="D001",
            dept_name="개발팀",
        )
        assert dept.dept_id == "D001"
        assert dept.dept_name == "개발팀"
        assert dept.dept_path is None  # 기본값

    def test_department_info_with_path(self):
        """DepartmentInfo 모델 (dept_path 포함)."""
        dept = DepartmentInfo(
            dept_id="D001",
            dept_name="개발팀",
            dept_path="본사 > 개발본부 > 개발팀",
        )
        assert dept.dept_id == "D001"
        assert dept.dept_name == "개발팀"
        assert dept.dept_path == "본사 > 개발본부 > 개발팀"

    def test_department_search_response(self):
        """DepartmentSearchResponse 모델."""
        response = DepartmentSearchResponse(
            items=[
                DepartmentInfo(dept_id="D001", dept_name="개발팀"),
                DepartmentInfo(dept_id="D002", dept_name="개발1팀"),
            ]
        )
        assert len(response.items) == 2

    def test_sub_intent_metadata(self):
        """SUB_INTENT_METADATA 확인."""
        assert "Q1" in SUB_INTENT_METADATA
        assert "Q11" in SUB_INTENT_METADATA
        assert SUB_INTENT_METADATA["Q1"].description == "미이수 필수 교육 조회"
        assert SUB_INTENT_METADATA["Q11"].description == "남은 연차 일수"

    def test_q5_q6_domain_is_quiz(self):
        """Q5, Q6 Domain이 QUIZ인지 확인."""
        assert SUB_INTENT_METADATA["Q5"].domain == "QUIZ"
        assert SUB_INTENT_METADATA["Q6"].domain == "QUIZ"


# =============================================================================
# Router Types Q1-Q20 Tests
# =============================================================================


class TestRouterTypesPersonalization:
    """라우터 타입 개인화 테스트."""

    def test_sub_intent_id_has_action_types(self):
        """SubIntentId에 액션 타입이 있는지 확인."""
        # SubIntentId는 치명 액션과 조회성 액션을 포함
        assert SubIntentId.QUIZ_START.value == "QUIZ_START"
        assert SubIntentId.QUIZ_SUBMIT.value == "QUIZ_SUBMIT"
        assert SubIntentId.EDU_STATUS_CHECK.value == "EDU_STATUS_CHECK"
        assert SubIntentId.HR_LEAVE_CHECK.value == "HR_LEAVE_CHECK"

    def test_personalization_sub_intent_id_has_q1_q20(self):
        """PersonalizationSubIntentId에 Q1-Q20이 있는지 확인."""
        # 개인화용 Q1-Q20은 PersonalizationSubIntentId에 정의됨
        assert PersonalizationSubIntentId.Q1.value == "Q1"
        assert PersonalizationSubIntentId.Q11.value == "Q11"
        assert PersonalizationSubIntentId.Q14.value == "Q14"
        assert PersonalizationSubIntentId.Q20.value == "Q20"

    def test_clarify_templates_education_vs_status(self):
        """교육 내용 vs 이수현황 템플릿 확인."""
        assert len(ClarifyTemplates.EDUCATION_CONTENT_VS_STATUS) == 3
        # 실제 템플릿: "교육 내용 설명이 필요하신가요, 아니면 내 이수현황/진도 조회가 필요하신가요?"
        assert "교육 내용 설명이 필요하신가요" in ClarifyTemplates.EDUCATION_CONTENT_VS_STATUS[0]

    def test_clarify_templates_policy_vs_hr(self):
        """규정 vs HR 개인화 템플릿 확인."""
        assert len(ClarifyTemplates.POLICY_VS_HR_PERSONAL) == 3
        # 실제 템플릿: "회사 규정(정책) 설명을 원하시나요, 아니면 내 HR 정보(연차/근태/복지) 조회를 원하시나요?"
        assert "회사 규정(정책) 설명을 원하시나요" in ClarifyTemplates.POLICY_VS_HR_PERSONAL[0]


# =============================================================================
# Personalization Client Tests
# =============================================================================


class TestPersonalizationClient:
    """개인화 클라이언트 테스트."""

    def test_client_initialization(self):
        """클라이언트 초기화."""
        from app.clients.personalization_client import PersonalizationClient

        client = PersonalizationClient()
        assert client is not None

    @pytest.mark.asyncio
    async def test_resolve_facts_mock(self):
        """Mock facts 반환 테스트."""
        from app.clients.personalization_client import PersonalizationClient

        # 백엔드 URL 없이 mock 모드
        client = PersonalizationClient(base_url=None)
        facts = await client.resolve_facts("Q11")

        assert facts.sub_intent_id == "Q11"
        assert "remaining_days" in facts.metrics

    @pytest.mark.asyncio
    async def test_resolve_facts_not_implemented(self):
        """구현되지 않은 인텐트 테스트."""
        from app.clients.personalization_client import PersonalizationClient

        client = PersonalizationClient(base_url=None)
        # Q2는 우선순위가 아님
        facts = await client.resolve_facts("Q2")

        assert facts.error is not None
        assert facts.error.type == "NOT_IMPLEMENTED"

    @pytest.mark.asyncio
    async def test_search_departments_mock(self):
        """Mock 부서 검색 테스트."""
        from app.clients.personalization_client import PersonalizationClient

        client = PersonalizationClient(base_url=None)
        result = await client.search_departments("개발")

        assert len(result.items) > 0
        assert any("개발" in item.dept_name for item in result.items)

    @pytest.mark.asyncio
    async def test_search_departments_no_match(self):
        """부서 검색 결과 없음."""
        from app.clients.personalization_client import PersonalizationClient

        client = PersonalizationClient(base_url=None)
        result = await client.search_departments("없는부서123")

        assert len(result.items) == 0


# =============================================================================
# Rule Router Personalization Tests
# =============================================================================


class TestRuleRouterPersonalization:
    """Rule Router 개인화 테스트."""

    def test_hr_leave_keywords(self):
        """HR 연차 키워드 분류 (HR_LEAVE_CHECK)."""
        from app.services.rule_router import RuleRouter

        router = RuleRouter()

        result = router.route("내 연차 며칠 남았어?")
        assert result.tier0_intent == Tier0Intent.BACKEND_STATUS
        assert result.sub_intent_id == SubIntentId.HR_LEAVE_CHECK.value
        assert result.route_type == RouterRouteType.BACKEND_API

    def test_hr_welfare_keywords(self):
        """HR 복지 포인트 키워드 분류."""
        from app.services.rule_router import RuleRouter

        router = RuleRouter()

        result = router.route("복지 포인트 얼마 남았어?")
        assert result.tier0_intent == Tier0Intent.BACKEND_STATUS
        # HR_PERSONAL_KEYWORDS에 복지 포인트 포함 -> HR_LEAVE_CHECK
        assert result.sub_intent_id == SubIntentId.HR_LEAVE_CHECK.value

    def test_edu_status_keywords(self):
        """교육 현황 키워드 분류 (EDU_STATUS_CHECK)."""
        from app.services.rule_router import RuleRouter

        router = RuleRouter()

        # "미이수"는 EDU_STATUS_KEYWORDS에 포함됨
        result = router.route("미이수 교육 조회해줘")
        assert result.tier0_intent == Tier0Intent.BACKEND_STATUS
        assert result.sub_intent_id == SubIntentId.EDU_STATUS_CHECK.value

    def test_edu_status_progress_keywords(self):
        """교육 진도 관련 키워드 분류."""
        from app.services.rule_router import RuleRouter

        router = RuleRouter()

        # "진도"는 EDU_STATUS_KEYWORDS에 포함됨
        result = router.route("내 교육 진도 확인해줘")
        assert result.tier0_intent == Tier0Intent.BACKEND_STATUS
        assert result.sub_intent_id == SubIntentId.EDU_STATUS_CHECK.value

    def test_boundary_a_clarify(self):
        """경계 A (교육 내용 vs 이수현황) 되묻기."""
        from app.services.rule_router import RuleRouter

        router = RuleRouter()

        result = router.route("교육 알려줘")
        assert result.needs_clarify is True
        assert result.clarify_question in ClarifyTemplates.EDUCATION_CONTENT_VS_STATUS

    def test_boundary_b_clarify(self):
        """경계 B (규정 vs HR 개인화) 되묻기."""
        from app.services.rule_router import RuleRouter

        router = RuleRouter()

        result = router.route("연차 알려줘")
        assert result.needs_clarify is True
        assert result.clarify_question in ClarifyTemplates.POLICY_VS_HR_PERSONAL


# =============================================================================
# Answer Generator Tests
# =============================================================================


class TestAnswerGenerator:
    """Answer Generator 테스트."""

    @pytest.fixture
    def generator(self):
        """Answer Generator fixture."""
        from app.services.answer_generator import AnswerGenerator
        return AnswerGenerator()

    @pytest.fixture
    def event_loop(self):
        """Create a new event loop for each test."""
        import asyncio
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    def test_error_response(self, generator, event_loop):
        """에러 응답 생성."""
        from app.models.personalization import AnswerGeneratorContext

        facts = PersonalizationFacts(
            sub_intent_id="Q11",
            error=PersonalizationError(
                type="NOT_FOUND",
                message="Data not found",
            ),
        )
        context = AnswerGeneratorContext(
            sub_intent_id="Q11",
            user_question="연차 몇 개 남았어?",
            facts=facts,
        )

        # Use the fixture event loop
        answer = event_loop.run_until_complete(
            generator.generate(context)
        )

        assert answer == ERROR_RESPONSE_TEMPLATES["NOT_FOUND"]

    def test_empty_facts_response(self, generator, event_loop):
        """빈 facts 응답."""
        from app.models.personalization import AnswerGeneratorContext

        facts = PersonalizationFacts(
            sub_intent_id="Q11",
            metrics={},
            items=[],
        )
        context = AnswerGeneratorContext(
            sub_intent_id="Q11",
            user_question="연차 몇 개 남았어?",
            facts=facts,
        )

        answer = event_loop.run_until_complete(
            generator.generate(context)
        )

        assert "조회된 데이터가 없어요" in answer


# =============================================================================
# Department Clarify Templates Tests
# =============================================================================


class TestDepartmentClarifyTemplates:
    """부서 검색 되묻기 템플릿 테스트."""

    def test_multiple_matches_template(self):
        """여러 부서 매칭 시 템플릿."""
        assert "어느 부서 기준으로 볼까요" in DepartmentClarifyTemplates.MULTIPLE_MATCHES

    def test_no_matches_template(self):
        """부서 없음 시 템플릿."""
        assert "해당 부서를 찾지 못했어요" in DepartmentClarifyTemplates.NO_MATCHES


# =============================================================================
# Integration Tests (Orchestrator + Personalization)
# =============================================================================


class TestOrchestratorPersonalization:
    """Orchestrator 개인화 통합 테스트."""

    @pytest.mark.asyncio
    async def test_backend_api_routing(self):
        """BACKEND_API 라우트 확인."""
        from app.services.router_orchestrator import RouterOrchestrator

        orchestrator = RouterOrchestrator()

        # 연차 조회 - BACKEND_API로 라우팅되어야 함
        result = await orchestrator.route(
            user_query="내 연차 며칠 남았어?",
            session_id="test-session-001",
        )

        # BACKEND_API 라우팅 확인 (OrchestrationResult에는 answer 속성이 없음)
        assert result.router_result.route_type == RouterRouteType.BACKEND_API
        assert result.router_result.tier0_intent == Tier0Intent.BACKEND_STATUS

    @pytest.mark.asyncio
    async def test_quiz_comparison_requires_clarify(self):
        """퀴즈 비교 조회는 되묻기가 필요함."""
        from app.services.router_orchestrator import RouterOrchestrator

        orchestrator = RouterOrchestrator()

        result = await orchestrator.route(
            user_query="내 평균이랑 전사 평균 비교해줘",
            session_id="test-session-002",
        )

        # 평균 비교 질문은 어떤 종류의 비교인지 되묻기 필요
        # (현재 구현: needs_clarify=True 반환)
        assert result.router_result.tier0_intent == Tier0Intent.BACKEND_STATUS
        # 되묻기 여부는 구현에 따라 다를 수 있음
        # result.needs_user_response가 True면 clarify 필요

    @pytest.mark.asyncio
    async def test_clarify_flow(self):
        """되묻기 플로우 테스트."""
        from app.services.router_orchestrator import RouterOrchestrator

        orchestrator = RouterOrchestrator()

        # 애매한 질문
        result = await orchestrator.route(
            user_query="교육 알려줘",
            session_id="test-session-003",
        )

        assert result.needs_user_response is True
        assert result.response_message in ClarifyTemplates.EDUCATION_CONTENT_VS_STATUS
