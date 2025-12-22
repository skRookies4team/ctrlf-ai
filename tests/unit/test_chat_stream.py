"""
Chat Stream API Tests (스트리밍 채팅 API 테스트)

POST /ai/chat/stream 엔드포인트 및 관련 서비스 테스트.
"""

import asyncio
import json
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.chat_stream import (
    ChatStreamRequest,
    InFlightRequest,
    StreamDoneEvent,
    StreamErrorCode,
    StreamErrorEvent,
    StreamMetaEvent,
    StreamMetrics,
    StreamTokenEvent,
)
from app.services.chat_stream_service import (
    ChatStreamService,
    InFlightTracker,
    get_in_flight_tracker,
)


# =============================================================================
# Model Tests
# =============================================================================


class TestStreamModels:
    """스트리밍 모델 테스트."""

    def test_chat_stream_request_valid(self):
        """유효한 요청 생성."""
        from app.models.chat import ChatMessage

        request = ChatStreamRequest(
            request_id="req-001",
            session_id="sess-001",
            user_id="user-001",
            user_role="EMPLOYEE",
            messages=[ChatMessage(role="user", content="안녕하세요")],
        )
        assert request.request_id == "req-001"
        assert request.session_id == "sess-001"
        assert request.user_id == "user-001"
        assert request.user_role == "EMPLOYEE"
        assert len(request.messages) == 1
        assert request.messages[0].content == "안녕하세요"

    def test_chat_stream_request_with_optional_fields(self):
        """선택 필드 포함 요청."""
        from app.models.chat import ChatMessage

        request = ChatStreamRequest(
            request_id="req-002",
            session_id="sess-002",
            user_id="user-002",
            user_role="MANAGER",
            department="보안팀",
            domain="POLICY",
            channel="MOBILE",
            messages=[ChatMessage(role="user", content="테스트")],
        )
        assert request.request_id == "req-002"
        assert request.department == "보안팀"
        assert request.domain == "POLICY"
        assert request.channel == "MOBILE"

    def test_chat_stream_request_defaults(self):
        """기본값 테스트."""
        from app.models.chat import ChatMessage

        request = ChatStreamRequest(
            request_id="req-003",
            session_id="sess-003",
            user_id="user-003",
            user_role="EMPLOYEE",
            messages=[ChatMessage(role="user", content="질문")],
        )
        assert request.department is None
        assert request.domain is None
        assert request.channel == "WEB"  # 기본값

    def test_stream_meta_event_to_ndjson(self):
        """meta 이벤트 NDJSON 변환."""
        event = StreamMetaEvent(
            request_id="req-001",
            model="gpt-4",
            timestamp="2025-01-01T00:00:00",
        )
        ndjson = event.to_ndjson()

        assert ndjson.endswith("\n")
        data = json.loads(ndjson.strip())
        assert data["type"] == "meta"
        assert data["request_id"] == "req-001"
        assert data["model"] == "gpt-4"

    def test_stream_token_event_to_ndjson(self):
        """token 이벤트 NDJSON 변환."""
        event = StreamTokenEvent(text="안")
        ndjson = event.to_ndjson()

        assert ndjson.endswith("\n")
        data = json.loads(ndjson.strip())
        assert data["type"] == "token"
        assert data["text"] == "안"

    def test_stream_done_event_to_ndjson(self):
        """done 이벤트 NDJSON 변환."""
        event = StreamDoneEvent(
            finish_reason="stop",
            total_tokens=100,
            elapsed_ms=1500,
            ttfb_ms=200,
        )
        ndjson = event.to_ndjson()

        assert ndjson.endswith("\n")
        data = json.loads(ndjson.strip())
        assert data["type"] == "done"
        assert data["finish_reason"] == "stop"
        assert data["total_tokens"] == 100
        assert data["elapsed_ms"] == 1500
        assert data["ttfb_ms"] == 200

    def test_stream_error_event_to_ndjson(self):
        """error 이벤트 NDJSON 변환."""
        event = StreamErrorEvent(
            code=StreamErrorCode.LLM_TIMEOUT.value,
            message="LLM 응답 시간 초과",
            request_id="req-001",
        )
        ndjson = event.to_ndjson()

        assert ndjson.endswith("\n")
        data = json.loads(ndjson.strip())
        assert data["type"] == "error"
        assert data["code"] == "LLM_TIMEOUT"
        assert data["request_id"] == "req-001"


# =============================================================================
# InFlightTracker Tests
# =============================================================================


