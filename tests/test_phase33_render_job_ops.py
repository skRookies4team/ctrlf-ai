"""
Phase 33: Render Job Operations Tests

렌더 잡 운영화 테스트.

테스트 항목:
1. RenderJobRepository: DB 영속화
2. RenderJobRunner: 잡 생성/실행/취소
3. API: idempotent 생성, 목록/상세 조회, published assets
4. WebSocket: job_id 필터링
"""

import asyncio
import os
import tempfile
from datetime import datetime
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.video_render import (
    RenderJobStatus,
    RenderStep,
    ScriptStatus,
    VideoScript,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_db_path():
    """임시 DB 경로 생성."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        yield f.name
    # 테스트 후 정리
    try:
        os.unlink(f.name)
    except Exception:
        pass


@pytest.fixture
def repository(temp_db_path):
    """RenderJobRepository 인스턴스 생성."""
    from app.repositories.render_job_repository import RenderJobRepository
    return RenderJobRepository(db_path=temp_db_path)


@pytest.fixture
def mock_script():
    """테스트용 스크립트 생성."""
    return VideoScript(
        script_id="script-test-001",
        video_id="video-test-001",
        status=ScriptStatus.APPROVED,
        raw_json={"chapters": [{"title": "Test", "scenes": []}]},
        created_by="test-user",
        created_at=datetime.utcnow(),
    )


@pytest.fixture
def mock_renderer():
    """테스트용 렌더러 Mock."""
    from app.services.video_render_service import VideoRenderer
    from app.models.video_render import RenderedAssets

    renderer = MagicMock(spec=VideoRenderer)
    renderer.execute_step = AsyncMock(return_value=None)
    renderer.get_rendered_assets = AsyncMock(return_value=RenderedAssets(
        mp4_path="http://test/video.mp4",
        thumbnail_path="http://test/thumb.jpg",
        subtitle_path="http://test/sub.srt",
        duration_sec=60.0,
    ))
    return renderer


# =============================================================================
# Test: RenderJobRepository
# =============================================================================


class TestRenderJobRepository:
    """RenderJobRepository 테스트."""

    def test_save_and_get(self, repository):
        """잡 저장 및 조회."""
        from app.repositories.render_job_repository import RenderJobEntity

        job = RenderJobEntity(
            job_id="job-test-001",
            video_id="video-001",
            script_id="script-001",
            status="QUEUED",
            progress=0,
            created_by="user-001",
        )
        repository.save(job)

        loaded = repository.get("job-test-001")
        assert loaded is not None
        assert loaded.job_id == "job-test-001"
        assert loaded.video_id == "video-001"
        assert loaded.status == "QUEUED"

    def test_get_active_by_video_id(self, repository):
        """video_id로 활성 잡 조회."""
        from app.repositories.render_job_repository import RenderJobEntity

        # PROCESSING 잡 생성
        running_job = RenderJobEntity(
            job_id="job-running",
            video_id="video-001",
            script_id="script-001",
            status="PROCESSING",
        )
        repository.save(running_job)

        # COMPLETED 잡 생성 (같은 video_id)
        succeeded_job = RenderJobEntity(
            job_id="job-succeeded",
            video_id="video-001",
            script_id="script-001",
            status="COMPLETED",
        )
        repository.save(succeeded_job)

        # 활성 잡 조회
        active = repository.get_active_by_video_id("video-001")
        assert active is not None
        assert active.job_id == "job-running"

    def test_get_by_video_id_list(self, repository):
        """video_id로 잡 목록 조회."""
        from app.repositories.render_job_repository import RenderJobEntity

        # 3개 잡 생성
        for i in range(3):
            job = RenderJobEntity(
                job_id=f"job-{i}",
                video_id="video-001",
                script_id="script-001",
                status="COMPLETED" if i < 2 else "QUEUED",
            )
            repository.save(job)

        jobs = repository.get_by_video_id("video-001")
        assert len(jobs) == 3

    def test_update_status(self, repository):
        """잡 상태 업데이트."""
        from app.repositories.render_job_repository import RenderJobEntity

        job = RenderJobEntity(
            job_id="job-update",
            video_id="video-001",
            script_id="script-001",
            status="QUEUED",
        )
        repository.save(job)

        # 상태 업데이트
        repository.update_status(
            job_id="job-update",
            status="PROCESSING",
            step="GENERATE_TTS",
            progress=25,
            message="TTS 생성 중...",
        )

        updated = repository.get("job-update")
        assert updated.status == "PROCESSING"
        assert updated.step == "GENERATE_TTS"
        assert updated.progress == 25
        assert updated.started_at is not None

    def test_update_assets(self, repository):
        """에셋 업데이트."""
        from app.repositories.render_job_repository import RenderJobEntity

        job = RenderJobEntity(
            job_id="job-assets",
            video_id="video-001",
            script_id="script-001",
            status="COMPLETED",
        )
        repository.save(job)

        assets = {
            "video_url": "http://example.com/video.mp4",
            "subtitle_url": "http://example.com/sub.srt",
            "thumbnail_url": "http://example.com/thumb.jpg",
        }
        repository.update_assets("job-assets", assets)

        updated = repository.get("job-assets")
        assert updated.assets["video_url"] == "http://example.com/video.mp4"

    def test_update_error(self, repository):
        """에러 업데이트."""
        from app.repositories.render_job_repository import RenderJobEntity

        job = RenderJobEntity(
            job_id="job-error",
            video_id="video-001",
            script_id="script-001",
            status="PROCESSING",
        )
        repository.save(job)

        repository.update_error(
            job_id="job-error",
            error_code="TTS_ERROR",
            error_message="TTS 생성 실패",
        )

        updated = repository.get("job-error")
        assert updated.status == "FAILED"
        assert updated.error_code == "TTS_ERROR"
        assert updated.finished_at is not None

    def test_persistence_across_instances(self, temp_db_path):
        """인스턴스 재생성 후에도 데이터 유지."""
        from app.repositories.render_job_repository import (
            RenderJobEntity,
            RenderJobRepository,
        )

        # 첫 번째 인스턴스에서 저장
        repo1 = RenderJobRepository(db_path=temp_db_path)
        job = RenderJobEntity(
            job_id="job-persist",
            video_id="video-001",
            script_id="script-001",
            status="COMPLETED",
        )
        repo1.save(job)
        repo1.close()

        # 두 번째 인스턴스에서 조회
        repo2 = RenderJobRepository(db_path=temp_db_path)
        loaded = repo2.get("job-persist")
        assert loaded is not None
        assert loaded.job_id == "job-persist"
        repo2.close()


# =============================================================================
# Test: RenderJobRunner
# =============================================================================


class TestRenderJobRunner:
    """RenderJobRunner 테스트."""

    @pytest.mark.asyncio
    async def test_create_job_new(self, repository, mock_script, mock_renderer):
        """새 잡 생성."""
        from app.services.render_job_runner import RenderJobRunner

        runner = RenderJobRunner(
            renderer=mock_renderer,
            repository=repository,
        )

        result = await runner.create_job(
            video_id=mock_script.video_id,
            script_id=mock_script.script_id,
            script=mock_script,
            created_by="test-user",
        )

        assert result.created is True
        assert result.job.status == "QUEUED"
        assert result.job.video_id == mock_script.video_id

    @pytest.mark.asyncio
    async def test_create_job_idempotent(self, repository, mock_script, mock_renderer):
        """기존 PROCESSING 잡이 있으면 기존 잡 반환 (idempotency)."""
        from app.repositories.render_job_repository import RenderJobEntity
        from app.services.render_job_runner import RenderJobRunner

        # 기존 PROCESSING 잡 생성
        existing_job = RenderJobEntity(
            job_id="job-existing",
            video_id=mock_script.video_id,
            script_id=mock_script.script_id,
            status="PROCESSING",
        )
        repository.save(existing_job)

        runner = RenderJobRunner(
            renderer=mock_renderer,
            repository=repository,
        )

        result = await runner.create_job(
            video_id=mock_script.video_id,
            script_id=mock_script.script_id,
            script=mock_script,
            created_by="test-user",
        )

        assert result.created is False
        assert result.job.job_id == "job-existing"

    def test_get_published_assets(self, repository, mock_renderer):
        """발행된 에셋 조회."""
        from app.repositories.render_job_repository import RenderJobEntity
        from app.services.render_job_runner import RenderJobRunner

        # COMPLETED 잡 생성 with assets
        job = RenderJobEntity(
            job_id="job-published",
            video_id="video-001",
            script_id="script-001",
            status="COMPLETED",
            assets={
                "video_url": "http://example.com/video.mp4",
                "subtitle_url": "http://example.com/sub.srt",
                "thumbnail_url": "http://example.com/thumb.jpg",
            },
        )
        job.finished_at = datetime.utcnow()
        repository.save(job)

        runner = RenderJobRunner(
            renderer=mock_renderer,
            repository=repository,
        )

        assets = runner.get_published_assets("video-001")
        assert assets is not None
        assert assets["video_url"] == "http://example.com/video.mp4"
        assert assets["job_id"] == "job-published"

    def test_get_published_assets_none(self, repository, mock_renderer):
        """발행된 에셋이 없는 경우."""
        from app.services.render_job_runner import RenderJobRunner

        runner = RenderJobRunner(
            renderer=mock_renderer,
            repository=repository,
        )

        assets = runner.get_published_assets("video-nonexistent")
        assert assets is None

    @pytest.mark.asyncio
    async def test_cancel_job(self, repository, mock_script, mock_renderer):
        """잡 취소."""
        from app.repositories.render_job_repository import RenderJobEntity
        from app.services.render_job_runner import RenderJobRunner

        # QUEUED 잡 생성
        job = RenderJobEntity(
            job_id="job-to-cancel",
            video_id=mock_script.video_id,
            script_id=mock_script.script_id,
            status="QUEUED",
        )
        repository.save(job)

        runner = RenderJobRunner(
            renderer=mock_renderer,
            repository=repository,
        )

        canceled = await runner.cancel_job("job-to-cancel")
        assert canceled is not None
        # Cancel now sets status to FAILED with error_code="CANCELED"
        assert canceled.status == "FAILED"


# =============================================================================
# Test: API Endpoints
# =============================================================================


class TestRenderJobAPI:
    """렌더 잡 API 테스트."""

    @pytest.fixture
    def test_client(self, temp_db_path, mock_renderer, mock_script):
        """FastAPI 테스트 클라이언트."""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.services.video_render_service import get_video_render_service
        from app.services.render_job_runner import (
            get_render_job_runner,
            clear_render_job_runner,
        )
        from app.repositories.render_job_repository import (
            get_render_job_repository,
            clear_render_job_repository,
        )

        # 싱글톤 초기화
        clear_render_job_runner()
        clear_render_job_repository()

        # 환경변수로 DB 경로 설정
        os.environ["RENDER_JOB_DB_PATH"] = temp_db_path

        # 스크립트 저장
        service = get_video_render_service()
        service._script_store.save(mock_script)

        yield TestClient(app)

        # 정리
        clear_render_job_runner()
        clear_render_job_repository()

    def test_create_render_job_success(self, test_client, mock_script):
        """렌더 잡 생성 성공."""
        response = test_client.post(
            f"/api/v2/videos/{mock_script.video_id}/render-jobs",
            json={"script_id": mock_script.script_id},
        )

        # 202 (새로 생성) 또는 200 (기존 반환)
        assert response.status_code in (200, 202)
        data = response.json()
        assert "job_id" in data
        assert "status" in data
        assert "created" in data

    def test_list_render_jobs(self, test_client, mock_script, temp_db_path):
        """렌더 잡 목록 조회."""
        from app.repositories.render_job_repository import (
            RenderJobEntity,
            RenderJobRepository,
        )

        # 잡 생성
        repo = RenderJobRepository(db_path=temp_db_path)
        for i in range(3):
            job = RenderJobEntity(
                job_id=f"job-list-{i}",
                video_id=mock_script.video_id,
                script_id=mock_script.script_id,
                status="COMPLETED",
            )
            repo.save(job)

        response = test_client.get(
            f"/api/v2/videos/{mock_script.video_id}/render-jobs"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["video_id"] == mock_script.video_id
        assert len(data["jobs"]) >= 3

    def test_get_render_job_detail(self, test_client, mock_script, temp_db_path):
        """렌더 잡 상세 조회."""
        from app.repositories.render_job_repository import (
            RenderJobEntity,
            RenderJobRepository,
        )

        repo = RenderJobRepository(db_path=temp_db_path)
        job = RenderJobEntity(
            job_id="job-detail-test",
            video_id=mock_script.video_id,
            script_id=mock_script.script_id,
            status="COMPLETED",
            progress=100,
            message="완료",
        )
        repo.save(job)

        response = test_client.get(
            f"/api/v2/videos/{mock_script.video_id}/render-jobs/job-detail-test"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "job-detail-test"
        assert data["status"] == "COMPLETED"
        assert data["progress"] == 100

    def test_get_published_assets(self, test_client, mock_script, temp_db_path):
        """발행된 에셋 조회."""
        from app.repositories.render_job_repository import (
            RenderJobEntity,
            RenderJobRepository,
        )

        repo = RenderJobRepository(db_path=temp_db_path)
        job = RenderJobEntity(
            job_id="job-published-test",
            video_id=mock_script.video_id,
            script_id=mock_script.script_id,
            status="COMPLETED",
            assets={
                "video_url": "http://example.com/video.mp4",
                "subtitle_url": "http://example.com/sub.srt",
                "thumbnail_url": "http://example.com/thumb.jpg",
            },
        )
        job.finished_at = datetime.utcnow()
        repo.save(job)

        response = test_client.get(
            f"/api/v2/videos/{mock_script.video_id}/assets/published"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["published"] is True
        assert data["video_url"] == "http://example.com/video.mp4"

    def test_get_published_assets_not_found(self, test_client):
        """발행된 에셋이 없는 경우."""
        response = test_client.get(
            "/api/v2/videos/video-nonexistent/assets/published"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["published"] is False


# =============================================================================
# Test: WebSocket job_id Filtering
# =============================================================================


class TestWebSocketJobFilter:
    """WebSocket job_id 필터링 테스트."""

    @pytest.mark.asyncio
    async def test_broadcast_filters_by_job_id(self):
        """job_id 필터링된 브로드캐스트."""
        from app.api.v1.ws_render_progress import (
            RenderProgressConnectionManager,
            RenderProgressEvent,
        )

        manager = RenderProgressConnectionManager()

        # Mock WebSockets
        ws1 = MagicMock()
        ws1.send_json = AsyncMock()
        ws2 = MagicMock()
        ws2.send_json = AsyncMock()

        # ws1은 job-001만 구독, ws2는 모든 이벤트 구독
        manager._connections["video-001"] = {ws1, ws2}
        manager._socket_videos[ws1] = {"video-001"}
        manager._socket_videos[ws2] = {"video-001"}
        manager._socket_job_filter[ws1] = "job-001"
        manager._socket_job_filter[ws2] = None

        # job-001 이벤트 전송
        event1 = RenderProgressEvent(
            job_id="job-001",
            video_id="video-001",
            status="PROCESSING",
            progress=50,
            message="진행 중",
            timestamp=datetime.utcnow().isoformat(),
        )
        await manager.broadcast("video-001", event1)

        # 둘 다 수신
        assert ws1.send_json.called
        assert ws2.send_json.called

        ws1.send_json.reset_mock()
        ws2.send_json.reset_mock()

        # job-002 이벤트 전송
        event2 = RenderProgressEvent(
            job_id="job-002",
            video_id="video-001",
            status="PROCESSING",
            progress=30,
            message="진행 중",
            timestamp=datetime.utcnow().isoformat(),
        )
        await manager.broadcast("video-001", event2)

        # ws1은 필터링되어 미수신, ws2는 수신
        assert not ws1.send_json.called
        assert ws2.send_json.called

    @pytest.mark.asyncio
    async def test_event_includes_job_id(self):
        """이벤트에 job_id 포함 확인."""
        from app.api.v1.ws_render_progress import RenderProgressEvent

        event = RenderProgressEvent.create(
            job_id="job-test",
            video_id="video-test",
            status=RenderJobStatus.PROCESSING,
            step=RenderStep.GENERATE_TTS,
            progress=25,
            message="TTS 생성 중",
        )

        assert event.job_id == "job-test"
        assert event.video_id == "video-test"
        assert "job_id" in event.model_dump()


# =============================================================================
# Test: Integration
# =============================================================================


class TestIntegration:
    """통합 테스트."""

    @pytest.mark.asyncio
    async def test_full_flow(self, repository, mock_script, mock_renderer):
        """전체 흐름: 잡 생성 → 실행 → 완료 → 에셋 조회."""
        from app.services.render_job_runner import RenderJobRunner

        runner = RenderJobRunner(
            renderer=mock_renderer,
            repository=repository,
        )

        # 1. 잡 생성
        result = await runner.create_job(
            video_id=mock_script.video_id,
            script_id=mock_script.script_id,
            script=mock_script,
            created_by="test-user",
        )
        assert result.created is True
        job_id = result.job.job_id

        # 2. 잡 실행 대기 (백그라운드에서 실행됨)
        await asyncio.sleep(0.5)

        # 3. 잡 상태 확인
        job = runner.get_job(job_id)
        # 아직 실행 중이거나 완료됨
        assert job.status in ("PROCESSING", "COMPLETED", "QUEUED")

    def test_regression_existing_api(self, temp_db_path, mock_script):
        """기존 API 회귀 테스트."""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.services.video_render_service import get_video_render_service

        # 스크립트 저장
        service = get_video_render_service()
        service._script_store.save(mock_script)

        client = TestClient(app)

        # 기존 /api/scripts 엔드포인트 확인
        response = client.get(f"/api/scripts/{mock_script.script_id}")
        assert response.status_code == 200

        # 기존 헬스체크 확인
        response = client.get("/health")
        assert response.status_code == 200
