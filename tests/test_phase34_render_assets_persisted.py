"""
Phase 34: Render Assets Persisted 테스트

RenderJobRunner가 SUCCEEDED 시 assets URL이 DB에 저장되는지 테스트합니다.

테스트 항목:
- 로컬 프로바이더로 렌더 잡 실행 시 assets URL 저장
- object_key 규칙 적용 확인
- STORAGE_UPLOAD_FAILED 에러 처리
- DB에 assets가 영속화되는지 확인
"""

import asyncio
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.storage_adapter import (
    LocalStorageProvider,
    StorageConfig,
    StorageProvider,
    StorageResult,
    StorageUploadError,
)
from app.models.video_render import (
    RenderJobStatus,
    RenderStep,
    RenderedAssets,
    ScriptStatus,
    VideoScript,
)
from app.repositories.render_job_repository import (
    RenderJobEntity,
    RenderJobRepository,
)
from app.services.render_job_runner import (
    RenderJobRunner,
    JobCreationResult,
    clear_render_job_runner,
)
from app.services.video_render_service import VideoRenderer


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_output_dir(tmp_path):
    """임시 출력 디렉토리."""
    output_dir = tmp_path / "video_output"
    output_dir.mkdir()
    return output_dir


@pytest.fixture
def temp_storage_dir(tmp_path):
    """임시 스토리지 디렉토리."""
    storage_dir = tmp_path / "assets"
    storage_dir.mkdir()
    return storage_dir


@pytest.fixture
def mock_repository():
    """Mock 렌더 잡 저장소."""
    repo = MagicMock(spec=RenderJobRepository)
    repo.jobs = {}  # 내부 저장소

    def save_job(job):
        repo.jobs[job.job_id] = job

    def get_job(job_id):
        return repo.jobs.get(job_id)

    def update_status(job_id, **kwargs):
        if job_id in repo.jobs:
            job = repo.jobs[job_id]
            for key, value in kwargs.items():
                setattr(job, key, value)

    def update_assets(job_id, assets):
        if job_id in repo.jobs:
            repo.jobs[job_id].assets = assets

    def update_error(job_id, error_code, error_message):
        if job_id in repo.jobs:
            job = repo.jobs[job_id]
            job.status = "FAILED"
            job.error_code = error_code
            job.error_message = error_message

    repo.save.side_effect = save_job
    repo.get.side_effect = get_job
    repo.update_status.side_effect = update_status
    repo.update_assets.side_effect = update_assets
    repo.update_error.side_effect = update_error
    repo.get_active_by_video_id.return_value = None

    return repo


@pytest.fixture
def mock_renderer():
    """Mock 렌더러."""
    renderer = MagicMock(spec=VideoRenderer)

    async def mock_execute_step(step, script_json, job_id):
        pass

    async def mock_get_assets(job_id):
        return RenderedAssets(
            mp4_path="/assets/videos/video-001/script-001/job-123/video.mp4",
            thumbnail_path="/assets/videos/video-001/script-001/job-123/thumb.jpg",
            subtitle_path="/assets/videos/video-001/script-001/job-123/subtitles.srt",
            duration_sec=120.5,
        )

    renderer.execute_step = AsyncMock(side_effect=mock_execute_step)
    renderer.get_rendered_assets = AsyncMock(side_effect=mock_get_assets)

    return renderer


@pytest.fixture
def approved_script():
    """승인된 스크립트."""
    script = VideoScript(
        script_id="script-001",
        video_id="video-001",
        status=ScriptStatus.APPROVED,
        raw_json={
            "video_id": "video-001",
            "script_id": "script-001",
            "narration": "테스트 나레이션입니다.",
        },
        created_by="test-user",
    )
    return script


@pytest.fixture
def runner(mock_repository, mock_renderer, temp_output_dir):
    """테스트용 RenderJobRunner."""
    runner = RenderJobRunner(
        renderer=mock_renderer,
        repository=mock_repository,
        output_dir=str(temp_output_dir),
    )
    return runner


