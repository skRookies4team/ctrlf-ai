"""
Phase 14: RAG Gap Candidate 플래그 테스트

테스트 항목:
1. is_rag_gap_candidate 헬퍼 함수 테스트
   - POLICY/EDU 도메인 + POLICY_QA/EDUCATION_QA 의도 + 결과 0건 → True
   - POLICY/EDU 도메인 + POLICY_QA/EDUCATION_QA 의도 + 낮은 점수 → True
   - POLICY/EDU 도메인 + POLICY_QA/EDUCATION_QA 의도 + 높은 점수 → False
   - INCIDENT/GENERAL_CHAT 도메인/의도 → 항상 False
2. ChatAnswerMeta에 rag_gap_candidate 필드 테스트
3. AILogEntry에 rag_gap_candidate 필드 및 직렬화 테스트
4. ChatService handle_chat에서 RAG Gap 후보 판정 테스트
5. AI Log 전파 테스트
"""

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.chat import (
    ChatAnswerMeta,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatSource,
)
from app.models.ai_log import AILogEntry, to_backend_log_payload
from app.models.intent import Domain, IntentType, RouteType
from app.services.chat_service import (
    ChatService,
    RAG_GAP_SCORE_THRESHOLD,
    RAG_GAP_TARGET_DOMAINS,
    RAG_GAP_TARGET_INTENTS,
    is_rag_gap_candidate,
)


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


# =============================================================================
# 1. is_rag_gap_candidate 헬퍼 함수 테스트
# =============================================================================


class TestIsRagGapCandidate:
    """is_rag_gap_candidate 헬퍼 함수 테스트."""

    def test_policy_qa_with_zero_sources_returns_true(self) -> None:
        """POLICY 도메인, POLICY_QA 의도, 결과 0건 → True."""
        result = is_rag_gap_candidate(
            domain="POLICY",
            intent="POLICY_QA",
            rag_source_count=0,
            rag_max_score=None,
        )
        assert result is True

    def test_policy_qa_with_domain_enum_and_zero_sources(self) -> None:
        """POLICY 도메인 (Enum value), POLICY_QA 의도, 결과 0건 → True."""
        result = is_rag_gap_candidate(
            domain=Domain.POLICY.value,
            intent=IntentType.POLICY_QA.value,
            rag_source_count=0,
            rag_max_score=None,
        )
        assert result is True

    def test_edu_qa_with_zero_sources_returns_true(self) -> None:
        """EDU 도메인, EDUCATION_QA 의도, 결과 0건 → True."""
        result = is_rag_gap_candidate(
            domain="EDU",
            intent="EDUCATION_QA",
            rag_source_count=0,
            rag_max_score=None,
        )
        assert result is True

    def test_edu_domain_enum_with_zero_sources(self) -> None:
        """EDU 도메인 (Enum value), EDUCATION_QA 의도 → True."""
        result = is_rag_gap_candidate(
            domain=Domain.EDU.value,
            intent=IntentType.EDUCATION_QA.value,
            rag_source_count=0,
            rag_max_score=None,
        )
        assert result is True

    def test_policy_qa_with_low_score_returns_true(self) -> None:
        """POLICY 도메인, POLICY_QA 의도, 낮은 점수 → True."""
        result = is_rag_gap_candidate(
            domain="POLICY",
            intent="POLICY_QA",
            rag_source_count=3,
            rag_max_score=0.3,  # 임계값 0.4 미만
        )
        assert result is True

    def test_policy_qa_with_score_at_threshold_returns_false(self) -> None:
        """POLICY 도메인, POLICY_QA 의도, 점수가 정확히 임계값 → False."""
        result = is_rag_gap_candidate(
            domain="POLICY",
            intent="POLICY_QA",
            rag_source_count=3,
            rag_max_score=RAG_GAP_SCORE_THRESHOLD,  # 0.4 (임계값과 동일)
        )
        assert result is False

    def test_policy_qa_with_high_score_returns_false(self) -> None:
        """POLICY 도메인, POLICY_QA 의도, 높은 점수 → False."""
        result = is_rag_gap_candidate(
            domain="POLICY",
            intent="POLICY_QA",
            rag_source_count=5,
            rag_max_score=0.85,
        )
        assert result is False

    def test_incident_domain_always_returns_false(self) -> None:
        """INCIDENT 도메인은 항상 False."""
        result = is_rag_gap_candidate(
            domain="INCIDENT",
            intent="INCIDENT_REPORT",
            rag_source_count=0,
            rag_max_score=None,
        )
        assert result is False

    def test_general_chat_intent_always_returns_false(self) -> None:
        """GENERAL_CHAT 의도는 항상 False (POLICY 도메인이어도)."""
        result = is_rag_gap_candidate(
            domain="POLICY",
            intent="GENERAL_CHAT",
            rag_source_count=0,
            rag_max_score=None,
        )
        assert result is False

    def test_general_domain_always_returns_false(self) -> None:
        """GENERAL 도메인은 항상 False."""
        result = is_rag_gap_candidate(
            domain="GENERAL",
            intent="GENERAL_CHAT",
            rag_source_count=0,
            rag_max_score=None,
        )
        assert result is False

    def test_custom_score_threshold(self) -> None:
        """커스텀 임계값 테스트."""
        # 기본 임계값(0.4)에서는 True
        result_default = is_rag_gap_candidate(
            domain="POLICY",
            intent="POLICY_QA",
            rag_source_count=3,
            rag_max_score=0.3,
        )
        assert result_default is True

        # 더 낮은 임계값(0.2)에서는 False
        result_custom = is_rag_gap_candidate(
            domain="POLICY",
            intent="POLICY_QA",
            rag_source_count=3,
            rag_max_score=0.3,
            score_threshold=0.2,
        )
        assert result_custom is False

    def test_sources_exist_but_no_score_returns_false(self) -> None:
        """결과가 있지만 점수가 None인 경우 False (점수 없으면 Gap 판정 안 함)."""
        result = is_rag_gap_candidate(
            domain="POLICY",
            intent="POLICY_QA",
            rag_source_count=3,
            rag_max_score=None,  # 결과는 있지만 점수 없음
        )
        assert result is False


