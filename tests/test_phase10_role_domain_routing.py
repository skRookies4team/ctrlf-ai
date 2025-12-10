"""
Phase 10: 역할(UserRole) × 도메인(Domain) × 라우트(RouteType) 테스트

이 모듈은 Phase 10에서 구현된 역할 기반 라우팅 정책을 테스트합니다.

테스트 항목:
1. UserRole 파싱 테스트
2. Domain 파싱 테스트
3. 역할별 라우팅 테스트 (EMPLOYEE, ADMIN, INCIDENT_MANAGER)
4. 가드레일 테스트
5. ChatService 통합 테스트
"""

from typing import List

import pytest

from app.models.chat import ChatMessage, ChatRequest, ChatResponse, ChatSource
from app.models.intent import (
    Domain,
    IntentResult,
    IntentType,
    RouteType,
    UserRole,
)
from app.clients.llm_client import LLMClient
from app.clients.ragflow_client import RagflowClient
from app.services.chat_service import ChatService
from app.services.guardrail_service import GuardrailService
from app.services.intent_service import IntentService
from app.services.pii_service import PiiService


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


@pytest.fixture
def intent_service() -> IntentService:
    """IntentService fixture."""
    return IntentService()


@pytest.fixture
def guardrail_service() -> GuardrailService:
    """GuardrailService fixture."""
    return GuardrailService()


# =============================================================================
# 1. UserRole 파싱 테스트
# =============================================================================


def test_parse_user_role_employee(intent_service: IntentService) -> None:
    """직원(EMPLOYEE) 역할 파싱 테스트."""
    assert intent_service._parse_user_role("EMPLOYEE") == UserRole.EMPLOYEE
    assert intent_service._parse_user_role("employee") == UserRole.EMPLOYEE


def test_parse_user_role_admin(intent_service: IntentService) -> None:
    """관리자(ADMIN) 역할 파싱 테스트."""
    assert intent_service._parse_user_role("ADMIN") == UserRole.ADMIN
    assert intent_service._parse_user_role("admin") == UserRole.ADMIN


def test_parse_user_role_incident_manager(intent_service: IntentService) -> None:
    """신고관리자(INCIDENT_MANAGER) 역할 파싱 테스트."""
    assert intent_service._parse_user_role("INCIDENT_MANAGER") == UserRole.INCIDENT_MANAGER
    assert intent_service._parse_user_role("incident_manager") == UserRole.INCIDENT_MANAGER


def test_parse_user_role_alias(intent_service: IntentService) -> None:
    """별칭 역할 파싱 테스트 (MANAGER → ADMIN)."""
    assert intent_service._parse_user_role("MANAGER") == UserRole.ADMIN
    assert intent_service._parse_user_role("HR") == UserRole.ADMIN
    assert intent_service._parse_user_role("SECURITY") == UserRole.ADMIN


def test_parse_user_role_unknown_defaults_employee(intent_service: IntentService) -> None:
    """알 수 없는 역할은 EMPLOYEE로 기본값 처리."""
    assert intent_service._parse_user_role("UNKNOWN") == UserRole.EMPLOYEE
    assert intent_service._parse_user_role("") == UserRole.EMPLOYEE
    assert intent_service._parse_user_role(None) == UserRole.EMPLOYEE


# =============================================================================
# 2. Domain 파싱 테스트
# =============================================================================


def test_parse_domain_hint_policy(intent_service: IntentService) -> None:
    """POLICY 도메인 파싱 테스트."""
    assert intent_service._parse_domain_hint("POLICY") == "POLICY"
    assert intent_service._parse_domain_hint("policy") == "POLICY"


def test_parse_domain_hint_incident(intent_service: IntentService) -> None:
    """INCIDENT 도메인 파싱 테스트."""
    assert intent_service._parse_domain_hint("INCIDENT") == "INCIDENT"
    assert intent_service._parse_domain_hint("incident") == "INCIDENT"


def test_parse_domain_hint_edu(intent_service: IntentService) -> None:
    """EDU 도메인 파싱 테스트."""
    assert intent_service._parse_domain_hint("EDU") == "EDU"
    assert intent_service._parse_domain_hint("EDUCATION") == "EDU"
    assert intent_service._parse_domain_hint("TRAINING") == "EDU"