class TestInFlightTracker:
    """중복 요청 방지 트래커 테스트."""

    def test_start_request_new(self):
        """새 요청 등록."""
        tracker = InFlightTracker()
        result = tracker.start_request("req-001")
        assert result is True

    def test_start_request_duplicate(self):
        """중복 요청 거부."""
        tracker = InFlightTracker()
        tracker.start_request("req-001")
        result = tracker.start_request("req-001")
        assert result is False

    def test_is_in_flight(self):
        """진행 중 요청 확인."""
        tracker = InFlightTracker()
        assert tracker.is_in_flight("req-001") is False

        tracker.start_request("req-001")
        assert tracker.is_in_flight("req-001") is True

    def test_complete_request(self):
        """요청 완료 처리."""
        tracker = InFlightTracker()
        tracker.start_request("req-001")
        assert tracker.is_in_flight("req-001") is True

        tracker.complete_request("req-001", "응답 결과")
        assert tracker.is_in_flight("req-001") is False

    def test_cancel_request(self):
        """요청 취소."""
        tracker = InFlightTracker()
        tracker.start_request("req-001")
        tracker.cancel_request("req-001")

        # 취소 후 다시 등록 가능
        result = tracker.start_request("req-001")
        assert result is True

    def test_get_cached_response(self):
        """캐시된 응답 조회."""
        tracker = InFlightTracker()
        tracker.start_request("req-001")
        tracker.complete_request("req-001", "캐시된 응답")

        cached = tracker.get_cached_response("req-001")
        assert cached == "캐시된 응답"

    def test_get_cached_response_not_found(self):
        """캐시 없음."""
        tracker = InFlightTracker()
        cached = tracker.get_cached_response("req-nonexistent")
        assert cached is None

    def test_cleanup_expired(self):
        """만료된 요청 정리."""
        tracker = InFlightTracker()
        tracker.CACHE_TTL_SECONDS = 1  # 테스트용 짧은 TTL

        tracker.start_request("req-001")
        tracker._requests["req-001"].started_at = datetime.now() - timedelta(seconds=10)

        # 정리 트리거
        tracker._last_cleanup = 0
        tracker.is_in_flight("req-002")

        # 만료된 요청 제거됨
        assert "req-001" not in tracker._requests


# =============================================================================
# ChatStreamService Tests
# =============================================================================


class TestChatStreamService:
    """스트리밍 서비스 테스트."""

    def _create_request(self, request_id: str) -> ChatStreamRequest:
        """테스트용 요청 생성 헬퍼."""
        from app.models.chat import ChatMessage

        return ChatStreamRequest(
            request_id=request_id,
            session_id="sess-001",
            user_id="user-001",
            user_role="EMPLOYEE",
            messages=[ChatMessage(role="user", content="테스트")],
        )

    @pytest.mark.asyncio
    async def test_stream_chat_duplicate_inflight(self):
        """중복 요청 시 DUPLICATE_INFLIGHT 에러."""
        tracker = InFlightTracker()
        tracker.start_request("req-001")

        service = ChatStreamService(tracker=tracker)
        request = self._create_request("req-001")

        chunks = []
        async for chunk in service.stream_chat(request):
            chunks.append(chunk)

        assert len(chunks) == 1
        data = json.loads(chunks[0].strip())
        assert data["type"] == "error"
        assert data["code"] == "DUPLICATE_INFLIGHT"

    @pytest.mark.asyncio
    async def test_stream_chat_meta_event_first(self):
        """meta 이벤트가 첫 번째로 전송."""
        tracker = InFlightTracker()
        service = ChatStreamService(tracker=tracker)
        request = self._create_request("req-002")

        chunks = []
        with patch.object(service, "_settings") as mock_settings:
            mock_settings.llm_base_url = None  # Fallback 모드
            mock_settings.LLM_MODEL_NAME = "test-model"

            async for chunk in service.stream_chat(request):
                chunks.append(chunk)

        # 첫 번째는 meta
        first_data = json.loads(chunks[0].strip())
        assert first_data["type"] == "meta"
        assert first_data["request_id"] == "req-002"

    @pytest.mark.asyncio
    async def test_stream_chat_done_event_last(self):
        """done 이벤트가 마지막으로 전송."""
        tracker = InFlightTracker()
        service = ChatStreamService(tracker=tracker)
        request = self._create_request("req-003")

        chunks = []
        with patch.object(service, "_settings") as mock_settings:
            mock_settings.llm_base_url = None
            mock_settings.LLM_MODEL_NAME = "test-model"

            async for chunk in service.stream_chat(request):
                chunks.append(chunk)

        # 마지막은 done
        last_data = json.loads(chunks[-1].strip())
        assert last_data["type"] == "done"
        assert "elapsed_ms" in last_data

    @pytest.mark.asyncio
    async def test_stream_chat_token_events(self):
        """token 이벤트 전송."""
        tracker = InFlightTracker()
        service = ChatStreamService(tracker=tracker)
        request = self._create_request("req-004")

        chunks = []
        with patch.object(service, "_settings") as mock_settings:
            mock_settings.llm_base_url = None
            mock_settings.LLM_MODEL_NAME = "test-model"

            async for chunk in service.stream_chat(request):
                chunks.append(chunk)

        # token 이벤트가 있어야 함
        token_chunks = [
            c for c in chunks
            if '"type":"token"' in c
        ]
        assert len(token_chunks) > 0

    @pytest.mark.asyncio
    async def test_stream_chat_ndjson_format(self):
        """모든 청크가 NDJSON 형식."""
        tracker = InFlightTracker()
        service = ChatStreamService(tracker=tracker)
        request = self._create_request("req-005")

        chunks = []
        with patch.object(service, "_settings") as mock_settings:
            mock_settings.llm_base_url = None
            mock_settings.LLM_MODEL_NAME = "test-model"

            async for chunk in service.stream_chat(request):
                chunks.append(chunk)

        # 모든 청크가 줄바꿈으로 끝나고 유효한 JSON
        for chunk in chunks:
            assert chunk.endswith("\n"), "각 청크는 줄바꿈으로 끝나야 함"
            json.loads(chunk.strip())  # 유효한 JSON이어야 함


