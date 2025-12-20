"""
Phase 32: Video Rendering Tests

실제 렌더링 파이프라인 테스트.

테스트 케이스:
1. TTS Provider 선택 로직
2. Storage 업로드 호출
3. Progress 이벤트 발행
4. 통합 테스트: 렌더 잡 생성 → RUNNING → SUCCEEDED
5. 기존 Phase 27/28/31 회귀 테스트
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.tts_provider import (
    BaseTTSProvider,
    GTTSProvider,
    MockTTSProvider,
    PollyTTSProvider,
    TTSProvider,
    TTSResult,
    get_tts_provider,
    clear_tts_provider,
)
from app.clients.storage_adapter import (
    BaseStorageProvider,
    LocalStorageProvider,
    S3StorageProvider,
    StorageConfig,
    StorageProvider,
    StorageResult,
    get_storage_provider,
    clear_storage_provider,
)
from app.services.video_composer import (
    ComposedVideo,
    SceneInfo,
    VideoComposer,
    get_video_composer,
    clear_video_composer,
)
from app.api.v1.ws_render_progress import (
    RenderProgressConnectionManager,
    RenderProgressEvent,
    get_connection_manager,
    clear_connection_manager,
    get_step_progress,
)
from app.models.video_render import RenderJobStatus, RenderStep


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def clear_singletons():
    """테스트 전후 싱글톤 초기화."""
    clear_tts_provider()
    clear_storage_provider()
    clear_video_composer()
    clear_connection_manager()
    yield
    clear_tts_provider()
    clear_storage_provider()
    clear_video_composer()
    clear_connection_manager()


@pytest.fixture
def temp_dir():
    """임시 디렉토리 fixture."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_script():
    """샘플 스크립트 JSON."""
    return {
        "chapters": [
            {
                "chapter_id": 1,
                "title": "보안교육 개요",
                "scenes": [
                    {
                        "scene_id": 1,
                        "narration": "안녕하세요. 보안교육을 시작하겠습니다.",
                        "on_screen_text": "보안교육 시작",
                    },
                    {
                        "scene_id": 2,
                        "narration": "오늘은 피싱 메일에 대해 알아보겠습니다.",
                        "on_screen_text": "피싱 메일 주의",
                    },
                ],
            },
        ],
    }


# =============================================================================
# TTS Provider Tests
# =============================================================================


class TestTTSProvider:
    """TTS Provider 테스트."""

    def test_get_mock_provider(self):
        """Mock provider 선택."""
        with patch.dict(os.environ, {"TTS_PROVIDER": "mock"}):
            provider = get_tts_provider()
            assert isinstance(provider, MockTTSProvider)

    def test_get_gtts_provider(self):
        """gTTS provider 선택."""
        with patch.dict(os.environ, {"TTS_PROVIDER": "gtts"}):
            provider = get_tts_provider()
            assert isinstance(provider, GTTSProvider)

    def test_unknown_provider_fallback(self):
        """알 수 없는 provider는 mock으로 fallback."""
        with patch.dict(os.environ, {"TTS_PROVIDER": "unknown"}):
            provider = get_tts_provider()
            assert isinstance(provider, MockTTSProvider)

    @pytest.mark.asyncio
    async def test_mock_tts_synthesize(self):
        """Mock TTS 합성."""
        provider = MockTTSProvider()
        result = await provider.synthesize(
            text="테스트 텍스트입니다.",
            language="ko",
        )

        assert isinstance(result, TTSResult)
        assert result.audio_bytes is not None
        assert result.duration_sec > 0
        assert result.format == "mp3"

    @pytest.mark.asyncio
    async def test_mock_tts_synthesize_to_file(self, temp_dir):
        """Mock TTS 파일로 저장."""
        provider = MockTTSProvider()
        output_path = temp_dir / "test.mp3"

        duration = await provider.synthesize_to_file(
            text="테스트 텍스트입니다.",
            output_path=output_path,
            language="ko",
        )

        assert output_path.exists()
        assert duration > 0


# =============================================================================
# Storage Adapter Tests
# =============================================================================