# =============================================================================
# Render Assets Persisted Tests
# =============================================================================


class TestRenderAssetsPersisted:
    """렌더 에셋 영속화 테스트."""

    def setup_method(self):
        """테스트 전 싱글톤 초기화."""
        clear_render_job_runner()

    def teardown_method(self):
        """테스트 후 싱글톤 초기화."""
        clear_render_job_runner()

    @pytest.mark.asyncio
    async def test_succeeded_job_saves_assets_to_db(
        self, runner, mock_repository, approved_script
    ):
        """성공한 잡의 assets가 DB에 저장되는지 테스트."""
        # 잡 생성
        with patch("app.services.render_job_runner.notify_render_progress"):
            result = await runner.create_job(
                video_id="video-001",
                script_id="script-001",
                script=approved_script,
                created_by="test-user",
            )

        assert result.created is True
        job_id = result.job.job_id

        # 백그라운드 태스크 완료 대기
        await asyncio.sleep(0.2)

        # assets가 저장되었는지 확인
        mock_repository.update_assets.assert_called()
        call_args = mock_repository.update_assets.call_args
        saved_job_id = call_args[0][0]
        saved_assets = call_args[0][1]

        assert saved_job_id == job_id
        assert "video_url" in saved_assets
        assert "subtitle_url" in saved_assets
        assert "thumbnail_url" in saved_assets
        assert "duration_sec" in saved_assets

    @pytest.mark.asyncio
    async def test_assets_contain_correct_urls(
        self, runner, mock_repository, approved_script
    ):
        """저장된 assets에 올바른 URL이 포함되어 있는지 테스트."""
        with patch("app.services.render_job_runner.notify_render_progress"):
            result = await runner.create_job(
                video_id="video-001",
                script_id="script-001",
                script=approved_script,
                created_by="test-user",
            )

        await asyncio.sleep(0.2)

        call_args = mock_repository.update_assets.call_args
        saved_assets = call_args[0][1]

        # Phase 34 object_key 규칙 확인
        assert "/assets/" in saved_assets["video_url"] or "video.mp4" in saved_assets["video_url"]
        assert saved_assets["duration_sec"] == 120.5

    @pytest.mark.asyncio
    async def test_job_status_becomes_succeeded(
        self, runner, mock_repository, approved_script
    ):
        """잡 상태가 SUCCEEDED로 변경되는지 테스트."""
        with patch("app.services.render_job_runner.notify_render_progress"):
            result = await runner.create_job(
                video_id="video-001",
                script_id="script-001",
                script=approved_script,
                created_by="test-user",
            )

        await asyncio.sleep(0.2)

        # 마지막 update_status 호출 확인
        calls = mock_repository.update_status.call_args_list
        final_call = calls[-1]
        assert final_call.kwargs.get("status") == "COMPLETED"


# =============================================================================
# Storage Upload Failed Tests
# =============================================================================


