"""
Phase 35: Backend Presigned Storage Provider 테스트

AI 서버가 AWS 자격증명 없이 백엔드 Presigned URL로 S3에 업로드하는 기능을 테스트합니다.

테스트 항목:
- Presign 요청 → PUT 업로드 → Complete 콜백 전체 흐름
- 용량 제한 초과 시 StorageUploadError 발생
- Presign 실패 시 StorageUploadError
- PUT 업로드 실패 시 StorageUploadError
- Complete 콜백 실패 시 StorageUploadError
- get_url URL 구성 테스트
- delete_object 백엔드 위임 테스트
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from app.clients.storage_adapter import (
    BackendPresignedStorageProvider,
    StorageProvider,
    StorageUploadError,
    get_storage_provider,
    clear_storage_provider,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_settings():
    """테스트용 Settings mock."""
    settings = MagicMock()
    settings.backend_base_url = "http://backend-mock:8081"
    settings.BACKEND_SERVICE_TOKEN = "test-service-token"
    settings.BACKEND_STORAGE_PRESIGN_PATH = "/internal/storage/presign-put"
    settings.BACKEND_STORAGE_COMPLETE_PATH = "/internal/storage/complete"
    settings.VIDEO_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB for testing
    settings.storage_public_base_url = "https://cdn.example.com"
    # Phase 36: 재시도 및 ETag 설정
    settings.STORAGE_UPLOAD_RETRY_MAX = 3  # 최대 3회 재시도
    settings.STORAGE_UPLOAD_RETRY_BASE_SEC = 0.01  # 테스트용 짧은 대기시간
    settings.STORAGE_ETAG_OPTIONAL = False  # 기본: ETag 필수
    return settings


@pytest.fixture
def provider(mock_settings):
    """BackendPresignedStorageProvider 인스턴스."""
    with patch("app.core.config.get_settings", return_value=mock_settings):
        return BackendPresignedStorageProvider()


@pytest.fixture
def sample_file(tmp_path):
    """샘플 파일 생성."""
    file_path = tmp_path / "sample.mp4"
    file_path.write_bytes(b"fake video content " * 100)  # ~1.9KB
    return file_path


@pytest.fixture
def large_file(tmp_path):
    """용량 초과 파일 생성 (mock settings 기준 10MB 초과)."""
    file_path = tmp_path / "large.mp4"
    # 11MB 파일 생성
    file_path.write_bytes(b"x" * (11 * 1024 * 1024))
    return file_path


@pytest.fixture
def mock_presign_response():
    """Presign API 응답 mock."""
    return {
        "upload_url": "https://s3.amazonaws.com/bucket/key?presigned-params...",
        "public_url": "https://cdn.example.com/videos/video-001/script-001/job-123/video.mp4",
        "headers": {"Content-Type": "video/mp4"},
        "expires_sec": 600,
    }


# =============================================================================
# Full Flow Tests
# =============================================================================


class TestBackendPresignedFullFlow:
    """전체 업로드 흐름 테스트."""

    @pytest.mark.asyncio
    async def test_put_object_full_flow_success(
        self, provider, sample_file, mock_presign_response
    ):
        """전체 흐름 성공: Presign → PUT → Complete."""

        def mock_transport(request: httpx.Request):
            url = str(request.url)

            # Presign 요청
            if "/internal/storage/presign-put" in url:
                return httpx.Response(200, json=mock_presign_response)

            # S3 PUT 업로드
            if "s3.amazonaws.com" in url:
                return httpx.Response(200, headers={"ETag": '"abc123def456"'})

            # Complete 콜백
            if "/internal/storage/complete" in url:
                return httpx.Response(200, json={"status": "ok"})

            return httpx.Response(404)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            # request에 따라 다른 응답 반환
            async def mock_request(method, url, **kwargs):
                if "presign-put" in url:
                    response = MagicMock()
                    response.json.return_value = mock_presign_response
                    response.raise_for_status = MagicMock()
                    return response
                return MagicMock()

            async def mock_post(url, **kwargs):
                response = MagicMock()
                if "presign-put" in url:
                    response.json.return_value = mock_presign_response
                elif "complete" in url:
                    response.json.return_value = {"status": "ok"}
                response.raise_for_status = MagicMock()
                return response

            async def mock_put(url, **kwargs):
                response = MagicMock()
                response.headers = {"ETag": '"abc123def456"'}
                response.raise_for_status = MagicMock()
                return response

            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-123/video.mp4"
            result = await provider.put_object(sample_file, key, "video/mp4")

            assert result.key == key
            assert result.url == mock_presign_response["public_url"]
            assert result.content_type == "video/mp4"
            assert result.size_bytes > 0

    @pytest.mark.asyncio
    async def test_put_object_bytes_success(self, provider, mock_presign_response):
        """bytes 데이터 업로드 성공."""
        data = b"test video content bytes"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            async def mock_post(url, **kwargs):
                response = MagicMock()
                if "presign-put" in url:
                    response.json.return_value = mock_presign_response
                else:
                    response.json.return_value = {"status": "ok"}
                response.raise_for_status = MagicMock()
                return response

            async def mock_put(url, **kwargs):
                response = MagicMock()
                response.headers = {"ETag": '"etag-bytes"'}
                response.raise_for_status = MagicMock()
                return response

            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-456/video.mp4"
            result = await provider.put_object(data, key, "video/mp4")

            assert result.key == key
            assert result.size_bytes == len(data)


# =============================================================================
# Size Limit Tests
# =============================================================================


class TestSizeLimitValidation:
    """용량 제한 테스트."""

    @pytest.mark.asyncio
    async def test_file_exceeds_max_size_raises_error(self, provider, large_file):
        """용량 초과 시 StorageUploadError 발생."""
        key = "videos/video-001/script-001/job-789/video.mp4"

        with pytest.raises(StorageUploadError) as exc_info:
            await provider.put_object(large_file, key, "video/mp4")

        assert "exceeds limit" in str(exc_info.value)
        assert key in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_bytes_exceeds_max_size_raises_error(self, provider):
        """bytes 용량 초과 시 StorageUploadError 발생."""
        # 11MB bytes
        data = b"x" * (11 * 1024 * 1024)
        key = "videos/video-001/script-001/job-999/video.mp4"

        with pytest.raises(StorageUploadError) as exc_info:
            await provider.put_object(data, key, "video/mp4")

        assert "exceeds limit" in str(exc_info.value)


# =============================================================================
# Presign Failure Tests
# =============================================================================


class TestPresignFailure:
    """Presign 요청 실패 테스트."""

    @pytest.mark.asyncio
    async def test_presign_http_error_raises_storage_error(self, provider, sample_file):
        """Presign HTTP 5xx 에러 시 재시도 후 StorageUploadError 발생 (Phase 36)."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            async def mock_post(url, **kwargs):
                response = MagicMock()
                response.status_code = 500
                response.text = "Internal Server Error"
                response.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "Server Error",
                    request=MagicMock(),
                    response=response,
                )
                return response

            mock_client.post = mock_post
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-err/video.mp4"

            with pytest.raises(StorageUploadError) as exc_info:
                await provider.put_object(sample_file, key, "video/mp4")

            # Phase 36: 5xx는 재시도 후 실패 메시지
            assert "after 4 attempts" in str(exc_info.value) or "Presign request" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_presign_connection_error_raises_storage_error(
        self, provider, sample_file
    ):
        """Presign 연결 에러 시 재시도 후 StorageUploadError 발생 (Phase 36)."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            async def mock_post(url, **kwargs):
                raise httpx.ConnectError("Connection refused")

            mock_client.post = mock_post
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-conn/video.mp4"

            with pytest.raises(StorageUploadError) as exc_info:
                await provider.put_object(sample_file, key, "video/mp4")

            # Phase 36: 네트워크 에러는 재시도 후 실패 메시지
            assert "after 4 attempts" in str(exc_info.value) or "Presign request" in str(exc_info.value)


# =============================================================================
# PUT Upload Failure Tests
# =============================================================================


class TestPutUploadFailure:
    """PUT 업로드 실패 테스트."""

    @pytest.mark.asyncio
    async def test_put_http_error_raises_storage_error(
        self, provider, sample_file, mock_presign_response
    ):
        """PUT 업로드 403 에러 시 재시도 없이 StorageUploadError 발생 (Phase 36)."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            async def mock_post(url, **kwargs):
                response = MagicMock()
                response.json.return_value = mock_presign_response
                response.raise_for_status = MagicMock()
                return response

            async def mock_put(url, **kwargs):
                response = MagicMock()
                response.status_code = 403
                response.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "Forbidden",
                    request=MagicMock(),
                    response=response,
                )
                return response

            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-put-err/video.mp4"

            with pytest.raises(StorageUploadError) as exc_info:
                await provider.put_object(sample_file, key, "video/mp4")

            # Phase 36: 4xx는 재시도 없이 즉시 실패
            assert "HTTP 403" in str(exc_info.value) or "Presigned PUT upload failed" in str(exc_info.value)


