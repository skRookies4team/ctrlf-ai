"""
Phase 11: BACKEND_API / MIXED_BACKEND_RAG 통합 테스트

이 모듈은 Phase 11에서 구현된 백엔드 데이터 통합 기능을 테스트합니다.

테스트 항목:
1. BackendDataClient 단위 테스트
2. BackendContextFormatter 단위 테스트
3. ChatService BACKEND_API 라우트 테스트
4. ChatService MIXED_BACKEND_RAG 라우트 테스트
5. 역할×도메인×의도별 백엔드 메서드 매핑 테스트
"""

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.clients.backend_client import (
    BackendDataClient,
    BackendDataResponse,
    BACKEND_EDU_STATUS_PATH,
    BACKEND_EDU_STATS_PATH,
    BACKEND_INCIDENT_OVERVIEW_PATH,
)
from app.clients.llm_client import LLMClient
from app.clients.ragflow_client import RagflowClient
from app.models.chat import ChatMessage, ChatRequest, ChatResponse, ChatSource
from app.models.intent import IntentType, RouteType, UserRole
from app.services.backend_context_formatter import BackendContextFormatter
from app.services.chat_service import ChatService
from app.services.guardrail_service import GuardrailService
from app.services.intent_service import IntentService
from app.services.pii_service import PiiService


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


# =============================================================================
# 1. BackendDataClient 단위 테스트
# =============================================================================


def test_backend_data_client_not_configured() -> None:
    """BackendDataClient URL 미설정 시 is_configured=False."""
    with patch("app.clients.backend_client.get_settings") as mock_get_settings:
        mock_settings = MagicMock()
        mock_settings.backend_base_url = ""
        mock_settings.BACKEND_API_TOKEN = ""
        mock_settings.BACKEND_INTERNAL_TOKEN = ""
        mock_settings.BACKEND_TIMEOUT_SEC = 30
        mock_get_settings.return_value = mock_settings

        client = BackendDataClient(base_url="")
        assert client.is_configured is False


def test_backend_data_client_configured() -> None:
    """BackendDataClient URL 설정 시 is_configured=True."""
    client = BackendDataClient(base_url="http://localhost:8080")
    assert client.is_configured is True


@pytest.mark.anyio
async def test_backend_data_client_mock_edu_status() -> None:
    """BackendDataClient URL 미설정 시 mock 데이터 반환."""
    with patch("app.clients.backend_client.get_settings") as mock_get_settings:
        mock_settings = MagicMock()
        mock_settings.backend_base_url = ""
        mock_settings.BACKEND_API_TOKEN = ""
        mock_settings.BACKEND_INTERNAL_TOKEN = ""
        mock_settings.BACKEND_TIMEOUT_SEC = 30
        mock_get_settings.return_value = mock_settings

        client = BackendDataClient(base_url="")

        result = await client.get_employee_edu_status("user-123")

        assert result.success is True
        assert "user_id" in result.data
        assert result.data["user_id"] == "user-123"
        assert "total_required" in result.data
        assert "courses" in result.data


@pytest.mark.anyio
async def test_backend_data_client_mock_dept_stats() -> None:
    """부서 교육 통계 mock 데이터 반환."""
    with patch("app.clients.backend_client.get_settings") as mock_get_settings:
        mock_settings = MagicMock()
        mock_settings.backend_base_url = ""
        mock_settings.BACKEND_API_TOKEN = ""
        mock_settings.BACKEND_INTERNAL_TOKEN = ""
        mock_settings.BACKEND_TIMEOUT_SEC = 30
        mock_get_settings.return_value = mock_settings

        client = BackendDataClient(base_url="")

        result = await client.get_department_edu_stats("dept-001")

        assert result.success is True
        assert "completion_rate" in result.data
        assert "by_course" in result.data


