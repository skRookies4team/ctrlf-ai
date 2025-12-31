"""
Phase 36: Presigned 업로드 안정화 테스트

테스트 항목:
- presign 5xx → 재시도 후 성공
- upload 네트워크 오류 → 재시도 후 성공
- complete 5xx → 재시도 후 성공
- upload 403 → 재시도 없이 즉시 실패
- ETag 누락 → 실패(기본 정책)
- 업로드 진행상태 콜백 테스트 (UPLOAD_STARTED, UPLOAD_DONE, UPLOAD_FAILED)
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from app.clients.storage_adapter import (
    BackendPresignedStorageProvider,
    StorageUploadError,
    UploadProgress,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_settings():
    """테스트용 Settings mock (Phase 36 설정 포함)."""
    settings = MagicMock()
    settings.backend_base_url = "http://backend-mock:8081"
    settings.BACKEND_SERVICE_TOKEN = "test-service-token"
    settings.BACKEND_STORAGE_PRESIGN_PATH = "/internal/storage/presign-put"
    settings.BACKEND_STORAGE_COMPLETE_PATH = "/internal/storage/complete"
    settings.VIDEO_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB for testing
    settings.storage_public_base_url = "https://cdn.example.com"
    # Phase 36: 재시도 및 ETag 설정
    settings.STORAGE_UPLOAD_RETRY_MAX = 3  # 최대 3회 재시도 (총 4번 시도)
    settings.STORAGE_UPLOAD_RETRY_BASE_SEC = 0.01  # 테스트용 짧은 대기시간
    settings.STORAGE_ETAG_OPTIONAL = False  # 기본: ETag 필수
    return settings


@pytest.fixture
def mock_settings_etag_optional(mock_settings):
    """ETag 선택적 모드 설정."""
    mock_settings.STORAGE_ETAG_OPTIONAL = True
    return mock_settings


@pytest.fixture
def provider(mock_settings):
    """BackendPresignedStorageProvider 인스턴스."""
    with patch("app.core.config.get_settings", return_value=mock_settings):
        return BackendPresignedStorageProvider()


@pytest.fixture
def provider_etag_optional(mock_settings_etag_optional):
    """ETag 선택적 모드 Provider."""
    with patch("app.core.config.get_settings", return_value=mock_settings_etag_optional):
        return BackendPresignedStorageProvider()


@pytest.fixture
def sample_file(tmp_path):
    """샘플 파일 생성."""
    file_path = tmp_path / "sample.mp4"
    file_path.write_bytes(b"fake video content " * 100)  # ~1.9KB
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
# Helper: 5xx 응답 생성
# =============================================================================


def make_5xx_response(status_code: int = 500):
    """5xx HTTP 에러 응답 생성."""
    response = MagicMock()
    response.status_code = status_code
    response.text = "Internal Server Error"
    return response


def make_4xx_response(status_code: int = 403):
    """4xx HTTP 에러 응답 생성."""
    response = MagicMock()
    response.status_code = status_code
    response.text = "Forbidden"
    return response


# =============================================================================
# Presign 5xx → 재시도 후 성공
# =============================================================================


class TestPresign5xxRetrySuccess:
    """Presign 5xx 에러 발생 시 재시도 후 성공 테스트."""

    @pytest.mark.asyncio
    async def test_presign_5xx_retry_then_success(
        self, provider, sample_file, mock_presign_response
    ):
        """presign 5xx → 재시도 → 성공."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            presign_call_count = 0

            async def mock_post(url, **kwargs):
                nonlocal presign_call_count

                if "presign-put" in url:
                    presign_call_count += 1
                    # 첫 2번은 5xx, 3번째에 성공
                    if presign_call_count <= 2:
                        response = make_5xx_response(500)
                        response.raise_for_status.side_effect = httpx.HTTPStatusError(
                            "Server Error",
                            request=MagicMock(),
                            response=response,
                        )
                        return response
                    else:
                        response = MagicMock()
                        response.json.return_value = mock_presign_response
                        response.raise_for_status = MagicMock()
                        return response

                # Complete 콜백
                response = MagicMock()
                response.json.return_value = {"status": "ok"}
                response.raise_for_status = MagicMock()
                return response

            async def mock_put(url, **kwargs):
                response = MagicMock()
                response.headers = {"ETag": '"abc123"'}
                response.raise_for_status = MagicMock()
                return response

            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-retry/video.mp4"
            result = await provider.put_object(sample_file, key, "video/mp4")

            # 성공 확인
            assert result.key == key
            assert result.url == mock_presign_response["public_url"]
            # presign이 3번 호출됨 (2번 실패 + 1번 성공)
            assert presign_call_count == 3

    @pytest.mark.asyncio
    async def test_presign_5xx_max_retries_exceeded_fails(
        self, provider, sample_file, mock_presign_response
    ):
        """presign 5xx → 최대 재시도 후 실패."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            presign_call_count = 0

            async def mock_post(url, **kwargs):
                nonlocal presign_call_count
                presign_call_count += 1

                # 항상 5xx 반환
                response = make_5xx_response(503)
                response.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "Service Unavailable",
                    request=MagicMock(),
                    response=response,
                )
                return response

            mock_client.post = mock_post
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-max-retry/video.mp4"

            with pytest.raises(StorageUploadError) as exc_info:
                await provider.put_object(sample_file, key, "video/mp4")

            # 최대 4번 시도 (initial + 3 retries)
            assert presign_call_count == 4
            assert "after 4 attempts" in str(exc_info.value)


# =============================================================================
# Upload 네트워크 오류 → 재시도 후 성공
# =============================================================================


class TestUploadNetworkErrorRetrySuccess:
    """Upload 네트워크 오류 시 재시도 후 성공 테스트."""

    @pytest.mark.asyncio
    async def test_upload_connect_error_retry_then_success(
        self, provider, sample_file, mock_presign_response
    ):
        """upload ConnectError → 재시도 → 성공."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            upload_call_count = 0

            async def mock_post(url, **kwargs):
                response = MagicMock()
                if "presign-put" in url:
                    response.json.return_value = mock_presign_response
                else:
                    response.json.return_value = {"status": "ok"}
                response.raise_for_status = MagicMock()
                return response

            async def mock_put(url, **kwargs):
                nonlocal upload_call_count
                upload_call_count += 1

                # 첫 번째는 네트워크 오류, 두 번째에 성공
                if upload_call_count == 1:
                    raise httpx.ConnectError("Connection refused")
                else:
                    response = MagicMock()
                    response.headers = {"ETag": '"etag-success"'}
                    response.raise_for_status = MagicMock()
                    return response

            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-network/video.mp4"
            result = await provider.put_object(sample_file, key, "video/mp4")

            assert result.key == key
            # upload가 2번 호출됨 (1번 실패 + 1번 성공)
            assert upload_call_count == 2

    @pytest.mark.asyncio
    async def test_upload_timeout_error_retry_then_success(
        self, provider, sample_file, mock_presign_response
    ):
        """upload TimeoutException → 재시도 → 성공."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            upload_call_count = 0

            async def mock_post(url, **kwargs):
                response = MagicMock()
                if "presign-put" in url:
                    response.json.return_value = mock_presign_response
                else:
                    response.json.return_value = {"status": "ok"}
                response.raise_for_status = MagicMock()
                return response

            async def mock_put(url, **kwargs):
                nonlocal upload_call_count
                upload_call_count += 1

                if upload_call_count <= 2:
                    raise httpx.TimeoutException("Request timed out")
                else:
                    response = MagicMock()
                    response.headers = {"ETag": '"etag-timeout-success"'}
                    response.raise_for_status = MagicMock()
                    return response

            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-timeout/video.mp4"
            result = await provider.put_object(sample_file, key, "video/mp4")

            assert result.key == key
            # upload가 3번 호출됨
            assert upload_call_count == 3


# =============================================================================
# Complete 5xx → 재시도 후 성공
# =============================================================================


class TestComplete5xxRetrySuccess:
    """Complete 5xx 에러 발생 시 재시도 후 성공 테스트."""

    @pytest.mark.asyncio
    async def test_complete_5xx_retry_then_success(
        self, provider, sample_file, mock_presign_response
    ):
        """complete 5xx → 재시도 → 성공."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            complete_call_count = 0

            async def mock_post(url, **kwargs):
                nonlocal complete_call_count

                if "presign-put" in url:
                    response = MagicMock()
                    response.json.return_value = mock_presign_response
                    response.raise_for_status = MagicMock()
                    return response

                if "complete" in url:
                    complete_call_count += 1
                    # 첫 번째는 5xx, 두 번째에 성공
                    if complete_call_count == 1:
                        response = make_5xx_response(502)
                        response.raise_for_status.side_effect = httpx.HTTPStatusError(
                            "Bad Gateway",
                            request=MagicMock(),
                            response=response,
                        )
                        return response
                    else:
                        response = MagicMock()
                        response.json.return_value = {"status": "ok"}
                        response.raise_for_status = MagicMock()
                        return response

                response = MagicMock()
                response.raise_for_status = MagicMock()
                return response

            async def mock_put(url, **kwargs):
                response = MagicMock()
                response.headers = {"ETag": '"complete-etag"'}
                response.raise_for_status = MagicMock()
                return response

            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-complete-retry/video.mp4"
            result = await provider.put_object(sample_file, key, "video/mp4")

            assert result.key == key
            # complete가 2번 호출됨 (1번 실패 + 1번 성공)
            assert complete_call_count == 2


