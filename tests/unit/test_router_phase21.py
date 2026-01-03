"""
Phase 21: Intent Router 테스트

RuleRouter, LLMRouter, RouterOrchestrator의 동작을 검증합니다:
- 애매한 질문에서 needs_clarify가 true로 나오는지
- 퀴즈 start/submit/gen이 requires_confirmation=true로 나오는지
- BACKEND_STATUS인데 sub_intent_id 비어있으면 clarify로 가는지
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.router_types import (
    ClarifyTemplates,
    ConfirmationTemplates,
    RouterDebugInfo,
    RouterDomain,
    RouterResult,
    RouterRouteType,
    SubIntentId,
    Tier0Intent,
)
from app.services.rule_router import RuleRouter
from app.services.llm_router import LLMRouter
from app.services.router_orchestrator import (
    RouterOrchestrator,
    PendingActionStore,
    PendingActionType,
    clear_pending_action_store,
)


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


@pytest.fixture(autouse=True)
def reset_pending_store():
    """테스트 전후 pending action store 초기화."""
    clear_pending_action_store()
    yield
    clear_pending_action_store()


# =============================================================================
# RuleRouter 테스트
# =============================================================================


class TestRuleRouterAmbiguousBoundaries:
    """애매한 경계 감지 테스트 (경계 A, B)."""

    def test_boundary_a_education_ambiguous(self):
        """경계 A: '교육 알려줘' - 내용인지 이수현황인지 애매함."""
        router = RuleRouter()

        ambiguous_queries = [
            "교육 알려줘",
            "교육 확인해줘",
            "교육 어떻게 되어있어?",
            "학습 현황 알려줘",
        ]

        for query in ambiguous_queries:
            result = router.route(query)
            assert result.needs_clarify is True, f"Query '{query}' should need clarification"
            assert "BOUNDARY_A" in result.debug.rule_hits or result.domain == RouterDomain.EDU

    def test_boundary_a_education_content_clear(self):
        """경계 A: '교육내용 알려줘' - 명확히 교육 내용 질문."""
        router = RuleRouter()

        clear_queries = [
            "4대교육 내용이 뭐야",
            "정보보호교육 설명해줘",
            "컴플라이언스교육 규정 알려줘",
        ]

        for query in clear_queries:
            result = router.route(query)
            assert result.needs_clarify is False, f"Query '{query}' should NOT need clarification"
            assert result.tier0_intent == Tier0Intent.EDUCATION_QA

    def test_boundary_a_education_status_clear(self):
        """경계 A: '내 교육 수료율' - 명확히 이수현황 조회."""
        router = RuleRouter()

        clear_queries = [
            "내 수료 현황 알려줘",
            "교육 이수율 확인해줘",
            "진도 얼마나 했어?",
            "언제까지 완료해야 해?",
        ]

        for query in clear_queries:
            result = router.route(query)
            assert result.needs_clarify is False, f"Query '{query}' should NOT need clarification"
            assert result.tier0_intent == Tier0Intent.BACKEND_STATUS

    def test_boundary_b_leave_ambiguous(self):
        """경계 B: '연차 알려줘' - 규정인지 내 잔여인지 애매함.

        Phase 50 업데이트:
        - '휴가 확인해줘'는 HR_PERSONAL_KEYWORDS에 '휴가 확인'이 추가되어 명확한 개인화로 분류됨
        - 진짜 애매한 패턴만 테스트 (알려줘, 어떻게 되어있어 등)
        """
        router = RuleRouter()

        ambiguous_queries = [
            "연차 알려줘",
            "연차 어떻게 되어있어?",
            "휴가 있어?",
            # Note: "휴가 확인해줘"는 Phase 50에서 명확한 개인화로 분류됨
        ]

        for query in ambiguous_queries:
            result = router.route(query)
            assert result.needs_clarify is True, f"Query '{query}' should need clarification"
            assert "BOUNDARY_B" in result.debug.rule_hits or result.domain == RouterDomain.HR

    def test_boundary_b_policy_clear(self):
        """경계 B: '연차 이월 규정' - 명확히 정책 질문."""
        router = RuleRouter()

        clear_queries = [
            "연차 이월 규정 알려줘",
            "휴가 정책이 뭐야",
            "연차 어떻게 계산해?",
        ]

        for query in clear_queries:
            result = router.route(query)
            assert result.needs_clarify is False, f"Query '{query}' should NOT need clarification"
            assert result.tier0_intent == Tier0Intent.POLICY_QA

    def test_boundary_b_personal_clear(self):
        """경계 B: '내 연차 며칠 남았어?' - 명확히 개인화 조회."""
        router = RuleRouter()

        clear_queries = [
            "내 연차 며칠 남았어?",
            "연차 잔여 알려줘",
            "내 휴가 몇 개야?",
            "내 급여 확인해줘",
        ]

        for query in clear_queries:
            result = router.route(query)
            assert result.needs_clarify is False, f"Query '{query}' should NOT need clarification"
            assert result.tier0_intent == Tier0Intent.BACKEND_STATUS


class TestRuleRouterCriticalActions:
    """치명 액션(퀴즈 3종) 확인 게이트 테스트."""

    def test_quiz_start_requires_confirmation(self):
        """QUIZ_START는 requires_confirmation=true."""
        router = RuleRouter()

        queries = [
            "퀴즈 시작해줘",
            "퀴즈 시작할게",
            "시험 시작",
            "퀴즈 풀게",
        ]

        for query in queries:
            result = router.route(query)
            assert result.requires_confirmation is True, f"Query '{query}' should require confirmation"
            assert result.sub_intent_id == SubIntentId.QUIZ_START.value
            assert ConfirmationTemplates.QUIZ_START in result.confirmation_prompt

    def test_quiz_submit_requires_confirmation(self):
        """QUIZ_SUBMIT은 requires_confirmation=true (퀴즈 문맥 필수).

        Phase 50 업데이트:
        - 오탐 방지를 위해 "채점해줘", "점수 확인" 등 범용 키워드는
          퀴즈/시험/테스트 문맥이 있어야만 QUIZ_SUBMIT으로 분류됨
        """
        router = RuleRouter()

        # 퀴즈 문맥이 있는 쿼리만 QUIZ_SUBMIT으로 분류
        queries_with_context = [
            "퀴즈 제출해줘",
            "시험 채점해줘",
            "테스트 점수 확인",
            "퀴즈 완료",
        ]

        for query in queries_with_context:
            result = router.route(query)
            assert result.requires_confirmation is True, f"Query '{query}' should require confirmation"
            assert result.sub_intent_id == SubIntentId.QUIZ_SUBMIT.value

    def test_quiz_submit_skipped_without_context(self):
        """퀴즈 문맥 없이 범용 키워드만 있으면 QUIZ_SUBMIT으로 분류되지 않음.

        Phase 50 추가: 오탐 방지 테스트
        - "채점해줘", "점수 확인" 등은 퀴즈 외 맥락에서도 사용 가능
        - 퀴즈 문맥 없이는 치명 액션으로 판정하지 않음
        """
        router = RuleRouter()

        queries_without_context = [
            "채점해줘",
            "점수 확인해줘",
            "제출할게",
        ]

        for query in queries_without_context:
            result = router.route(query)
            # 퀴즈 문맥 없음 → 치명 액션이 아님
            assert result.requires_confirmation is False, \
                f"Query '{query}' should NOT require confirmation (no quiz context)"
            assert result.sub_intent_id != SubIntentId.QUIZ_SUBMIT.value

    def test_quiz_generation_requires_confirmation(self):
        """QUIZ_GENERATION은 requires_confirmation=true."""
        router = RuleRouter()

        queries = [
            "퀴즈 생성해줘",
            "문제 만들어줘",
            "퀴즈 출제해줘",
        ]

        for query in queries:
            result = router.route(query)
            assert result.requires_confirmation is True, f"Query '{query}' should require confirmation"
            assert result.sub_intent_id == SubIntentId.QUIZ_GENERATION.value


class TestRuleRouterBasicClassification:
    """기본 의도 분류 테스트."""

    def test_policy_qa(self):
        """정책 질문은 POLICY_QA로 분류."""
        router = RuleRouter()

        queries = [
            "보안 규정 알려줘",
            "개인정보보호 정책이 뭐야",
            "결재 절차 설명해줘",
        ]

        for query in queries:
            result = router.route(query)
            assert result.tier0_intent == Tier0Intent.POLICY_QA
            assert result.route_type == RouterRouteType.RAG_INTERNAL

    def test_general_chat(self):
        """일반 잡담은 GENERAL_CHAT로 분류."""
        router = RuleRouter()

        queries = [
            "안녕하세요",
            "ㅎㅎ 반가워",
            "날씨 좋다",
        ]

        for query in queries:
            result = router.route(query)
            assert result.tier0_intent == Tier0Intent.GENERAL_CHAT
            assert result.route_type == RouterRouteType.LLM_ONLY

    def test_system_help(self):
        """시스템 도움말은 SYSTEM_HELP로 분류."""
        router = RuleRouter()

        queries = [
            "사용법 알려줘",
            "메뉴 어디있어?",
            "어떻게 사용해?",
        ]

        for query in queries:
            result = router.route(query)
            assert result.tier0_intent == Tier0Intent.SYSTEM_HELP
            assert result.route_type == RouterRouteType.ROUTE_SYSTEM_HELP

    def test_unknown_fallback(self):
        """분류 불가 시 기본값은 POLICY_QA + 낮은 confidence.

        Phase 43 변경: UNKNOWN → POLICY_QA로 기본값 변경 (RAG 우선 정책)
        무의미한 입력도 RAG를 먼저 타게 하여 최대한 답변을 시도함.
        """
        router = RuleRouter()

        result = router.route("ㅁㄴㅇㄹ")  # 무의미한 입력
        # Phase 43: 기본값이 POLICY_QA로 변경 (RAG 우선)
        assert result.tier0_intent == Tier0Intent.POLICY_QA
        assert result.confidence <= 0.5  # 낮은 confidence (0.5 이하)


# =============================================================================
# LLMRouter 테스트
# =============================================================================


class TestLLMRouterParsing:
    """LLM 응답 파싱 테스트."""

    @pytest.mark.anyio
    async def test_parse_valid_json(self):
        """유효한 JSON 응답 파싱."""
        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion = AsyncMock(return_value='''{
            "tier0_intent": "POLICY_QA",
            "domain": "POLICY",
            "route_type": "RAG_INTERNAL",
            "sub_intent_id": "",
            "confidence": 0.9,
            "needs_clarify": false,
            "clarify_question": "",
            "requires_confirmation": false,
            "confirmation_prompt": "",
            "debug": {"rule_hits": [], "keywords": ["규정"]}
        }''')

        router = LLMRouter(llm_client=mock_llm)
        result = await router.route("보안 규정 알려줘")

        assert result.tier0_intent == Tier0Intent.POLICY_QA
        assert result.domain == RouterDomain.POLICY
        assert result.route_type == RouterRouteType.RAG_INTERNAL
        assert result.confidence == 0.9

    @pytest.mark.anyio
    async def test_parse_json_with_markdown(self):
        """마크다운 코드블록으로 감싸진 JSON 파싱."""
        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion = AsyncMock(return_value='''```json
{
    "tier0_intent": "GENERAL_CHAT",
    "domain": "GENERAL",
    "route_type": "LLM_ONLY",
    "sub_intent_id": "",
    "confidence": 0.85,
    "needs_clarify": false,
    "clarify_question": "",
    "requires_confirmation": false,
    "confirmation_prompt": "",
    "debug": {}
}
```''')

        router = LLMRouter(llm_client=mock_llm)
        result = await router.route("안녕")

        assert result.tier0_intent == Tier0Intent.GENERAL_CHAT
        assert result.route_type == RouterRouteType.LLM_ONLY

    @pytest.mark.anyio
    async def test_fallback_on_invalid_json(self):
        """JSON 파싱 실패 시 폴백."""
        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion = AsyncMock(return_value="잘 모르겠습니다.")

        router = LLMRouter(llm_client=mock_llm)
        result = await router.route("이상한 질문")

        assert result.tier0_intent == Tier0Intent.UNKNOWN
        assert result.route_type == RouterRouteType.ROUTE_UNKNOWN
        assert result.needs_clarify is True

    @pytest.mark.anyio
    async def test_validate_backend_status_without_sub_intent(self):
        """BACKEND_STATUS인데 sub_intent_id 비어있으면 needs_clarify."""
        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion = AsyncMock(return_value='''{
            "tier0_intent": "BACKEND_STATUS",
            "domain": "HR",
            "route_type": "BACKEND_API",
            "sub_intent_id": "",
            "confidence": 0.7,
            "needs_clarify": false,
            "clarify_question": "",
            "requires_confirmation": false,
            "confirmation_prompt": "",
            "debug": {}
        }''')

        router = LLMRouter(llm_client=mock_llm)
        result = await router.route("내 정보 알려줘")

        # 유효성 검증에서 needs_clarify가 true로 보정되어야 함
        assert result.tier0_intent == Tier0Intent.BACKEND_STATUS
        assert result.needs_clarify is True

    @pytest.mark.anyio
    async def test_force_confirmation_for_critical_action(self):
        """치명 액션은 requires_confirmation 강제."""
        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion = AsyncMock(return_value='''{
            "tier0_intent": "BACKEND_STATUS",
            "domain": "QUIZ",
            "route_type": "BACKEND_API",
            "sub_intent_id": "QUIZ_START",
            "confidence": 0.9,
            "needs_clarify": false,
            "clarify_question": "",
            "requires_confirmation": false,
            "confirmation_prompt": "",
            "debug": {}
        }''')

        router = LLMRouter(llm_client=mock_llm)
        result = await router.route("퀴즈 시작")

        # 유효성 검증에서 requires_confirmation이 true로 보정되어야 함
        assert result.sub_intent_id == SubIntentId.QUIZ_START.value
        assert result.requires_confirmation is True


# =============================================================================
# RouterOrchestrator 테스트
# =============================================================================


class TestRouterOrchestrator:
    """RouterOrchestrator 통합 테스트."""

    @pytest.mark.anyio
    async def test_high_confidence_skips_llm(self):
        """높은 신뢰도면 LLM router 스킵."""
        mock_llm_router = AsyncMock()

        orchestrator = RouterOrchestrator(
            rule_router=RuleRouter(),
            llm_router=mock_llm_router,
        )

        # 명확한 정책 질문 (높은 신뢰도)
        result = await orchestrator.route(
            user_query="보안 규정 알려줘",
            session_id="test-session",
        )

        assert result.router_result.tier0_intent == Tier0Intent.POLICY_QA
        assert result.can_execute is True
        # LLM router는 호출되지 않아야 함
        mock_llm_router.route.assert_not_called()

    @pytest.mark.anyio
    async def test_clarify_response_stored(self):
        """되묻기 필요 시 pending action 저장."""
        orchestrator = RouterOrchestrator(rule_router=RuleRouter())

        result = await orchestrator.route(
            user_query="교육 알려줘",  # 애매한 질문
            session_id="test-session-clarify",
        )

        assert result.needs_user_response is True
        assert result.can_execute is False
        assert result.pending_action is not None
        assert result.pending_action.action_type == PendingActionType.CLARIFY
        assert "교육" in result.response_message or "이수현황" in result.response_message

    @pytest.mark.anyio
    async def test_confirmation_response_stored(self):
        """확인 필요 시 pending action 저장."""
        orchestrator = RouterOrchestrator(rule_router=RuleRouter())

        result = await orchestrator.route(
            user_query="퀴즈 시작해줘",
            session_id="test-session-confirm",
        )

        assert result.needs_user_response is True
        assert result.can_execute is False
        assert result.pending_action is not None
        assert result.pending_action.action_type == PendingActionType.CONFIRM
        assert result.pending_action.sub_intent_id == SubIntentId.QUIZ_START.value

    @pytest.mark.anyio
    async def test_confirmation_accepted(self):
        """사용자가 확인하면 실행 가능."""
        orchestrator = RouterOrchestrator(rule_router=RuleRouter())

        # 먼저 확인 요청
        await orchestrator.route(
            user_query="퀴즈 시작해줘",
            session_id="test-session-accept",
        )

        # 사용자가 '예' 응답
        result = await orchestrator.handle_confirmation(
            session_id="test-session-accept",
            confirmed=True,
        )

        assert result.can_execute is True
        assert result.needs_user_response is False

    @pytest.mark.anyio
    async def test_confirmation_declined(self):
        """사용자가 거부하면 취소."""
        orchestrator = RouterOrchestrator(rule_router=RuleRouter())

        # 먼저 확인 요청
        await orchestrator.route(
            user_query="퀴즈 시작해줘",
            session_id="test-session-decline",
        )

        # 사용자가 '아니오' 응답
        result = await orchestrator.handle_confirmation(
            session_id="test-session-decline",
            confirmed=False,
        )

        assert result.can_execute is False
        assert "취소" in result.response_message

    @pytest.mark.anyio
    async def test_pending_response_via_query(self):
        """대기 상태에서 응답이 오면 처리."""
        orchestrator = RouterOrchestrator(rule_router=RuleRouter())

        # 확인 대기 상태 만들기
        await orchestrator.route(
            user_query="퀴즈 시작해줘",
            session_id="test-session-pending",
        )

        # '예'로 응답
        result = await orchestrator.route(
            user_query="예",
            session_id="test-session-pending",
        )

        assert result.can_execute is True


class TestPendingActionStore:
    """PendingActionStore 테스트."""

    def test_set_and_get(self):
        """저장 및 조회."""
        from app.services.router_orchestrator import PendingAction

        store = PendingActionStore()
        action = PendingAction(
            action_type=PendingActionType.CONFIRM,
            trace_id="test-trace",
            pending_intent=Tier0Intent.BACKEND_STATUS,
            sub_intent_id="QUIZ_START",
        )

        store.set("session-1", action)
        retrieved = store.get("session-1")

        assert retrieved is not None
        assert retrieved.trace_id == "test-trace"
        assert retrieved.sub_intent_id == "QUIZ_START"

    def test_delete(self):
        """삭제."""
        from app.services.router_orchestrator import PendingAction

        store = PendingActionStore()
        action = PendingAction(
            action_type=PendingActionType.CLARIFY,
            trace_id="test-trace-2",
            pending_intent=Tier0Intent.UNKNOWN,
        )

        store.set("session-2", action)
        store.delete("session-2")

        assert store.get("session-2") is None

    def test_nonexistent_session(self):
        """존재하지 않는 세션 조회."""
        store = PendingActionStore()
        assert store.get("nonexistent") is None


# =============================================================================
# 예시 입력 5개에 대한 RouterResult 출력 테스트
# =============================================================================


class TestExampleInputs:
    """prompt.txt에 명시된 예시 입력 테스트."""

    def test_example_1_policy_clear(self):
        """예시 1: 명확한 정책 질문."""
        router = RuleRouter()
        result = router.route("연차 이월 규정 알려줘")

        assert result.tier0_intent == Tier0Intent.POLICY_QA
        assert result.domain == RouterDomain.POLICY
        assert result.route_type == RouterRouteType.RAG_INTERNAL
        assert result.confidence >= 0.8
        assert result.needs_clarify is False
        assert result.requires_confirmation is False

    def test_example_2_hr_personal(self):
        """예시 2: HR 개인화 조회."""
        router = RuleRouter()
        result = router.route("내 연차 며칠 남았어?")

        assert result.tier0_intent == Tier0Intent.BACKEND_STATUS
        assert result.domain == RouterDomain.HR
        assert result.route_type == RouterRouteType.BACKEND_API
        assert result.confidence >= 0.8
        assert result.needs_clarify is False

    def test_example_3_education_ambiguous(self):
        """예시 3: 교육 관련 애매한 질문."""
        router = RuleRouter()
        result = router.route("교육 알려줘")

        assert result.needs_clarify is True
        assert result.clarify_question != ""

    def test_example_4_quiz_start(self):
        """예시 4: 퀴즈 시작 (치명 액션)."""
        router = RuleRouter()
        result = router.route("퀴즈 시작해줘")

        assert result.tier0_intent == Tier0Intent.BACKEND_STATUS
        assert result.domain == RouterDomain.QUIZ
        assert result.sub_intent_id == SubIntentId.QUIZ_START.value
        assert result.requires_confirmation is True
        assert result.confirmation_prompt != ""

    def test_example_5_general_chat(self):
        """예시 5: 일반 잡담."""
        router = RuleRouter()
        result = router.route("안녕하세요")

        assert result.tier0_intent == Tier0Intent.GENERAL_CHAT
        assert result.domain == RouterDomain.GENERAL
        assert result.route_type == RouterRouteType.LLM_ONLY
        assert result.confidence >= 0.7


# =============================================================================
# Phase 50: Q20 라우팅 테스트
# =============================================================================


class TestPersonalizationRouting:
    """개인화 Q20 라우팅 테스트.

    Phase 50 추가:
    - Q20: HR 할 일/미완료 항목 (HR_TODO_CHECK)

    Note: Q5(평균비교), Q6(보안토픽TOP3)는 프로젝트 범위에서 제외됨.
    """

    def test_hr_todo_check_q20(self):
        """Q20: HR 할 일/미완료 항목 → HR_TODO_CHECK."""
        router = RuleRouter()

        queries = [
            "HR 할 일 뭐야",
            "인사 업무 알려줘",
            "연말정산 해야해?",
            "미완료 HR 업무",
        ]

        for query in queries:
            result = router.route(query)
            assert result.sub_intent_id == SubIntentId.HR_TODO_CHECK.value, \
                f"Query '{query}' should be HR_TODO_CHECK"
            assert result.domain == RouterDomain.HR


# =============================================================================
# Phase 50: 키워드 충돌 회귀 테스트
# =============================================================================


class TestKeywordCollisionRegression:
    """키워드 부분문자열 충돌 회귀 테스트.

    Phase 50 추가:
    - "완료" ↔ "미완료" 충돌 방지
    - 부정 접두어 충돌 케이스 검증
    """

    def test_complete_vs_incomplete_collision(self):
        """'완료' vs '미완료' 부분문자열 충돌 방지.

        버그 재현: QUIZ_SUBMIT_KEYWORDS에 "완료"가 있으면
        "미완료 HR 업무"도 QUIZ_SUBMIT으로 잘못 분류됨.

        해결: "완료" 대신 "퀴즈 완료", "시험 완료" 등 문맥 포함 키워드 사용
        """
        router = RuleRouter()

        # "미완료 HR 업무" → HR_TODO_CHECK (NOT QUIZ_SUBMIT)
        result = router.route("미완료 HR 업무")
        assert result.sub_intent_id == SubIntentId.HR_TODO_CHECK.value, \
            "'미완료 HR 업무' should be HR_TODO_CHECK, not QUIZ_SUBMIT"
        assert result.requires_confirmation is False

        # "퀴즈 제출 완료" → QUIZ_SUBMIT
        result2 = router.route("퀴즈 완료")
        assert result2.sub_intent_id == SubIntentId.QUIZ_SUBMIT.value
        assert result2.requires_confirmation is True

    def test_edu_vs_hr_todo_priority(self):
        """EDU_STATUS vs HR_TODO 우선순위 충돌 방지.

        버그 재현: "할 일" 키워드가 EDU_STATUS_KEYWORDS와 HR_TODO_KEYWORDS
        둘 다에 있으면 먼저 체크되는 쪽으로 분류됨.

        해결: HR_TODO는 "hr/인사" 명시 키워드만 포함, 범용 키워드 제외
        """
        router = RuleRouter()

        # "이번 주 할 일" → EDU_STATUS_CHECK (교육 할 일)
        result = router.route("이번 주 할 일 뭐야?")
        assert result.sub_intent_id == SubIntentId.EDU_STATUS_CHECK.value

        # "올해 HR 할 일" → HR_TODO_CHECK (HR 할 일)
        result2 = router.route("올해 HR 할 일 뭐야?")
        assert result2.sub_intent_id == SubIntentId.HR_TODO_CHECK.value

    def test_quiz_context_prevents_false_positive(self):
        """퀴즈 문맥 조건이 오탐을 방지하는지 검증.

        버그 재현: "점수 확인해줘" 같은 범용 표현이
        퀴즈 문맥 없이도 QUIZ_SUBMIT으로 분류됨.

        해결: QUIZ_SUBMIT은 "퀴즈/시험/테스트" 문맥이 있어야만 판정
        """
        router = RuleRouter()

        # 퀴즈 문맥 없음 → NOT QUIZ_SUBMIT
        result = router.route("점수 확인해줘")
        assert result.sub_intent_id != SubIntentId.QUIZ_SUBMIT.value
        assert result.requires_confirmation is False

        # 퀴즈 문맥 있음 → QUIZ_SUBMIT
        result2 = router.route("퀴즈 점수 확인해줘")
        assert result2.sub_intent_id == SubIntentId.QUIZ_SUBMIT.value
        assert result2.requires_confirmation is True