# =============================================================================
# Complete Callback Failure Tests
# =============================================================================


class TestCompleteCallbackFailure:
    """Complete 콜백 실패 테스트."""

    @pytest.mark.asyncio
    async def test_complete_http_error_raises_storage_error(
        self, provider, sample_file, mock_presign_response
    ):
        """Complete 콜백 HTTP 5xx 에러 시 재시도 후 StorageUploadError 발생 (Phase 36)."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            presign_done = False

            async def mock_post(url, **kwargs):
                nonlocal presign_done

                response = MagicMock()
                if "presign-put" in url and not presign_done:
                    # Presign 요청
                    presign_done = True
                    response.json.return_value = mock_presign_response
                    response.raise_for_status = MagicMock()
                else:
                    # Complete 요청 - 5xx 에러
                    response.status_code = 500
                    response.raise_for_status.side_effect = httpx.HTTPStatusError(
                        "Server Error",
                        request=MagicMock(),
                        response=response,
                    )
                return response

            async def mock_put(url, **kwargs):
                response = MagicMock()
                response.headers = {"ETag": '"test-etag"'}
                response.raise_for_status = MagicMock()
                return response

            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-complete-err/video.mp4"

            with pytest.raises(StorageUploadError) as exc_info:
                await provider.put_object(sample_file, key, "video/mp4")

            # Phase 36: 5xx는 재시도 후 실패 메시지
            assert "after 4 attempts" in str(exc_info.value) or "Complete notification" in str(exc_info.value)


# =============================================================================
# get_url Tests
# =============================================================================


class TestGetUrl:
    """get_url 테스트."""

    @pytest.mark.asyncio
    async def test_get_url_constructs_public_url(self, provider):
        """get_url은 public_base_url + key 형태로 URL 구성."""
        key = "videos/video-001/script-001/job-123/video.mp4"

        url = await provider.get_url(key)

        assert url == "https://cdn.example.com/videos/video-001/script-001/job-123/video.mp4"

    @pytest.mark.asyncio
    async def test_get_url_handles_trailing_slash(self, mock_settings):
        """trailing slash 처리 확인."""
        mock_settings.storage_public_base_url = "https://cdn.example.com/"

        with patch("app.core.config.get_settings", return_value=mock_settings):
            provider = BackendPresignedStorageProvider()

        key = "videos/video-001/video.mp4"
        url = await provider.get_url(key)

        # 중복 슬래시 없어야 함
        assert url == "https://cdn.example.com/videos/video-001/video.mp4"


# =============================================================================
# delete_object Tests
# =============================================================================


class TestDeleteObject:
    """delete_object 테스트."""

    @pytest.mark.asyncio
    async def test_delete_object_success(self, provider):
        """delete_object 성공."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            async def mock_post(url, **kwargs):
                response = MagicMock()
                response.raise_for_status = MagicMock()
                return response

            mock_client.post = mock_post
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-del/video.mp4"
            result = await provider.delete_object(key)

            assert result is True

    @pytest.mark.asyncio
    async def test_delete_object_failure_returns_false(self, provider):
        """delete_object 실패 시 False 반환."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            async def mock_post(url, **kwargs):
                raise httpx.ConnectError("Connection refused")

            mock_client.post = mock_post
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-del-fail/video.mp4"
            result = await provider.delete_object(key)

            assert result is False


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestFactoryFunction:
    """get_storage_provider 팩토리 함수 테스트."""

    def setup_method(self):
        """테스트 전 싱글톤 초기화."""
        clear_storage_provider()

    def teardown_method(self):
        """테스트 후 싱글톤 초기화."""
        clear_storage_provider()

    def test_backend_presigned_provider_selection(self, mock_settings, monkeypatch):
        """STORAGE_PROVIDER=backend_presigned 시 올바른 Provider 반환."""
        monkeypatch.setenv("STORAGE_PROVIDER", "backend_presigned")

        with patch("app.core.config.get_settings", return_value=mock_settings):
            provider = get_storage_provider(StorageProvider.BACKEND_PRESIGNED)

        assert isinstance(provider, BackendPresignedStorageProvider)

    def test_env_backend_presigned_provider(self, mock_settings, monkeypatch):
        """환경변수로 backend_presigned 선택."""
        monkeypatch.setenv("STORAGE_PROVIDER", "backend_presigned")

        with patch("app.core.config.get_settings", return_value=mock_settings):
            provider = get_storage_provider()

        assert isinstance(provider, BackendPresignedStorageProvider)


# =============================================================================
# Authorization Header Tests
# =============================================================================


class TestAuthorizationHeader:
    """Authorization 헤더 테스트."""

    @pytest.mark.asyncio
    async def test_service_token_included_in_headers(
        self, provider, sample_file, mock_presign_response
    ):
        """서비스 토큰이 Authorization 헤더에 포함됨."""
        captured_headers = {}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            async def mock_post(url, **kwargs):
                nonlocal captured_headers
                captured_headers = kwargs.get("headers", {})

                response = MagicMock()
                if "presign-put" in url:
                    response.json.return_value = mock_presign_response
                else:
                    response.json.return_value = {"status": "ok"}
                response.raise_for_status = MagicMock()
                return response

            async def mock_put(url, **kwargs):
                response = MagicMock()
                response.headers = {"ETag": '"test-etag"'}
                response.raise_for_status = MagicMock()
                return response

            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-auth/video.mp4"
            await provider.put_object(sample_file, key, "video/mp4")

            assert "Authorization" in captured_headers
            assert captured_headers["Authorization"] == "Bearer test-service-token"


# =============================================================================
# File Not Found Tests
# =============================================================================


class TestFileNotFound:
    """파일 미존재 테스트."""

    @pytest.mark.asyncio
    async def test_path_not_found_raises_error(self, provider, tmp_path):
        """존재하지 않는 Path 객체 시 StorageUploadError 발생."""
        nonexistent = tmp_path / "nonexistent.mp4"
        key = "videos/video-001/script-001/job-notfound/video.mp4"

        with pytest.raises(StorageUploadError) as exc_info:
            await provider.put_object(nonexistent, key, "video/mp4")

        assert "File not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_str_path_not_found_raises_error(self, provider, tmp_path):
        """존재하지 않는 문자열 경로 시 StorageUploadError 발생."""
        nonexistent = str(tmp_path / "nonexistent.mp4")
        key = "videos/video-001/script-001/job-notfound2/video.mp4"

        with pytest.raises(StorageUploadError) as exc_info:
            await provider.put_object(nonexistent, key, "video/mp4")

        assert "File not found" in str(exc_info.value)


# =============================================================================
# Content-Type Inference Tests
# =============================================================================


class TestContentTypeInference:
    """Content-Type 추론 테스트."""

    @pytest.mark.asyncio
    async def test_content_type_inferred_from_key(
        self, provider, mock_presign_response
    ):
        """key에서 Content-Type 자동 추론."""
        data = b"test content"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            captured_content_type = None

            async def mock_post(url, **kwargs):
                nonlocal captured_content_type
                json_data = kwargs.get("json", {})
                if "content_type" in json_data:
                    captured_content_type = json_data["content_type"]

                response = MagicMock()
                if "presign-put" in url:
                    response.json.return_value = mock_presign_response
                else:
                    response.json.return_value = {"status": "ok"}
                response.raise_for_status = MagicMock()
                return response

            async def mock_put(url, **kwargs):
                response = MagicMock()
                response.headers = {"ETag": '"test-etag"'}
                response.raise_for_status = MagicMock()
                return response

            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client_class.return_value = mock_client

            # .mp4 확장자로 video/mp4 추론
            key = "videos/video-001/script-001/job-ct/video.mp4"
            await provider.put_object(data, key)  # content_type 생략

            assert captured_content_type == "video/mp4"
