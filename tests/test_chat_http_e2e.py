"""
Chat HTTP E2E Test Module (Phase 7)

FastAPI /ai/chat/messages 엔드포인트를 직접 호출하는 E2E 테스트입니다.

테스트 목표:
- HTTP 레벨에서 전체 파이프라인 검증
- PII (입력/출력/로그), Intent/Route, RAG, LLM, AiLog가 한 번의 요청에서 연결되는지 확인
- Fake 서비스 주입을 통한 deterministic 테스트

테스트 시나리오:
1. POLICY 도메인 + RAG + LLM + PII + 로그 해피패스
2. POLICY 도메인 + RAG 결과 0건 + fallback + 로그
3. ROUTE_LLM_ONLY (일반 질문) + PII + 로그

구조:
- FastAPI TestClient 사용
- dependency_overrides로 ChatService에 Fake 의존성 주입
"""

import re
from typing import Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from app.api.v1.chat import get_chat_service
from app.clients.llm_client import LLMClient
from app.clients.ragflow_client import RagflowClient
from app.main import app
from app.models.ai_log import AILogEntry
from app.models.chat import ChatMessage, ChatRequest, ChatSource
from app.models.intent import (
    IntentResult,
    IntentType,
    MaskingStage,
    PiiMaskResult,
    PiiTag,
    RouteType,
)
from app.models.rag import RagDocument
from app.services.ai_log_service import AILogService
from app.services.chat_service import ChatService
from app.services.intent_service import IntentService
from app.services.pii_service import PiiService


# =============================================================================
# Fake 클래스 정의
# =============================================================================


class FakePiiService(PiiService):
    """
    테스트용 Fake PiiService.

    간단한 규칙 기반 마스킹을 수행하고, 호출 기록을 저장합니다.
    - 전화번호 패턴: 010-XXXX-XXXX → [PHONE]
    - 이메일 패턴: xxx@xxx.xxx → [EMAIL]
    """

    # 전화번호/이메일 정규식
    PHONE_PATTERN = re.compile(r"01[0-9]-\d{3,4}-\d{4}")
    EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

    def __init__(self) -> None:
        # 부모 초기화 없이 독립적으로 동작
        self._enabled = True
        self._base_url = "http://fake-pii:8003"
        self._client = None

        # 호출 기록
        self.input_calls: List[str] = []
        self.output_calls: List[str] = []
        self.log_calls: List[str] = []
        self.all_results: List[PiiMaskResult] = []

    async def detect_and_mask(
        self,
        text: str,
        stage: MaskingStage,
    ) -> PiiMaskResult:
        """간단한 규칙 기반 마스킹."""
        # 호출 기록
        if stage == MaskingStage.INPUT:
            self.input_calls.append(text)
        elif stage == MaskingStage.OUTPUT:
            self.output_calls.append(text)
        elif stage == MaskingStage.LOG:
            self.log_calls.append(text)

        # 마스킹 수행
        masked_text = text
        tags: List[PiiTag] = []
        has_pii = False

        # 전화번호 마스킹
        for match in self.PHONE_PATTERN.finditer(text):
            has_pii = True
            tags.append(
                PiiTag(
                    entity=match.group(),
                    label="PHONE",
                    start=match.start(),
                    end=match.end(),
                )
            )
        masked_text = self.PHONE_PATTERN.sub("[PHONE]", masked_text)

        # 이메일 마스킹
        for match in self.EMAIL_PATTERN.finditer(text):
            has_pii = True
            tags.append(
                PiiTag(
                    entity=match.group(),
                    label="EMAIL",
                    start=match.start(),
                    end=match.end(),
                )
            )
        masked_text = self.EMAIL_PATTERN.sub("[EMAIL]", masked_text)

        result = PiiMaskResult(
            original_text=text,
            masked_text=masked_text,
            has_pii=has_pii,
            tags=tags,
        )
        self.all_results.append(result)
        return result

    def has_pii_in_text(self, text: str) -> bool:
        """텍스트에 원본 PII가 포함되어 있는지 확인."""
        return bool(self.PHONE_PATTERN.search(text) or self.EMAIL_PATTERN.search(text))


class FakeIntentService(IntentService):
    """
    테스트용 Fake IntentService.

    미리 설정된 IntentResult를 반환합니다.
    """

    def __init__(
        self,
        intent: IntentType = IntentType.POLICY_QA,
        domain: str = "POLICY",
        route: RouteType = RouteType.ROUTE_RAG_INTERNAL,
    ):
        self._fake_intent = intent
        self._fake_domain = domain
        self._fake_route = route
        self.call_count = 0
        self.last_query: Optional[str] = None

    def classify(self, req: ChatRequest, user_query: str) -> IntentResult:
        """미리 설정된 결과 반환."""
        self.call_count += 1
        self.last_query = user_query
        return IntentResult(
            intent=self._fake_intent,
            domain=self._fake_domain,
            route=self._fake_route,
        )