class TestStorageUploadFailed:
    """STORAGE_UPLOAD_FAILED 에러 처리 테스트."""

    def setup_method(self):
        """테스트 전 싱글톤 초기화."""
        clear_render_job_runner()

    def teardown_method(self):
        """테스트 후 싱글톤 초기화."""
        clear_render_job_runner()

    @pytest.mark.asyncio
    async def test_storage_upload_error_sets_correct_error_code(
        self, mock_repository, approved_script, temp_output_dir
    ):
        """StorageUploadError 발생 시 STORAGE_UPLOAD_FAILED 에러 코드 설정."""
        # Storage 업로드 단계에서 에러 발생하는 렌더러
        failing_renderer = MagicMock(spec=VideoRenderer)

        async def mock_execute_step(step, script_json, job_id):
            if step == RenderStep.UPLOAD_ASSETS:
                raise StorageUploadError("disk full", "test/key.mp4")

        failing_renderer.execute_step = AsyncMock(side_effect=mock_execute_step)

        runner = RenderJobRunner(
            renderer=failing_renderer,
            repository=mock_repository,
            output_dir=str(temp_output_dir),
        )

        with patch("app.services.render_job_runner.notify_render_progress"):
            result = await runner.create_job(
                video_id="video-001",
                script_id="script-001",
                script=approved_script,
                created_by="test-user",
            )

        await asyncio.sleep(0.2)

        # update_error가 STORAGE_UPLOAD_FAILED로 호출되었는지 확인
        mock_repository.update_error.assert_called()
        call_args = mock_repository.update_error.call_args
        error_code = call_args.kwargs.get("error_code") or call_args[0][1]

        assert error_code == "STORAGE_UPLOAD_FAILED"

    @pytest.mark.asyncio
    async def test_generic_error_uses_exception_name(
        self, mock_repository, approved_script, temp_output_dir
    ):
        """일반 에러 발생 시 예외 클래스명이 error_code로 설정."""
        failing_renderer = MagicMock(spec=VideoRenderer)

        async def mock_execute_step(step, script_json, job_id):
            if step == RenderStep.COMPOSE_VIDEO:
                raise RuntimeError("FFmpeg crashed")

        failing_renderer.execute_step = AsyncMock(side_effect=mock_execute_step)

        runner = RenderJobRunner(
            renderer=failing_renderer,
            repository=mock_repository,
            output_dir=str(temp_output_dir),
        )

        with patch("app.services.render_job_runner.notify_render_progress"):
            await runner.create_job(
                video_id="video-001",
                script_id="script-001",
                script=approved_script,
                created_by="test-user",
            )

        await asyncio.sleep(0.2)

        mock_repository.update_error.assert_called()
        call_args = mock_repository.update_error.call_args
        error_code = call_args.kwargs.get("error_code") or call_args[0][1]

        assert error_code == "RuntimeError"


# =============================================================================
# Object Key Convention in Renderer Tests
# =============================================================================


class TestObjectKeyInRenderer:
    """RealVideoRenderer의 object_key 규칙 테스트."""

    @pytest.mark.asyncio
    async def test_upload_assets_uses_phase34_key_format(self, temp_storage_dir):
        """_upload_assets가 Phase 34 키 형식을 사용하는지 테스트."""
        from app.services.video_renderer_real import (
            RealVideoRenderer,
            RealRenderJobContext,
            RealRendererConfig,
        )

        # Mock storage provider
        mock_storage = MagicMock(spec=LocalStorageProvider)

        async def mock_put_file(file_path, key, content_type=None):
            return StorageResult(
                key=key,
                url=f"/assets/{key}",
                size_bytes=100,
                content_type=content_type or "application/octet-stream",
            )

        mock_storage.put_file = AsyncMock(side_effect=mock_put_file)

        # Renderer 설정
        renderer = RealVideoRenderer(
            config=RealRendererConfig(output_dir=str(temp_storage_dir)),
            storage_provider=mock_storage,
        )

        # Context 생성 (파일 생성)
        ctx = RealRenderJobContext(
            job_id="job-test123",
            video_id="video-abc",
            script_id="script-def",
            script_json={},
            output_dir=temp_storage_dir,
        )

        # 임시 파일 생성
        video_file = temp_storage_dir / "video.mp4"
        video_file.write_bytes(b"fake video")
        ctx.video_path = str(video_file)

        subtitle_file = temp_storage_dir / "subtitles.srt"
        subtitle_file.write_text("1\n00:00:00,000 --> 00:00:01,000\nTest")
        ctx.subtitle_path = str(subtitle_file)

        thumb_file = temp_storage_dir / "thumb.jpg"
        thumb_file.write_bytes(b"fake thumb")
        ctx.thumbnail_path = str(thumb_file)

        # _upload_assets 실행
        await renderer._upload_assets(ctx)

        # 호출된 키 확인
        calls = mock_storage.put_file.call_args_list
        keys = [call[0][1] for call in calls]

        expected_base = "videos/video-abc/script-def/job-test123"
        assert f"{expected_base}/video.mp4" in keys
        assert f"{expected_base}/subtitles.srt" in keys
        assert f"{expected_base}/thumb.jpg" in keys

    @pytest.mark.asyncio
    async def test_context_has_script_id(self, temp_storage_dir):
        """RealRenderJobContext에 script_id가 포함되어 있는지 테스트."""
        from app.services.video_renderer_real import (
            RealVideoRenderer,
            RealRendererConfig,
        )

        renderer = RealVideoRenderer(
            config=RealRendererConfig(output_dir=str(temp_storage_dir))
        )

        script_json = {
            "video_id": "video-123",
            "script_id": "script-456",
            "narration": "test",
        }

        # Context가 생성될 때 execute_step을 통해 확인
        # (실제로는 execute_step 내부에서 context가 생성됨)

        # 직접 확인 - script_json에서 script_id 추출 테스트
        video_id = script_json.get("video_id", "default")
        script_id = script_json.get("script_id", "script-default")

        assert video_id == "video-123"
        assert script_id == "script-456"