def test_parse_domain_hint_none(intent_service: IntentService) -> None:
    """도메인 힌트 없음 테스트."""
    assert intent_service._parse_domain_hint(None) is None
    assert intent_service._parse_domain_hint("") is None


# =============================================================================
# 3. 역할별 라우팅 테스트 - EMPLOYEE
# =============================================================================


def test_employee_policy_qa_route(intent_service: IntentService) -> None:
    """
    직원(EMPLOYEE) + 정책 질문 → RAG_INTERNAL
    domain=POLICY 힌트를 사용하여 명확한 라우팅 테스트.
    """
    request = ChatRequest(
        session_id="test",
        user_id="user-123",
        user_role="EMPLOYEE",
        domain="POLICY",  # 도메인 힌트 사용
        messages=[ChatMessage(role="user", content="결재 승인 관련 문의")],
    )
    result = intent_service.classify(req=request, user_query="결재 승인 관련 문의")

    assert result.user_role == UserRole.EMPLOYEE
    assert result.intent == IntentType.POLICY_QA
    assert result.domain == "POLICY"
    assert result.route == RouteType.RAG_INTERNAL


def test_employee_incident_report_route(intent_service: IntentService) -> None:
    """
    직원(EMPLOYEE) + 사고 신고 → BACKEND_API
    """
    request = ChatRequest(
        session_id="test",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="보안 사고가 발생해서 신고하려고 합니다")],
    )
    result = intent_service.classify(
        req=request, user_query="보안 사고가 발생해서 신고하려고 합니다"
    )

    assert result.user_role == UserRole.EMPLOYEE
    assert result.intent == IntentType.INCIDENT_REPORT
    assert result.domain == "INCIDENT"
    assert result.route == RouteType.BACKEND_API


def test_employee_incident_qa_route(intent_service: IntentService) -> None:
    """
    직원(EMPLOYEE) + 사고 문의 → RAG_INTERNAL
    """
    request = ChatRequest(
        session_id="test",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="보안사고 유형이 뭐가 있나요?")],
    )
    result = intent_service.classify(
        req=request, user_query="보안사고 유형이 뭐가 있나요?"
    )

    assert result.user_role == UserRole.EMPLOYEE
    assert result.intent == IntentType.INCIDENT_QA
    assert result.domain == "INCIDENT"
    assert result.route == RouteType.RAG_INTERNAL


def test_employee_edu_status_route(intent_service: IntentService) -> None:
    """
    직원(EMPLOYEE) + 교육 현황 조회 → BACKEND_API
    """
    request = ChatRequest(
        session_id="test",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="내 교육 수료 현황 확인해줘")],
    )
    result = intent_service.classify(
        req=request, user_query="내 교육 수료 현황 확인해줘"
    )

    assert result.user_role == UserRole.EMPLOYEE
    assert result.intent == IntentType.EDU_STATUS
    assert result.domain == "EDU"
    assert result.route == RouteType.BACKEND_API


def test_employee_education_qa_route(intent_service: IntentService) -> None:
    """
    직원(EMPLOYEE) + 교육 내용 질문 → RAG_INTERNAL
    """
    request = ChatRequest(
        session_id="test",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="보안교육 강의 내용 알려줘")],
    )
    result = intent_service.classify(
        req=request, user_query="보안교육 강의 내용 알려줘"
    )

    assert result.user_role == UserRole.EMPLOYEE
    assert result.intent == IntentType.EDUCATION_QA
    assert result.domain == "EDU"
    assert result.route == RouteType.RAG_INTERNAL


def test_employee_general_chat_route(intent_service: IntentService) -> None:
    """
    직원(EMPLOYEE) + 일반 잡담 → LLM_ONLY
    """
    request = ChatRequest(
        session_id="test",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="안녕 ㅎㅎ")],
    )
    result = intent_service.classify(req=request, user_query="안녕 ㅎㅎ")

    assert result.user_role == UserRole.EMPLOYEE
    assert result.intent == IntentType.GENERAL_CHAT
    assert result.route == RouteType.LLM_ONLY


# =============================================================================
# 3. 역할별 라우팅 테스트 - ADMIN
# =============================================================================