# =============================================================================
# 2. ChatAnswerMeta rag_gap_candidate 필드 테스트
# =============================================================================


class TestChatAnswerMetaRagGapCandidate:
    """ChatAnswerMeta에 rag_gap_candidate 필드 테스트."""

    def test_default_value_is_false(self) -> None:
        """기본값은 False."""
        meta = ChatAnswerMeta()
        assert meta.rag_gap_candidate is False

    def test_can_set_to_true(self) -> None:
        """True로 설정 가능."""
        meta = ChatAnswerMeta(rag_gap_candidate=True)
        assert meta.rag_gap_candidate is True

    def test_json_serialization(self) -> None:
        """JSON 직렬화 시 rag_gap_candidate 포함."""
        meta = ChatAnswerMeta(
            route="RAG_INTERNAL",
            intent="POLICY_QA",
            domain="POLICY",
            rag_used=True,
            rag_source_count=0,
            rag_gap_candidate=True,
        )

        json_data = meta.model_dump()

        assert "rag_gap_candidate" in json_data
        assert json_data["rag_gap_candidate"] is True


class TestChatResponseRagGapCandidate:
    """ChatResponse에서 rag_gap_candidate 필드 테스트."""

    def test_response_includes_rag_gap_candidate_in_meta(self) -> None:
        """ChatResponse.meta에 rag_gap_candidate 포함."""
        response = ChatResponse(
            answer="테스트 응답",
            sources=[],
            meta=ChatAnswerMeta(
                route="RAG_INTERNAL",
                domain="POLICY",
                intent="POLICY_QA",
                rag_gap_candidate=True,
            ),
        )

        json_data = response.model_dump()

        assert json_data["meta"]["rag_gap_candidate"] is True


# =============================================================================
# 3. AILogEntry rag_gap_candidate 필드 테스트
# =============================================================================