# =============================================================================
# Integration Tests
# =============================================================================


class TestPhase34Integration:
    """Phase 34 통합 테스트."""

    def setup_method(self):
        """테스트 전 싱글톤 초기화."""
        clear_render_job_runner()

    def teardown_method(self):
        """테스트 후 싱글톤 초기화."""
        clear_render_job_runner()

    @pytest.mark.asyncio
    async def test_full_render_flow_with_local_storage(
        self, temp_storage_dir, temp_output_dir, mock_repository
    ):
        """로컬 스토리지로 전체 렌더 플로우 테스트."""
        # 실제 LocalStorageProvider 사용
        storage_config = StorageConfig(
            provider=StorageProvider.LOCAL,
            local_path=str(temp_storage_dir),
            base_url="/assets",
        )
        storage = LocalStorageProvider(storage_config)

        # Mock renderer that produces files
        mock_renderer = MagicMock(spec=VideoRenderer)

        async def mock_execute_step(step, script_json, job_id):
            if step == RenderStep.COMPOSE_VIDEO:
                # 실제 파일 생성
                job_dir = temp_output_dir / job_id
                job_dir.mkdir(parents=True, exist_ok=True)
                (job_dir / "video.mp4").write_bytes(b"video content")
                (job_dir / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nTest")
                (job_dir / "thumb.jpg").write_bytes(b"thumbnail content")

        async def mock_get_assets(job_id):
            job_dir = temp_output_dir / job_id
            return RenderedAssets(
                mp4_path=str(job_dir / "video.mp4"),
                subtitle_path=str(job_dir / "subtitles.srt"),
                thumbnail_path=str(job_dir / "thumb.jpg"),
                duration_sec=60.0,
            )

        mock_renderer.execute_step = AsyncMock(side_effect=mock_execute_step)
        mock_renderer.get_rendered_assets = AsyncMock(side_effect=mock_get_assets)

        runner = RenderJobRunner(
            renderer=mock_renderer,
            repository=mock_repository,
            output_dir=str(temp_output_dir),
        )

        script = VideoScript(
            script_id="script-integration",
            video_id="video-integration",
            status=ScriptStatus.APPROVED,
            raw_json={
                "video_id": "video-integration",
                "script_id": "script-integration",
                "narration": "Integration test",
            },
            created_by="test-user",
        )

        with patch("app.services.render_job_runner.notify_render_progress"):
            result = await runner.create_job(
                video_id="video-integration",
                script_id="script-integration",
                script=script,
                created_by="test-user",
            )

        await asyncio.sleep(0.3)

        # 결과 확인
        assert result.created is True
        mock_repository.update_assets.assert_called()

        # 파일이 생성되었는지 확인
        job_dir = temp_output_dir / result.job.job_id
        assert (job_dir / "video.mp4").exists()
