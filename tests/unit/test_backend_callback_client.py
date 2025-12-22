"""
Backend Callback Client Tests

스크립트 생성 완료 콜백 클라이언트 테스트.

테스트 케이스:
1. 정상적인 스크립트 완료 콜백 (200 응답)
2. 201/204 응답 처리
3. BACKEND_BASE_URL 미설정 시 스킵
4. 401 Unauthorized 에러
5. 403 Forbidden 에러
6. 404 Not Found 에러
7. 500 Server Error
8. 네트워크 타임아웃
9. 네트워크 에러
"""

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from app.clients.backend_client import (
    BackendCallbackClient,
    ScriptCompleteCallbackError,
    ScriptCompleteRequest,
    ScriptCompleteResponse,
    clear_backend_callback_client,
    get_backend_callback_client,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def clear_singleton():
    """테스트 전후 싱글톤 초기화."""
    clear_backend_callback_client()
    yield
    clear_backend_callback_client()


@pytest.fixture
def mock_settings():
    """Mock settings for testing."""
    mock = MagicMock()
    mock.backend_base_url = "http://backend:8080"
    mock.BACKEND_INTERNAL_TOKEN = "test-token"
    mock.BACKEND_TIMEOUT_SEC = 30.0
    return mock


@pytest.fixture
def sample_script_complete_request():
    """샘플 스크립트 완료 요청."""
    return {
        "material_id": "material-001",
        "script_id": "script-001",
        "script": '{"chapters": [{"chapter_id": 1, "title": "테스트"}]}',
        "version": 1,
    }


# =============================================================================
# Request Model Tests
# =============================================================================


class TestScriptCompleteRequest:
    """ScriptCompleteRequest 모델 테스트."""

    def test_create_request(self):
        """요청 모델 생성 테스트."""
        request = ScriptCompleteRequest(
            materialId="mat-001",
            scriptId="script-001",
            script='{"test": true}',
            version=1,
        )
        assert request.materialId == "mat-001"
        assert request.scriptId == "script-001"
        assert request.script == '{"test": true}'
        assert request.version == 1

    def test_request_to_dict(self):
        """요청 모델 dict 변환 테스트."""
        request = ScriptCompleteRequest(
            materialId="mat-001",
            scriptId="script-001",
            script='{"test": true}',
            version=2,
        )
        data = request.model_dump()
        assert data == {
            "materialId": "mat-001",
            "scriptId": "script-001",
            "script": '{"test": true}',
            "version": 2,
        }


# =============================================================================
# BackendCallbackClient Tests
# =============================================================================


class TestBackendCallbackClient:
    """BackendCallbackClient 테스트."""

    @pytest.mark.asyncio
    async def test_notify_script_complete_success_200(
        self, mock_settings, sample_script_complete_request
    ):
        """200 응답 - 스크립트 완료 콜백 성공."""
        # Mock HTTP response
        mock_response = httpx.Response(
            status_code=200,
            json={"success": True, "message": "OK"},
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.clients.backend_client.get_settings", return_value=mock_settings):
            client = BackendCallbackClient(client=mock_client)
            result = await client.notify_script_complete(
                **sample_script_complete_request
            )

        assert result.success is True
        mock_client.post.assert_called_once()

        # 호출 인자 확인
        call_args = mock_client.post.call_args
        assert "/video/script/complete" in call_args[0][0]
        assert call_args[1]["headers"]["X-Internal-Token"] == "test-token"

    @pytest.mark.asyncio
    async def test_notify_script_complete_success_201(
        self, mock_settings, sample_script_complete_request
    ):
        """201 응답 - 스크립트 생성됨."""
        mock_response = httpx.Response(
            status_code=201,
            json={"success": True},
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.clients.backend_client.get_settings", return_value=mock_settings):
            client = BackendCallbackClient(client=mock_client)
            result = await client.notify_script_complete(
                **sample_script_complete_request
            )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_notify_script_complete_success_204(
        self, mock_settings, sample_script_complete_request
    ):
        """204 응답 - 빈 응답 처리."""
        mock_response = httpx.Response(
            status_code=204,
            content=b"",
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.clients.backend_client.get_settings", return_value=mock_settings):
            client = BackendCallbackClient(client=mock_client)
            result = await client.notify_script_complete(
                **sample_script_complete_request
            )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_notify_script_complete_no_base_url(
        self, sample_script_complete_request
    ):
        """BACKEND_BASE_URL 미설정 시 스킵."""
        mock_settings = MagicMock()
        mock_settings.backend_base_url = None
        mock_settings.BACKEND_INTERNAL_TOKEN = None
        mock_settings.BACKEND_TIMEOUT_SEC = 30.0

        with patch("app.clients.backend_client.get_settings", return_value=mock_settings):
            client = BackendCallbackClient()
            result = await client.notify_script_complete(
                **sample_script_complete_request
            )

        # URL 미설정 시 실패로 표시하되 예외는 발생하지 않음
        assert result.success is False
        assert "not configured" in result.message

    @pytest.mark.asyncio
    async def test_notify_script_complete_error_401(
        self, mock_settings, sample_script_complete_request
    ):
        """401 Unauthorized 에러."""
        mock_response = httpx.Response(
            status_code=401,
            text="Unauthorized",
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.clients.backend_client.get_settings", return_value=mock_settings):
            client = BackendCallbackClient(client=mock_client)

            with pytest.raises(ScriptCompleteCallbackError) as exc_info:
                await client.notify_script_complete(**sample_script_complete_request)

            assert exc_info.value.status_code == 401
            assert exc_info.value.error_code == "CALLBACK_UNAUTHORIZED"

    @pytest.mark.asyncio
    async def test_notify_script_complete_error_403(
        self, mock_settings, sample_script_complete_request
    ):
        """403 Forbidden 에러."""
        mock_response = httpx.Response(
            status_code=403,
            text="Forbidden",
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.clients.backend_client.get_settings", return_value=mock_settings):
            client = BackendCallbackClient(client=mock_client)

            with pytest.raises(ScriptCompleteCallbackError) as exc_info:
                await client.notify_script_complete(**sample_script_complete_request)

            assert exc_info.value.status_code == 403
            assert exc_info.value.error_code == "CALLBACK_FORBIDDEN"

    @pytest.mark.asyncio
    async def test_notify_script_complete_error_404(
        self, mock_settings, sample_script_complete_request
    ):
        """404 Not Found 에러."""
        mock_response = httpx.Response(
            status_code=404,
            text="Not Found",
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.clients.backend_client.get_settings", return_value=mock_settings):
            client = BackendCallbackClient(client=mock_client)

            with pytest.raises(ScriptCompleteCallbackError) as exc_info:
                await client.notify_script_complete(**sample_script_complete_request)

            assert exc_info.value.status_code == 404
            assert exc_info.value.error_code == "CALLBACK_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_notify_script_complete_error_500(
        self, mock_settings, sample_script_complete_request
    ):
        """500 Server Error."""
        mock_response = httpx.Response(
            status_code=500,
            text="Internal Server Error",
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.clients.backend_client.get_settings", return_value=mock_settings):
            client = BackendCallbackClient(client=mock_client)

            with pytest.raises(ScriptCompleteCallbackError) as exc_info:
                await client.notify_script_complete(**sample_script_complete_request)

            assert exc_info.value.status_code == 500
            assert exc_info.value.error_code == "CALLBACK_SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_notify_script_complete_timeout(
        self, mock_settings, sample_script_complete_request
    ):
        """네트워크 타임아웃."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))

        with patch("app.clients.backend_client.get_settings", return_value=mock_settings):
            client = BackendCallbackClient(client=mock_client)

            with pytest.raises(ScriptCompleteCallbackError) as exc_info:
                await client.notify_script_complete(**sample_script_complete_request)

            assert exc_info.value.error_code == "CALLBACK_TIMEOUT"

    @pytest.mark.asyncio
    async def test_notify_script_complete_network_error(
        self, mock_settings, sample_script_complete_request
    ):
        """네트워크 에러."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.RequestError("Connection refused")
        )

        with patch("app.clients.backend_client.get_settings", return_value=mock_settings):
            client = BackendCallbackClient(client=mock_client)

            with pytest.raises(ScriptCompleteCallbackError) as exc_info:
                await client.notify_script_complete(**sample_script_complete_request)

            assert exc_info.value.error_code == "CALLBACK_NETWORK_ERROR"

    @pytest.mark.asyncio
    async def test_request_headers(self, mock_settings, sample_script_complete_request):
        """요청 헤더 확인."""
        mock_response = httpx.Response(status_code=200, json={"success": True})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.clients.backend_client.get_settings", return_value=mock_settings):
            client = BackendCallbackClient(client=mock_client)
            await client.notify_script_complete(**sample_script_complete_request)

        call_args = mock_client.post.call_args
        headers = call_args[1]["headers"]
        assert headers["Content-Type"] == "application/json"
        assert headers["Accept"] == "application/json"
        assert headers["X-Internal-Token"] == "test-token"

    @pytest.mark.asyncio
    async def test_request_body(self, mock_settings, sample_script_complete_request):
        """요청 본문 확인."""
        mock_response = httpx.Response(status_code=200, json={"success": True})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.clients.backend_client.get_settings", return_value=mock_settings):
            client = BackendCallbackClient(client=mock_client)
            await client.notify_script_complete(**sample_script_complete_request)

        call_args = mock_client.post.call_args
        body = call_args[1]["json"]
        assert body["materialId"] == "material-001"
        assert body["scriptId"] == "script-001"
        assert body["version"] == 1


# =============================================================================
# Singleton Tests
# =============================================================================


class TestSingleton:
    """싱글톤 인스턴스 테스트."""

    def test_get_singleton_instance(self):
        """싱글톤 인스턴스 반환."""
        with patch("app.clients.backend_client.get_settings") as mock:
            mock.return_value.backend_base_url = "http://test:8080"
            mock.return_value.BACKEND_INTERNAL_TOKEN = "token"
            mock.return_value.BACKEND_TIMEOUT_SEC = 30.0

            client1 = get_backend_callback_client()
            client2 = get_backend_callback_client()

            assert client1 is client2

    def test_clear_singleton(self):
        """싱글톤 초기화."""
        with patch("app.clients.backend_client.get_settings") as mock:
            mock.return_value.backend_base_url = "http://test:8080"
            mock.return_value.BACKEND_INTERNAL_TOKEN = "token"
            mock.return_value.BACKEND_TIMEOUT_SEC = 30.0

            client1 = get_backend_callback_client()
            clear_backend_callback_client()
            client2 = get_backend_callback_client()

            assert client1 is not client2


# =============================================================================
# Job Complete Callback Tests
# =============================================================================


class TestJobCompleteCallback:
    """영상 생성 완료 콜백 테스트."""

    @pytest.fixture
    def sample_job_complete_request(self):
        """샘플 잡 완료 요청."""
        return {
            "job_id": "job-001",
            "video_url": "s3://bucket/videos/job-001/output.mp4",
            "duration": 120,
            "status": "COMPLETED",
        }

    @pytest.mark.asyncio
    async def test_notify_job_complete_success_200(
        self, mock_settings, sample_job_complete_request
    ):
        """200 응답 - 잡 완료 콜백 성공."""
        from app.clients.backend_client import JobCompleteCallbackError

        mock_response = httpx.Response(
            status_code=200,
            json={"saved": True},
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.clients.backend_client.get_settings", return_value=mock_settings):
            client = BackendCallbackClient(client=mock_client)
            result = await client.notify_job_complete(**sample_job_complete_request)

        assert result.saved is True
        mock_client.post.assert_called_once()

        # URL 확인
        call_args = mock_client.post.call_args
        assert "/video/job/job-001/complete" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_notify_job_complete_success_204(
        self, mock_settings, sample_job_complete_request
    ):
        """204 응답 - 빈 응답 처리."""
        mock_response = httpx.Response(
            status_code=204,
            content=b"",
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.clients.backend_client.get_settings", return_value=mock_settings):
            client = BackendCallbackClient(client=mock_client)
            result = await client.notify_job_complete(**sample_job_complete_request)

        assert result.saved is True

    @pytest.mark.asyncio
    async def test_notify_job_complete_no_base_url(
        self, sample_job_complete_request
    ):
        """BACKEND_BASE_URL 미설정 시 스킵."""
        mock_settings = MagicMock()
        mock_settings.backend_base_url = None
        mock_settings.BACKEND_INTERNAL_TOKEN = None
        mock_settings.BACKEND_TIMEOUT_SEC = 30.0

        with patch("app.clients.backend_client.get_settings", return_value=mock_settings):
            client = BackendCallbackClient()
            result = await client.notify_job_complete(**sample_job_complete_request)

        assert result.saved is False

    @pytest.mark.asyncio
    async def test_notify_job_complete_error_404(
        self, mock_settings, sample_job_complete_request
    ):
        """404 Not Found 에러."""
        from app.clients.backend_client import JobCompleteCallbackError

        mock_response = httpx.Response(
            status_code=404,
            text="Not Found",
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.clients.backend_client.get_settings", return_value=mock_settings):
            client = BackendCallbackClient(client=mock_client)

            with pytest.raises(JobCompleteCallbackError) as exc_info:
                await client.notify_job_complete(**sample_job_complete_request)

            assert exc_info.value.status_code == 404
            assert exc_info.value.error_code == "CALLBACK_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_notify_job_complete_error_500(
        self, mock_settings, sample_job_complete_request
    ):
        """500 Server Error."""
        from app.clients.backend_client import JobCompleteCallbackError

        mock_response = httpx.Response(
            status_code=500,
            text="Internal Server Error",
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.clients.backend_client.get_settings", return_value=mock_settings):
            client = BackendCallbackClient(client=mock_client)

            with pytest.raises(JobCompleteCallbackError) as exc_info:
                await client.notify_job_complete(**sample_job_complete_request)

            assert exc_info.value.status_code == 500
            assert exc_info.value.error_code == "CALLBACK_SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_notify_job_complete_timeout(
        self, mock_settings, sample_job_complete_request
    ):
        """네트워크 타임아웃."""
        from app.clients.backend_client import JobCompleteCallbackError

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))

        with patch("app.clients.backend_client.get_settings", return_value=mock_settings):
            client = BackendCallbackClient(client=mock_client)

            with pytest.raises(JobCompleteCallbackError) as exc_info:
                await client.notify_job_complete(**sample_job_complete_request)

            assert exc_info.value.error_code == "CALLBACK_TIMEOUT"

    @pytest.mark.asyncio
    async def test_notify_job_complete_request_body(
        self, mock_settings, sample_job_complete_request
    ):
        """요청 본문 확인."""
        mock_response = httpx.Response(status_code=200, json={"saved": True})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.clients.backend_client.get_settings", return_value=mock_settings):
            client = BackendCallbackClient(client=mock_client)
            await client.notify_job_complete(**sample_job_complete_request)

        call_args = mock_client.post.call_args
        body = call_args[1]["json"]
        assert body["jobId"] == "job-001"
        assert body["videoUrl"] == "s3://bucket/videos/job-001/output.mp4"
        assert body["duration"] == 120
        assert body["status"] == "COMPLETED"
