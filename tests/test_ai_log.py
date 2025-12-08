"""
AI 로그 모델 및 서비스 테스트 모듈

AILogEntry 모델, AILogService의 로그 생성 및 전송 기능을 테스트합니다.
"""

import pytest

from app.models.ai_log import AILogEntry, AILogRequest, AILogResponse
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
        service = AILogService()
        # BACKEND_BASE_URL이 설정되지 않은 경우

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