class FakeRagflowClient(RagflowClient):
    """
    테스트용 Fake RagflowClient.

    미리 설정된 문서를 반환하거나, 호출되면 안 되는 시나리오에서 예외 발생.
    """

    def __init__(
        self,
        documents: Optional[List[RagDocument]] = None,
        should_fail: bool = False,
        fail_if_called: bool = False,
    ):
        super().__init__(base_url="http://fake-ragflow:8000")
        self._fake_documents = documents or []
        self._should_fail = should_fail
        self._fail_if_called = fail_if_called
        self.call_count = 0
        self.last_query: Optional[str] = None
        self.last_domain: Optional[str] = None

    async def search(
        self,
        query: str,
        top_k: int = 5,
        dataset: Optional[str] = None,
        domain: Optional[str] = None,
        user_role: Optional[str] = None,
        department: Optional[str] = None,
    ) -> List[RagDocument]:
        """Fake search implementation."""
        self.call_count += 1
        self.last_query = query
        self.last_domain = dataset or domain

        if self._fail_if_called:
            raise AssertionError(
                "RagflowClient.search should not be called in this scenario"
            )

        if self._should_fail:
            raise ConnectionError("Fake RAGFlow connection error")

        return self._fake_documents[:top_k]

    async def search_as_sources(
        self,
        query: str,
        domain: Optional[str],
        user_role: str,
        department: Optional[str],
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

    미리 설정된 응답을 반환하고 호출 기록을 저장합니다.
    """

    def __init__(
        self,
        response: str = "테스트용 LLM 응답입니다.",
        should_fail: bool = False,
    ):
        super().__init__(base_url="http://fake-llm:8000")
        self._fake_response = response
        self._should_fail = should_fail
        self.call_count = 0
        self.last_messages: Optional[List[Dict[str, str]]] = None

    async def generate_chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> str:
        """Fake generate_chat_completion implementation."""
        self.call_count += 1
        self.last_messages = messages

        if self._should_fail:
            raise ConnectionError("Fake LLM connection error")

        return self._fake_response


class FakeAILogService(AILogService):
    """
    테스트용 Fake AILogService.

    실제 백엔드 호출 없이 로그를 메모리에 저장합니다.
    """

    def __init__(self, pii_service: Optional[PiiService] = None):
        super().__init__(pii_service=pii_service)
        self.logs: List[AILogEntry] = []
        self.call_count = 0

    async def send_log(self, log_entry: AILogEntry) -> bool:
        """로그를 메모리에 저장."""
        self.call_count += 1
        self.logs.append(log_entry)
        return True

    async def send_log_async(self, log_entry: AILogEntry) -> None:
        """동기적으로 로그 저장 (테스트 검증 용이성)."""
        await self.send_log(log_entry)

    @property
    def last_log(self) -> Optional[AILogEntry]:
        """가장 최근 로그 반환."""
        return self.logs[-1] if self.logs else None


# =============================================================================
# 테스트 Fixtures
# =============================================================================


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


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
    ]


def create_test_chat_service(
    pii_service: PiiService,
    intent_service: IntentService,
    ragflow_client: RagflowClient,
    llm_client: LLMClient,
    ai_log_service: AILogService,
) -> ChatService:
    """테스트용 ChatService 생성."""
    return ChatService(
        ragflow_client=ragflow_client,
        llm_client=llm_client,
        pii_service=pii_service,
        intent_service=intent_service,
        ai_log_service=ai_log_service,
    )


# =============================================================================
# 시나리오 1: POLICY 도메인 + RAG + LLM + PII + 로그 해피패스
# =============================================================================


def test_e2e_policy_with_pii_rag_llm_and_logging(
    sample_policy_documents: List[RagDocument],
) -> None:
    """
    시나리오 1: POLICY 도메인, RAG + LLM + PII + 로그 해피패스

    사용자 질문에 PII(전화번호)가 포함된 POLICY 관련 질문.
    - PII가 INPUT 단계에서 마스킹되어 RAG/LLM에 전달
    - RAG 검색 결과가 sources에 포함
    - LLM 응답이 answer에 포함
    - 로그에 PII 원문이 포함되지 않음
    """
    # Arrange - Fake 서비스들 생성
    fake_pii = FakePiiService()
    fake_intent = FakeIntentService(
        intent=IntentType.POLICY_QA,
        domain="POLICY",
        route=RouteType.ROUTE_RAG_INTERNAL,
    )
    fake_rag = FakeRagflowClient(documents=sample_policy_documents)
    fake_llm = FakeLLMClient(
        response="연차휴가 이월은 최대 10일까지 가능합니다. (HR-001 참조)"
    )
    fake_log = FakeAILogService(pii_service=fake_pii)

    # ChatService 생성
    test_service = create_test_chat_service(
        pii_service=fake_pii,
        intent_service=fake_intent,
        ragflow_client=fake_rag,
        llm_client=fake_llm,
        ai_log_service=fake_log,
    )

    # FastAPI dependency override
    app.dependency_overrides[get_chat_service] = lambda: test_service

    try:
        client = TestClient(app)

        # Act - HTTP 요청
        response = client.post(
            "/ai/chat/messages",
            json={
                "session_id": "test-session-001",
                "user_id": "emp-12345",
                "user_role": "EMPLOYEE",
                "department": "개발팀",
                "domain": "POLICY",
                "channel": "WEB",
                "messages": [
                    {
                        "role": "user",
                        "content": "제 전화번호 010-1234-5678 남기고 연차 이월 규정 알려줘",
                    }
                ],
            },
        )

        # Assert - HTTP 레벨
        assert response.status_code == 200
        data = response.json()
        assert "answer" in data
        assert "sources" in data
        assert "meta" in data
        assert isinstance(data["answer"], str)
        assert isinstance(data["sources"], list)

        # Assert - PII INPUT 마스킹
        assert len(fake_pii.input_calls) >= 1
        # RAG에 전달된 쿼리에 원본 전화번호가 없어야 함
        assert fake_rag.call_count == 1
        assert "010-1234-5678" not in fake_rag.last_query
        assert "[PHONE]" in fake_rag.last_query

        # Assert - LLM에 전달된 메시지에 원본 PII 없음
        assert fake_llm.call_count == 1
        llm_messages_str = str(fake_llm.last_messages)
        assert "010-1234-5678" not in llm_messages_str

        # Assert - 최종 응답에 원본 PII 없음
        assert "010-1234-5678" not in data["answer"]

        # Assert - RAG + LLM 결과
        assert len(data["sources"]) == 1
        assert data["sources"][0]["doc_id"] == "HR-001"
        assert "연차휴가" in data["answer"] or "10일" in data["answer"]

        # Assert - meta 필드
        meta = data["meta"]
        assert meta["rag_used"] is True
        assert meta["rag_source_count"] == 1
        assert meta["domain"] == "POLICY"
        assert meta["route"] == "ROUTE_RAG_INTERNAL"
        assert meta["intent"] == "POLICY_QA"
        assert meta["has_pii_input"] is True
        assert meta["latency_ms"] is not None

        # Assert - AI 로그
        assert fake_log.call_count >= 1
        log_entry = fake_log.last_log
        assert log_entry is not None
        assert log_entry.rag_used is True
        assert log_entry.rag_source_count == 1
        assert log_entry.has_pii_input is True
        # 로그의 question_masked에 원본 PII 없음
        if log_entry.question_masked:
            assert "010-1234-5678" not in log_entry.question_masked

    finally:
        # Cleanup
        app.dependency_overrides.clear()


# =============================================================================
# 시나리오 2: POLICY 도메인 + RAG 결과 0건 + fallback + 로그
# =============================================================================


def test_e2e_policy_rag_no_results_fallback_and_logging() -> None:
    """
    시나리오 2: POLICY 도메인, RAG 결과 0건 + fallback + 로그

    RAG 검색 결과가 없는 경우 fallback 처리.
    - meta.rag_used == False
    - meta.rag_source_count == 0
    - answer에 fallback 안내 문구 포함
    """
    # Arrange
    fake_pii = FakePiiService()
    fake_intent = FakeIntentService(
        intent=IntentType.POLICY_QA,
        domain="POLICY",
        route=RouteType.ROUTE_RAG_INTERNAL,
    )
    fake_rag = FakeRagflowClient(documents=[])  # 빈 결과
    fake_llm = FakeLLMClient(
        response="해당 내용에 대한 구체적인 사내 규정을 찾지 못했습니다."
    )
    fake_log = FakeAILogService(pii_service=fake_pii)

    test_service = create_test_chat_service(
        pii_service=fake_pii,
        intent_service=fake_intent,
        ragflow_client=fake_rag,
        llm_client=fake_llm,
        ai_log_service=fake_log,
    )

    app.dependency_overrides[get_chat_service] = lambda: test_service

    try:
        client = TestClient(app)

        # Act
        response = client.post(
            "/ai/chat/messages",
            json={
                "session_id": "test-session-002",
                "user_id": "emp-12345",
                "user_role": "EMPLOYEE",
                "domain": "POLICY",
                "channel": "WEB",
                "messages": [
                    {
                        "role": "user",
                        "content": "우리 회사에 연차를 현금으로 정산해달라고 할 수 있나요?",
                    }
                ],
            },
        )

        # Assert - HTTP 레벨
        assert response.status_code == 200
        data = response.json()

        # Assert - RAG 결과 없음
        assert data["sources"] == []
        meta = data["meta"]
        assert meta["rag_used"] is False
        assert meta["rag_source_count"] == 0
        assert meta["route"] == "ROUTE_RAG_INTERNAL"

        # Assert - Fallback 안내 문구
        assert data["answer"] != ""
        # fallback 안내가 포함되어야 함
        assert (
            "관련 문서를 찾지 못" in data["answer"]
            or "담당 부서" in data["answer"]
            or "찾지 못했습니다" in data["answer"]
        )

        # Assert - RAG 호출됨 (결과만 없음)
        assert fake_rag.call_count == 1

        # Assert - 로그
        assert fake_log.call_count >= 1
        log_entry = fake_log.last_log
        assert log_entry.rag_used is False
        assert log_entry.rag_source_count == 0

    finally:
        app.dependency_overrides.clear()


# =============================================================================
# 시나리오 3: ROUTE_LLM_ONLY (일반 질문) + PII + 로그
# =============================================================================


def test_e2e_llm_only_route_with_pii_and_logging() -> None:
    """
    시나리오 3: ROUTE_LLM_ONLY (일반/헬프성 질문) + PII + 로그

    RAG 검색을 하지 않고 LLM만으로 응답하는 케이스.
    - RagflowClient가 호출되지 않아야 함
    - meta.rag_used == False
    - meta.route == ROUTE_LLM_ONLY
    """
    # Arrange
    fake_pii = FakePiiService()
    fake_intent = FakeIntentService(
        intent=IntentType.GENERAL_CHAT,
        domain="GENERAL",
        route=RouteType.ROUTE_LLM_ONLY,
    )
    # RAG 호출되면 테스트 실패하도록 설정
    fake_rag = FakeRagflowClient(documents=[], fail_if_called=True)
    fake_llm = FakeLLMClient(
        response="안녕하세요! 정보보호 교육 일정은 사내 공지사항을 확인해주세요."
    )
    fake_log = FakeAILogService(pii_service=fake_pii)

    test_service = create_test_chat_service(
        pii_service=fake_pii,
        intent_service=fake_intent,
        ragflow_client=fake_rag,
        llm_client=fake_llm,
        ai_log_service=fake_log,
    )

    app.dependency_overrides[get_chat_service] = lambda: test_service

    try:
        client = TestClient(app)

        # Act - PII(이메일) 포함된 일반 질문
        response = client.post(
            "/ai/chat/messages",
            json={
                "session_id": "test-session-003",
                "user_id": "emp-12345",
                "user_role": "EMPLOYEE",
                "domain": None,
                "channel": "WEB",
                "messages": [
                    {
                        "role": "user",
                        "content": "제 이메일 test@example.com으로 정보보호 교육 일정 알려줄 수 있어?",
                    }
                ],
            },
        )

        # Assert - HTTP 레벨
        assert response.status_code == 200
        data = response.json()

        # Assert - RAG 미사용
        assert data["sources"] == []
        meta = data["meta"]
        assert meta["rag_used"] is False
        assert meta["rag_source_count"] == 0
        assert meta["route"] == "ROUTE_LLM_ONLY"
        assert meta["intent"] == "GENERAL_CHAT"

        # Assert - RagflowClient 호출 안 됨
        assert fake_rag.call_count == 0

        # Assert - LLM 호출됨
        assert fake_llm.call_count == 1
        assert data["answer"] != ""

        # Assert - PII 마스킹 (이메일)
        assert len(fake_pii.input_calls) >= 1
        # LLM에 전달된 메시지에 원본 이메일 없음
        llm_messages_str = str(fake_llm.last_messages)
        assert "test@example.com" not in llm_messages_str
        assert "[EMAIL]" in llm_messages_str

        # Assert - 최종 응답에 원본 PII 없음
        assert "test@example.com" not in data["answer"]

        # Assert - 로그
        assert fake_log.call_count >= 1
        log_entry = fake_log.last_log
        assert log_entry.rag_used is False
        assert log_entry.route == "ROUTE_LLM_ONLY"
        # 로그에 원본 이메일 없음
        if log_entry.question_masked:
            assert "test@example.com" not in log_entry.question_masked

    finally:
        app.dependency_overrides.clear()


# =============================================================================
# 추가 시나리오: RAG 에러 시 LLM-only fallback
# =============================================================================


def test_e2e_rag_error_fallback_to_llm_only() -> None:
    """
    추가 시나리오: RAG 호출 에러 시 LLM-only fallback

    RAG 서비스 장애 상황에서도 LLM 응답은 정상 반환.
    """
    # Arrange
    fake_pii = FakePiiService()
    fake_intent = FakeIntentService(
        intent=IntentType.POLICY_QA,
        domain="POLICY",
        route=RouteType.ROUTE_RAG_INTERNAL,
    )
    fake_rag = FakeRagflowClient(documents=[], should_fail=True)  # 에러 발생
    fake_llm = FakeLLMClient(response="RAG 없이 생성된 일반 답변입니다.")
    fake_log = FakeAILogService(pii_service=fake_pii)

    test_service = create_test_chat_service(
        pii_service=fake_pii,
        intent_service=fake_intent,
        ragflow_client=fake_rag,
        llm_client=fake_llm,
        ai_log_service=fake_log,
    )

    app.dependency_overrides[get_chat_service] = lambda: test_service

    try:
        client = TestClient(app)

        # Act
        response = client.post(
            "/ai/chat/messages",
            json={
                "session_id": "test-session-004",
                "user_id": "emp-12345",
                "user_role": "EMPLOYEE",
                "domain": "POLICY",
                "channel": "WEB",
                "messages": [
                    {"role": "user", "content": "연차휴가 규정 알려줘"}
                ],
            },
        )

        # Assert
        assert response.status_code == 200
        data = response.json()

        # RAG 에러로 sources 없음
        assert data["sources"] == []
        meta = data["meta"]
        assert meta["rag_used"] is False
        assert meta["rag_source_count"] == 0
        # route는 원래 의도대로 유지
        assert meta["route"] == "ROUTE_RAG_INTERNAL"

        # LLM 응답은 정상
        assert data["answer"] != ""
        assert "RAG 없이" in data["answer"]

    finally:
        app.dependency_overrides.clear()


# =============================================================================
# 추가 시나리오: 응답 스키마 완전성 검증
# =============================================================================


def test_e2e_response_schema_completeness(
    sample_policy_documents: List[RagDocument],
) -> None:
    """
    응답 스키마 완전성 검증

    ChatResponse의 모든 필드가 올바르게 반환되는지 확인.
    """
    # Arrange
    fake_pii = FakePiiService()
    fake_intent = FakeIntentService()
    fake_rag = FakeRagflowClient(documents=sample_policy_documents)
    fake_llm = FakeLLMClient(response="테스트 응답")
    fake_log = FakeAILogService(pii_service=fake_pii)

    test_service = create_test_chat_service(
        pii_service=fake_pii,
        intent_service=fake_intent,
        ragflow_client=fake_rag,
        llm_client=fake_llm,
        ai_log_service=fake_log,
    )

    app.dependency_overrides[get_chat_service] = lambda: test_service

    try:
        client = TestClient(app)

        response = client.post(
            "/ai/chat/messages",
            json={
                "session_id": "test-session-005",
                "user_id": "emp-12345",
                "user_role": "EMPLOYEE",
                "messages": [{"role": "user", "content": "테스트 질문"}],
            },
        )

        assert response.status_code == 200
        data = response.json()

        # 최상위 필드
        assert "answer" in data
        assert "sources" in data
        assert "meta" in data

        # sources 필드 (있는 경우)
        if data["sources"]:
            source = data["sources"][0]
            assert "doc_id" in source
            assert "title" in source
            # page, score, snippet은 optional

        # meta 필드
        meta = data["meta"]
        assert "used_model" in meta
        assert "route" in meta
        assert "intent" in meta
        assert "domain" in meta
        assert "masked" in meta
        assert "has_pii_input" in meta
        assert "has_pii_output" in meta
        assert "rag_used" in meta
        assert "rag_source_count" in meta
        assert "latency_ms" in meta

    finally:
        app.dependency_overrides.clear()