@pytest.mark.anyio
async def test_backend_data_client_mock_incident_overview() -> None:
    """사고 현황 mock 데이터 반환."""
    with patch("app.clients.backend_client.get_settings") as mock_get_settings:
        mock_settings = MagicMock()
        mock_settings.backend_base_url = ""
        mock_settings.BACKEND_API_TOKEN = ""
        mock_settings.BACKEND_INTERNAL_TOKEN = ""
        mock_settings.BACKEND_TIMEOUT_SEC = 30
        mock_get_settings.return_value = mock_settings

        client = BackendDataClient(base_url="")

        result = await client.get_incident_overview()

        assert result.success is True
        assert "total_incidents" in result.data
        assert "by_status" in result.data
        assert "by_type" in result.data


@pytest.mark.anyio
async def test_backend_data_client_mock_report_guide() -> None:
    """신고 안내 mock 데이터 반환."""
    with patch("app.clients.backend_client.get_settings") as mock_get_settings:
        mock_settings = MagicMock()
        mock_settings.backend_base_url = ""
        mock_settings.BACKEND_API_TOKEN = ""
        mock_settings.BACKEND_INTERNAL_TOKEN = ""
        mock_settings.BACKEND_TIMEOUT_SEC = 30
        mock_get_settings.return_value = mock_settings

        client = BackendDataClient(base_url="")

        result = await client.get_report_guide()

        assert result.success is True
        assert "steps" in result.data
        assert "official_channels" in result.data


@pytest.mark.anyio
async def test_backend_data_client_http_mock_transport_success() -> None:
    """BackendDataClient HTTP 성공 응답 테스트 (MockTransport 사용)."""
    mock_response = {
        "user_id": "user-123",
        "total_required": 4,
        "completed": 3,
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_response)

    mock_transport = httpx.MockTransport(mock_handler)
    mock_client = httpx.AsyncClient(transport=mock_transport)

    with patch("app.clients.backend_client.get_async_http_client", return_value=mock_client):
        client = BackendDataClient(base_url="http://test-backend:8080")
        result = await client.get_employee_edu_status("user-123")

        assert result.success is True
        assert result.data["user_id"] == "user-123"


@pytest.mark.anyio
async def test_backend_data_client_http_mock_transport_error() -> None:
    """BackendDataClient HTTP 에러 응답 테스트 (MockTransport 사용)."""

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    mock_transport = httpx.MockTransport(mock_handler)
    mock_client = httpx.AsyncClient(transport=mock_transport)

    with patch("app.clients.backend_client.get_async_http_client", return_value=mock_client):
        client = BackendDataClient(base_url="http://test-backend:8080")
        result = await client.get_employee_edu_status("user-123")

        assert result.success is False
        assert "500" in result.error_message


# =============================================================================
# 2. BackendContextFormatter 단위 테스트
# =============================================================================


def test_formatter_edu_status() -> None:
    """교육 현황 데이터 포맷팅 테스트."""
    formatter = BackendContextFormatter()
    data = {
        "total_required": 4,
        "completed": 3,
        "pending": 1,
        "next_deadline": "2025-12-31",
        "courses": [
            {"name": "정보보호교육", "status": "completed", "completed_at": "2025-03-15"},
            {"name": "산업안전보건", "status": "pending", "deadline": "2025-12-31"},
        ],
    }

    result = formatter.format_edu_status_for_llm(data)

    assert "[교육 수료 현황]" in result
    assert "총 필수 교육: 4개" in result
    assert "수료 완료: 3개" in result
    assert "미수료: 1개" in result
    assert "[수료 완료 교육]" in result
    assert "정보보호교육" in result
    assert "[미수료 교육]" in result
    assert "산업안전보건" in result


def test_formatter_edu_stats() -> None:
    """부서 교육 통계 포맷팅 테스트."""
    formatter = BackendContextFormatter()
    data = {
        "department_name": "개발팀",
        "total_employees": 50,
        "completion_rate": 85.0,
        "by_course": [
            {"name": "정보보호교육", "completed": 45, "pending": 5},
        ],
        "pending_count": 10,
    }

    result = formatter.format_edu_stats_for_llm(data)

    assert "[교육 이수 통계]" in result
    assert "개발팀" in result
    assert "85.0%" in result
    assert "[교육별 현황]" in result