def test_admin_policy_qa_route(intent_service: IntentService) -> None:
    """
    관리자(ADMIN) + 정책 질문 → RAG_INTERNAL
    domain=POLICY 힌트를 사용하여 명확한 라우팅 테스트.
    """
    request = ChatRequest(
        session_id="test",
        user_id="admin-123",
        user_role="ADMIN",
        domain="POLICY",  # 도메인 힌트 사용
        messages=[ChatMessage(role="user", content="결재 승인 관련 문의")],
    )
    result = intent_service.classify(req=request, user_query="결재 승인 관련 문의")

    assert result.user_role == UserRole.ADMIN
    assert result.intent == IntentType.POLICY_QA
    assert result.route == RouteType.RAG_INTERNAL


def test_admin_incident_qa_route(intent_service: IntentService) -> None:
    """
    관리자(ADMIN) + 사고 문의 → MIXED_BACKEND_RAG
    """
    request = ChatRequest(
        session_id="test",
        user_id="admin-123",
        user_role="ADMIN",
        messages=[ChatMessage(role="user", content="이번 달 보안사고 통계 알려줘")],
    )
    result = intent_service.classify(
        req=request, user_query="이번 달 보안사고 통계 알려줘"
    )

    assert result.user_role == UserRole.ADMIN
    assert result.intent == IntentType.INCIDENT_QA
    assert result.domain == "INCIDENT"
    assert result.route == RouteType.MIXED_BACKEND_RAG


def test_admin_edu_status_route(intent_service: IntentService) -> None:
    """
    관리자(ADMIN) + 교육 현황 조회 → MIXED_BACKEND_RAG
    """
    request = ChatRequest(
        session_id="test",
        user_id="admin-123",
        user_role="ADMIN",
        messages=[ChatMessage(role="user", content="부서별 교육 수료율 확인해줘")],
    )
    result = intent_service.classify(
        req=request, user_query="부서별 교육 수료율 확인해줘"
    )

    assert result.user_role == UserRole.ADMIN
    assert result.intent == IntentType.EDU_STATUS
    assert result.domain == "EDU"
    assert result.route == RouteType.MIXED_BACKEND_RAG


def test_admin_education_qa_route(intent_service: IntentService) -> None:
    """
    관리자(ADMIN) + 교육 내용 질문 → RAG_INTERNAL
    """
    request = ChatRequest(
        session_id="test",
        user_id="admin-123",
        user_role="ADMIN",
        messages=[ChatMessage(role="user", content="보안교육 내용 알려줘")],
    )
    result = intent_service.classify(
        req=request, user_query="보안교육 내용 알려줘"
    )

    assert result.user_role == UserRole.ADMIN
    assert result.intent == IntentType.EDUCATION_QA
    assert result.domain == "EDU"
    assert result.route == RouteType.RAG_INTERNAL


# =============================================================================
# 3. 역할별 라우팅 테스트 - INCIDENT_MANAGER
# =============================================================================


def test_incident_manager_incident_route(intent_service: IntentService) -> None:
    """
    신고관리자(INCIDENT_MANAGER) + 사고 관련 → MIXED_BACKEND_RAG
    """
    request = ChatRequest(
        session_id="test",
        user_id="manager-123",
        user_role="INCIDENT_MANAGER",
        messages=[ChatMessage(role="user", content="최근 보안사고 현황 보여줘")],
    )
    result = intent_service.classify(
        req=request, user_query="최근 보안사고 현황 보여줘"
    )

    assert result.user_role == UserRole.INCIDENT_MANAGER
    assert result.intent == IntentType.INCIDENT_QA
    assert result.domain == "INCIDENT"
    assert result.route == RouteType.MIXED_BACKEND_RAG


def test_incident_manager_policy_route(intent_service: IntentService) -> None:
    """
    신고관리자(INCIDENT_MANAGER) + 정책 질문 → RAG_INTERNAL
    """
    request = ChatRequest(
        session_id="test",
        user_id="manager-123",
        user_role="INCIDENT_MANAGER",
        messages=[ChatMessage(role="user", content="사고 처리 절차 알려줘")],
    )
    result = intent_service.classify(
        req=request, user_query="사고 처리 절차 알려줘"
    )

    # "사고" 키워드가 있으므로 INCIDENT 도메인으로 분류
    assert result.user_role == UserRole.INCIDENT_MANAGER
    assert result.domain == "INCIDENT"
    assert result.route == RouteType.MIXED_BACKEND_RAG


# =============================================================================
# 4. 가드레일 테스트
# =============================================================================