class TestAILogEntryRagGapCandidate:
    """AILogEntry에 rag_gap_candidate 필드 테스트."""

    def test_default_value_is_false(self) -> None:
        """기본값은 False."""
        entry = AILogEntry(
            session_id="test-session",
            user_id="test-user",
            user_role="EMPLOYEE",
            domain="POLICY",
            intent="POLICY_QA",
            route="RAG_INTERNAL",
            latency_ms=100,
        )
        assert entry.rag_gap_candidate is False

    def test_can_set_to_true(self) -> None:
        """True로 설정 가능."""
        entry = AILogEntry(
            session_id="test-session",
            user_id="test-user",
            user_role="EMPLOYEE",
            domain="POLICY",
            intent="POLICY_QA",
            route="RAG_INTERNAL",
            latency_ms=100,
            rag_gap_candidate=True,
        )
        assert entry.rag_gap_candidate is True

    def test_camelcase_serialization(self) -> None:
        """camelCase 직렬화 시 ragGapCandidate로 변환."""
        entry = AILogEntry(
            session_id="test-session",
            user_id="test-user",
            user_role="EMPLOYEE",
            domain="POLICY",
            intent="POLICY_QA",
            route="RAG_INTERNAL",
            latency_ms=100,
            rag_gap_candidate=True,
        )

        json_data = entry.model_dump(by_alias=True)

        assert "ragGapCandidate" in json_data
        assert json_data["ragGapCandidate"] is True

    def test_to_backend_log_payload_includes_rag_gap_candidate(self) -> None:
        """to_backend_log_payload에서 ragGapCandidate 포함."""
        entry = AILogEntry(
            session_id="test-session",
            user_id="test-user",
            user_role="EMPLOYEE",
            domain="POLICY",
            intent="POLICY_QA",
            route="RAG_INTERNAL",
            latency_ms=100,
            rag_gap_candidate=True,
        )

        payload = to_backend_log_payload(entry)

        assert "ragGapCandidate" in payload["log"]
        assert payload["log"]["ragGapCandidate"] is True


# =============================================================================
# 4. 상수 정의 확인 테스트
# =============================================================================


class TestRagGapConstants:
    """RAG Gap 관련 상수 정의 테스트."""

    def test_target_domains_include_policy_and_edu(self) -> None:
        """대상 도메인에 POLICY, EDU 포함."""
        assert "POLICY" in RAG_GAP_TARGET_DOMAINS
        assert "EDU" in RAG_GAP_TARGET_DOMAINS
        assert Domain.POLICY.value in RAG_GAP_TARGET_DOMAINS
        assert Domain.EDU.value in RAG_GAP_TARGET_DOMAINS

    def test_target_domains_exclude_incident(self) -> None:
        """대상 도메인에 INCIDENT 미포함."""
        assert "INCIDENT" not in RAG_GAP_TARGET_DOMAINS
        assert Domain.INCIDENT.value not in RAG_GAP_TARGET_DOMAINS

    def test_target_intents_include_qa_types(self) -> None:
        """대상 의도에 QA 유형 포함."""
        assert "POLICY_QA" in RAG_GAP_TARGET_INTENTS
        assert "EDUCATION_QA" in RAG_GAP_TARGET_INTENTS
        assert IntentType.POLICY_QA.value in RAG_GAP_TARGET_INTENTS
        assert IntentType.EDUCATION_QA.value in RAG_GAP_TARGET_INTENTS

    def test_target_intents_exclude_non_qa_types(self) -> None:
        """대상 의도에 비-QA 유형 미포함."""
        assert "GENERAL_CHAT" not in RAG_GAP_TARGET_INTENTS
        assert "INCIDENT_REPORT" not in RAG_GAP_TARGET_INTENTS

    def test_score_threshold_value(self) -> None:
        """점수 임계값 확인."""
        assert RAG_GAP_SCORE_THRESHOLD == 0.4


# =============================================================================
# 5. ChatService RAG Gap 판정 통합 테스트
# =============================================================================


