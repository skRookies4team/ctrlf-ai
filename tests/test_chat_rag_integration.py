"""
Chat RAG Integration Test Module (Phase 6)

POLICY 도메인에 대한 RAG + LLM E2E 통합 테스트입니다.

테스트 목표:
- IntentService가 domain="POLICY", route=RAG_INTERNAL로 분류
- RagflowClient가 문서를 반환하는 경우 정상 처리 확인
- RAG 결과 없음/에러 시 fallback 동작 검증
- ChatResponse의 answer, sources, meta 필드 검증

테스트 방법:
- RagflowClient와 LLMClient를 fake/mock으로 교체
- 실제 외부 서비스 호출 없이 E2E 플로우 검증
"""

from typing import List
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.clients.llm_client import LLMClient
from app.clients.ragflow_client import RagflowClient
from app.models.chat import ChatMessage, ChatRequest, ChatSource
from app.models.intent import IntentResult, IntentType, RouteType, UserRole
from app.models.rag import RagDocument
from app.services.chat_service import ChatService, NO_RAG_RESULTS_NOTICE
from app.services.intent_service import IntentService
from app.services.pii_service import PiiService


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


# =============================================================================
# Fake/Mock 클래스들
# =============================================================================


class FakeRagflowClient(RagflowClient):
    """
    테스트용 Fake RagflowClient.

    실제 HTTP 호출 없이 미리 설정된 문서를 반환합니다.
    """

    def __init__(
        self,
        documents: List[RagDocument] = None,
        should_fail: bool = False,
    ):
        """
        Args:
            documents: 검색 시 반환할 RagDocument 리스트
            should_fail: True면 검색 시 예외 발생
        """
        # 부모 초기화 (base_url 빈 문자열로)
        super().__init__(base_url="http://fake-ragflow:8000")
        self._fake_documents = documents or []
        self._should_fail = should_fail
        self._search_called = False
        self._last_query = None
        self._last_domain = None

    async def search(
        self,
        query: str,
        top_k: int = 5,
        dataset: str = None,
        domain: str = None,
        user_role: str = None,
        department: str = None,
    ) -> List[RagDocument]:
        """Fake search implementation."""
        self._search_called = True
        self._last_query = query
        self._last_domain = dataset or domain

        if self._should_fail:
            raise ConnectionError("Fake RAGFlow connection error")

        return self._fake_documents[:top_k]

    async def search_as_sources(
        self,
        query: str,
        domain: str,
        user_role: str,
        department: str,
        top_k: int = 5,
    ) -> List[ChatSource]:
        """Fake search_as_sources implementation."""
        documents = await self.search(
            query=query,
            top_k=top_k,
            domain=domain,
            user_role=user_role,
            department=department,
        )
        return [self._to_chat_source(doc) for doc in documents]