def test_guardrail_employee_incident_report_prefix(guardrail_service: GuardrailService) -> None:
    """
    직원(EMPLOYEE) + INCIDENT_REPORT → 답변 앞에 주의 문구 추가
    """
    prefix = guardrail_service.get_answer_prefix(
        user_role=UserRole.EMPLOYEE,
        domain="INCIDENT",
        intent=IntentType.INCIDENT_REPORT,
    )

    assert prefix != ""
    assert "신고" in prefix or "개인정보" in prefix or "주의" in prefix


def test_guardrail_employee_edu_status_prefix(guardrail_service: GuardrailService) -> None:
    """
    직원(EMPLOYEE) + EDU_STATUS → 답변 앞에 안내 문구 추가
    """
    prefix = guardrail_service.get_answer_prefix(
        user_role=UserRole.EMPLOYEE,
        domain="EDU",
        intent=IntentType.EDU_STATUS,
    )

    assert prefix != ""
    assert "교육" in prefix or "본인" in prefix


def test_guardrail_employee_policy_qa_no_prefix(guardrail_service: GuardrailService) -> None:
    """
    직원(EMPLOYEE) + POLICY_QA → 답변 앞에 prefix 없음
    """
    prefix = guardrail_service.get_answer_prefix(
        user_role=UserRole.EMPLOYEE,
        domain="POLICY",
        intent=IntentType.POLICY_QA,
    )

    assert prefix == ""


def test_guardrail_admin_incident_system_prompt(guardrail_service: GuardrailService) -> None:
    """
    관리자(ADMIN) + INCIDENT 도메인 → system prompt에 실명 일반화 지시
    """
    system_prefix = guardrail_service.get_system_prompt_prefix(
        user_role=UserRole.ADMIN,
        domain="INCIDENT",
        intent=IntentType.INCIDENT_QA,
    )

    assert system_prefix != ""
    assert "일반화" in system_prefix or "관련자" in system_prefix or "이름" in system_prefix


def test_guardrail_incident_manager_system_prompt(guardrail_service: GuardrailService) -> None:
    """
    신고관리자(INCIDENT_MANAGER) + INCIDENT → system prompt에 익명화/징계추천금지 지시
    """
    system_prefix = guardrail_service.get_system_prompt_prefix(
        user_role=UserRole.INCIDENT_MANAGER,
        domain="INCIDENT",
        intent=IntentType.INCIDENT_QA,
    )

    assert system_prefix != ""
    assert "익명" in system_prefix or "실명" in system_prefix or "징계" in system_prefix


def test_guardrail_apply_to_answer(guardrail_service: GuardrailService) -> None:
    """
    apply_to_answer 메서드 테스트
    """
    original_answer = "교육 수료 현황입니다."

    result = guardrail_service.apply_to_answer(
        answer=original_answer,
        user_role=UserRole.EMPLOYEE,
        domain="EDU",
        intent=IntentType.EDU_STATUS,
    )

    # prefix가 적용되었으므로 원본보다 길어야 함
    assert len(result) > len(original_answer)
    assert original_answer in result


# =============================================================================
# 5. ChatService 통합 테스트
# =============================================================================


@pytest.mark.anyio
async def test_chat_service_employee_policy_returns_rag_internal() -> None:
    """
    ChatService: 직원 + 정책 질문 → route=RAG_INTERNAL, meta.user_role 포함
    domain=POLICY 힌트를 사용하여 명확한 라우팅 테스트.
    """
    ragflow_client = RagflowClient(base_url="")
    llm_client = LLMClient(base_url="")
    pii_service = PiiService(base_url="", enabled=False)
    intent_service = IntentService()
    guardrail_service = GuardrailService()

    service = ChatService(
        ragflow_client=ragflow_client,
        llm_client=llm_client,
        pii_service=pii_service,
        intent_service=intent_service,
        guardrail_service=guardrail_service,
    )

    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        domain="POLICY",  # 도메인 힌트 사용
        messages=[ChatMessage(role="user", content="결재 승인 관련 문의")],
    )

    response = await service.handle_chat(request)

    assert isinstance(response, ChatResponse)
    assert response.meta.route == "RAG_INTERNAL"
    assert response.meta.user_role == "EMPLOYEE"
    assert response.meta.intent == "POLICY_QA"