def test_formatter_incident_overview() -> None:
    """사고 현황 포맷팅 테스트."""
    formatter = BackendContextFormatter()
    data = {
        "period": "2025-Q4",
        "total_incidents": 15,
        "by_status": {"open": 3, "in_progress": 5, "closed": 7},
        "by_type": {"security": 8, "privacy": 5},
        "trend": {"previous_period": 12, "change_rate": 25.0},
    }

    result = formatter.format_incident_overview_for_llm(data)

    assert "[사고 현황 요약]" in result
    assert "2025-Q4" in result
    assert "총 건수: 15건" in result
    assert "[상태별 현황]" in result
    assert "[유형별 현황]" in result


def test_formatter_report_guide() -> None:
    """신고 안내 포맷팅 테스트."""
    formatter = BackendContextFormatter()
    data = {
        "title": "보안사고 신고 안내",
        "steps": ["1. 사고 확인", "2. 증거 수집", "3. 공식 신고"],
        "official_channels": [{"name": "보안팀", "contact": "security@company.com"}],
        "warnings": ["개인정보를 포함하지 마세요"],
    }

    result = formatter.format_report_guide_for_llm(data)

    assert "[보안사고 신고 안내]" in result
    assert "【신고 절차】" in result
    assert "【공식 신고 채널】" in result
    assert "【주의사항】" in result


def test_formatter_mixed_context() -> None:
    """RAG + Backend 통합 컨텍스트 포맷팅 테스트."""
    formatter = BackendContextFormatter()
    rag_context = "1) [DOC-001] 정보보안정책 (p.5)"
    backend_context = "[교육 이수 통계]\n- 이수율: 85%"

    result = formatter.format_mixed_context(
        rag_context=rag_context,
        backend_context=backend_context,
        domain="EDU",
    )

    assert "[교육 관련 정책 근거]" in result
    assert "[실제 현황/통계]" in result
    assert "정보보안정책" in result
    assert "이수율: 85%" in result


def test_formatter_empty_data() -> None:
    """빈 데이터 포맷팅 테스트."""
    formatter = BackendContextFormatter()

    assert "[교육 현황 정보 없음]" in formatter.format_edu_status_for_llm({})
    assert "[교육 통계 정보 없음]" in formatter.format_edu_stats_for_llm({})
    assert "[사고 현황 정보 없음]" in formatter.format_incident_overview_for_llm({})


# =============================================================================
# 3. ChatService BACKEND_API 라우트 테스트
# =============================================================================


class FakeIntentServiceBackendApi(IntentService):
    """BACKEND_API 라우트를 반환하는 Fake IntentService."""

    def __init__(
        self,
        intent: IntentType = IntentType.EDU_STATUS,
        domain: str = "EDU",
        user_role: UserRole = UserRole.EMPLOYEE,
    ):
        self._fake_intent = intent
        self._fake_domain = domain
        self._fake_user_role = user_role

    def classify(self, req: ChatRequest, user_query: str):
        from app.models.intent import IntentResult

        return IntentResult(
            user_role=self._fake_user_role,
            intent=self._fake_intent,
            domain=self._fake_domain,
            route=RouteType.BACKEND_API,
        )


