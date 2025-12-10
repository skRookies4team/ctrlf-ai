"""
Intent and PII Service Test Module

Tests for intent classification, PII masking, and their integration
with ChatService.

These tests verify that:
- IntentService correctly classifies user queries
- PiiService returns valid results when disabled or not configured
- ChatService integrates PII and Intent services correctly
- MaskingStage enum works for all three stages (INPUT, OUTPUT, LOG)
"""

import pytest

from app.models.chat import ChatMessage, ChatRequest, ChatResponse
from app.models.intent import (
    IntentResult,
    IntentType,
    MaskingStage,
    PiiMaskResult,
    RouteType,
)
from app.clients.llm_client import LLMClient
from app.clients.ragflow_client import RagflowClient
from app.services.chat_service import ChatService
from app.services.intent_service import IntentService
from app.services.pii_service import PiiService


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


# --- IntentService Unit Tests ---


def test_intent_policy_qa() -> None:
    """
    Test that policy-related query is classified as POLICY_QA.
    Phase 10: domain=POLICY 힌트를 사용하여 명확한 라우팅.
    """
    service = IntentService()
    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        domain="POLICY",  # 도메인 힌트 사용
        messages=[ChatMessage(role="user", content="결재 승인 관련 문의")],
    )

    result = service.classify(req=request, user_query="결재 승인 관련 문의")

    assert isinstance(result, IntentResult)
    assert result.intent == IntentType.POLICY_QA
    assert result.route == RouteType.RAG_INTERNAL


def test_intent_incident_report() -> None:
    """
    Test that incident-related query is classified as INCIDENT_REPORT.
    Phase 10: INCIDENT_REPORT → BACKEND_API (신고 플로우 안내)
    """
    service = IntentService()
    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="보안 사고가 발생해서 신고하려고 합니다")],
    )

    result = service.classify(
        req=request,
        user_query="보안 사고가 발생해서 신고하려고 합니다",
    )

    assert result.intent == IntentType.INCIDENT_REPORT
    assert result.route == RouteType.BACKEND_API


def test_intent_education_qa() -> None:
    """
    Test that education content query is classified as EDUCATION_QA.
    Phase 10: 교육 "내용" 질문 → RAG_INTERNAL
    """
    service = IntentService()
    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="보안교육 강의 내용 알려줘")],
    )

    result = service.classify(
        req=request,
        user_query="보안교육 강의 내용 알려줘",
    )

    assert result.intent == IntentType.EDUCATION_QA
    assert result.route == RouteType.RAG_INTERNAL


def test_intent_general_chat() -> None:
    """
    Test that casual greeting is classified as GENERAL_CHAT.
    Phase 10: GENERAL_CHAT → LLM_ONLY
    """
    service = IntentService()
    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="안녕 ㅎㅎ")],
    )

    result = service.classify(req=request, user_query="안녕 ㅎㅎ")

    assert result.intent == IntentType.GENERAL_CHAT
    assert result.route == RouteType.LLM_ONLY


def test_intent_with_domain_policy() -> None:
    """
    Test that when domain is POLICY, intent is classified as POLICY_QA.
    Phase 10: domain=POLICY 힌트 사용 시 POLICY_QA → RAG_INTERNAL
    """
    service = IntentService()
    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        domain="POLICY",
        messages=[ChatMessage(role="user", content="결재 승인 관련 문의")],
    )

    result = service.classify(
        req=request,
        user_query="결재 승인 관련 문의",
    )

    assert result.intent == IntentType.POLICY_QA
    assert result.route == RouteType.RAG_INTERNAL
    assert result.domain == "POLICY"


def test_intent_default_fallback() -> None:
    """
    Test that unknown queries default to POLICY_QA with RAG.
    Phase 10: domain=POLICY 힌트 사용 시 POLICY_QA → RAG_INTERNAL
    """
    service = IntentService()
    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        domain="POLICY",  # 도메인 힌트로 명확한 라우팅
        messages=[ChatMessage(role="user", content="일반 문의사항 있어요")],
    )

    result = service.classify(
        req=request,
        user_query="일반 문의사항 있어요",
    )

    # Default should be POLICY_QA with RAG
    assert result.intent == IntentType.POLICY_QA
    assert result.route == RouteType.RAG_INTERNAL


# --- PiiService Unit Tests ---