class FakeLLMClient(LLMClient):
    """
    테스트용 Fake LLMClient.

    실제 LLM 호출 없이 미리 설정된 응답을 반환합니다.
    """

    def __init__(
        self,
        response: str = "테스트용 LLM 응답입니다.",
        should_fail: bool = False,
    ):
        """
        Args:
            response: 반환할 응답 텍스트
            should_fail: True면 호출 시 예외 발생
        """
        super().__init__(base_url="http://fake-llm:8000")
        self._fake_response = response
        self._should_fail = should_fail
        self._called = False
        self._last_messages = None

    async def generate_chat_completion(
        self,
        messages: list,
        model: str = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> str:
        """Fake generate_chat_completion implementation."""
        self._called = True
        self._last_messages = messages

        if self._should_fail:
            raise ConnectionError("Fake LLM connection error")

        return self._fake_response


class FakeIntentService(IntentService):
    """
    테스트용 Fake IntentService.

    미리 설정된 IntentResult를 반환합니다.
    Phase 10: user_role 필드 추가
    """

    def __init__(
        self,
        intent: IntentType = IntentType.POLICY_QA,
        domain: str = "POLICY",
        route: RouteType = RouteType.RAG_INTERNAL,
        user_role: UserRole = UserRole.EMPLOYEE,
    ):
        self._fake_intent = intent
        self._fake_domain = domain
        self._fake_route = route
        self._fake_user_role = user_role

    def classify(self, req: ChatRequest, user_query: str) -> IntentResult:
        """Fake classify implementation."""
        return IntentResult(
            user_role=self._fake_user_role,
            intent=self._fake_intent,
            domain=self._fake_domain,
            route=self._fake_route,
        )


# =============================================================================
# 테스트 Fixtures
# =============================================================================


@pytest.fixture
def sample_policy_documents() -> List[RagDocument]:
    """POLICY 도메인 샘플 문서들."""
    return [
        RagDocument(
            doc_id="HR-001",
            title="연차휴가 관리 규정",
            page=12,
            score=0.92,
            snippet="연차휴가의 이월은 최대 10일을 초과할 수 없으며, 이월된 연차는 다음 해 6월 30일까지 사용해야 합니다.",
        ),
        RagDocument(
            doc_id="HR-002",
            title="근태관리 지침",
            page=5,
            score=0.85,
            snippet="지각 3회는 결근 1일로 간주되며, 월 5회 이상 지각 시 인사고과에 반영됩니다.",
        ),
    ]


@pytest.fixture
def sample_chat_request() -> ChatRequest:
    """샘플 ChatRequest 객체."""
    return ChatRequest(
        session_id="test-session-001",
        user_id="emp-12345",
        user_role="EMPLOYEE",
        department="개발팀",
        domain="POLICY",
        channel="WEB",
        messages=[
            ChatMessage(role="user", content="연차휴가 이월 규정이 어떻게 되나요?"),
        ],
    )


# =============================================================================
# 시나리오 1: 정상 RAG + POLICY 도메인
# =============================================================================


@pytest.mark.anyio
async def test_chat_service_policy_rag_success(
    sample_policy_documents: List[RagDocument],
    sample_chat_request: ChatRequest,
) -> None:
    """
    시나리오 1: 정상 RAG + POLICY 도메인

    Given:
        - IntentResult(intent=POLICY_QA, domain="POLICY", route=RAG_INTERNAL)
        - RagflowClient → RagDocument 2개 반환

    When:
        - ChatService.handle_chat(ChatRequest(...))

    Then:
        - response.answer != ""
        - len(response.sources) == 2
        - response.meta.rag_used is True
        - response.meta.rag_source_count == 2
        - response.meta.domain == "POLICY"
        - response.meta.route == "RAG_INTERNAL"
    """
    # Arrange
    fake_ragflow = FakeRagflowClient(documents=sample_policy_documents)
    fake_llm = FakeLLMClient(response="연차휴가 이월은 최대 10일까지 가능합니다. (HR-001 참조)")
    fake_intent = FakeIntentService(
        intent=IntentType.POLICY_QA,
        domain="POLICY",
        route=RouteType.RAG_INTERNAL,
    )

    service = ChatService(
        ragflow_client=fake_ragflow,
        llm_client=fake_llm,
        pii_service=PiiService(),  # 실제 PiiService (disabled)
        intent_service=fake_intent,
        ai_log_service=None,  # 로그 서비스는 생략
    )

    # Act
    response = await service.handle_chat(sample_chat_request)

    # Assert
    assert response.answer != ""
    assert "연차휴가" in response.answer or "10일" in response.answer

    assert len(response.sources) == 2
    assert response.sources[0].doc_id == "HR-001"
    assert response.sources[0].title == "연차휴가 관리 규정"
    assert response.sources[0].score == 0.92
    assert response.sources[1].doc_id == "HR-002"

    assert response.meta.rag_used is True
    assert response.meta.rag_source_count == 2
    assert response.meta.domain == "POLICY"
    assert response.meta.route == "RAG_INTERNAL"
    assert response.meta.intent == "POLICY_QA"
    assert response.meta.latency_ms is not None
    assert response.meta.latency_ms >= 0

    # Verify fake clients were called
    assert fake_ragflow._search_called is True
    assert fake_ragflow._last_domain == "POLICY"
    assert fake_llm._called is True


@pytest.mark.anyio
async def test_chat_service_policy_single_document(
    sample_chat_request: ChatRequest,
) -> None:
    """
    시나리오 1-B: 단일 문서 반환 케이스

    RagflowClient가 1개의 문서만 반환하는 경우.
    """
    # Arrange
    single_doc = [
        RagDocument(
            doc_id="HR-001",
            title="연차휴가 관리 규정",
            page=12,
            score=0.92,
            snippet="연차휴가의 이월은 최대 10일을 초과할 수 없습니다.",
        ),
    ]

    fake_ragflow = FakeRagflowClient(documents=single_doc)
    fake_llm = FakeLLMClient(response="연차휴가 이월 규정에 대한 답변입니다.")
    fake_intent = FakeIntentService(
        intent=IntentType.POLICY_QA,
        domain="POLICY",
        route=RouteType.RAG_INTERNAL,
    )

    service = ChatService(
        ragflow_client=fake_ragflow,
        llm_client=fake_llm,
        intent_service=fake_intent,
    )

    # Act
    response = await service.handle_chat(sample_chat_request)

    # Assert
    assert len(response.sources) == 1
    assert response.meta.rag_used is True
    assert response.meta.rag_source_count == 1


# =============================================================================
# 시나리오 2: RAG 결과 없음
# =============================================================================


@pytest.mark.anyio
async def test_chat_service_no_rag_results(
    sample_chat_request: ChatRequest,
) -> None:
    """
    시나리오 2: RAG 결과 없음

    Given:
        - RagflowClient.search → 빈 리스트 반환

    Then:
        - response.sources == []
        - meta.rag_used == False
        - meta.rag_source_count == 0
        - answer에 fallback 안내 문구 포함
    """
    # Arrange
    fake_ragflow = FakeRagflowClient(documents=[])  # 빈 결과
    fake_llm = FakeLLMClient(response="일반적인 답변입니다.")
    fake_intent = FakeIntentService(
        intent=IntentType.POLICY_QA,
        domain="POLICY",
        route=RouteType.RAG_INTERNAL,
    )

    service = ChatService(
        ragflow_client=fake_ragflow,
        llm_client=fake_llm,
        intent_service=fake_intent,
    )

    # Act
    response = await service.handle_chat(sample_chat_request)

    # Assert
    assert response.sources == []
    assert response.meta.rag_used is False
    assert response.meta.rag_source_count == 0
    assert response.answer != ""

    # RAG 결과 없을 때 안내 문구가 포함되어야 함
    assert "관련 문서를 찾지 못" in response.answer or "담당 부서" in response.answer


# =============================================================================
# 시나리오 3: RAG 호출 실패 (Fallback)
# =============================================================================


@pytest.mark.anyio
async def test_chat_service_rag_failure_fallback(
    sample_chat_request: ChatRequest,
) -> None:
    """
    시나리오 3: RAG 호출 실패 시 Fallback

    Given:
        - RagflowClient.search에서 예외 발생

    Then:
        - RAG 없이 LLM-only로 진행
        - response.sources == []
        - meta.rag_used == False
        - meta.rag_source_count == 0
        - answer는 빈 문자열이 아님 (LLM 응답)
    """
    # Arrange
    fake_ragflow = FakeRagflowClient(documents=[], should_fail=True)
    fake_llm = FakeLLMClient(response="RAG 없이 생성된 응답입니다.")
    fake_intent = FakeIntentService(
        intent=IntentType.POLICY_QA,
        domain="POLICY",
        route=RouteType.RAG_INTERNAL,
    )

    service = ChatService(
        ragflow_client=fake_ragflow,
        llm_client=fake_llm,
        intent_service=fake_intent,
    )

    # Act
    response = await service.handle_chat(sample_chat_request)

    # Assert
    assert response.sources == []
    assert response.meta.rag_used is False
    assert response.meta.rag_source_count == 0
    assert response.answer != ""
    assert "RAG 없이 생성된 응답" in response.answer

    # RAG 실패해도 route는 원래 의도대로 유지
    assert response.meta.route == "RAG_INTERNAL"


# =============================================================================
# 시나리오 4: LLM 호출 실패
# =============================================================================


@pytest.mark.anyio
async def test_chat_service_llm_failure(
    sample_policy_documents: List[RagDocument],
    sample_chat_request: ChatRequest,
) -> None:
    """
    시나리오 4: LLM 호출 실패

    Given:
        - RAG 검색은 성공
        - LLM 호출에서 예외 발생

    Then:
        - 에러 메시지가 answer에 포함
        - meta.route == "ERROR"
    """
    # Arrange
    fake_ragflow = FakeRagflowClient(documents=sample_policy_documents)
    fake_llm = FakeLLMClient(response="", should_fail=True)
    fake_intent = FakeIntentService(
        intent=IntentType.POLICY_QA,
        domain="POLICY",
        route=RouteType.RAG_INTERNAL,
    )

    service = ChatService(
        ragflow_client=fake_ragflow,
        llm_client=fake_llm,
        intent_service=fake_intent,
    )

    # Act
    response = await service.handle_chat(sample_chat_request)

    # Assert
    assert "일시적인 문제" in response.answer or "다시 시도" in response.answer
    assert response.meta.route == "ERROR"


# =============================================================================
# 시나리오 5: LLM_ONLY (RAG 스킵)
# =============================================================================


@pytest.mark.anyio
async def test_chat_service_llm_only_route(
    sample_chat_request: ChatRequest,
) -> None:
    """
    시나리오 5: LLM_ONLY 라우팅

    Given:
        - IntentService가 route=LLM_ONLY 반환
        - RouterOrchestrator 비활성화

    Then:
        - RAG 검색이 호출되지 않음
        - response.sources == []
        - meta.rag_used == False
    """
    # Arrange
    fake_ragflow = FakeRagflowClient(documents=[])
    fake_llm = FakeLLMClient(response="일반 대화 응답입니다.")
    fake_intent = FakeIntentService(
        intent=IntentType.GENERAL_CHAT,
        domain="GENERAL",
        route=RouteType.LLM_ONLY,
    )

    # Phase 22: RouterOrchestrator 비활성화 (ROUTER_ORCHESTRATOR_ENABLED=False)
    # IntentService의 LLM_ONLY 결과가 그대로 사용되도록 함
    with patch("app.services.chat_service.get_settings") as mock_settings:
        mock_settings.return_value.ROUTER_ORCHESTRATOR_ENABLED = False
        mock_settings.return_value.llm_base_url = ""
        mock_settings.return_value.MILVUS_ENABLED = False

        service = ChatService(
            ragflow_client=fake_ragflow,
            llm_client=fake_llm,
            intent_service=fake_intent,
        )

        # Act
        response = await service.handle_chat(sample_chat_request)

    # Assert
    assert response.sources == []
    assert response.meta.rag_used is False
    assert response.meta.rag_source_count == 0
    assert response.meta.route == "LLM_ONLY"

    # RAG 검색이 호출되지 않았어야 함
    assert fake_ragflow._search_called is False


# =============================================================================
# 시나리오 6: 메타 필드 완전성 검증
# =============================================================================


@pytest.mark.anyio
async def test_chat_response_meta_completeness(
    sample_policy_documents: List[RagDocument],
    sample_chat_request: ChatRequest,
) -> None:
    """
    시나리오 6: ChatResponse.meta 필드 완전성 검증

    모든 메타 필드가 올바르게 설정되는지 확인.
    """
    # Arrange
    fake_ragflow = FakeRagflowClient(documents=sample_policy_documents)
    fake_llm = FakeLLMClient(response="테스트 응답")
    fake_intent = FakeIntentService(
        intent=IntentType.POLICY_QA,
        domain="POLICY",
        route=RouteType.RAG_INTERNAL,
    )

    service = ChatService(
        ragflow_client=fake_ragflow,
        llm_client=fake_llm,
        intent_service=fake_intent,
    )

    # Act
    response = await service.handle_chat(sample_chat_request)

    # Assert - 모든 meta 필드 검증
    meta = response.meta

    # 필수 필드들
    assert meta.used_model is not None
    assert meta.route == "RAG_INTERNAL"
    assert meta.intent == "POLICY_QA"
    assert meta.domain == "POLICY"

    # PII 관련 필드
    assert meta.masked is not None
    assert meta.has_pii_input is not None
    assert meta.has_pii_output is not None

    # RAG 관련 필드
    assert meta.rag_used is True
    assert meta.rag_source_count == 2

    # 성능 필드
    assert meta.latency_ms is not None
    assert isinstance(meta.latency_ms, int)
    assert meta.latency_ms >= 0


# =============================================================================
# 시나리오 7: 빈 메시지 처리
# =============================================================================


@pytest.mark.anyio
async def test_chat_service_empty_messages() -> None:
    """
    시나리오 7: 빈 메시지 요청 처리

    messages가 빈 리스트인 경우 fallback 응답 반환.
    """
    # Arrange
    request = ChatRequest(
        session_id="test-session",
        user_id="emp-12345",
        user_role="EMPLOYEE",
        messages=[],  # 빈 메시지
    )

    service = ChatService()

    # Act
    response = await service.handle_chat(request)

    # Assert
    assert "No messages" in response.answer
    assert response.meta.route == "FALLBACK"
    assert response.sources == []


# =============================================================================
# 시나리오 8: ChatSource 매핑 검증
# =============================================================================


@pytest.mark.anyio
async def test_rag_document_to_chat_source_mapping(
    sample_policy_documents: List[RagDocument],
    sample_chat_request: ChatRequest,
) -> None:
    """
    시나리오 8: RagDocument → ChatSource 매핑 검증

    RagDocument의 필드가 ChatSource로 올바르게 변환되는지 확인.
    """
    # Arrange
    fake_ragflow = FakeRagflowClient(documents=sample_policy_documents)
    fake_llm = FakeLLMClient(response="테스트 응답")
    fake_intent = FakeIntentService(
        intent=IntentType.POLICY_QA,
        domain="POLICY",
        route=RouteType.RAG_INTERNAL,
    )

    service = ChatService(
        ragflow_client=fake_ragflow,
        llm_client=fake_llm,
        intent_service=fake_intent,
    )

    # Act
    response = await service.handle_chat(sample_chat_request)

    # Assert - 첫 번째 source 상세 검증
    source = response.sources[0]
    original = sample_policy_documents[0]

    assert source.doc_id == original.doc_id
    assert source.title == original.title
    assert source.page == original.page
    assert source.score == original.score
    assert source.snippet == original.snippet