class TestChatServiceRagGapIntegration:
    """ChatService에서 RAG Gap 후보 판정 통합 테스트."""

    @pytest.mark.anyio
    async def test_rag_gap_true_when_policy_qa_no_sources(self) -> None:
        """POLICY_QA + 결과 0건 → rag_gap_candidate=True."""
        from app.services.pii_service import PiiService
        from app.services.intent_service import IntentService
        from app.clients.ragflow_client import RagflowClient
        from app.clients.llm_client import LLMClient
        from app.services.guardrail_service import GuardrailService
        from app.models.intent import IntentResult, UserRole

        # Mock 서비스들
        mock_pii = AsyncMock(spec=PiiService)
        mock_pii.detect_and_mask.return_value = MagicMock(
            masked_text="테스트 질문",
            has_pii=False,
            tags=[],
        )

        mock_intent = MagicMock(spec=IntentService)
        mock_intent.classify.return_value = IntentResult(
            intent=IntentType.POLICY_QA,
            domain="POLICY",
            route=RouteType.RAG_INTERNAL,
            user_role=UserRole.EMPLOYEE,
        )

        mock_ragflow = AsyncMock(spec=RagflowClient)
        mock_ragflow.search_as_sources.return_value = []  # 결과 0건

        mock_llm = AsyncMock(spec=LLMClient)
        mock_llm.generate_chat_completion.return_value = "테스트 응답"

        mock_guardrail = MagicMock(spec=GuardrailService)
        mock_guardrail.get_system_prompt_prefix.return_value = ""
        mock_guardrail.apply_to_answer.return_value = "테스트 응답"

        service = ChatService(
            ragflow_client=mock_ragflow,
            llm_client=mock_llm,
            pii_service=mock_pii,
            intent_service=mock_intent,
            guardrail_service=mock_guardrail,
        )

        request = ChatRequest(
            session_id="test-session",
            user_id="test-user",
            user_role="EMPLOYEE",
            messages=[ChatMessage(role="user", content="연차 규정이 뭐야?")],
        )

        response = await service.handle_chat(request)

        # Phase 44: 답변 허용 정책으로 error_type은 None
        # rag_gap_candidate는 여전히 True (RAG 결과 0건)
        assert response.meta.error_type is None  # Phase 44: 차단 대신 허용
        assert response.meta.rag_gap_candidate is True  # RAG 결과 0건 → gap 후보

    @pytest.mark.anyio
    async def test_rag_gap_true_when_policy_qa_low_score(self) -> None:
        """POLICY_QA + 낮은 점수 → rag_gap_candidate=True."""
        from app.services.pii_service import PiiService
        from app.services.intent_service import IntentService
        from app.clients.ragflow_client import RagflowClient
        from app.clients.llm_client import LLMClient
        from app.services.guardrail_service import GuardrailService
        from app.models.intent import IntentResult, UserRole

        mock_pii = AsyncMock(spec=PiiService)
        mock_pii.detect_and_mask.return_value = MagicMock(
            masked_text="테스트 질문",
            has_pii=False,
            tags=[],
        )

        mock_intent = MagicMock(spec=IntentService)
        mock_intent.classify.return_value = IntentResult(
            intent=IntentType.POLICY_QA,
            domain="POLICY",
            route=RouteType.RAG_INTERNAL,
            user_role=UserRole.EMPLOYEE,
        )

        # 낮은 점수의 결과
        mock_ragflow = AsyncMock(spec=RagflowClient)
        mock_ragflow.search_as_sources.return_value = [
            ChatSource(
                doc_id="doc-1",
                title="테스트 문서",
                score=0.25,  # 임계값 0.4 미만
            )
        ]

        mock_llm = AsyncMock(spec=LLMClient)
        mock_llm.generate_chat_completion.return_value = "테스트 응답"

        mock_guardrail = MagicMock(spec=GuardrailService)
        mock_guardrail.get_system_prompt_prefix.return_value = ""
        mock_guardrail.apply_to_answer.return_value = "테스트 응답"

        service = ChatService(
            ragflow_client=mock_ragflow,
            llm_client=mock_llm,
            pii_service=mock_pii,
            intent_service=mock_intent,
            guardrail_service=mock_guardrail,
        )

        request = ChatRequest(
            session_id="test-session",
            user_id="test-user",
            user_role="EMPLOYEE",
            messages=[ChatMessage(role="user", content="연차 규정이 뭐야?")],
        )

        response = await service.handle_chat(request)

        # RAG Gap 후보여야 함
        assert response.meta.rag_gap_candidate is True

    @pytest.mark.anyio
    async def test_rag_gap_false_when_policy_qa_high_score(self) -> None:
        """POLICY_QA + 높은 점수 → rag_gap_candidate=False."""
        from app.services.pii_service import PiiService
        from app.services.intent_service import IntentService
        from app.clients.ragflow_client import RagflowClient
        from app.clients.llm_client import LLMClient
        from app.services.guardrail_service import GuardrailService
        from app.models.intent import IntentResult, UserRole

        mock_pii = AsyncMock(spec=PiiService)
        mock_pii.detect_and_mask.return_value = MagicMock(
            masked_text="테스트 질문",
            has_pii=False,
            tags=[],
        )

        mock_intent = MagicMock(spec=IntentService)
        mock_intent.classify.return_value = IntentResult(
            intent=IntentType.POLICY_QA,
            domain="POLICY",
            route=RouteType.RAG_INTERNAL,
            user_role=UserRole.EMPLOYEE,
        )

        # 높은 점수의 결과
        mock_ragflow = AsyncMock(spec=RagflowClient)
        mock_ragflow.search_as_sources.return_value = [
            ChatSource(
                doc_id="doc-1",
                title="테스트 문서",
                score=0.85,  # 임계값 0.4 이상
            )
        ]

        mock_llm = AsyncMock(spec=LLMClient)
        mock_llm.generate_chat_completion.return_value = "테스트 응답"

        mock_guardrail = MagicMock(spec=GuardrailService)
        mock_guardrail.get_system_prompt_prefix.return_value = ""
        mock_guardrail.apply_to_answer.return_value = "테스트 응답"

        service = ChatService(
            ragflow_client=mock_ragflow,
            llm_client=mock_llm,
            pii_service=mock_pii,
            intent_service=mock_intent,
            guardrail_service=mock_guardrail,
        )

        request = ChatRequest(
            session_id="test-session",
            user_id="test-user",
            user_role="EMPLOYEE",
            messages=[ChatMessage(role="user", content="연차 규정이 뭐야?")],
        )

        response = await service.handle_chat(request)

        # RAG Gap 후보가 아님
        assert response.meta.rag_gap_candidate is False

    @pytest.mark.anyio
    async def test_rag_gap_false_for_general_chat(self) -> None:
        """GENERAL_CHAT 의도 → rag_gap_candidate=False."""
        from app.services.pii_service import PiiService
        from app.services.intent_service import IntentService
        from app.clients.ragflow_client import RagflowClient
        from app.clients.llm_client import LLMClient
        from app.services.guardrail_service import GuardrailService
        from app.models.intent import IntentResult, UserRole

        mock_pii = AsyncMock(spec=PiiService)
        mock_pii.detect_and_mask.return_value = MagicMock(
            masked_text="안녕하세요",
            has_pii=False,
            tags=[],
        )

        mock_intent = MagicMock(spec=IntentService)
        mock_intent.classify.return_value = IntentResult(
            intent=IntentType.GENERAL_CHAT,
            domain="GENERAL",
            route=RouteType.LLM_ONLY,
            user_role=UserRole.EMPLOYEE,
        )

        mock_ragflow = AsyncMock(spec=RagflowClient)
        mock_ragflow.search_as_sources.return_value = []

        mock_llm = AsyncMock(spec=LLMClient)
        mock_llm.generate_chat_completion.return_value = "안녕하세요!"

        mock_guardrail = MagicMock(spec=GuardrailService)
        mock_guardrail.get_system_prompt_prefix.return_value = ""
        mock_guardrail.apply_to_answer.return_value = "안녕하세요!"

        service = ChatService(
            ragflow_client=mock_ragflow,
            llm_client=mock_llm,
            pii_service=mock_pii,
            intent_service=mock_intent,
            guardrail_service=mock_guardrail,
        )

        request = ChatRequest(
            session_id="test-session",
            user_id="test-user",
            user_role="EMPLOYEE",
            messages=[ChatMessage(role="user", content="안녕하세요")],
        )

        response = await service.handle_chat(request)

        # GENERAL_CHAT이므로 RAG Gap 후보 아님
        assert response.meta.rag_gap_candidate is False

    @pytest.mark.anyio
    async def test_rag_gap_false_for_incident_domain(self) -> None:
        """INCIDENT 도메인 → rag_gap_candidate=False."""
        from app.services.pii_service import PiiService
        from app.services.intent_service import IntentService
        from app.clients.ragflow_client import RagflowClient
        from app.clients.llm_client import LLMClient
        from app.services.guardrail_service import GuardrailService
        from app.models.intent import IntentResult, UserRole

        mock_pii = AsyncMock(spec=PiiService)
        mock_pii.detect_and_mask.return_value = MagicMock(
            masked_text="보안 사고 신고",
            has_pii=False,
            tags=[],
        )

        mock_intent = MagicMock(spec=IntentService)
        mock_intent.classify.return_value = IntentResult(
            intent=IntentType.INCIDENT_REPORT,
            domain="INCIDENT",
            route=RouteType.INCIDENT,
            user_role=UserRole.EMPLOYEE,
        )

        mock_ragflow = AsyncMock(spec=RagflowClient)
        mock_ragflow.search_as_sources.return_value = []

        mock_llm = AsyncMock(spec=LLMClient)
        mock_llm.generate_chat_completion.return_value = "사고 신고 안내..."

        mock_guardrail = MagicMock(spec=GuardrailService)
        mock_guardrail.get_system_prompt_prefix.return_value = ""
        mock_guardrail.apply_to_answer.return_value = "사고 신고 안내..."

        service = ChatService(
            ragflow_client=mock_ragflow,
            llm_client=mock_llm,
            pii_service=mock_pii,
            intent_service=mock_intent,
            guardrail_service=mock_guardrail,
        )

        request = ChatRequest(
            session_id="test-session",
            user_id="test-user",
            user_role="EMPLOYEE",
            messages=[ChatMessage(role="user", content="보안 사고 신고")],
        )

        response = await service.handle_chat(request)

        # INCIDENT 도메인이므로 RAG Gap 후보 아님
        assert response.meta.rag_gap_candidate is False