@pytest.mark.anyio
async def test_pii_disabled_returns_original() -> None:
    """
    Test that PiiService returns original text when PII_ENABLED=False.
    """
    # Create service with PII disabled
    service = PiiService(base_url="", enabled=False)

    result = await service.detect_and_mask(
        text="010-1234-5678",
        stage=MaskingStage.INPUT,
    )

    assert isinstance(result, PiiMaskResult)
    assert result.original_text == "010-1234-5678"
    assert result.masked_text == "010-1234-5678"
    assert result.has_pii is False
    assert result.tags == []


@pytest.mark.anyio
async def test_pii_no_base_url_returns_original() -> None:
    """
    Test that PiiService returns original text when PII_BASE_URL is not set.
    """
    # Create service with no base_url
    service = PiiService(base_url="", enabled=True)

    result = await service.detect_and_mask(
        text="홍길동 010-1234-5678",
        stage=MaskingStage.INPUT,
    )

    assert result.original_text == "홍길동 010-1234-5678"
    assert result.masked_text == "홍길동 010-1234-5678"
    assert result.has_pii is False
    assert result.tags == []


@pytest.mark.anyio
async def test_pii_all_stages_work() -> None:
    """
    Test that PiiService handles all MaskingStage values without errors.
    """
    service = PiiService(base_url="", enabled=False)
    text = "테스트 텍스트"

    # Test INPUT stage
    result_input = await service.detect_and_mask(text, MaskingStage.INPUT)
    assert result_input.masked_text == text
    assert result_input.has_pii is False

    # Test OUTPUT stage
    result_output = await service.detect_and_mask(text, MaskingStage.OUTPUT)
    assert result_output.masked_text == text
    assert result_output.has_pii is False

    # Test LOG stage
    result_log = await service.detect_and_mask(text, MaskingStage.LOG)
    assert result_log.masked_text == text
    assert result_log.has_pii is False


@pytest.mark.anyio
async def test_pii_preserves_text_on_skip() -> None:
    """
    Test that when PII masking is skipped, text is preserved exactly.
    """
    service = PiiService(base_url="", enabled=False)

    test_texts = [
        "일반 텍스트",
        "email@example.com",
        "주민등록번호: 123456-1234567",
        "전화: 02-123-4567",
    ]

    for text in test_texts:
        result = await service.detect_and_mask(text, MaskingStage.INPUT)
        assert result.masked_text == text
        assert result.original_text == text


# --- ChatService Integration Tests ---


@pytest.mark.anyio
async def test_chat_service_with_intent_and_pii() -> None:
    """
    Test that ChatService integrates IntentService and PiiService.
    Phase 10: domain=POLICY 힌트 사용 시 RAG_INTERNAL
    """
    # Create services with empty base_url (fallback mode)
    ragflow_client = RagflowClient(base_url="")
    llm_client = LLMClient(base_url="")
    pii_service = PiiService(base_url="", enabled=False)
    intent_service = IntentService()

    service = ChatService(
        ragflow_client=ragflow_client,
        llm_client=llm_client,
        pii_service=pii_service,
        intent_service=intent_service,
    )

    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        domain="POLICY",  # 도메인 힌트 사용
        messages=[ChatMessage(role="user", content="결재 승인 관련 문의")],
    )

    response = await service.handle_chat(request)

    # Verify response structure
    assert isinstance(response, ChatResponse)
    assert isinstance(response.answer, str)
    assert len(response.answer) > 0
    assert response.meta is not None

    # Verify route is set (should be RAG_INTERNAL for policy query)
    assert response.meta.route is not None
    assert len(response.meta.route) > 0
    assert response.meta.route == "RAG_INTERNAL"

    # Verify masked flag is set (should be False since PII disabled)
    assert response.meta.masked is False


@pytest.mark.anyio
async def test_chat_service_general_chat_route() -> None:
    """
    Test that ChatService routes general chat to LLM only.
    Phase 10: GENERAL_CHAT → LLM_ONLY
    """
    ragflow_client = RagflowClient(base_url="")
    llm_client = LLMClient(base_url="")
    pii_service = PiiService(base_url="", enabled=False)
    intent_service = IntentService()

    service = ChatService(
        ragflow_client=ragflow_client,
        llm_client=llm_client,
        pii_service=pii_service,
        intent_service=intent_service,
    )

    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="안녕 ㅎㅎ")],
    )

    response = await service.handle_chat(request)

    # Verify route is LLM_ONLY for general chat
    assert response.meta.route == "LLM_ONLY"
    # Sources should be empty for LLM only route
    assert response.sources == []