@pytest.mark.anyio
async def test_chat_service_backend_api_employee_edu_status() -> None:
    """
    ChatService: EMPLOYEE × EDU_STATUS → BACKEND_API
    BackendDataClient가 호출되고 RAG는 호출되지 않아야 함.
    """
    rag_called = False
    backend_called = False

    class SpyRagflowClient(RagflowClient):
        def __init__(self) -> None:
            super().__init__(base_url="")

        async def search_as_sources(self, *args, **kwargs) -> List[ChatSource]:
            nonlocal rag_called
            rag_called = True
            return []

    class SpyBackendDataClient(BackendDataClient):
        def __init__(self) -> None:
            super().__init__(base_url="")

        async def get_employee_edu_status(self, user_id: str, year: Optional[int] = None):
            nonlocal backend_called
            backend_called = True
            return self._mock_employee_edu_status(user_id)

    service = ChatService(
        ragflow_client=SpyRagflowClient(),
        llm_client=LLMClient(base_url=""),
        pii_service=PiiService(base_url="", enabled=False),
        intent_service=FakeIntentServiceBackendApi(
            intent=IntentType.EDU_STATUS,
            domain="EDU",
            user_role=UserRole.EMPLOYEE,
        ),
        guardrail_service=GuardrailService(),
        backend_data_client=SpyBackendDataClient(),
    )

    request = ChatRequest(
        session_id="test",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="내 교육 수료 현황 알려줘")],
    )

    response = await service.handle_chat(request)

    # 검증
    assert response.meta.route == "BACKEND_API"
    assert backend_called is True  # Backend 호출됨
    assert rag_called is False  # RAG 미호출
    assert response.meta.rag_used is False


@pytest.mark.anyio
async def test_chat_service_backend_api_incident_report() -> None:
    """
    ChatService: EMPLOYEE × INCIDENT_REPORT → BACKEND_API
    신고 안내 데이터 조회.
    """
    backend_called = False

    class SpyBackendDataClient(BackendDataClient):
        def __init__(self) -> None:
            super().__init__(base_url="")

        async def get_report_guide(self, incident_type: Optional[str] = None):
            nonlocal backend_called
            backend_called = True
            return self._mock_report_guide(incident_type)

    service = ChatService(
        ragflow_client=RagflowClient(base_url=""),
        llm_client=LLMClient(base_url=""),
        pii_service=PiiService(base_url="", enabled=False),
        intent_service=FakeIntentServiceBackendApi(
            intent=IntentType.INCIDENT_REPORT,
            domain="INCIDENT",
            user_role=UserRole.EMPLOYEE,
        ),
        guardrail_service=GuardrailService(),
        backend_data_client=SpyBackendDataClient(),
    )

    # Note: Query must avoid triggering complaint fast path (avoid "하" keyword)
    request = ChatRequest(
        session_id="test",
        user_id="user-123",
        user_role="EMPLOYEE",
        messages=[ChatMessage(role="user", content="보안 사고 발생 보고")],
    )

    response = await service.handle_chat(request)

    assert response.meta.route == "BACKEND_API"
    assert backend_called is True


# =============================================================================
# 4. ChatService MIXED_BACKEND_RAG 라우트 테스트
# =============================================================================


class FakeIntentServiceMixed(IntentService):
    """MIXED_BACKEND_RAG 라우트를 반환하는 Fake IntentService."""

    def __init__(
        self,
        intent: IntentType = IntentType.INCIDENT_QA,
        domain: str = "INCIDENT",
        user_role: UserRole = UserRole.ADMIN,
    ):
        self._fake_intent = intent
        self._fake_domain = domain
        self._fake_user_role = user_role

    def classify(self, req: ChatRequest, user_query: str):
        from app.models.intent import IntentResult

        return IntentResult(
            user_role=self._fake_user_role,
            intent=self._fake_intent,
            domain=self._fake_domain,
            route=RouteType.MIXED_BACKEND_RAG,
        )


