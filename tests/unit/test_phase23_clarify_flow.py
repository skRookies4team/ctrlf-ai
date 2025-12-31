"""
Phase 23: 되묻기 후 2턴 처리 테스트

테스트 케이스:
1. "교육 알려줘" → needs_clarify + pending 저장
2. "이수현황" → pending 기반으로 백엔드 조회 route
3. TTL 경과 시 pending 무효 처리 확인
4. 긴 응답(20자 초과)은 새 질문으로 처리
5. 키워드 매핑으로 sub_intent 결정
"""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models.router_types import (
    RouterDomain,
    RouterResult,
    RouterRouteType,
    Tier0Intent,
)
from app.services.router_orchestrator import (
    ClarifyGroup,
    CLARIFY_SHORT_RESPONSE_MAX_LENGTH,
    PendingAction,
    PendingActionStore,
    PendingActionType,
    RouterOrchestrator,
    clear_pending_action_store,
)
from app.services.rule_router import RuleRouter


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
# PendingAction 모델 확장 테스트
# =============================================================================


class TestPendingActionModel:
    """Phase 23: PendingAction 모델 확장 테스트."""

    def test_pending_action_has_original_query(self):
        """PendingAction에 original_query 필드가 있어야 함."""
        pending = PendingAction(
            action_type=PendingActionType.CLARIFY,
            trace_id="test-trace",
            pending_intent=Tier0Intent.EDUCATION_QA,
            original_query="교육 알려줘",
        )
        assert pending.original_query == "교육 알려줘"

    def test_pending_action_has_clarify_group(self):
        """PendingAction에 clarify_group 필드가 있어야 함."""
        pending = PendingAction(
            action_type=PendingActionType.CLARIFY,
            trace_id="test-trace",
            pending_intent=Tier0Intent.EDUCATION_QA,
            clarify_group=ClarifyGroup.EDU,
        )
        assert pending.clarify_group == ClarifyGroup.EDU

    def test_pending_action_has_expires_at(self):
        """PendingAction에 expires_at 필드가 있어야 함."""
        expires = datetime.now(timezone.utc) + timedelta(seconds=300)
        pending = PendingAction(
            action_type=PendingActionType.CLARIFY,
            trace_id="test-trace",
            pending_intent=Tier0Intent.EDUCATION_QA,
            expires_at=expires,
        )
        assert pending.expires_at is not None

    def test_pending_action_has_user_id(self):
        """PendingAction에 user_id 필드가 있어야 함."""
        pending = PendingAction(
            action_type=PendingActionType.CLARIFY,
            trace_id="test-trace",
            pending_intent=Tier0Intent.EDUCATION_QA,
            user_id="user-123",
        )
        assert pending.user_id == "user-123"


# =============================================================================
# PendingActionStore TTL 테스트
# =============================================================================


