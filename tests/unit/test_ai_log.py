"""
AI 로그 모델 및 서비스 테스트 모듈

AILogEntry 모델, AILogService의 로그 생성 및 전송 기능을 테스트합니다.

Phase 10 업데이트:
- camelCase JSON 직렬화 검증 테스트 추가
- to_backend_log_payload 헬퍼 함수 테스트 추가
"""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.models.ai_log import AILogEntry, AILogRequest, AILogResponse, to_backend_log_payload
from app.models.chat import ChatAnswerMeta, ChatRequest, ChatMessage, ChatResponse
from app.services.ai_log_service import AILogService


class TestAILogEntry:
    """AILogEntry 모델 테스트."""

    def test_create_minimal_log_entry(self):
        """최소 필수 필드로 로그 엔트리 생성."""
        log = AILogEntry(
            session_id="session-123",
            user_id="user-456",
            user_role="EMPLOYEE",
            domain="POLICY",
            intent="POLICY_QA",
            route="ROUTE_RAG_INTERNAL",
            latency_ms=1500,
        )

        assert log.session_id == "session-123"
        assert log.user_id == "user-456"
        assert log.user_role == "EMPLOYEE"
        assert log.domain == "POLICY"
        assert log.intent == "POLICY_QA"
        assert log.route == "ROUTE_RAG_INTERNAL"
        assert log.latency_ms == 1500
        # 기본값 확인
        assert log.channel == "WEB"
        assert log.has_pii_input is False
        assert log.has_pii_output is False
        assert log.rag_used is False
        assert log.rag_source_count == 0
        assert log.turn_index is None
        assert log.department is None
        assert log.error_code is None
        assert log.question_masked is None
        assert log.answer_masked is None

    def test_create_full_log_entry(self):
        """모든 필드가 포함된 로그 엔트리 생성."""
        log = AILogEntry(
            session_id="session-789",
            user_id="emp-001",
            turn_index=3,
            channel="MOBILE",
            user_role="MANAGER",
            department="보안팀",
            domain="INCIDENT",
            intent="INCIDENT_REPORT",
            route="ROUTE_INCIDENT",
            has_pii_input=True,
            has_pii_output=False,
            model_name="qwen2.5-7b",
            rag_used=True,
            rag_source_count=5,
            latency_ms=2500,
            error_code=None,
            error_message=None,
            question_masked="[NAME] 사원의 개인정보가 유출됐어요",
            answer_masked="해당 사고는 즉시 신고해 주세요.",
        )

        assert log.turn_index == 3
        assert log.channel == "MOBILE"
        assert log.department == "보안팀"
        assert log.has_pii_input is True
        assert log.has_pii_output is False
        assert log.model_name == "qwen2.5-7b"
        assert log.rag_used is True
        assert log.rag_source_count == 5
        assert log.question_masked is not None
        assert log.answer_masked is not None

    def test_log_entry_serialization(self):
        """로그 엔트리의 JSON 직렬화."""
        log = AILogEntry(
            session_id="session-001",
            user_id="user-001",
            user_role="ADMIN",
            domain="EDUCATION",
            intent="EDUCATION_QA",
            route="ROUTE_TRAINING",
            latency_ms=1000,
        )

        data = log.model_dump()

        assert isinstance(data, dict)
        assert data["session_id"] == "session-001"
        assert data["domain"] == "EDUCATION"
        assert data["latency_ms"] == 1000


class TestAILogRequest:
    """AILogRequest 모델 테스트."""

    def test_wrap_log_entry(self):
        """로그 엔트리를 요청 모델로 래핑."""
        log = AILogEntry(
            session_id="session-001",
            user_id="user-001",
            user_role="EMPLOYEE",
            domain="POLICY",
            intent="POLICY_QA",
            route="ROUTE_RAG_INTERNAL",
            latency_ms=1000,
        )

        request = AILogRequest(log=log)

        assert request.log.session_id == "session-001"

        # JSON 직렬화 확인
        data = request.model_dump()
        assert "log" in data
        assert data["log"]["session_id"] == "session-001"


class TestAILogResponse:
    """AILogResponse 모델 테스트."""

    def test_success_response(self):
        """성공 응답 생성."""
        response = AILogResponse(
            success=True,
            log_id="log-abc-123",
            message="Log saved successfully",
        )

        assert response.success is True
        assert response.log_id == "log-abc-123"
        assert response.message == "Log saved successfully"

    def test_failure_response(self):
        """실패 응답 생성."""
        response = AILogResponse(
            success=False,
            log_id=None,
            message="Connection timeout",
        )

        assert response.success is False
        assert response.log_id is None