@pytest.mark.anyio
async def test_chat_service_admin_incident_returns_mixed_backend_rag() -> None:
    """
    ChatService: 관리자 + 사고 통계 질문 → route=MIXED_BACKEND_RAG
    """
    ragflow_client = RagflowClient(base_url="")
    llm_client = LLMClient(base_url="")
    pii_service = PiiService(base_url="", enabled=False)
    intent_service = IntentService()
    guardrail_service = GuardrailService()

    service = ChatService(
        ragflow_client=ragflow_client,
        llm_client=llm_client,
        pii_service=pii_service,
        intent_service=intent_service,
        guardrail_service=guardrail_service,
    )

    request = ChatRequest(
        session_id="test-session",
        user_id="admin-123",
        user_role="ADMIN",
        messages=[ChatMessage(role="user", content="이번 달 보안사고 통계 보여줘")],
    )

    response = await service.handle_chat(request)

    assert isinstance(response, ChatResponse)
    assert response.meta.route == "MIXED_BACKEND_RAG"
    assert response.meta.user_role == "ADMIN"


@pytest.mark.anyio
async def test_chat_service_employee_incident_report_returns_backend_api() -> None:
    """
    ChatService: 직원 + 사고 신고 → route=BACKEND_API
    """
    ragflow_client = RagflowClient(base_url="")
    llm_client = LLMClient(base_url="")
    pii_service = PiiService(base_url="", enabled=False)
    intent_service = IntentService()
    guardrail_service = GuardrailService()

    service = ChatService(
        ragflow_client=ragflow_client,
        llm_client=llm_client,
        pii_service=pii_service,
        intent_service=intent_service,
        guardrail_service=guardrail_service,
    )

    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="보안 사고가 발생해서 신고하려고 합니다")],
    )

    response = await service.handle_chat(request)

    assert isinstance(response, ChatResponse)
    assert response.meta.route == "BACKEND_API"
    assert response.meta.user_role == "EMPLOYEE"
    assert response.meta.intent == "INCIDENT_REPORT"


@pytest.mark.anyio
async def test_chat_service_employee_edu_status_returns_backend_api() -> None:
    """
    ChatService: 직원 + 교육 현황 조회 → route=BACKEND_API
    """
    ragflow_client = RagflowClient(base_url="")
    llm_client = LLMClient(base_url="")
    pii_service = PiiService(base_url="", enabled=False)
    intent_service = IntentService()
    guardrail_service = GuardrailService()

    service = ChatService(
        ragflow_client=ragflow_client,
        llm_client=llm_client,
        pii_service=pii_service,
        intent_service=intent_service,
        guardrail_service=guardrail_service,
    )

    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="내 교육 수료 현황 확인해줘")],
    )

    response = await service.handle_chat(request)

    assert isinstance(response, ChatResponse)
    assert response.meta.route == "BACKEND_API"
    assert response.meta.user_role == "EMPLOYEE"
    assert response.meta.intent == "EDU_STATUS"


@pytest.mark.anyio
async def test_chat_service_general_chat_returns_llm_only() -> None:
    """
    ChatService: 일반 잡담 → route=LLM_ONLY
    """
    ragflow_client = RagflowClient(base_url="")
    llm_client = LLMClient(base_url="")
    pii_service = PiiService(base_url="", enabled=False)
    intent_service = IntentService()
    guardrail_service = GuardrailService()

    service = ChatService(
        ragflow_client=ragflow_client,
        llm_client=llm_client,
        pii_service=pii_service,
        intent_service=intent_service,
        guardrail_service=guardrail_service,
    )

    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="안녕 ㅎㅎ")],
    )

    response = await service.handle_chat(request)

    assert isinstance(response, ChatResponse)
    assert response.meta.route == "LLM_ONLY"


# =============================================================================
# 6. Enum 테스트
# =============================================================================


def test_user_role_enum_values() -> None:
    """UserRole enum 값 테스트."""
    assert UserRole.EMPLOYEE.value == "EMPLOYEE"
    assert UserRole.ADMIN.value == "ADMIN"
    assert UserRole.INCIDENT_MANAGER.value == "INCIDENT_MANAGER"


def test_domain_enum_values() -> None:
    """Domain enum 값 테스트."""
    assert Domain.POLICY.value == "POLICY"
    assert Domain.INCIDENT.value == "INCIDENT"
    assert Domain.EDU.value == "EDU"


