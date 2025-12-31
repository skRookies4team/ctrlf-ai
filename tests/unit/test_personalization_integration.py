"""
Personalization Integration Tests (개인화 통합 테스트)

ChatService에서 개인화 분기 시 PersonalizationClient.resolve_facts와
AnswerGenerator.generate가 올바르게 호출되는지 테스트합니다.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.chat import ChatRequest, ChatMessage, ChatResponse
from app.models.personalization import PersonalizationFacts
from app.models.router_types import (
    ClarifyTemplates,
    RouterResult,
    RouterRouteType,
    Tier0Intent,
)
from app.services.chat_service import ChatService


# =============================================================================
# ChatService 개인화 분기 테스트
# =============================================================================


@pytest.mark.skip(reason="Personalization feature not fully integrated yet")
class TestChatServicePersonalization:
    """ChatService 개인화 분기 테스트."""

    @pytest.fixture
    def mock_dependencies(self):
        """의존성 Mock 생성."""
        # LLM Client
        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(return_value="테스트 응답")

        # PII Service
        mock_pii = MagicMock()
        mock_pii.detect_and_mask = AsyncMock(return_value=MagicMock(
            masked_text="테스트 질문",
            has_pii=False,
            tags=[],
        ))

        # Intent Service
        mock_intent = MagicMock()
        mock_intent.classify = MagicMock(return_value=MagicMock(
            intent=MagicMock(value="EDU_STATUS"),
            domain="EDU",
            route=MagicMock(value="BACKEND_API"),
            user_role=MagicMock(value="EMPLOYEE"),
        ))

        # Router Orchestrator
        mock_orchestrator = MagicMock()

        # PersonalizationClient
        mock_personalization = MagicMock()
        mock_personalization.resolve_facts = AsyncMock()

        # AnswerGenerator
        mock_answer_gen = MagicMock()
        mock_answer_gen.generate = AsyncMock()

        return {
            "llm_client": mock_llm,
            "pii_service": mock_pii,
            "intent_service": mock_intent,
            "router_orchestrator": mock_orchestrator,
            "personalization_client": mock_personalization,
            "answer_generator": mock_answer_gen,
        }

    @pytest.fixture
    def chat_request(self):
        """테스트용 ChatRequest 생성."""
        return ChatRequest(
            session_id="test-session-001",
            user_id="emp001",
            user_role="EMPLOYEE",
            department="개발팀",
            messages=[
                ChatMessage(role="user", content="내 연차 며칠 남았어?"),
            ],
        )

    @pytest.mark.asyncio
    async def test_personalization_client_called_for_hr_leave(
        self, mock_dependencies, chat_request
    ):
        """HR 연차 조회 시 PersonalizationClient.resolve_facts 호출 확인."""
        # Mock 설정
        mock_orchestrator = mock_dependencies["router_orchestrator"]
        mock_orchestrator.route = AsyncMock(return_value=MagicMock(
            needs_user_response=False,
            router_result=RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id="HR_LEAVE_CHECK",  # 연차 조회
                confidence=0.95,
            ),
            response_message="",
        ))

        mock_personalization = mock_dependencies["personalization_client"]
        mock_personalization.resolve_facts = AsyncMock(return_value=PersonalizationFacts(
            sub_intent_id="Q11",
            period_start="2025-01-01",
            period_end="2025-12-31",
            metrics={"remaining_days": 7, "total_days": 15, "used_days": 8},
        ))

        mock_answer_gen = mock_dependencies["answer_generator"]
        mock_answer_gen.generate = AsyncMock(return_value="남은 연차: 7일")

        # ChatService 생성 (모든 의존성 주입)
        with patch("app.services.chat_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(ROUTER_ORCHESTRATOR_ENABLED=True)

            service = ChatService(
                llm_client=mock_dependencies["llm_client"],
                pii_service=mock_dependencies["pii_service"],
                intent_service=mock_dependencies["intent_service"],
                router_orchestrator=mock_orchestrator,
                personalization_client=mock_personalization,
                answer_generator=mock_answer_gen,
            )

            # 테스트 실행
            response = await service.handle_chat(chat_request)

            # PersonalizationClient.resolve_facts 호출 확인
            mock_personalization.resolve_facts.assert_called_once()
            call_args = mock_personalization.resolve_facts.call_args
            assert call_args.kwargs["sub_intent_id"] == "Q11"  # HR_LEAVE_CHECK -> Q11

            # AnswerGenerator.generate 호출 확인
            mock_answer_gen.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_clarify_response_when_sub_intent_id_empty(
        self, mock_dependencies, chat_request
    ):
        """sub_intent_id가 비어있을 때 clarify 응답 반환."""
        # Mock 설정: sub_intent_id가 빈 문자열
        mock_orchestrator = mock_dependencies["router_orchestrator"]
        mock_orchestrator.route = AsyncMock(return_value=MagicMock(
            needs_user_response=False,
            router_result=RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id="",  # 비어있음
                confidence=0.5,
            ),
            response_message="",
        ))

        with patch("app.services.chat_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(ROUTER_ORCHESTRATOR_ENABLED=True)

            service = ChatService(
                llm_client=mock_dependencies["llm_client"],
                pii_service=mock_dependencies["pii_service"],
                intent_service=mock_dependencies["intent_service"],
                router_orchestrator=mock_orchestrator,
                personalization_client=mock_dependencies["personalization_client"],
                answer_generator=mock_dependencies["answer_generator"],
            )

            response = await service.handle_chat(chat_request)

            # clarify 응답 확인
            assert response.meta.route == "CLARIFY"
            assert response.answer in ClarifyTemplates.BACKEND_STATUS_CLARIFY

    @pytest.mark.asyncio
    async def test_personalization_for_edu_status_check(
        self, mock_dependencies
    ):
        """EDU_STATUS_CHECK 시 올바른 Q로 변환되어 호출되는지 확인."""
        chat_request = ChatRequest(
            session_id="test-session-002",
            user_id="emp002",
            user_role="EMPLOYEE",
            messages=[
                ChatMessage(role="user", content="미이수 교육 조회해줘"),
            ],
        )

        mock_orchestrator = mock_dependencies["router_orchestrator"]
        mock_orchestrator.route = AsyncMock(return_value=MagicMock(
            needs_user_response=False,
            router_result=RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id="EDU_STATUS_CHECK",  # 교육 현황
                confidence=0.92,
            ),
            response_message="",
        ))

        mock_personalization = mock_dependencies["personalization_client"]
        mock_personalization.resolve_facts = AsyncMock(return_value=PersonalizationFacts(
            sub_intent_id="Q1",
            metrics={"remaining": 2},
            items=[
                {"title": "개인정보보호 교육", "deadline": "2025-02-28"},
            ],
        ))

        mock_answer_gen = mock_dependencies["answer_generator"]
        mock_answer_gen.generate = AsyncMock(return_value="미이수 필수 교육 2건")

        with patch("app.services.chat_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(ROUTER_ORCHESTRATOR_ENABLED=True)

            service = ChatService(
                llm_client=mock_dependencies["llm_client"],
                pii_service=mock_dependencies["pii_service"],
                intent_service=mock_dependencies["intent_service"],
                router_orchestrator=mock_orchestrator,
                personalization_client=mock_personalization,
                answer_generator=mock_answer_gen,
            )

            response = await service.handle_chat(chat_request)

            # EDU_STATUS_CHECK + "미이수" 키워드 -> Q1으로 변환
            call_args = mock_personalization.resolve_facts.call_args
            assert call_args.kwargs["sub_intent_id"] == "Q1"

    @pytest.mark.asyncio
    async def test_non_personalization_uses_backend_handler(
        self, mock_dependencies
    ):
        """개인화가 아닌 BACKEND_API는 기존 BackendHandler 사용."""
        chat_request = ChatRequest(
            session_id="test-session-003",
            user_id="emp003",
            user_role="EMPLOYEE",
            messages=[
                ChatMessage(role="user", content="퀴즈 시작해줘"),
            ],
        )

        mock_orchestrator = mock_dependencies["router_orchestrator"]
        mock_orchestrator.route = AsyncMock(return_value=MagicMock(
            needs_user_response=True,  # 퀴즈는 확인 필요
            router_result=RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id="QUIZ_START",  # 개인화 아님
                confidence=0.95,
                requires_confirmation=True,
                confirmation_prompt="퀴즈를 시작할까요?",
            ),
            response_message="퀴즈를 시작할까요?",
        ))

        with patch("app.services.chat_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(ROUTER_ORCHESTRATOR_ENABLED=True)

            service = ChatService(
                llm_client=mock_dependencies["llm_client"],
                pii_service=mock_dependencies["pii_service"],
                intent_service=mock_dependencies["intent_service"],
                router_orchestrator=mock_orchestrator,
                personalization_client=mock_dependencies["personalization_client"],
                answer_generator=mock_dependencies["answer_generator"],
            )

            response = await service.handle_chat(chat_request)

            # 퀴즈는 확인 응답 반환 (개인화 아님)
            assert "퀴즈" in response.answer or response.meta.route == "CONFIRMATION"


# =============================================================================
# 개인화 Q 매핑 통합 테스트
# =============================================================================


class TestPersonalizationQMapping:
    """개인화 Q 매핑 통합 테스트."""

    @pytest.mark.asyncio
    async def test_q11_mapping_from_hr_leave_check(self):
        """HR_LEAVE_CHECK -> Q11 매핑 확인."""
        from app.services.personalization_mapper import to_personalization_q

        q = to_personalization_q("HR_LEAVE_CHECK", "내 연차 며칠?")
        assert q == "Q11"

    @pytest.mark.asyncio
    async def test_q14_mapping_from_hr_welfare_check(self):
        """HR_WELFARE_CHECK -> Q14 매핑 확인."""
        from app.services.personalization_mapper import to_personalization_q

        q = to_personalization_q("HR_WELFARE_CHECK", "복지 포인트 조회")
        assert q == "Q14"

    @pytest.mark.asyncio
    async def test_q1_mapping_from_edu_status_with_keyword(self):
        """EDU_STATUS_CHECK + 미이수 키워드 -> Q1 매핑 확인."""
        from app.services.personalization_mapper import to_personalization_q

        q = to_personalization_q("EDU_STATUS_CHECK", "미이수 교육 알려줘")
        assert q == "Q1"

    @pytest.mark.asyncio
    async def test_q3_mapping_from_edu_status_with_deadline(self):
        """EDU_STATUS_CHECK + 마감 키워드 -> Q3 매핑 확인."""
        from app.services.personalization_mapper import to_personalization_q

        q = to_personalization_q("EDU_STATUS_CHECK", "이번 달 마감 교육")
        assert q == "Q3"