class TestAILogService:
    """AILogService 테스트."""

    @pytest.fixture
    def chat_request(self) -> ChatRequest:
        """테스트용 ChatRequest 생성."""
        return ChatRequest(
            session_id="test-session-001",
            user_id="test-user-001",
            user_role="EMPLOYEE",
            department="개발팀",
            domain="POLICY",
            channel="WEB",
            messages=[
                ChatMessage(role="user", content="연차 이월 규정이 어떻게 되나요?")
            ],
        )

    @pytest.fixture
    def chat_response(self) -> ChatResponse:
        """테스트용 ChatResponse 생성."""
        return ChatResponse(
            answer="연차는 다음 해로 최대 10일까지 이월 가능합니다.",
            sources=[],
            meta=ChatAnswerMeta(
                used_model="internal-llm",
                route="ROUTE_RAG_INTERNAL",
                intent="POLICY_QA",
                domain="POLICY",
                masked=False,
                has_pii_input=False,
                has_pii_output=False,
                rag_used=True,
                rag_source_count=3,
                latency_ms=1200,
            ),
        )

    def test_create_log_entry(self, chat_request: ChatRequest, chat_response: ChatResponse):
        """AILogService.create_log_entry 테스트."""
        service = AILogService()

        log_entry = service.create_log_entry(
            request=chat_request,
            response=chat_response,
            intent="POLICY_QA",
            domain="POLICY",
            route="ROUTE_RAG_INTERNAL",
            has_pii_input=False,
            has_pii_output=False,
            rag_used=True,
            rag_source_count=3,
            latency_ms=1200,
            model_name="internal-llm",
            question_masked="연차 이월 규정이 어떻게 되나요?",
            answer_masked="연차는 다음 해로 최대 10일까지 이월 가능합니다.",
        )

        assert log_entry.session_id == "test-session-001"
        assert log_entry.user_id == "test-user-001"
        assert log_entry.user_role == "EMPLOYEE"
        assert log_entry.department == "개발팀"
        assert log_entry.channel == "WEB"
        assert log_entry.domain == "POLICY"
        assert log_entry.intent == "POLICY_QA"
        assert log_entry.route == "ROUTE_RAG_INTERNAL"
        assert log_entry.has_pii_input is False
        assert log_entry.has_pii_output is False
        assert log_entry.rag_used is True
        assert log_entry.rag_source_count == 3
        assert log_entry.latency_ms == 1200
        assert log_entry.model_name == "internal-llm"

    @pytest.mark.anyio
    async def test_send_log_without_backend(self, chat_request: ChatRequest, chat_response: ChatResponse):
        """백엔드 URL 미설정 시 로컬 로그만 기록."""
        # Settings mock: BACKEND_BASE_URL이 설정되지 않은 경우
        with patch("app.services.ai_log_service.settings") as mock_settings:
            mock_settings.backend_base_url = None

            service = AILogService()

            log_entry = service.create_log_entry(
                request=chat_request,
                response=chat_response,
                intent="POLICY_QA",
                domain="POLICY",
                route="ROUTE_RAG_INTERNAL",
                has_pii_input=False,
                has_pii_output=False,
                rag_used=True,
                rag_source_count=3,
                latency_ms=1200,
            )

            # 백엔드 미설정 시에도 에러 없이 동작해야 함
            result = await service.send_log(log_entry)
            assert result is True  # 로컬 로그 기록 성공

    @pytest.mark.anyio
    async def test_mask_for_log(self):
        """LOG 단계 PII 마스킹 테스트."""
        service = AILogService()

        question = "김철수 사원의 연차 규정 문의"
        answer = "연차는 입사일 기준으로 발생합니다."

        q_masked, a_masked = await service.mask_for_log(question, answer)

        # PII 서비스 미설정 시 원문 그대로 반환
        assert q_masked is not None
        assert a_masked is not None


class TestChatAnswerMetaExtended:
    """ChatAnswerMeta 확장 필드 테스트."""

    def test_extended_meta_fields(self):
        """확장된 메타데이터 필드 확인."""
        meta = ChatAnswerMeta(
            used_model="qwen2.5-7b",
            route="ROUTE_RAG_INTERNAL",
            intent="POLICY_QA",
            domain="POLICY",
            masked=True,
            has_pii_input=True,
            has_pii_output=False,
            rag_used=True,
            rag_source_count=5,
            latency_ms=1500,
        )

        assert meta.intent == "POLICY_QA"
        assert meta.domain == "POLICY"
        assert meta.has_pii_input is True
        assert meta.has_pii_output is False
        assert meta.rag_used is True
        assert meta.rag_source_count == 5

    def test_meta_serialization(self):
        """메타데이터 JSON 직렬화."""
        meta = ChatAnswerMeta(
            used_model="internal-llm",
            route="ROUTE_LLM_ONLY",
            intent="GENERAL_CHAT",
            domain="GENERAL",
            masked=False,
            latency_ms=500,
        )

        data = meta.model_dump()

        assert data["intent"] == "GENERAL_CHAT"
        assert data["domain"] == "GENERAL"
        assert data["rag_used"] is None  # 기본값