def test_route_type_new_values() -> None:
    """RouteType 새 값 테스트 (Phase 10)."""
    assert RouteType.RAG_INTERNAL.value == "RAG_INTERNAL"
    assert RouteType.LLM_ONLY.value == "LLM_ONLY"
    assert RouteType.BACKEND_API.value == "BACKEND_API"
    assert RouteType.MIXED_BACKEND_RAG.value == "MIXED_BACKEND_RAG"


def test_route_type_legacy_values() -> None:
    """RouteType 레거시 값 테스트 (하위 호환성)."""
    assert RouteType.ROUTE_RAG_INTERNAL.value == "ROUTE_RAG_INTERNAL"
    assert RouteType.ROUTE_LLM_ONLY.value == "ROUTE_LLM_ONLY"
    assert RouteType.ROUTE_INCIDENT.value == "ROUTE_INCIDENT"
    assert RouteType.ROUTE_TRAINING.value == "ROUTE_TRAINING"


def test_intent_type_new_values() -> None:
    """IntentType 새 값 테스트 (Phase 10)."""
    assert IntentType.INCIDENT_QA.value == "INCIDENT_QA"
    assert IntentType.EDU_STATUS.value == "EDU_STATUS"


def test_intent_result_includes_user_role() -> None:
    """IntentResult에 user_role 필드가 포함되어 있는지 테스트."""
    result = IntentResult(
        user_role=UserRole.EMPLOYEE,
        intent=IntentType.POLICY_QA,
        domain="POLICY",
        route=RouteType.RAG_INTERNAL,
    )

    assert result.user_role == UserRole.EMPLOYEE
    assert result.intent == IntentType.POLICY_QA
    assert result.domain == "POLICY"
    assert result.route == RouteType.RAG_INTERNAL


# =============================================================================
# 7. GENERAL_CHAT Fallback 테스트 (기타 케이스 → LLM_ONLY)
# =============================================================================


def test_general_chat_lunch_question(intent_service: IntentService) -> None:
    """
    기타 케이스: 점심 메뉴 질문 + 잡담 키워드 → GENERAL_CHAT + LLM_ONLY
    GENERAL_CHAT_KEYWORDS에 포함된 키워드가 있어야 GENERAL_CHAT으로 분류된다.
    """
    request = ChatRequest(
        session_id="test",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="심심해 오늘 점심 뭐 먹지?")],
    )
    result = intent_service.classify(req=request, user_query="심심해 오늘 점심 뭐 먹지?")

    assert result.intent == IntentType.GENERAL_CHAT
    assert result.route == RouteType.LLM_ONLY


def test_general_chat_weather_question(intent_service: IntentService) -> None:
    """
    기타 케이스: 날씨 질문 → GENERAL_CHAT + LLM_ONLY
    """
    request = ChatRequest(
        session_id="test",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="오늘 날씨 어때?")],
    )
    result = intent_service.classify(req=request, user_query="오늘 날씨 어때?")

    assert result.intent == IntentType.GENERAL_CHAT
    assert result.route == RouteType.LLM_ONLY


def test_general_chat_random_topic(intent_service: IntentService) -> None:
    """
    기타 케이스: 임의 주제 잡담 + 농담 키워드 → GENERAL_CHAT + LLM_ONLY
    """
    request = ChatRequest(
        session_id="test",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="농담 하나 해줘 ㅋㅋ")],
    )
    result = intent_service.classify(req=request, user_query="농담 하나 해줘 ㅋㅋ")

    assert result.intent == IntentType.GENERAL_CHAT
    assert result.route == RouteType.LLM_ONLY


def test_general_chat_admin_random_question(intent_service: IntentService) -> None:
    """
    ADMIN 역할도 잡담 키워드 사용 시 → GENERAL_CHAT + LLM_ONLY
    """
    request = ChatRequest(
        session_id="test",
        user_id="admin-123",
        user_role="ADMIN",
        messages=[ChatMessage(role="user", content="안녕 반가워")],
    )
    result = intent_service.classify(req=request, user_query="안녕 반가워")

    assert result.user_role == UserRole.ADMIN
    assert result.intent == IntentType.GENERAL_CHAT
    assert result.route == RouteType.LLM_ONLY