# =============================================================================
# API Endpoint Tests
# =============================================================================


class TestChatStreamEndpoint:
    """스트리밍 API 엔드포인트 테스트."""

    @pytest.fixture
    def client(self):
        """테스트 클라이언트."""
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    def _valid_request_body(self, request_id: str = "test-001") -> dict:
        """유효한 요청 바디 생성."""
        return {
            "request_id": request_id,
            "session_id": "sess-001",
            "user_id": "user-001",
            "user_role": "EMPLOYEE",
            "messages": [{"role": "user", "content": "안녕하세요"}],
        }

    def test_stream_endpoint_exists(self, client):
        """엔드포인트 존재 확인."""
        response = client.post(
            "/ai/chat/stream",
            json=self._valid_request_body("test-001"),
        )
        # 422가 아닌 다른 응답 (200 또는 스트리밍)
        assert response.status_code != 404

    def test_stream_endpoint_validation_error_missing_request_id(self, client):
        """request_id 누락 시 유효성 검증 실패."""
        body = self._valid_request_body()
        del body["request_id"]
        response = client.post("/ai/chat/stream", json=body)
        assert response.status_code == 422

    def test_stream_endpoint_validation_error_missing_session_id(self, client):
        """session_id 누락 시 유효성 검증 실패."""
        body = self._valid_request_body()
        del body["session_id"]
        response = client.post("/ai/chat/stream", json=body)
        assert response.status_code == 422

    def test_stream_endpoint_validation_error_missing_user_id(self, client):
        """user_id 누락 시 유효성 검증 실패."""
        body = self._valid_request_body()
        del body["user_id"]
        response = client.post("/ai/chat/stream", json=body)
        assert response.status_code == 422

    def test_stream_endpoint_validation_error_missing_user_role(self, client):
        """user_role 누락 시 유효성 검증 실패."""
        body = self._valid_request_body()
        del body["user_role"]
        response = client.post("/ai/chat/stream", json=body)
        assert response.status_code == 422

    def test_stream_endpoint_validation_error_empty_messages(self, client):
        """빈 messages 배열 거부."""
        body = self._valid_request_body()
        body["messages"] = []
        response = client.post("/ai/chat/stream", json=body)
        assert response.status_code == 422

    def test_stream_endpoint_content_type(self, client):
        """응답 Content-Type 확인."""
        response = client.post(
            "/ai/chat/stream",
            json=self._valid_request_body("test-003"),
        )
        assert "application/x-ndjson" in response.headers.get("content-type", "")

    def test_stream_endpoint_response_format(self, client):
        """응답 형식 확인 (NDJSON)."""
        response = client.post(
            "/ai/chat/stream",
            json=self._valid_request_body("test-004"),
        )

        # 응답을 줄 단위로 파싱
        lines = response.text.strip().split("\n")
        assert len(lines) >= 2  # 최소 meta + done

        # 첫 번째는 meta
        first_data = json.loads(lines[0])
        assert first_data["type"] == "meta"

        # 마지막은 done 또는 error
        last_data = json.loads(lines[-1])
        assert last_data["type"] in ["done", "error"]


# =============================================================================
# Metrics Tests
# =============================================================================


class TestStreamMetrics:
    """메트릭 테스트."""

    def test_metrics_model(self):
        """메트릭 모델 생성."""
        metrics = StreamMetrics(
            request_id="req-001",
            model="gpt-4",
            ttfb_ms=200,
            total_elapsed_ms=1500,
            total_tokens=100,
            completed=True,
        )
        assert metrics.request_id == "req-001"
        assert metrics.ttfb_ms == 200
        assert metrics.total_tokens == 100

    def test_metrics_with_error(self):
        """에러 포함 메트릭."""
        metrics = StreamMetrics(
            request_id="req-002",
            model="gpt-4",
            error_code="LLM_TIMEOUT",
            completed=False,
        )
        assert metrics.error_code == "LLM_TIMEOUT"
        assert metrics.completed is False