class TestStorageAdapter:
    """Storage Adapter 테스트."""

    def test_get_local_provider(self):
        """Local provider 선택."""
        with patch.dict(os.environ, {"STORAGE_PROVIDER": "local"}):
            provider = get_storage_provider()
            assert isinstance(provider, LocalStorageProvider)

    def test_unknown_provider_fallback(self):
        """알 수 없는 provider는 local로 fallback."""
        with patch.dict(os.environ, {"STORAGE_PROVIDER": "unknown"}):
            provider = get_storage_provider()
            assert isinstance(provider, LocalStorageProvider)

    @pytest.mark.asyncio
    async def test_local_storage_put_bytes(self, temp_dir):
        """Local storage에 bytes 저장."""
        config = StorageConfig(
            provider=StorageProvider.LOCAL,
            local_path=str(temp_dir),
            base_url="http://localhost:8000/static",
        )
        provider = LocalStorageProvider(config)

        result = await provider.put_object(
            data=b"test content",
            key="test/file.txt",
            content_type="text/plain",
        )

        assert result.key == "test/file.txt"
        assert "http://localhost:8000/static" in result.url
        assert result.size_bytes == 12
        assert (temp_dir / "test" / "file.txt").exists()

    @pytest.mark.asyncio
    async def test_local_storage_put_file(self, temp_dir):
        """Local storage에 파일 저장."""
        config = StorageConfig(
            provider=StorageProvider.LOCAL,
            local_path=str(temp_dir / "output"),
            base_url="http://localhost:8000/static",
        )
        provider = LocalStorageProvider(config)

        # 소스 파일 생성
        src_file = temp_dir / "source.txt"
        src_file.write_text("source content")

        result = await provider.put_file(
            file_path=src_file,
            key="copied.txt",
        )

        assert result.key == "copied.txt"
        assert (temp_dir / "output" / "copied.txt").exists()

    @pytest.mark.asyncio
    async def test_local_storage_delete(self, temp_dir):
        """Local storage 파일 삭제."""
        config = StorageConfig(
            provider=StorageProvider.LOCAL,
            local_path=str(temp_dir),
        )
        provider = LocalStorageProvider(config)

        # 파일 생성
        test_file = temp_dir / "to_delete.txt"
        test_file.write_text("delete me")

        # 삭제
        result = await provider.delete_object("to_delete.txt")
        assert result is True
        assert not test_file.exists()

    @pytest.mark.asyncio
    async def test_local_storage_get_url(self, temp_dir):
        """Local storage URL 조회."""
        config = StorageConfig(
            provider=StorageProvider.LOCAL,
            local_path=str(temp_dir),
            base_url="http://example.com/files",
        )
        provider = LocalStorageProvider(config)

        url = await provider.get_url("test/file.mp4")
        assert url == "http://example.com/files/test/file.mp4"


# =============================================================================
# Video Composer Tests
# =============================================================================


class TestVideoComposer:
    """Video Composer 테스트."""

    @pytest.fixture
    def composer(self):
        """Composer fixture."""
        return VideoComposer()

    def test_ffmpeg_availability_check(self, composer):
        """FFmpeg 사용 가능 여부 확인."""
        # is_available은 시스템에 따라 다름
        assert hasattr(composer, "is_available")

    @pytest.mark.asyncio
    async def test_generate_srt(self, composer, temp_dir):
        """SRT 자막 생성."""
        scenes = [
            SceneInfo(1, "첫 번째 씬 나레이션", "첫 번째 자막", duration_sec=5.0),
            SceneInfo(2, "두 번째 씬 나레이션", "두 번째 자막", duration_sec=5.0),
        ]

        output_path = temp_dir / "test.srt"
        composer._generate_srt(scenes, output_path)

        assert output_path.exists()
        content = output_path.read_text(encoding="utf-8")
        assert "첫 번째 자막" in content
        assert "두 번째 자막" in content
        assert "00:00:00,000" in content
        assert "-->" in content

    def test_format_srt_time(self, composer):
        """SRT 시간 포맷 변환."""
        assert composer._format_srt_time(0) == "00:00:00,000"
        assert composer._format_srt_time(61.5) == "00:01:01,500"
        assert composer._format_srt_time(3661.123) == "01:01:01,123"

    def test_calculate_scene_durations(self, composer):
        """씬 duration 계산."""
        scenes = [
            SceneInfo(1, "씬 1", duration_sec=None),
            SceneInfo(2, "씬 2", duration_sec=None),
        ]

        result = composer._calculate_scene_durations(scenes, 10.0)

        assert result[0].duration_sec == 5.0
        assert result[1].duration_sec == 5.0

    def test_calculate_scene_durations_mixed(self, composer):
        """고정/가변 duration 혼합 계산."""
        scenes = [
            SceneInfo(1, "씬 1", duration_sec=3.0),  # 고정
            SceneInfo(2, "씬 2", duration_sec=None),  # 가변
            SceneInfo(3, "씬 3", duration_sec=None),  # 가변
        ]

        result = composer._calculate_scene_durations(scenes, 10.0)

        assert result[0].duration_sec == 3.0
        assert result[1].duration_sec == 3.5
        assert result[2].duration_sec == 3.5


