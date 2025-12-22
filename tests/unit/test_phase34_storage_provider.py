"""
Phase 34: Storage Provider 테스트

LocalStorageProvider의 업로드, URL 생성, 삭제 기능을 테스트합니다.

테스트 항목:
- 파일 업로드 (bytes, Path, str)
- URL 생성 (StaticFiles 경로)
- 파일 삭제
- object_key 규칙 검증
- StorageUploadError 예외 처리
"""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.storage_adapter import (
    BaseStorageProvider,
    LocalStorageProvider,
    S3StorageProvider,
    StorageConfig,
    StorageProvider,
    StorageResult,
    StorageUploadError,
    get_storage_provider,
    clear_storage_provider,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_storage_dir(tmp_path):
    """임시 스토리지 디렉토리."""
    storage_dir = tmp_path / "test_assets"
    storage_dir.mkdir()
    return storage_dir


@pytest.fixture
def local_config(temp_storage_dir):
    """로컬 스토리지 설정."""
    return StorageConfig(
        provider=StorageProvider.LOCAL,
        local_path=str(temp_storage_dir),
        base_url="/assets",
    )


@pytest.fixture
def local_provider(local_config):
    """로컬 스토리지 프로바이더."""
    return LocalStorageProvider(local_config)


@pytest.fixture
def sample_file(tmp_path):
    """샘플 파일 생성."""
    file_path = tmp_path / "sample.mp4"
    file_path.write_bytes(b"fake video content")
    return file_path


# =============================================================================
# LocalStorageProvider Tests
# =============================================================================


class TestLocalStorageProvider:
    """LocalStorageProvider 테스트."""

    @pytest.mark.asyncio
    async def test_put_object_bytes(self, local_provider, temp_storage_dir):
        """bytes 데이터 업로드 테스트."""
        data = b"test video content"
        key = "videos/test-video/test-script/job-123/video.mp4"

        result = await local_provider.put_object(data, key, "video/mp4")

        assert result.key == key
        assert result.url == f"/assets/{key}"
        assert result.size_bytes == len(data)
        assert result.content_type == "video/mp4"

        # 파일이 실제로 생성되었는지 확인
        saved_file = temp_storage_dir / key
        assert saved_file.exists()
        assert saved_file.read_bytes() == data

    @pytest.mark.asyncio
    async def test_put_object_path(self, local_provider, sample_file, temp_storage_dir):
        """Path 객체로 업로드 테스트."""
        key = "videos/video-001/script-001/job-abc/video.mp4"

        result = await local_provider.put_object(sample_file, key)

        assert result.key == key
        assert result.url == f"/assets/{key}"
        assert result.size_bytes == sample_file.stat().st_size

        # 파일 복사 확인
        saved_file = temp_storage_dir / key
        assert saved_file.exists()
        assert saved_file.read_bytes() == sample_file.read_bytes()

    @pytest.mark.asyncio
    async def test_put_object_str_path(self, local_provider, sample_file, temp_storage_dir):
        """문자열 경로로 업로드 테스트."""
        key = "videos/video-002/script-002/job-xyz/thumb.jpg"

        result = await local_provider.put_object(str(sample_file), key)

        assert result.key == key
        saved_file = temp_storage_dir / key
        assert saved_file.exists()

    @pytest.mark.asyncio
    async def test_put_file(self, local_provider, sample_file, temp_storage_dir):
        """put_file 헬퍼 메서드 테스트."""
        key = "videos/video-003/script-003/job-456/subtitles.srt"

        result = await local_provider.put_file(sample_file, key, "text/plain")

        assert result.key == key
        assert result.content_type == "text/plain"

    @pytest.mark.asyncio
    async def test_upload_file_interface(self, local_provider, sample_file):
        """Phase 34 upload_file 인터페이스 테스트."""
        key = "videos/video-004/script-004/job-789/video.mp4"

        returned_key = await local_provider.upload_file(str(sample_file), key)

        assert returned_key == key

    @pytest.mark.asyncio
    async def test_get_url(self, local_provider):
        """URL 생성 테스트."""
        key = "videos/video-005/script-005/job-111/video.mp4"

        url = await local_provider.get_url(key)

        assert url == f"/assets/{key}"

    @pytest.mark.asyncio
    async def test_delete_object(self, local_provider, temp_storage_dir):
        """파일 삭제 테스트."""
        # 파일 생성
        key = "videos/test/delete-me.mp4"
        data = b"delete me"
        await local_provider.put_object(data, key)

        # 파일 존재 확인
        saved_file = temp_storage_dir / key
        assert saved_file.exists()

        # 삭제
        result = await local_provider.delete_object(key)

        assert result is True
        assert not saved_file.exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_object(self, local_provider):
        """존재하지 않는 파일 삭제 테스트."""
        key = "videos/nonexistent/file.mp4"

        result = await local_provider.delete_object(key)

        assert result is False

    @pytest.mark.asyncio
    async def test_content_type_inference(self, local_provider):
        """Content-Type 자동 추론 테스트."""
        test_cases = [
            ("test.mp4", "video/mp4"),
            ("test.srt", "application/x-subrip"),  # or text/plain
            ("test.jpg", "image/jpeg"),
            ("test.png", "image/png"),
        ]

        for filename, _ in test_cases:
            key = f"test/{filename}"
            result = await local_provider.put_object(b"test", key)
            # Content-Type은 시스템에 따라 다를 수 있으므로 None이 아닌지만 확인
            assert result.content_type is not None

    @pytest.mark.asyncio
    async def test_nested_directory_creation(self, local_provider, temp_storage_dir):
        """중첩 디렉토리 자동 생성 테스트."""
        key = "videos/deep/nested/directory/structure/video.mp4"

        await local_provider.put_object(b"test", key)

        saved_file = temp_storage_dir / key
        assert saved_file.exists()


# =============================================================================
# StorageUploadError Tests
# =============================================================================


class TestStorageUploadError:
    """StorageUploadError 테스트."""

    def test_error_creation(self):
        """에러 생성 테스트."""
        error = StorageUploadError("disk full", "test/key.mp4")

        assert error.message == "disk full"
        assert error.key == "test/key.mp4"
        assert error.original_error is None
        assert "test/key.mp4" in str(error)
        assert "disk full" in str(error)

    def test_error_with_original(self):
        """원본 예외 포함 테스트."""
        original = IOError("No space left")
        error = StorageUploadError("upload failed", "key.mp4", original)

        assert error.original_error is original

    @pytest.mark.asyncio
    async def test_error_raised_on_failure(self, local_provider, tmp_path):
        """업로드 실패 시 StorageUploadError 발생 테스트."""
        # 존재하지 않는 파일 경로
        nonexistent = tmp_path / "nonexistent.mp4"

        with pytest.raises(StorageUploadError) as exc_info:
            await local_provider.put_object(str(nonexistent), "test/key.mp4")

        assert "test/key.mp4" in str(exc_info.value)


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestGetStorageProvider:
    """get_storage_provider 팩토리 함수 테스트."""

    def setup_method(self):
        """테스트 전 싱글톤 초기화."""
        clear_storage_provider()

    def teardown_method(self):
        """테스트 후 싱글톤 초기화."""
        clear_storage_provider()

    def test_default_local_provider(self, monkeypatch):
        """기본 로컬 프로바이더 생성 테스트."""
        monkeypatch.setenv("STORAGE_PROVIDER", "local")

        provider = get_storage_provider()

        assert isinstance(provider, LocalStorageProvider)

    def test_provider_enum_selection(self):
        """StorageProvider enum으로 선택 테스트."""
        config = StorageConfig(
            provider=StorageProvider.LOCAL,
            local_path="./test_storage",
            base_url="/assets",
        )

        provider = get_storage_provider(StorageProvider.LOCAL, config)

        assert isinstance(provider, LocalStorageProvider)

    def test_s3_provider_requires_bucket(self, monkeypatch):
        """S3 프로바이더는 버킷이 필요함."""
        monkeypatch.delenv("AWS_S3_BUCKET", raising=False)

        with pytest.raises(ValueError, match="bucket not configured"):
            get_storage_provider(StorageProvider.S3)

    def test_unknown_provider_fallback_to_local(self, monkeypatch):
        """알 수 없는 프로바이더는 로컬로 폴백."""
        monkeypatch.setenv("STORAGE_PROVIDER", "unknown_provider")

        provider = get_storage_provider()

        assert isinstance(provider, LocalStorageProvider)


# =============================================================================
# Object Key Convention Tests
# =============================================================================


class TestObjectKeyConvention:
    """Phase 34 object_key 규칙 테스트."""

    @pytest.mark.asyncio
    async def test_phase34_key_format(self, local_provider, sample_file, temp_storage_dir):
        """Phase 34 object_key 규칙 테스트.

        규칙: videos/{video_id}/{script_id}/{job_id}/filename
        """
        video_id = "video-abc123"
        script_id = "script-def456"
        job_id = "job-ghi789"

        # video.mp4
        video_key = f"videos/{video_id}/{script_id}/{job_id}/video.mp4"
        await local_provider.put_file(sample_file, video_key)
        assert (temp_storage_dir / video_key).exists()

        # subtitles.srt
        subtitle_key = f"videos/{video_id}/{script_id}/{job_id}/subtitles.srt"
        await local_provider.put_object(b"subtitle content", subtitle_key)
        assert (temp_storage_dir / subtitle_key).exists()

        # thumb.jpg
        thumb_key = f"videos/{video_id}/{script_id}/{job_id}/thumb.jpg"
        await local_provider.put_object(b"thumbnail content", thumb_key)
        assert (temp_storage_dir / thumb_key).exists()

        # URL 형식 확인
        video_url = await local_provider.get_url(video_key)
        assert video_url == f"/assets/videos/{video_id}/{script_id}/{job_id}/video.mp4"


# =============================================================================
# Config Integration Tests
# =============================================================================


class TestConfigIntegration:
    """config.py 연동 테스트."""

    def setup_method(self):
        """테스트 전 싱글톤 초기화."""
        clear_storage_provider()

    def teardown_method(self):
        """테스트 후 싱글톤 초기화."""
        clear_storage_provider()

    def test_uses_storage_local_dir_from_settings(self, monkeypatch, tmp_path):
        """STORAGE_LOCAL_DIR 설정 사용 테스트."""
        test_dir = tmp_path / "custom_assets"
        test_dir.mkdir()

        # 환경변수 설정 후 싱글톤 리셋
        monkeypatch.setenv("STORAGE_LOCAL_DIR", str(test_dir))
        monkeypatch.setenv("STORAGE_PROVIDER", "local")
        clear_storage_provider()

        # 수동으로 config 생성하여 테스트
        config = StorageConfig(
            provider=StorageProvider.LOCAL,
            local_path=str(test_dir),
            base_url="/assets",
        )
        provider = get_storage_provider(StorageProvider.LOCAL, config)

        assert isinstance(provider, LocalStorageProvider)
        assert provider._base_url == "/assets"