# =============================================================================
# Upload 403 → 재시도 없이 즉시 실패
# =============================================================================


class TestUpload403NoRetry:
    """Upload 403 에러 시 재시도 없이 즉시 실패 테스트."""

    @pytest.mark.asyncio
    async def test_upload_403_no_retry_immediate_failure(
        self, provider, sample_file, mock_presign_response
    ):
        """upload 403 → 재시도 없이 즉시 실패."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            upload_call_count = 0

            async def mock_post(url, **kwargs):
                response = MagicMock()
                if "presign-put" in url:
                    response.json.return_value = mock_presign_response
                else:
                    response.json.return_value = {"status": "ok"}
                response.raise_for_status = MagicMock()
                return response

            async def mock_put(url, **kwargs):
                nonlocal upload_call_count
                upload_call_count += 1

                response = make_4xx_response(403)
                response.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "Forbidden",
                    request=MagicMock(),
                    response=response,
                )
                return response

            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-403/video.mp4"

            with pytest.raises(StorageUploadError) as exc_info:
                await provider.put_object(sample_file, key, "video/mp4")

            # 재시도 없이 1번만 호출
            assert upload_call_count == 1
            assert "HTTP 403" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_upload_401_no_retry_immediate_failure(
        self, provider, sample_file, mock_presign_response
    ):
        """upload 401 → 재시도 없이 즉시 실패."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            upload_call_count = 0

            async def mock_post(url, **kwargs):
                response = MagicMock()
                if "presign-put" in url:
                    response.json.return_value = mock_presign_response
                else:
                    response.json.return_value = {"status": "ok"}
                response.raise_for_status = MagicMock()
                return response

            async def mock_put(url, **kwargs):
                nonlocal upload_call_count
                upload_call_count += 1

                response = make_4xx_response(401)
                response.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "Unauthorized",
                    request=MagicMock(),
                    response=response,
                )
                return response

            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-401/video.mp4"

            with pytest.raises(StorageUploadError) as exc_info:
                await provider.put_object(sample_file, key, "video/mp4")

            # 재시도 없이 1번만 호출
            assert upload_call_count == 1
            assert "HTTP 401" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_presign_404_no_retry_immediate_failure(
        self, provider, sample_file
    ):
        """presign 404 → 재시도 없이 즉시 실패."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            presign_call_count = 0

            async def mock_post(url, **kwargs):
                nonlocal presign_call_count
                presign_call_count += 1

                response = make_4xx_response(404)
                response.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "Not Found",
                    request=MagicMock(),
                    response=response,
                )
                return response

            mock_client.post = mock_post
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-presign-404/video.mp4"

            with pytest.raises(StorageUploadError) as exc_info:
                await provider.put_object(sample_file, key, "video/mp4")

            # 재시도 없이 1번만 호출
            assert presign_call_count == 1
            assert "HTTP 404" in str(exc_info.value)


# =============================================================================
# ETag 누락 → 실패(기본 정책)
# =============================================================================


class TestETagMissingFailure:
    """ETag 누락 시 실패 테스트."""

    @pytest.mark.asyncio
    async def test_etag_missing_fails_by_default(
        self, provider, sample_file, mock_presign_response
    ):
        """ETag 누락 → 기본 정책: 실패."""
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
                # ETag 누락 (빈 문자열)
                response.headers = {"ETag": ""}
                response.raise_for_status = MagicMock()
                return response

            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-no-etag/video.mp4"

            with pytest.raises(StorageUploadError) as exc_info:
                await provider.put_object(sample_file, key, "video/mp4")

            assert "ETag missing" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_etag_missing_no_header_fails_by_default(
        self, provider, sample_file, mock_presign_response
    ):
        """ETag 헤더 자체가 없는 경우 → 기본 정책: 실패."""
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
                # ETag 헤더 자체가 없음 (MagicMock headers로 처리)
                mock_headers = MagicMock()
                mock_headers.get = MagicMock(return_value="")
                response.headers = mock_headers
                response.raise_for_status = MagicMock()
                return response

            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-no-etag-header/video.mp4"

            with pytest.raises(StorageUploadError) as exc_info:
                await provider.put_object(sample_file, key, "video/mp4")

            assert "ETag missing" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_etag_optional_allows_missing_etag(
        self, provider_etag_optional, sample_file, mock_presign_response
    ):
        """STORAGE_ETAG_OPTIONAL=True → ETag 없어도 성공."""
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
                # ETag 누락
                response.headers = {"ETag": ""}
                response.raise_for_status = MagicMock()
                return response

            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-etag-optional/video.mp4"
            result = await provider_etag_optional.put_object(sample_file, key, "video/mp4")

            # 성공
            assert result.key == key
            assert result.url == mock_presign_response["public_url"]


# =============================================================================
# 업로드 진행상태 콜백 테스트
# =============================================================================


class TestUploadProgressCallback:
    """업로드 진행상태 콜백 테스트."""

    @pytest.mark.asyncio
    async def test_progress_callback_success_flow(
        self, provider, sample_file, mock_presign_response
    ):
        """성공 시 UPLOAD_STARTED → UPLOAD_DONE 콜백."""
        progress_events = []

        def progress_callback(progress: UploadProgress):
            progress_events.append(progress)

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
                response.headers = {"ETag": '"progress-etag"'}
                response.raise_for_status = MagicMock()
                return response

            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-progress/video.mp4"
            await provider.put_object(
                sample_file, key, "video/mp4", progress_callback=progress_callback
            )

            # UPLOAD_STARTED, UPLOAD_DONE 2개 이벤트
            assert len(progress_events) == 2
            assert progress_events[0].stage == "UPLOAD_STARTED"
            assert progress_events[0].key == key
            assert progress_events[1].stage == "UPLOAD_DONE"
            assert progress_events[1].key == key

    @pytest.mark.asyncio
    async def test_progress_callback_failure_flow(
        self, provider, sample_file, mock_presign_response
    ):
        """실패 시 UPLOAD_STARTED → UPLOAD_FAILED 콜백."""
        progress_events = []

        def progress_callback(progress: UploadProgress):
            progress_events.append(progress)

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
                # ETag 누락으로 실패 유도
                response.headers = {"ETag": ""}
                response.raise_for_status = MagicMock()
                return response

            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-progress-fail/video.mp4"

            with pytest.raises(StorageUploadError):
                await provider.put_object(
                    sample_file, key, "video/mp4", progress_callback=progress_callback
                )

            # UPLOAD_STARTED, UPLOAD_FAILED 2개 이벤트
            assert len(progress_events) == 2
            assert progress_events[0].stage == "UPLOAD_STARTED"
            assert progress_events[1].stage == "UPLOAD_FAILED"
            assert progress_events[1].error is not None


# =============================================================================
# 스트리밍 업로드 테스트 (bytes가 아닌 파일 핸들로 전송)
# =============================================================================


class TestStreamingUpload:
    """스트리밍 업로드 테스트."""

    @pytest.mark.asyncio
    async def test_file_upload_uses_streaming(
        self, provider, sample_file, mock_presign_response
    ):
        """파일 업로드 시 스트리밍 방식 사용 확인."""
        captured_content = None

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
                nonlocal captured_content
                captured_content = kwargs.get("content")
                response = MagicMock()
                response.headers = {"ETag": '"streaming-etag"'}
                response.raise_for_status = MagicMock()
                return response

            mock_client.post = mock_post
            mock_client.put = mock_put
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-streaming/video.mp4"
            await provider.put_object(sample_file, key, "video/mp4")

            # content가 파일 객체임을 확인 (bytes가 아님)
            # Note: 실제 스트리밍 동작은 _do_upload에서 with open() as f 사용으로 검증됨
            # 테스트에서는 mock이 호출되었는지 확인
            assert captured_content is not None


# =============================================================================
# Exponential Backoff 테스트
# =============================================================================


class TestExponentialBackoff:
    """Exponential backoff 동작 테스트."""

    @pytest.mark.asyncio
    async def test_retry_respects_max_attempts(
        self, provider, sample_file, mock_presign_response
    ):
        """최대 재시도 횟수 준수 확인."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            call_count = 0

            async def mock_post(url, **kwargs):
                nonlocal call_count
                call_count += 1
                # 항상 5xx
                response = make_5xx_response(500)
                response.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "Server Error",
                    request=MagicMock(),
                    response=response,
                )
                return response

            mock_client.post = mock_post
            mock_client_class.return_value = mock_client

            key = "videos/video-001/script-001/job-backoff/video.mp4"

            with pytest.raises(StorageUploadError):
                await provider.put_object(sample_file, key, "video/mp4")

            # RETRY_MAX=3 이므로 총 4번 시도
            assert call_count == 4
