"""
Phase 22: ChatService와 RouterOrchestrator 통합 테스트

테스트 케이스:
1. RouterOrchestrator 호출 및 결과 매핑
2. needs_clarify=true → clarify_question 반환
3. requires_confirmation=true → confirmation_prompt 반환
4. SYSTEM_HELP 라우트 → 시스템 도움말 응답
5. UNKNOWN 라우트 → 되묻기 응답

Phase 22 수정:
- ROUTER_ORCHESTRATOR_ENABLED 플래그를 모킹하여 orchestrator 활성화
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.intent import IntentType, RouteType


# Phase 22 수정: Settings 모킹을 위한 헬퍼
def get_mock_settings_with_router_enabled():
    """ROUTER_ORCHESTRATOR_ENABLED=True인 Settings mock."""
    mock_settings = MagicMock()
    mock_settings.ROUTER_ORCHESTRATOR_ENABLED = True
    mock_settings.llm_base_url = "http://mock-llm:8001"
    return mock_settings


# Phase 22: IntentResult mock 생성 헬퍼
def create_mock_intent_result(intent=IntentType.POLICY_QA, domain="POLICY", route=RouteType.RAG_INTERNAL, user_role="EMPLOYEE"):
    """실제 IntentType/RouteType을 반환하는 IntentResult mock."""
    mock_result = MagicMock()
    mock_result.intent = intent
    mock_result.domain = domain
    mock_result.route = route
    # user_role은 .value 속성을 가져야 함
    mock_user_role = MagicMock()
    mock_user_role.value = user_role
    mock_result.user_role = mock_user_role
    return mock_result


from app.models.chat import ChatMessage, ChatRequest
from app.models.router_types import (
    RouterDebugInfo,
    RouterDomain,
    RouterResult,
    RouterRouteType,
    Tier0Intent,
)
from app.services.chat_service import ChatService
from app.services.router_orchestrator import (
    OrchestrationResult,
    PendingAction,
    PendingActionType,
    RouterOrchestrator,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


@pytest.fixture
def mock_orchestrator():
    """Mock RouterOrchestrator."""
    return AsyncMock(spec=RouterOrchestrator)


@pytest.fixture
def mock_chat_request():
    """테스트용 ChatRequest."""
    return ChatRequest(
        session_id="test-session-001",
        user_id="user-001",
        user_role="EMPLOYEE",
        department="IT",
        messages=[
            ChatMessage(role="user", content="연차 규정 알려줘")
        ],
    )


# =============================================================================
# 테스트 1: RouterOrchestrator 정상 호출
# =============================================================================


@pytest.mark.anyio
async def test_chat_service_calls_orchestrator(mock_chat_request):
    """ChatService가 RouterOrchestrator를 호출하는지 확인."""
    # Arrange
    mock_orchestrator = AsyncMock(spec=RouterOrchestrator)
    mock_orchestrator.route.return_value = OrchestrationResult(
        router_result=RouterResult(
            tier0_intent=Tier0Intent.POLICY_QA,
            domain=RouterDomain.POLICY,
            route_type=RouterRouteType.RAG_INTERNAL,
            confidence=0.95,
        ),
        needs_user_response=False,
        can_execute=True,
    )

    # Mock other services
    mock_llm = AsyncMock()
    mock_llm.generate_chat_completion = AsyncMock(return_value="테스트 응답입니다.")

    mock_pii = MagicMock()
    mock_pii.detect_and_mask = AsyncMock(return_value=MagicMock(
        masked_text="연차 규정 알려줘",
        has_pii=False,
        tags=[],
    ))

    mock_ragflow = MagicMock()
    mock_ragflow.search_as_sources = AsyncMock(return_value=[])

    # Phase 22 수정: Settings mock 추가
    mock_settings = get_mock_settings_with_router_enabled()

    # Phase 22: app.core.config.get_settings를 패치 (import 위치)
    with patch.object(ChatService, '__init__', lambda x, **kwargs: None), \
         patch('app.core.config.get_settings', return_value=mock_settings):
        service = ChatService()
        service._router_orchestrator = mock_orchestrator
        service._llm = mock_llm
        service._pii = mock_pii
        service._ragflow = mock_ragflow
        # 실제 IntentType/RouteType 사용
        service._intent = MagicMock()
        service._intent.classify.return_value = create_mock_intent_result()
        service._guardrail = MagicMock()
        service._guardrail.get_system_prompt_prefix.return_value = ""
        service._guardrail.apply_to_answer.return_value = "테스트 응답입니다."
        service._ai_log = MagicMock()
        service._ai_log.mask_for_log = AsyncMock(return_value=("질문", "답변"))
        service._ai_log.create_log_entry = MagicMock()
        service._ai_log.send_log_async = AsyncMock()
        service._backend_data = MagicMock()
        service._backend_data.get_last_latency_ms.return_value = None
        service._context_formatter = MagicMock()
        service._video_progress = MagicMock()
        # Milvus 관련 속성 추가
        service._milvus_enabled = False
        service._milvus = None
        # Phase 2 리팩토링: 핸들러 mock 추가
        service._rag_handler = MagicMock()
        service._rag_handler.perform_search_with_fallback = AsyncMock(return_value=([], False, "RAGFLOW"))
        service._backend_handler = MagicMock()
        service._backend_handler.fetch_for_api = AsyncMock(return_value="")
        service._backend_handler.fetch_for_mixed = AsyncMock(return_value="")
        service._message_builder = MagicMock()
        service._message_builder.build_rag_messages = MagicMock(return_value=[
            {"role": "system", "content": "test"},
            {"role": "user", "content": "test"}
        ])
        # Answer guard mock 추가
        service._answer_guard = MagicMock()
        service._answer_guard.check_complaint_fast_path = MagicMock(return_value=None)
        service._answer_guard.check_answerability = MagicMock(return_value=(True, None))
        service._answer_guard.create_debug_info = MagicMock(return_value={})
        service._answer_guard.apply_citation_check = MagicMock(return_value=("테스트 응답입니다.", False))
        service._answer_guard.validate_citation = MagicMock(return_value=(True, "테스트 응답입니다."))
        service._answer_guard.apply_language_check = MagicMock(return_value=("테스트 응답입니다.", False))
        service._answer_guard.enforce_korean_output = AsyncMock(return_value=(True, "테스트 응답입니다."))
        # Phase 45/46: 소프트 가드레일 mock
        service._answer_guard.check_soft_guardrail = MagicMock(return_value=(False, None))
        service._answer_guard.get_soft_guardrail_system_instruction = MagicMock(return_value="")
        # Phase 39: last error reason
        service._last_error_reason = None

        # Act
        response = await service.handle_chat(mock_chat_request)

        # Assert: Orchestrator가 호출됨
        mock_orchestrator.route.assert_called_once()
        call_args = mock_orchestrator.route.call_args
        assert call_args.kwargs["session_id"] == "test-session-001"


# =============================================================================
# 테스트 2: needs_clarify=true → 되묻기 응답
# =============================================================================


@pytest.mark.anyio
async def test_chat_returns_clarify_response(mock_chat_request):
    """needs_clarify=true일 때 되묻기 응답을 반환."""
    # Arrange
    clarify_question = "교육 내용 설명이 필요하신가요, 아니면 내 이수현황/진도 조회가 필요하신가요?"

    mock_orchestrator = AsyncMock(spec=RouterOrchestrator)
    mock_orchestrator.route.return_value = OrchestrationResult(
        router_result=RouterResult(
            tier0_intent=Tier0Intent.EDUCATION_QA,
            domain=RouterDomain.EDU,
            route_type=RouterRouteType.RAG_INTERNAL,
            confidence=0.5,
            needs_clarify=True,
            clarify_question=clarify_question,
        ),
        needs_user_response=True,
        response_message=clarify_question,
        can_execute=False,
    )

    mock_pii = MagicMock()
    mock_pii.detect_and_mask = AsyncMock(return_value=MagicMock(
        masked_text="교육 알려줘",
        has_pii=False,
        tags=[],
    ))

    # Phase 22 수정: Settings mock 추가
    mock_settings = get_mock_settings_with_router_enabled()

    # Phase 22: app.core.config.get_settings를 패치
    with patch.object(ChatService, '__init__', lambda x, **kwargs: None), \
         patch('app.core.config.get_settings', return_value=mock_settings):
        service = ChatService()
        service._router_orchestrator = mock_orchestrator
        service._pii = mock_pii
        service._llm = MagicMock()
        service._ragflow = MagicMock()
        # 실제 IntentType/RouteType 사용
        service._intent = MagicMock()
        service._intent.classify.return_value = create_mock_intent_result(
            intent=IntentType.EDUCATION_QA,
            domain="EDU",
        )
        service._guardrail = MagicMock()
        service._ai_log = MagicMock()
        service._backend_data = MagicMock()
        service._context_formatter = MagicMock()
        service._video_progress = MagicMock()
        # Answer guard mock 추가
        service._answer_guard = MagicMock()
        service._answer_guard.check_complaint_fast_path = MagicMock(return_value=None)
        # Phase 45/46: 소프트 가드레일 mock
        service._answer_guard.check_soft_guardrail = MagicMock(return_value=(False, None))
        service._answer_guard.get_soft_guardrail_system_instruction = MagicMock(return_value="")
        # Phase 39: last error reason
        service._last_error_reason = None

        # ChatRequest with 교육 관련 질문
        request = ChatRequest(
            session_id="test-session-002",
            user_id="user-001",
            user_role="EMPLOYEE",
            messages=[ChatMessage(role="user", content="교육 알려줘")],
        )

        # Act
        response = await service.handle_chat(request)

        # Assert
        assert response.answer == clarify_question
        assert response.meta.route == "CLARIFY"
        assert response.meta.intent == "EDUCATION_QA"


# =============================================================================
# 테스트 3: requires_confirmation=true → 확인 응답
# =============================================================================


@pytest.mark.anyio
async def test_chat_returns_confirmation_response(mock_chat_request):
    """requires_confirmation=true일 때 확인 프롬프트 반환."""
    # Arrange
    confirmation_prompt = "퀴즈를 지금 시작할까요? (예/아니오)"

    mock_orchestrator = AsyncMock(spec=RouterOrchestrator)
    mock_orchestrator.route.return_value = OrchestrationResult(
        router_result=RouterResult(
            tier0_intent=Tier0Intent.BACKEND_STATUS,
            domain=RouterDomain.QUIZ,
            route_type=RouterRouteType.BACKEND_API,
            confidence=0.9,
            sub_intent_id="QUIZ_START",
            requires_confirmation=True,
            confirmation_prompt=confirmation_prompt,
        ),
        needs_user_response=True,
        response_message=confirmation_prompt,
        can_execute=False,
    )

    mock_pii = MagicMock()
    mock_pii.detect_and_mask = AsyncMock(return_value=MagicMock(
        masked_text="퀴즈 시작해줘",
        has_pii=False,
        tags=[],
    ))

    # Phase 22 수정: Settings mock 추가
    mock_settings = get_mock_settings_with_router_enabled()

    # Phase 22: app.core.config.get_settings를 패치
    with patch.object(ChatService, '__init__', lambda x, **kwargs: None), \
         patch('app.core.config.get_settings', return_value=mock_settings):
        service = ChatService()
        service._router_orchestrator = mock_orchestrator
        service._pii = mock_pii
        service._llm = MagicMock()
        service._ragflow = MagicMock()
        # 실제 IntentType/RouteType 사용
        service._intent = MagicMock()
        service._intent.classify.return_value = create_mock_intent_result(
            intent=IntentType.GENERAL_CHAT,
            domain="QUIZ",
        )
        service._guardrail = MagicMock()
        service._ai_log = MagicMock()
        service._backend_data = MagicMock()
        service._context_formatter = MagicMock()
        service._video_progress = MagicMock()
        # Answer guard mock 추가
        service._answer_guard = MagicMock()
        service._answer_guard.check_complaint_fast_path = MagicMock(return_value=None)
        # Phase 45/46: 소프트 가드레일 mock
        service._answer_guard.check_soft_guardrail = MagicMock(return_value=(False, None))
        service._answer_guard.get_soft_guardrail_system_instruction = MagicMock(return_value="")
        # Phase 39: last error reason
        service._last_error_reason = None

        request = ChatRequest(
            session_id="test-session-003",
            user_id="user-001",
            user_role="EMPLOYEE",
            messages=[ChatMessage(role="user", content="퀴즈 시작해줘")],
        )

        # Act
        response = await service.handle_chat(request)

        # Assert
        assert response.answer == confirmation_prompt
        assert response.meta.route == "CONFIRMATION"


# =============================================================================
# 테스트 4: SYSTEM_HELP 라우트 → 시스템 도움말
# =============================================================================


@pytest.mark.anyio
async def test_chat_returns_system_help(mock_chat_request):
    """SYSTEM_HELP 라우트일 때 도움말 응답."""
    # Arrange
    mock_orchestrator = AsyncMock(spec=RouterOrchestrator)
    mock_orchestrator.route.return_value = OrchestrationResult(
        router_result=RouterResult(
            tier0_intent=Tier0Intent.SYSTEM_HELP,
            domain=RouterDomain.GENERAL,
            route_type=RouterRouteType.ROUTE_SYSTEM_HELP,
            confidence=0.95,
        ),
        needs_user_response=False,
        can_execute=True,
    )

    mock_pii = MagicMock()
    mock_pii.detect_and_mask = AsyncMock(return_value=MagicMock(
        masked_text="도움말",
        has_pii=False,
        tags=[],
    ))

    # Phase 22 수정: Settings mock 추가
    mock_settings = get_mock_settings_with_router_enabled()

    # Phase 22: app.core.config.get_settings를 패치
    with patch.object(ChatService, '__init__', lambda x, **kwargs: None), \
         patch('app.core.config.get_settings', return_value=mock_settings):
        service = ChatService()
        service._router_orchestrator = mock_orchestrator
        service._pii = mock_pii
        service._llm = MagicMock()
        service._ragflow = MagicMock()
        # 실제 IntentType/RouteType 사용
        service._intent = MagicMock()
        service._intent.classify.return_value = create_mock_intent_result(
            intent=IntentType.GENERAL_CHAT,
            domain="GENERAL",
        )
        service._guardrail = MagicMock()
        service._ai_log = MagicMock()
        service._backend_data = MagicMock()
        service._context_formatter = MagicMock()
        service._video_progress = MagicMock()
        # Answer guard mock 추가
        service._answer_guard = MagicMock()
        service._answer_guard.check_complaint_fast_path = MagicMock(return_value=None)
        # Phase 45/46: 소프트 가드레일 mock
        service._answer_guard.check_soft_guardrail = MagicMock(return_value=(False, None))
        service._answer_guard.get_soft_guardrail_system_instruction = MagicMock(return_value="")
        # Phase 39: last error reason
        service._last_error_reason = None

        request = ChatRequest(
            session_id="test-session-004",
            user_id="user-001",
            user_role="EMPLOYEE",
            messages=[ChatMessage(role="user", content="도움말")],
        )

        # Act
        response = await service.handle_chat(request)

        # Assert
        assert "CTRL+F AI 어시스턴트입니다" in response.answer
        assert response.meta.route == "ROUTE_SYSTEM_HELP"
        assert response.meta.intent == "SYSTEM_HELP"


# =============================================================================
# 테스트 5: UNKNOWN 라우트 → 되묻기 응답
# =============================================================================


@pytest.mark.anyio
async def test_chat_returns_unknown_response(mock_chat_request):
    """UNKNOWN 라우트일 때 범위 밖 안내."""
    # Arrange
    # confidence >= 0.5 이어야 UNKNOWN 라우트가 트리거됨
    mock_orchestrator = AsyncMock(spec=RouterOrchestrator)
    mock_orchestrator.route.return_value = OrchestrationResult(
        router_result=RouterResult(
            tier0_intent=Tier0Intent.UNKNOWN,
            domain=RouterDomain.GENERAL,
            route_type=RouterRouteType.ROUTE_UNKNOWN,
            confidence=0.7,  # >= 0.5로 설정
        ),
        needs_user_response=False,
        can_execute=False,
    )

    mock_pii = MagicMock()
    mock_pii.detect_and_mask = AsyncMock(return_value=MagicMock(
        masked_text="알 수 없는 질문",
        has_pii=False,
        tags=[],
    ))

    # Phase 22 수정: Settings mock 추가
    mock_settings = get_mock_settings_with_router_enabled()

    # Phase 22: app.core.config.get_settings를 패치
    with patch.object(ChatService, '__init__', lambda x, **kwargs: None), \
         patch('app.core.config.get_settings', return_value=mock_settings):
        service = ChatService()
        service._router_orchestrator = mock_orchestrator
        service._pii = mock_pii
        service._llm = MagicMock()
        service._ragflow = MagicMock()
        # 실제 IntentType/RouteType 사용
        service._intent = MagicMock()
        service._intent.classify.return_value = create_mock_intent_result(
            intent=IntentType.UNKNOWN,
            domain="GENERAL",
        )
        service._guardrail = MagicMock()
        service._ai_log = MagicMock()
        service._backend_data = MagicMock()
        service._context_formatter = MagicMock()
        service._video_progress = MagicMock()
        # Answer guard mock 추가
        service._answer_guard = MagicMock()
        service._answer_guard.check_complaint_fast_path = MagicMock(return_value=None)
        # Phase 45/46: 소프트 가드레일 mock
        service._answer_guard.check_soft_guardrail = MagicMock(return_value=(False, None))
        service._answer_guard.get_soft_guardrail_system_instruction = MagicMock(return_value="")
        # Phase 39: last error reason
        service._last_error_reason = None

        request = ChatRequest(
            session_id="test-session-005",
            user_id="user-001",
            user_role="EMPLOYEE",
            messages=[ChatMessage(role="user", content="알 수 없는 질문")],
        )

        # Act
        response = await service.handle_chat(request)

        # Assert
        assert "이해하지 못했습니다" in response.answer
        assert response.meta.route == "ROUTE_UNKNOWN"
        assert response.meta.intent == "UNKNOWN"


# =============================================================================
# 테스트 6: Tier0Intent → IntentType 매핑 테스트
# =============================================================================


def test_map_tier0_to_intent():
    """Tier0Intent를 IntentType으로 올바르게 매핑하는지 확인."""
    from app.models.intent import IntentType
    from app.services.chat_service import ChatService

    with patch.object(ChatService, '__init__', lambda x, **kwargs: None):
        service = ChatService()

        # Test mappings
        assert service._map_tier0_to_intent(Tier0Intent.POLICY_QA) == IntentType.POLICY_QA
        assert service._map_tier0_to_intent(Tier0Intent.EDUCATION_QA) == IntentType.EDUCATION_QA
        assert service._map_tier0_to_intent(Tier0Intent.GENERAL_CHAT) == IntentType.GENERAL_CHAT
        assert service._map_tier0_to_intent(Tier0Intent.UNKNOWN) == IntentType.UNKNOWN


# =============================================================================
# 테스트 7: RouterRouteType → RouteType 매핑 테스트
# =============================================================================


def test_map_router_route_to_route_type():
    """RouterRouteType을 RouteType으로 올바르게 매핑하는지 확인."""
    from app.models.intent import RouteType
    from app.services.chat_service import ChatService

    with patch.object(ChatService, '__init__', lambda x, **kwargs: None):
        service = ChatService()

        # Test mappings
        assert service._map_router_route_to_route_type(RouterRouteType.RAG_INTERNAL) == RouteType.RAG_INTERNAL
        assert service._map_router_route_to_route_type(RouterRouteType.BACKEND_API) == RouteType.BACKEND_API
        assert service._map_router_route_to_route_type(RouterRouteType.LLM_ONLY) == RouteType.LLM_ONLY
        assert service._map_router_route_to_route_type(RouterRouteType.ROUTE_UNKNOWN) == RouteType.FALLBACK