# =============================================================================
# WebSocket Progress Tests
# =============================================================================


class TestWebSocketProgress:
    """WebSocket Progress 테스트."""

    @pytest.fixture
    def manager(self):
        """ConnectionManager fixture."""
        return RenderProgressConnectionManager()

    def test_step_progress_mapping(self):
        """단계별 진행률 매핑."""
        # VALIDATE_SCRIPT: 0-5%
        progress, msg = get_step_progress(RenderStep.VALIDATE_SCRIPT, 0.0)
        assert progress == 0
        assert "스크립트" in msg

        progress, msg = get_step_progress(RenderStep.VALIDATE_SCRIPT, 1.0)
        assert progress == 5

        # GENERATE_TTS: 5-30%
        progress, msg = get_step_progress(RenderStep.GENERATE_TTS, 0.5)
        assert 5 < progress < 30
        assert "음성" in msg

        # FINALIZE: 95-100%
        progress, msg = get_step_progress(RenderStep.FINALIZE, 1.0)
        assert progress == 100

    def test_render_progress_event_create(self):
        """RenderProgressEvent 생성."""
        event = RenderProgressEvent.create(
            job_id="job-123",
            video_id="video-456",
            status=RenderJobStatus.RUNNING,
            step=RenderStep.GENERATE_TTS,
            progress=25,
            message="TTS 생성 중...",
        )

        assert event.job_id == "job-123"
        assert event.video_id == "video-456"
        assert event.status == "RUNNING"
        assert event.step == "GENERATE_TTS"
        assert event.progress == 25
        assert event.timestamp.endswith("Z")

    def test_connection_manager_initial_state(self, manager):
        """초기 상태 확인."""
        assert manager.get_connection_count() == 0
        assert manager.get_active_video_ids() == []


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """통합 테스트."""

    @pytest.fixture
    def mock_tts(self):
        """Mock TTS Provider."""
        mock = MagicMock(spec=BaseTTSProvider)
        mock.synthesize = AsyncMock(return_value=TTSResult(
            audio_bytes=b"\x00" * 1024,
            duration_sec=10.0,
            format="mp3",
        ))
        mock.synthesize_to_file = AsyncMock(return_value=10.0)
        return mock

    @pytest.fixture
    def mock_storage(self):
        """Mock Storage Provider."""
        mock = MagicMock(spec=BaseStorageProvider)
        mock.put_object = AsyncMock(return_value=StorageResult(
            key="test/video.mp4",
            url="http://example.com/test/video.mp4",
            size_bytes=1024,
            content_type="video/mp4",
        ))
        mock.put_file = AsyncMock(return_value=StorageResult(
            key="test/video.mp4",
            url="http://example.com/test/video.mp4",
            size_bytes=1024,
            content_type="video/mp4",
        ))
        return mock

    @pytest.mark.asyncio
    async def test_real_renderer_validate_script(self, sample_script, temp_dir):
        """RealVideoRenderer 스크립트 검증."""
        from app.services.video_renderer_real import (
            RealVideoRenderer,
            RealRendererConfig,
            RealRenderJobContext,
        )

        config = RealRendererConfig(output_dir=str(temp_dir))
        renderer = RealVideoRenderer(config=config)

        # 컨텍스트 생성
        ctx = RealRenderJobContext(
            job_id="job-test",
            video_id="video-test",
            script_id="script-test",
            script_json=sample_script,
            output_dir=temp_dir,
        )

        # 검증 실행
        await renderer._validate_script(ctx)

        assert ctx.validated is True
        assert len(ctx.scenes) == 2
        assert ctx.scenes[0].narration == "안녕하세요. 보안교육을 시작하겠습니다."

    @pytest.mark.asyncio
    async def test_real_renderer_generate_tts(self, sample_script, temp_dir, mock_tts):
        """RealVideoRenderer TTS 생성."""
        from app.services.video_renderer_real import (
            RealVideoRenderer,
            RealRendererConfig,
            RealRenderJobContext,
        )
        from app.services.video_composer import SceneInfo

        config = RealRendererConfig(output_dir=str(temp_dir))
        renderer = RealVideoRenderer(config=config, tts_provider=mock_tts)

        # 컨텍스트 생성
        ctx = RealRenderJobContext(
            job_id="job-test",
            video_id="video-test",
            script_id="script-test",
            script_json=sample_script,
            output_dir=temp_dir,
        )
        ctx.scenes = [
            SceneInfo(1, "테스트 나레이션 1"),
            SceneInfo(2, "테스트 나레이션 2"),
        ]

        # TTS 생성
        await renderer._generate_tts(ctx)

        assert mock_tts.synthesize_to_file.called
        assert ctx.duration_sec == 10.0

    @pytest.mark.asyncio
    async def test_full_render_pipeline_mock(self, sample_script, temp_dir, mock_tts, mock_storage):
        """전체 렌더 파이프라인 (Mock 모드)."""
        from app.services.video_renderer_real import (
            RealVideoRenderer,
            RealRendererConfig,
        )

        # Mock Composer
        mock_composer = MagicMock()
        mock_composer.is_available = False
        mock_composer.compose = AsyncMock(return_value=MagicMock(
            video_path=str(temp_dir / "video.mp4"),
            subtitle_path=str(temp_dir / "subtitle.srt"),
            thumbnail_path=str(temp_dir / "thumb.jpg"),
            duration_sec=10.0,
            scenes=[],
        ))

        # 파일 생성 (mock)
        (temp_dir / "video.mp4").write_bytes(b"mock video")
        (temp_dir / "subtitle.srt").write_text("mock srt")
        (temp_dir / "thumb.jpg").write_bytes(b"mock thumb")

        config = RealRendererConfig(output_dir=str(temp_dir))
        renderer = RealVideoRenderer(
            config=config,
            tts_provider=mock_tts,
            storage_provider=mock_storage,
            video_composer=mock_composer,
        )

        # 단계별 실행
        steps = [
            RenderStep.VALIDATE_SCRIPT,
            RenderStep.GENERATE_TTS,
            RenderStep.GENERATE_SUBTITLE,
            RenderStep.RENDER_SLIDES,
            RenderStep.COMPOSE_VIDEO,
            RenderStep.UPLOAD_ASSETS,
            RenderStep.FINALIZE,
        ]

        for step in steps:
            await renderer.execute_step(step, sample_script, "job-test")

        # 결과 확인
        assets = await renderer.get_rendered_assets("job-test")
        assert assets.duration_sec == 10.0


# =============================================================================
# Regression Tests
# =============================================================================


class TestRegression:
    """회귀 테스트 - 기존 Phase 27/28 동작 확인."""

    @pytest.fixture
    def client(self):
        """FastAPI TestClient fixture."""
        from app.main import app
        from fastapi.testclient import TestClient
        return TestClient(app)

    def test_scripts_api_still_works(self, client, sample_script):
        """기존 POST /api/scripts 동작 확인."""
        response = client.post(
            "/api/scripts",
            json={
                "video_id": "video-regression-001",
                "raw_json": sample_script,
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "DRAFT"

    def test_render_job_api_still_works(self, client, sample_script):
        """기존 렌더 잡 생성 API 동작 확인."""
        # 스크립트 생성
        create_resp = client.post(
            "/api/scripts",
            json={
                "video_id": "video-regression-003",
                "raw_json": sample_script,
            },
        )
        script_id = create_resp.json()["script_id"]

        # 렌더 잡 생성
        render_resp = client.post(
            "/api/videos/video-regression-003/render-jobs",
            json={"script_id": script_id},
        )
        assert render_resp.status_code == 200
        assert render_resp.json()["status"] == "PENDING"