@pytest.mark.anyio
async def test_chat_service_mixed_backend_rag_admin_incident() -> None:
    """
    ChatService: ADMIN × INCIDENT → MIXED_BACKEND_RAG
    RAG와 Backend 둘 다 호출되어야 함.
    """
    rag_called = False
    backend_called = False

    class SpyRagflowClient(RagflowClient):
        def __init__(self) -> None:
            super().__init__(base_url="")

        async def search_as_sources(self, *args, **kwargs) -> List[ChatSource]:
            nonlocal rag_called
            rag_called = True
            return [
                ChatSource(
                    doc_id="DOC-001",
                    title="정보보안정책",
                    snippet="보안사고 발생 시...",
                    score=0.85,
                )
            ]

    class SpyBackendDataClient(BackendDataClient):
        def __init__(self) -> None:
            super().__init__(base_url="")

        async def get_incident_overview(self, filters: Optional[Dict] = None):
            nonlocal backend_called
            backend_called = True
            return self._mock_incident_overview(filters)

    service = ChatService(
        ragflow_client=SpyRagflowClient(),
        llm_client=LLMClient(base_url=""),
        pii_service=PiiService(base_url="", enabled=False),
        intent_service=FakeIntentServiceMixed(
            intent=IntentType.INCIDENT_QA,
            domain="INCIDENT",
            user_role=UserRole.ADMIN,
        ),
        guardrail_service=GuardrailService(),
        backend_data_client=SpyBackendDataClient(),
    )

    request = ChatRequest(
        session_id="test",
        user_id="admin-123",
        user_role="ADMIN",
        messages=[ChatMessage(role="user", content="이번 분기 보안사고 현황 알려줘")],
    )

    response = await service.handle_chat(request)

    # 검증
    assert response.meta.route == "MIXED_BACKEND_RAG"
    assert rag_called is True  # RAG 호출됨
    assert backend_called is True  # Backend 호출됨
    assert response.meta.rag_used is True  # RAG 결과 있음
    assert len(response.sources) > 0


@pytest.mark.anyio
async def test_chat_service_mixed_incident_manager() -> None:
    """
    ChatService: INCIDENT_MANAGER × INCIDENT → MIXED_BACKEND_RAG
    """
    backend_called = False

    class SpyBackendDataClient(BackendDataClient):
        def __init__(self) -> None:
            super().__init__(base_url="")

        async def get_incident_overview(self, filters: Optional[Dict] = None):
            nonlocal backend_called
            backend_called = True
            return self._mock_incident_overview(filters)

    service = ChatService(
        ragflow_client=RagflowClient(base_url=""),
        llm_client=LLMClient(base_url=""),
        pii_service=PiiService(base_url="", enabled=False),
        intent_service=FakeIntentServiceMixed(
            intent=IntentType.INCIDENT_QA,
            domain="INCIDENT",
            user_role=UserRole.INCIDENT_MANAGER,
        ),
        guardrail_service=GuardrailService(),
        backend_data_client=SpyBackendDataClient(),
    )

    request = ChatRequest(
        session_id="test",
        user_id="manager-123",
        user_role="INCIDENT_MANAGER",
        messages=[ChatMessage(role="user", content="최근 사고 현황 보여줘")],
    )

    response = await service.handle_chat(request)

    assert response.meta.route == "MIXED_BACKEND_RAG"
    assert backend_called is True


# =============================================================================
# 5. 역할×도메인×의도별 백엔드 메서드 매핑 테스트
# =============================================================================


@pytest.mark.anyio
async def test_backend_mapping_employee_edu_status() -> None:
    """EMPLOYEE × EDU_STATUS → get_employee_edu_status 매핑."""
    with patch("app.clients.backend_client.get_settings") as mock_get_settings:
        mock_settings = MagicMock()
        mock_settings.backend_base_url = ""
        mock_settings.BACKEND_API_TOKEN = ""
        mock_settings.BACKEND_INTERNAL_TOKEN = ""
        mock_settings.BACKEND_TIMEOUT_SEC = 30
        mock_get_settings.return_value = mock_settings

        client = BackendDataClient(base_url="")

        # mock 데이터 반환 확인
        response = await client.get_employee_edu_status("user-123")
        assert response.success is True
        assert "courses" in response.data


@pytest.mark.anyio
async def test_backend_mapping_admin_edu_stats() -> None:
    """ADMIN × EDU_STATUS → get_department_edu_stats 매핑."""
    with patch("app.clients.backend_client.get_settings") as mock_get_settings:
        mock_settings = MagicMock()
        mock_settings.backend_base_url = ""
        mock_settings.BACKEND_API_TOKEN = ""
        mock_settings.BACKEND_INTERNAL_TOKEN = ""
        mock_settings.BACKEND_TIMEOUT_SEC = 30
        mock_get_settings.return_value = mock_settings

        client = BackendDataClient(base_url="")

        response = await client.get_department_edu_stats("dept-001")
        assert response.success is True
        assert "completion_rate" in response.data