@pytest.mark.anyio
async def test_chat_service_general_chat_no_rag_call() -> None:
    """
    ChatService 통합: 잡담 키워드가 포함된 질문에서 RAGFlowClient가 호출되지 않는지 검증.
    GENERAL_CHAT → LLM_ONLY로 라우팅되어 RAG 검색이 수행되지 않아야 한다.
    """
    # Spy/Mock: RagflowClient의 search_as_sources 호출 여부 추적
    rag_called = False
    original_sources: List[ChatSource] = []

    class SpyRagflowClient(RagflowClient):
        def __init__(self) -> None:
            super().__init__(base_url="")

        async def search_as_sources(
            self,
            query: str,
            domain: str = "POLICY",
            user_role: str | None = None,
            department: str | None = None,
            top_k: int = 5,
        ) -> List[ChatSource]:
            nonlocal rag_called
            rag_called = True
            return original_sources

    spy_ragflow = SpyRagflowClient()
    llm_client = LLMClient(base_url="")
    pii_service = PiiService(base_url="", enabled=False)
    intent_service = IntentService()
    guardrail_service = GuardrailService()

    service = ChatService(
        ragflow_client=spy_ragflow,
        llm_client=llm_client,
        pii_service=pii_service,
        intent_service=intent_service,
        guardrail_service=guardrail_service,
    )

    # 잡담 키워드 "심심" 포함
    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="심심해 뭐 해야 하지?")],
    )

    response = await service.handle_chat(request)

    # 검증: GENERAL_CHAT → LLM_ONLY, RAG 미호출
    assert response.meta.route == "LLM_ONLY"
    assert response.meta.intent == "GENERAL_CHAT"
    assert rag_called is False  # RAG가 호출되지 않아야 함
    assert response.sources == []  # sources도 비어 있어야 함


@pytest.mark.anyio
async def test_chat_service_general_chat_greeting_no_rag() -> None:
    """
    ChatService: 인사말에서 RAG가 호출되지 않는지 검증.
    """
    rag_called = False

    class SpyRagflowClient(RagflowClient):
        def __init__(self) -> None:
            super().__init__(base_url="")

        async def search_as_sources(self, *args, **kwargs) -> List[ChatSource]:
            nonlocal rag_called
            rag_called = True
            return []

    spy_ragflow = SpyRagflowClient()
    llm_client = LLMClient(base_url="")
    pii_service = PiiService(base_url="", enabled=False)
    intent_service = IntentService()

    service = ChatService(
        ragflow_client=spy_ragflow,
        llm_client=llm_client,
        pii_service=pii_service,
        intent_service=intent_service,
    )

    request = ChatRequest(
        session_id="test-session",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="안녕하세요~")],
    )

    response = await service.handle_chat(request)

    assert response.meta.route == "LLM_ONLY"
    assert rag_called is False


# =============================================================================
# 8. Guardrail 네거티브 테스트 (가드레일이 적용되면 안 되는 경우)
# =============================================================================


def test_guardrail_employee_policy_qa_no_incident_warning(guardrail_service: GuardrailService) -> None:
    """
    네거티브 테스트: EMPLOYEE + POLICY_QA에서 INCIDENT 관련 경고가 붙지 않아야 함.
    """
    system_prefix = guardrail_service.get_system_prompt_prefix(
        user_role=UserRole.EMPLOYEE,
        domain="POLICY",
        intent=IntentType.POLICY_QA,
    )
    answer_prefix = guardrail_service.get_answer_prefix(
        user_role=UserRole.EMPLOYEE,
        domain="POLICY",
        intent=IntentType.POLICY_QA,
    )

    # INCIDENT 관련 키워드가 포함되어선 안 됨
    incident_keywords = ["사고 신고", "공식 신고 채널", "개인정보 입력 금지", "실명", "익명"]

    for keyword in incident_keywords:
        assert keyword not in system_prefix, f"POLICY_QA에 '{keyword}'가 포함되면 안 됨"
        assert keyword not in answer_prefix, f"POLICY_QA에 '{keyword}'가 포함되면 안 됨"


def test_guardrail_employee_policy_qa_returns_empty_prefixes(guardrail_service: GuardrailService) -> None:
    """
    네거티브 테스트: EMPLOYEE + POLICY_QA는 아무 prefix도 붙지 않아야 함.
    """
    system_prefix = guardrail_service.get_system_prompt_prefix(
        user_role=UserRole.EMPLOYEE,
        domain="POLICY",
        intent=IntentType.POLICY_QA,
    )
    answer_prefix = guardrail_service.get_answer_prefix(
        user_role=UserRole.EMPLOYEE,
        domain="POLICY",
        intent=IntentType.POLICY_QA,
    )

    assert system_prefix == ""
    assert answer_prefix == ""