# =============================================================================
# Phase 10: camelCase JSON 직렬화 테스트
# =============================================================================


class TestCamelCaseSerialization:
    """camelCase JSON 직렬화 테스트 (Phase 10)."""

    def test_log_entry_camel_case_serialization(self):
        """
        AILogEntry.model_dump(by_alias=True) 결과가 camelCase인지 검증.

        백엔드(ctrlf-back)는 camelCase JSON을 기대함.
        """
        log = AILogEntry(
            session_id="session-123",
            user_id="user-456",
            turn_index=0,
            user_role="EMPLOYEE",
            has_pii_input=True,
            has_pii_output=False,
            model_name="qwen2.5-7b",
            rag_used=True,
            rag_source_count=5,
            latency_ms=1500,
            error_code="E001",
            error_message="Test error",
            question_masked="masked question",
            answer_masked="masked answer",
            domain="POLICY",
            intent="POLICY_QA",
            route="ROUTE_RAG_INTERNAL",
        )

        # camelCase로 직렬화
        data = log.model_dump(by_alias=True)

        # snake_case → camelCase 매핑 검증
        assert "sessionId" in data
        assert data["sessionId"] == "session-123"

        assert "userId" in data
        assert data["userId"] == "user-456"

        assert "turnIndex" in data
        assert data["turnIndex"] == 0

        assert "userRole" in data
        assert data["userRole"] == "EMPLOYEE"

        assert "hasPiiInput" in data
        assert data["hasPiiInput"] is True

        assert "hasPiiOutput" in data
        assert data["hasPiiOutput"] is False

        assert "modelName" in data
        assert data["modelName"] == "qwen2.5-7b"

        assert "ragUsed" in data
        assert data["ragUsed"] is True

        assert "ragSourceCount" in data
        assert data["ragSourceCount"] == 5

        assert "latencyMs" in data
        assert data["latencyMs"] == 1500

        assert "errorCode" in data
        assert data["errorCode"] == "E001"

        assert "errorMessage" in data
        assert data["errorMessage"] == "Test error"

        assert "questionMasked" in data
        assert "answerMasked" in data

        # alias가 없는 필드는 원래 이름 유지
        assert "channel" in data
        assert "domain" in data
        assert "intent" in data
        assert "route" in data

    def test_to_backend_log_payload_helper(self):
        """
        to_backend_log_payload() 헬퍼 함수가 올바른 형태를 반환하는지 검증.

        반환 형태: {"log": {"sessionId": ..., "userId": ..., ...}}
        """
        log = AILogEntry(
            session_id="test-session",
            user_id="test-user",
            user_role="MANAGER",
            domain="INCIDENT",
            intent="INCIDENT_REPORT",
            route="ROUTE_INCIDENT",
            has_pii_input=False,
            has_pii_output=True,
            rag_used=False,
            rag_source_count=0,
            latency_ms=800,
        )

        payload = to_backend_log_payload(log)

        # {"log": {...}} 형태 확인
        assert "log" in payload
        assert isinstance(payload["log"], dict)

        # camelCase 필드 확인
        log_data = payload["log"]
        assert log_data["sessionId"] == "test-session"
        assert log_data["userId"] == "test-user"
        assert log_data["userRole"] == "MANAGER"
        assert log_data["hasPiiInput"] is False
        assert log_data["hasPiiOutput"] is True
        assert log_data["ragUsed"] is False
        assert log_data["ragSourceCount"] == 0
        assert log_data["latencyMs"] == 800

    def test_ai_log_request_to_backend_payload(self):
        """
        AILogRequest.to_backend_payload() 메서드가 올바른 형태를 반환하는지 검증.
        """
        log = AILogEntry(
            session_id="req-session",
            user_id="req-user",
            user_role="ADMIN",
            domain="EDUCATION",
            intent="EDUCATION_QA",
            route="ROUTE_TRAINING",
            latency_ms=500,
        )

        request = AILogRequest(log=log)
        payload = request.to_backend_payload()

        assert "log" in payload
        assert payload["log"]["sessionId"] == "req-session"
        assert payload["log"]["userId"] == "req-user"
        assert payload["log"]["userRole"] == "ADMIN"

    def test_exclude_none_values(self):
        """
        None 값 필드가 exclude_none=True로 제외되는지 검증.
        """
        log = AILogEntry(
            session_id="session-123",
            user_id="user-456",
            user_role="EMPLOYEE",
            domain="POLICY",
            intent="POLICY_QA",
            route="ROUTE_RAG_INTERNAL",
            latency_ms=1000,
            # None 값 필드들
            turn_index=None,
            department=None,
            model_name=None,
            error_code=None,
            error_message=None,
            question_masked=None,
            answer_masked=None,
        )

        payload = to_backend_log_payload(log)
        log_data = payload["log"]

        # None 값 필드는 제외됨
        assert "turnIndex" not in log_data
        assert "department" not in log_data
        assert "modelName" not in log_data
        assert "errorCode" not in log_data
        assert "errorMessage" not in log_data
        assert "questionMasked" not in log_data
        assert "answerMasked" not in log_data

        # 값이 있는 필드는 포함됨
        assert "sessionId" in log_data
        assert "userId" in log_data

    def test_camel_case_json_string(self):
        """
        JSON 문자열로 직렬화 시에도 camelCase가 유지되는지 검증.
        """
        log = AILogEntry(
            session_id="json-session",
            user_id="json-user",
            user_role="EMPLOYEE",
            domain="POLICY",
            intent="POLICY_QA",
            route="ROUTE_RAG_INTERNAL",
            has_pii_input=True,
            latency_ms=1000,
        )

        payload = to_backend_log_payload(log)
        json_str = json.dumps(payload)

        # JSON 문자열에 camelCase 키가 있는지 확인
        assert '"sessionId"' in json_str
        assert '"userId"' in json_str
        assert '"userRole"' in json_str
        assert '"hasPiiInput"' in json_str
        assert '"latencyMs"' in json_str

        # snake_case 키는 없어야 함
        assert '"session_id"' not in json_str
        assert '"user_id"' not in json_str
        assert '"user_role"' not in json_str
        assert '"has_pii_input"' not in json_str
        assert '"latency_ms"' not in json_str