# =============================================================================
# 6. AI Log 전파 테스트
# =============================================================================


class TestAILogServiceRagGapPropagation:
    """AILogService에서 rag_gap_candidate 전파 테스트."""

    def test_create_log_entry_includes_rag_gap_candidate(self) -> None:
        """create_log_entry에서 rag_gap_candidate 포함."""
        from app.services.ai_log_service import AILogService
        from app.services.pii_service import PiiService

        mock_pii = MagicMock(spec=PiiService)
        service = AILogService(pii_service=mock_pii)

        request = ChatRequest(
            session_id="test-session",
            user_id="test-user",
            user_role="EMPLOYEE",
            messages=[ChatMessage(role="user", content="연차 규정")],
        )

        response = ChatResponse(
            answer="연차 규정 답변",
            sources=[],
            meta=ChatAnswerMeta(),
        )

        entry = service.create_log_entry(
            request=request,
            response=response,
            intent="POLICY_QA",
            domain="POLICY",
            route="RAG_INTERNAL",
            has_pii_input=False,
            has_pii_output=False,
            rag_used=False,
            rag_source_count=0,
            latency_ms=100,
            rag_gap_candidate=True,
        )

        assert entry.rag_gap_candidate is True

    def test_create_log_entry_default_rag_gap_candidate_is_false(self) -> None:
        """create_log_entry에서 rag_gap_candidate 기본값은 False."""
        from app.services.ai_log_service import AILogService
        from app.services.pii_service import PiiService

        mock_pii = MagicMock(spec=PiiService)
        service = AILogService(pii_service=mock_pii)

        request = ChatRequest(
            session_id="test-session",
            user_id="test-user",
            user_role="EMPLOYEE",
            messages=[ChatMessage(role="user", content="안녕")],
        )

        response = ChatResponse(
            answer="안녕하세요",
            sources=[],
            meta=ChatAnswerMeta(),
        )

        entry = service.create_log_entry(
            request=request,
            response=response,
            intent="GENERAL_CHAT",
            domain="GENERAL",
            route="LLM_ONLY",
            has_pii_input=False,
            has_pii_output=False,
            rag_used=False,
            rag_source_count=0,
            latency_ms=50,
            # rag_gap_candidate 미지정
        )

        assert entry.rag_gap_candidate is False