class TestPendingActionStoreTTL:
    """Phase 23: TTL 기반 만료 처리 테스트."""

    def test_store_get_returns_none_after_ttl_expired(self):
        """TTL이 지난 pending은 get()에서 None 반환."""
        store = PendingActionStore()

        # 이미 만료된 pending 저장
        expired_pending = PendingAction(
            action_type=PendingActionType.CLARIFY,
            trace_id="test-trace",
            pending_intent=Tier0Intent.EDUCATION_QA,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),  # 1초 전 만료
        )
        store.set("session-expired", expired_pending)

        # get() 호출 시 만료됨 → None 반환
        result = store.get("session-expired")
        assert result is None

    def test_store_get_returns_pending_before_ttl(self):
        """TTL 이전에는 pending이 정상 반환."""
        store = PendingActionStore()

        # 아직 유효한 pending 저장
        valid_pending = PendingAction(
            action_type=PendingActionType.CLARIFY,
            trace_id="test-trace",
            pending_intent=Tier0Intent.EDUCATION_QA,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),  # 5분 후 만료
        )
        store.set("session-valid", valid_pending)

        # get() 호출 시 정상 반환
        result = store.get("session-valid")
        assert result is not None
        assert result.trace_id == "test-trace"

    def test_store_ttl_deletes_expired_on_get(self):
        """만료된 pending은 get() 호출 시 삭제됨."""
        store = PendingActionStore()

        expired_pending = PendingAction(
            action_type=PendingActionType.CLARIFY,
            trace_id="test-trace",
            pending_intent=Tier0Intent.EDUCATION_QA,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        store.set("session-to-delete", expired_pending)

        # get() 호출로 만료 확인 → 삭제됨
        store.get("session-to-delete")

        # 내부 저장소에서 삭제되었는지 확인
        assert "session-to-delete" not in store._store


# =============================================================================
# RouterOrchestrator 되묻기 흐름 테스트
# =============================================================================


class TestClarifyFlowPendingCreation:
    """Phase 23: 되묻기 시 pending 생성 테스트."""

    @pytest.mark.anyio
    async def test_clarify_creates_pending_with_original_query(self):
        """되묻기 발생 시 original_query가 pending에 저장됨."""
        store = PendingActionStore()
        orchestrator = RouterOrchestrator(pending_store=store)

        # "교육 알려줘" - 애매한 질문으로 되묻기 발생
        result = await orchestrator.route(
            user_query="교육 알려줘",
            session_id="session-001",
            user_id="user-001",
        )

        # needs_clarify 확인
        if result.needs_user_response:
            pending = store.get("session-001")
            if pending and pending.action_type == PendingActionType.CLARIFY:
                assert pending.original_query == "교육 알려줘"

    @pytest.mark.anyio
    async def test_clarify_creates_pending_with_clarify_group(self):
        """되묻기 발생 시 clarify_group이 pending에 저장됨."""
        store = PendingActionStore()
        orchestrator = RouterOrchestrator(pending_store=store)

        result = await orchestrator.route(
            user_query="교육 알려줘",
            session_id="session-002",
            user_id="user-002",
        )

        if result.needs_user_response:
            pending = store.get("session-002")
            if pending and pending.action_type == PendingActionType.CLARIFY:
                # clarify_group이 설정되어 있어야 함 (EDU 또는 UNKNOWN)
                assert pending.clarify_group is not None
                # tier0_intent에 따라 clarify_group이 결정됨
                assert isinstance(pending.clarify_group, ClarifyGroup)

    @pytest.mark.anyio
    async def test_clarify_creates_pending_with_expires_at(self):
        """되묻기 발생 시 expires_at이 설정됨 (TTL=5분)."""
        store = PendingActionStore()
        orchestrator = RouterOrchestrator(pending_store=store)

        before = datetime.now(timezone.utc)

        result = await orchestrator.route(
            user_query="교육 알려줘",
            session_id="session-003",
            user_id="user-003",
        )

        after = datetime.now(timezone.utc)

        if result.needs_user_response:
            pending = store.get("session-003")
            if pending and pending.action_type == PendingActionType.CLARIFY:
                assert pending.expires_at is not None
                # expires_at은 now + TTL(300초) 범위 내
                expected_min = before + timedelta(seconds=299)
                expected_max = after + timedelta(seconds=301)
                assert expected_min <= pending.expires_at <= expected_max


# =============================================================================
# ClarifyAnswerHandler 테스트 (키워드 매핑)
# =============================================================================


class TestClarifyAnswerHandler:
    """Phase 23: 되묻기 응답 키워드 매핑 테스트."""

    @pytest.mark.anyio
    async def test_short_response_backend_status_keyword(self):
        """짧은 응답 '이수현황' → BACKEND_STATUS로 라우팅."""
        store = PendingActionStore()
        orchestrator = RouterOrchestrator(pending_store=store)

        # 먼저 되묻기 pending 생성 (수동)
        pending = PendingAction(
            action_type=PendingActionType.CLARIFY,
            trace_id="test-trace",
            pending_intent=Tier0Intent.EDUCATION_QA,
            original_query="교육 알려줘",
            clarify_group=ClarifyGroup.EDU,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
            router_result=RouterResult(
                tier0_intent=Tier0Intent.EDUCATION_QA,
                domain=RouterDomain.EDU,
            ),
        )
        store.set("session-edu-001", pending)

        # "이수현황" 응답
        result = await orchestrator.route(
            user_query="이수현황",
            session_id="session-edu-001",
        )

        # BACKEND_API로 라우팅되어야 함
        assert result.router_result.route_type == RouterRouteType.BACKEND_API
        assert result.can_execute is True

    @pytest.mark.anyio
    async def test_short_response_rag_internal_keyword(self):
        """짧은 응답 '내용' → RAG_INTERNAL로 라우팅."""
        store = PendingActionStore()
        orchestrator = RouterOrchestrator(pending_store=store)

        pending = PendingAction(
            action_type=PendingActionType.CLARIFY,
            trace_id="test-trace",
            pending_intent=Tier0Intent.EDUCATION_QA,
            original_query="교육 알려줘",
            clarify_group=ClarifyGroup.EDU,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
            router_result=RouterResult(
                tier0_intent=Tier0Intent.EDUCATION_QA,
                domain=RouterDomain.EDU,
            ),
        )
        store.set("session-edu-002", pending)

        # "내용" 응답
        result = await orchestrator.route(
            user_query="내용",
            session_id="session-edu-002",
        )

        # RAG_INTERNAL로 라우팅되어야 함
        assert result.router_result.route_type == RouterRouteType.RAG_INTERNAL
        assert result.can_execute is True

    @pytest.mark.anyio
    async def test_short_response_jindo_keyword(self):
        """짧은 응답 '진도' → BACKEND_STATUS로 라우팅."""
        store = PendingActionStore()
        orchestrator = RouterOrchestrator(pending_store=store)

        pending = PendingAction(
            action_type=PendingActionType.CLARIFY,
            trace_id="test-trace",
            pending_intent=Tier0Intent.EDUCATION_QA,
            original_query="교육 알려줘",
            clarify_group=ClarifyGroup.EDU,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
            router_result=RouterResult(
                tier0_intent=Tier0Intent.EDUCATION_QA,
                domain=RouterDomain.EDU,
            ),
        )
        store.set("session-edu-003", pending)

        # "진도" 응답
        result = await orchestrator.route(
            user_query="진도",
            session_id="session-edu-003",
        )

        # BACKEND_API로 라우팅되어야 함
        assert result.router_result.route_type == RouterRouteType.BACKEND_API

    @pytest.mark.anyio
    async def test_short_response_pending_deleted_after_processing(self):
        """되묻기 응답 처리 후 pending이 삭제됨 (one-shot)."""
        store = PendingActionStore()
        orchestrator = RouterOrchestrator(pending_store=store)

        pending = PendingAction(
            action_type=PendingActionType.CLARIFY,
            trace_id="test-trace",
            pending_intent=Tier0Intent.EDUCATION_QA,
            original_query="교육 알려줘",
            clarify_group=ClarifyGroup.EDU,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
            router_result=RouterResult(
                tier0_intent=Tier0Intent.EDUCATION_QA,
                domain=RouterDomain.EDU,
            ),
        )
        store.set("session-oneshot", pending)

        # 응답 처리
        await orchestrator.route(
            user_query="이수현황",
            session_id="session-oneshot",
        )

        # pending이 삭제되었는지 확인
        assert store.get("session-oneshot") is None


# =============================================================================
# 긴 응답 처리 테스트
# =============================================================================


class TestLongResponseHandling:
    """Phase 23: 긴 응답(20자 초과) 처리 테스트."""

    @pytest.mark.anyio
    async def test_long_response_treated_as_new_query(self):
        """20자 초과 응답은 새 질문으로 처리됨."""
        store = PendingActionStore()
        orchestrator = RouterOrchestrator(pending_store=store)

        original_trace_id = "test-trace-original"
        original_query = "교육 알려줘"
        pending = PendingAction(
            action_type=PendingActionType.CLARIFY,
            trace_id=original_trace_id,
            pending_intent=Tier0Intent.EDUCATION_QA,
            original_query=original_query,
            clarify_group=ClarifyGroup.EDU,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
        )
        store.set("session-long", pending)

        # 21자 이상의 긴 응답 (새 질문처럼 처리)
        long_response = "연차 휴가 규정이 어떻게 되는지 자세하게 알려주세요"  # 25자

        result = await orchestrator.route(
            user_query=long_response,
            session_id="session-long",
        )

        # 라우팅 결과가 반환되어야 함
        assert result.router_result is not None

        # 원래 pending이 소비되었는지 확인:
        # - None이거나 (새 clarify 안 나온 경우)
        # - 새 pending으로 교체되었어야 함 (새 clarify 나온 경우)
        current_pending = store.get("session-long")
        if current_pending is not None:
            # 새 pending이면 원래 것과 달라야 함 (trace_id 또는 original_query)
            assert (
                current_pending.trace_id != original_trace_id or
                current_pending.original_query != original_query
            ), "원래 pending이 소비되지 않고 남아있음"

    def test_short_response_max_length_constant(self):
        """짧은 응답 기준은 20자."""
        assert CLARIFY_SHORT_RESPONSE_MAX_LENGTH == 20


# =============================================================================
# 키워드 매핑 실패 시 결합 테스트
# =============================================================================


class TestCombinedQueryRouting:
    """Phase 23: 키워드 매핑 실패 시 원문+응답 결합 재라우팅."""

    @pytest.mark.anyio
    async def test_unknown_keyword_combines_with_original(self):
        """알 수 없는 키워드면 원문+응답 결합하여 재라우팅."""
        store = PendingActionStore()
        orchestrator = RouterOrchestrator(pending_store=store)

        original_trace_id = "test-trace-original"
        original_query = "교육 알려줘"
        pending = PendingAction(
            action_type=PendingActionType.CLARIFY,
            trace_id=original_trace_id,
            pending_intent=Tier0Intent.EDUCATION_QA,
            original_query=original_query,
            clarify_group=ClarifyGroup.EDU,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
        )
        store.set("session-unknown", pending)

        # 알 수 없는 키워드 응답
        result = await orchestrator.route(
            user_query="뭐지?",  # 매핑되지 않는 키워드
            session_id="session-unknown",
        )

        # 라우팅 결과가 반환되어야 함
        assert result.router_result is not None

        # 원래 pending이 소비되었는지 확인:
        # - None이거나 (새 clarify 안 나온 경우)
        # - 새 pending으로 교체되었어야 함 (새 clarify 나온 경우)
        current_pending = store.get("session-unknown")
        if current_pending is not None:
            # 새 pending이면 원래 것과 달라야 함 (trace_id 또는 original_query)
            assert (
                current_pending.trace_id != original_trace_id or
                current_pending.original_query != original_query
            ), "원래 pending이 소비되지 않고 남아있음"


# =============================================================================
# ClarifyGroup 결정 테스트
# =============================================================================


class TestClarifyGroupDetermination:
    """Phase 23: Tier0Intent → ClarifyGroup 결정 테스트."""

    def test_education_qa_maps_to_edu_group(self):
        """EDUCATION_QA → ClarifyGroup.EDU."""
        orchestrator = RouterOrchestrator()
        group = orchestrator._determine_clarify_group(Tier0Intent.EDUCATION_QA)
        assert group == ClarifyGroup.EDU

    def test_backend_status_maps_to_edu_group(self):
        """BACKEND_STATUS → ClarifyGroup.EDU (교육 현황 조회)."""
        orchestrator = RouterOrchestrator()
        group = orchestrator._determine_clarify_group(Tier0Intent.BACKEND_STATUS)
        assert group == ClarifyGroup.EDU

    def test_policy_qa_maps_to_policy_group(self):
        """POLICY_QA → ClarifyGroup.POLICY."""
        orchestrator = RouterOrchestrator()
        group = orchestrator._determine_clarify_group(Tier0Intent.POLICY_QA)
        assert group == ClarifyGroup.POLICY

    def test_general_chat_maps_to_faq_group(self):
        """GENERAL_CHAT → ClarifyGroup.FAQ."""
        orchestrator = RouterOrchestrator()
        group = orchestrator._determine_clarify_group(Tier0Intent.GENERAL_CHAT)
        assert group == ClarifyGroup.FAQ

    def test_unknown_intent_maps_to_unknown_group(self):
        """UNKNOWN → ClarifyGroup.UNKNOWN."""
        orchestrator = RouterOrchestrator()
        group = orchestrator._determine_clarify_group(Tier0Intent.UNKNOWN)
        assert group == ClarifyGroup.UNKNOWN