@pytest.mark.anyio
async def test_chat_service_incident_route() -> None:
    """
    Test that ChatService routes incident report correctly.
    Phase 10: INCIDENT_REPORT → BACKEND_API
    """
    ragflow_client = RagflowClient(base_url="")
    llm_client = LLMClient(base_url="")
    pii_service = PiiService(base_url="", enabled=False)
    intent_service = IntentService()

    service = ChatService(
        ragflow_client=ragflow_client,
        llm_client=llm_client,
        pii_service=pii_service,
        intent_service=intent_service,
    )

    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="보안 사고가 발생해서 신고하려고 합니다")],
    )

    response = await service.handle_chat(request)

    # Verify route is BACKEND_API for incident report
    assert response.meta.route == "BACKEND_API"


@pytest.mark.anyio
async def test_chat_service_education_route() -> None:
    """
    Test that ChatService routes education content queries correctly.
    Phase 10: EDUCATION_QA → RAG_INTERNAL
    """
    ragflow_client = RagflowClient(base_url="")
    llm_client = LLMClient(base_url="")
    pii_service = PiiService(base_url="", enabled=False)
    intent_service = IntentService()

    service = ChatService(
        ragflow_client=ragflow_client,
        llm_client=llm_client,
        pii_service=pii_service,
        intent_service=intent_service,
    )

    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="보안교육 강의 내용 알려줘")],
    )

    response = await service.handle_chat(request)

    # Verify route is RAG_INTERNAL for education content query
    assert response.meta.route == "RAG_INTERNAL"


@pytest.mark.anyio
async def test_chat_service_meta_has_required_fields() -> None:
    """
    Test that ChatService response meta has all required fields.
    """
    ragflow_client = RagflowClient(base_url="")
    llm_client = LLMClient(base_url="")
    pii_service = PiiService(base_url="", enabled=False)

    service = ChatService(
        ragflow_client=ragflow_client,
        llm_client=llm_client,
        pii_service=pii_service,
    )

    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="테스트")],
    )

    response = await service.handle_chat(request)

    # Verify all meta fields are present and have correct types
    assert response.meta.route is not None
    assert isinstance(response.meta.route, str)
    assert len(response.meta.route) > 0

    assert response.meta.masked is not None
    assert isinstance(response.meta.masked, bool)

    assert response.meta.latency_ms is not None
    assert isinstance(response.meta.latency_ms, int)
    assert response.meta.latency_ms >= 0


# --- MaskingStage Enum Tests ---


def test_masking_stage_enum_values() -> None:
    """
    Test that MaskingStage enum has correct values.
    """
    assert MaskingStage.INPUT.value == "input"
    assert MaskingStage.OUTPUT.value == "output"
    assert MaskingStage.LOG.value == "log"


def test_masking_stage_enum_members() -> None:
    """
    Test that all MaskingStage members exist.
    """
    stages = list(MaskingStage)
    assert len(stages) == 3
    assert MaskingStage.INPUT in stages
    assert MaskingStage.OUTPUT in stages
    assert MaskingStage.LOG in stages


# --- IntentType and RouteType Enum Tests ---


def test_intent_type_enum_values() -> None:
    """
    Test that IntentType enum has all expected values.
    """
    assert IntentType.POLICY_QA.value == "POLICY_QA"
    assert IntentType.INCIDENT_REPORT.value == "INCIDENT_REPORT"
    assert IntentType.EDUCATION_QA.value == "EDUCATION_QA"
    assert IntentType.GENERAL_CHAT.value == "GENERAL_CHAT"
    assert IntentType.SYSTEM_HELP.value == "SYSTEM_HELP"
    assert IntentType.UNKNOWN.value == "UNKNOWN"


def test_route_type_enum_values() -> None:
    """
    Test that RouteType enum has all expected values.
    """
    assert RouteType.ROUTE_RAG_INTERNAL.value == "ROUTE_RAG_INTERNAL"
    assert RouteType.ROUTE_LLM_ONLY.value == "ROUTE_LLM_ONLY"
    assert RouteType.ROUTE_INCIDENT.value == "ROUTE_INCIDENT"
    assert RouteType.ROUTE_TRAINING.value == "ROUTE_TRAINING"
    assert RouteType.ROUTE_FALLBACK.value == "ROUTE_FALLBACK"
    assert RouteType.ROUTE_ERROR.value == "ROUTE_ERROR"