@pytest.mark.anyio
async def test_backend_mapping_admin_incident() -> None:
    """ADMIN × INCIDENT → get_incident_overview 매핑."""
    with patch("app.clients.backend_client.get_settings") as mock_get_settings:
        mock_settings = MagicMock()
        mock_settings.backend_base_url = ""
        mock_settings.BACKEND_API_TOKEN = ""
        mock_settings.BACKEND_INTERNAL_TOKEN = ""
        mock_settings.BACKEND_TIMEOUT_SEC = 30
        mock_get_settings.return_value = mock_settings

        client = BackendDataClient(base_url="")

        response = await client.get_incident_overview()
        assert response.success is True
        assert "by_status" in response.data


@pytest.mark.anyio
async def test_backend_mapping_incident_manager() -> None:
    """INCIDENT_MANAGER × INCIDENT → get_incident_overview 매핑."""
    with patch("app.clients.backend_client.get_settings") as mock_get_settings:
        mock_settings = MagicMock()
        mock_settings.backend_base_url = ""
        mock_settings.BACKEND_API_TOKEN = ""
        mock_settings.BACKEND_INTERNAL_TOKEN = ""
        mock_settings.BACKEND_TIMEOUT_SEC = 30
        mock_get_settings.return_value = mock_settings

        client = BackendDataClient(base_url="")

        response = await client.get_incident_overview()
        assert response.success is True


# =============================================================================
# 6. 엔드포인트 상수 테스트
# =============================================================================


def test_endpoint_constants_defined() -> None:
    """엔드포인트 상수가 정의되어 있는지 확인."""
    assert BACKEND_EDU_STATUS_PATH == "/api/edu/status"
    assert BACKEND_EDU_STATS_PATH == "/api/edu/stats"
    assert BACKEND_INCIDENT_OVERVIEW_PATH == "/api/incidents/overview"


# =============================================================================
# 7. LLM 메시지 빌더 테스트
# =============================================================================


def test_build_backend_api_llm_messages() -> None:
    """BACKEND_API용 LLM 메시지 빌더 테스트."""
    service = ChatService(
        ragflow_client=RagflowClient(base_url=""),
        llm_client=LLMClient(base_url=""),
        pii_service=PiiService(base_url="", enabled=False),
    )

    messages = service._build_backend_api_llm_messages(
        user_query="내 교육 현황 알려줘",
        backend_context="[교육 수료 현황]\n- 수료 완료: 3개",
        user_role=UserRole.EMPLOYEE,
        domain="EDU",
        intent=IntentType.EDU_STATUS,
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "[조회된 데이터]" in messages[0]["content"]
    assert "수료 완료: 3개" in messages[0]["content"]


def test_build_mixed_llm_messages() -> None:
    """MIXED_BACKEND_RAG용 LLM 메시지 빌더 테스트."""
    service = ChatService(
        ragflow_client=RagflowClient(base_url=""),
        llm_client=LLMClient(base_url=""),
        pii_service=PiiService(base_url="", enabled=False),
    )

    sources = [
        ChatSource(doc_id="DOC-001", title="정보보안정책", snippet="보안사고 처리 절차..."),
    ]

    messages = service._build_mixed_llm_messages(
        user_query="보안사고 현황 알려줘",
        sources=sources,
        backend_context="[사고 현황]\n- 총 15건",
        domain="INCIDENT",
        user_role=UserRole.ADMIN,
        intent=IntentType.INCIDENT_QA,
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "[정책/규정" in messages[0]["content"] or "정책" in messages[0]["content"]
    assert "[실제 현황/통계]" in messages[0]["content"]