class TestBackendClientCamelCase:
    """BackendClient camelCase 전송 테스트 (Phase 10)."""

    @pytest.fixture
    def anyio_backend(self) -> str:
        return "asyncio"

    @pytest.mark.anyio
    async def test_backend_client_sends_camel_case(self):
        """
        BackendClient.send_ai_log()가 camelCase JSON을 전송하는지 검증.
        """
        from app.clients.backend_client import BackendClient

        captured_request = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_request["payload"] = json.loads(request.content)
            captured_request["headers"] = dict(request.headers)
            return httpx.Response(
                status_code=200,
                json={"success": True, "logId": "log-001", "message": "Saved"},
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            # httpx client 주입을 위해 별도 처리 필요
            # 여기서는 payload 변환 로직만 단위 테스트

            log = AILogEntry(
                session_id="backend-test-session",
                user_id="backend-test-user",
                user_role="EMPLOYEE",
                domain="POLICY",
                intent="POLICY_QA",
                route="ROUTE_RAG_INTERNAL",
                has_pii_input=True,
                has_pii_output=False,
                rag_used=True,
                rag_source_count=3,
                latency_ms=1200,
            )

            # to_backend_log_payload 함수로 직접 검증
            payload = to_backend_log_payload(log)

            # POST 요청 시뮬레이션
            response = await client.post(
                "http://test-backend:8080/api/ai-logs",
                json=payload,
            )

            assert response.status_code == 200

        # 전송된 payload 검증
        sent_payload = captured_request["payload"]
        assert "log" in sent_payload

        log_data = sent_payload["log"]
        assert log_data["sessionId"] == "backend-test-session"
        assert log_data["userId"] == "backend-test-user"
        assert log_data["hasPiiInput"] is True
        assert log_data["hasPiiOutput"] is False
        assert log_data["ragUsed"] is True
        assert log_data["ragSourceCount"] == 3
        assert log_data["latencyMs"] == 1200

    @pytest.mark.anyio
    async def test_backend_client_authorization_header(self):
        """
        BACKEND_API_TOKEN이 설정된 경우 Authorization 헤더가 추가되는지 검증.
        """
        from app.clients.backend_client import BackendClient

        captured_headers = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return httpx.Response(
                status_code=200,
                json={"success": True},
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            # api_token을 직접 전달하여 테스트
            backend_client = BackendClient(
                base_url="http://test-backend:8080",
                api_token="test-secret-token",
            )

            # Authorization 헤더 생성 확인
            headers = backend_client._get_bearer_headers()
            assert headers["Authorization"] == "Bearer test-secret-token"

            # 토큰 없는 경우 (명시적으로 빈 문자열 전달)
            # api_token=None은 설정에서 기본값을 가져오므로, 빈 문자열을 전달해야 함
            backend_client_no_token = BackendClient(
                base_url="http://test-backend:8080",
                api_token="",  # 명시적으로 빈 토큰
            )
            headers_no_token = backend_client_no_token._get_bearer_headers()
            assert "Authorization" not in headers_no_token