def test_guardrail_admin_policy_qa_no_incident_warning(guardrail_service: GuardrailService) -> None:
    """
    네거티브 테스트: ADMIN + POLICY_QA에서 INCIDENT 전용 안내가 붙지 않아야 함.
    """
    system_prefix = guardrail_service.get_system_prompt_prefix(
        user_role=UserRole.ADMIN,
        domain="POLICY",
        intent=IntentType.POLICY_QA,
    )
    answer_prefix = guardrail_service.get_answer_prefix(
        user_role=UserRole.ADMIN,
        domain="POLICY",
        intent=IntentType.POLICY_QA,
    )

    # INCIDENT 전용 키워드가 포함되어선 안 됨
    incident_keywords = ["사고 신고", "공식 신고 채널", "일반화", "관련자", "실명", "익명", "징계"]

    for keyword in incident_keywords:
        assert keyword not in system_prefix, f"ADMIN+POLICY_QA에 '{keyword}'가 포함되면 안 됨"
        assert keyword not in answer_prefix, f"ADMIN+POLICY_QA에 '{keyword}'가 포함되면 안 됨"


def test_guardrail_admin_policy_qa_returns_empty_prefixes(guardrail_service: GuardrailService) -> None:
    """
    네거티브 테스트: ADMIN + POLICY_QA는 아무 prefix도 붙지 않아야 함.
    """
    system_prefix = guardrail_service.get_system_prompt_prefix(
        user_role=UserRole.ADMIN,
        domain="POLICY",
        intent=IntentType.POLICY_QA,
    )
    answer_prefix = guardrail_service.get_answer_prefix(
        user_role=UserRole.ADMIN,
        domain="POLICY",
        intent=IntentType.POLICY_QA,
    )

    assert system_prefix == ""
    assert answer_prefix == ""


def test_guardrail_general_chat_no_guardrails(guardrail_service: GuardrailService) -> None:
    """
    네거티브 테스트: GENERAL_CHAT에는 어떤 가드레일도 적용되지 않아야 함.
    """
    for role in [UserRole.EMPLOYEE, UserRole.ADMIN, UserRole.INCIDENT_MANAGER]:
        system_prefix = guardrail_service.get_system_prompt_prefix(
            user_role=role,
            domain="GENERAL",
            intent=IntentType.GENERAL_CHAT,
        )
        answer_prefix = guardrail_service.get_answer_prefix(
            user_role=role,
            domain="GENERAL",
            intent=IntentType.GENERAL_CHAT,
        )

        assert system_prefix == "", f"{role.value}+GENERAL_CHAT에 system_prefix가 있으면 안 됨"
        assert answer_prefix == "", f"{role.value}+GENERAL_CHAT에 answer_prefix가 있으면 안 됨"


def test_guardrail_apply_to_answer_no_change_for_policy(guardrail_service: GuardrailService) -> None:
    """
    네거티브 테스트: POLICY_QA에 apply_to_answer 적용 시 원본과 동일해야 함.
    """
    original_answer = "연차 규정은 다음과 같습니다..."

    result = guardrail_service.apply_to_answer(
        answer=original_answer,
        user_role=UserRole.EMPLOYEE,
        domain="POLICY",
        intent=IntentType.POLICY_QA,
    )

    assert result == original_answer, "POLICY_QA에서는 answer가 변경되면 안 됨"


def test_guardrail_apply_to_answer_no_change_for_education_qa(guardrail_service: GuardrailService) -> None:
    """
    네거티브 테스트: EDUCATION_QA(교육 내용 질문)에 apply_to_answer 적용 시 원본과 동일해야 함.
    """
    original_answer = "보안교육 내용은 피싱 방지, 비밀번호 관리 등을 다룹니다."

    result = guardrail_service.apply_to_answer(
        answer=original_answer,
        user_role=UserRole.EMPLOYEE,
        domain="EDU",
        intent=IntentType.EDUCATION_QA,
    )

    assert result == original_answer, "EDUCATION_QA에서는 answer가 변경되면 안 됨"
