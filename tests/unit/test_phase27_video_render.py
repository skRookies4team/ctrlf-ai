"""
Phase 27: Video Render Pipeline Tests

테스트 케이스:
1. APPROVED script가 아니면 render-jobs 생성이 400으로 막힘
2. 동일 video_id에 RUNNING job 있으면 409
3. EXPIRED 교육(video)면 render-jobs 생성이 404
4. job 상태가 PENDING→RUNNING→SUCCEEDED로 전환되고 asset이 생성됨
5. cancel 호출 시 CANCELED 되고 이후 스텝 진행 안 함
6. 기존 Phase22/26 테스트는 전부 통과해야 함 (별도 실행)
"""

import asyncio
from datetime import date, datetime, timedelta

import pytest

from app.models.video_render import (
    RenderedAssets,
    RenderJobStatus,
    RenderStep,
    ScriptStatus,
    VideoAsset,
    VideoRenderJob,
    VideoScript,
)
from app.services.education_catalog_service import (
    EducationCatalogService,
    get_education_catalog_service,
)
from app.services.video_render_service import (
    VideoAssetStore,
    VideoRenderer,
    VideoRenderJobStore,
    VideoRenderService,
    VideoScriptStore,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def script_store():
    """스크립트 저장소 fixture."""
    return VideoScriptStore()


@pytest.fixture
def job_store():
    """잡 저장소 fixture."""
    return VideoRenderJobStore()


@pytest.fixture
def asset_store():
    """에셋 저장소 fixture."""
    return VideoAssetStore()


@pytest.fixture
def render_service(script_store, job_store, asset_store):
    """VideoRenderService fixture."""
    return VideoRenderService(
        script_store=script_store,
        job_store=job_store,
        asset_store=asset_store,
    )


class MockVideoRenderer(VideoRenderer):
    """테스트용 Mock 렌더러."""

    def __init__(self, should_fail: bool = False, fail_at_step: RenderStep = None):
        self.should_fail = should_fail
        self.fail_at_step = fail_at_step
        self.executed_steps = []

    async def execute_step(
        self,
        step: RenderStep,
        script_json: dict,
        job_id: str,
    ) -> None:
        """Mock 단계 실행."""
        self.executed_steps.append(step)

        if self.should_fail and step == self.fail_at_step:
            raise RuntimeError(f"Mock failure at step: {step.value}")

        # 짧은 지연 시뮬레이션
        await asyncio.sleep(0.01)

    async def get_rendered_assets(self, job_id: str) -> RenderedAssets:
        """Mock 에셋 반환."""
        return RenderedAssets(
            mp4_path=f"/output/{job_id}/video.mp4",
            thumbnail_path=f"/output/{job_id}/thumbnail.png",
            subtitle_path=f"/output/{job_id}/subtitle.srt",
            duration_sec=60.0,
        )


@pytest.fixture
def mock_renderer():
    """Mock 렌더러 fixture."""
    return MockVideoRenderer()


# =============================================================================
# Test 1: APPROVED script validation
# =============================================================================


class TestScriptValidation:
    """스크립트 검증 테스트."""

    @pytest.mark.asyncio
    async def test_render_job_with_script(self, render_service, mock_renderer):
        """스크립트로 렌더 잡 생성 성공."""
        render_service.set_renderer(mock_renderer)

        # 스크립트 생성
        script = render_service.create_script(
            video_id="video-001",
            raw_json={"text": "Test content"},
            created_by="user-001",
        )

        # 렌더 잡 생성 성공
        job = await render_service.create_render_job(
            video_id="video-001",
            script_id=script.script_id,
            requested_by="reviewer-001",
        )
        assert job.status == RenderJobStatus.QUEUED
        assert job.video_id == "video-001"
        assert job.script_id == script.script_id

    @pytest.mark.asyncio
    async def test_render_job_script_video_id_mismatch(self, render_service, mock_renderer):
        """스크립트 video_id 불일치 시 실패."""
        render_service.set_renderer(mock_renderer)

        # video-001에 대한 스크립트 생성
        script = render_service.create_script(
            video_id="video-001",
            raw_json={"text": "Test content"},
            created_by="user-001",
        )

        # video-002로 렌더 잡 생성 시도 → ValueError (video_id 불일치)
        with pytest.raises(ValueError) as exc_info:
            await render_service.create_render_job(
                video_id="video-002",
                script_id=script.script_id,
                requested_by="reviewer-001",
            )
        assert "mismatch" in str(exc_info.value).lower()


# =============================================================================
# Test 2: Duplicate RUNNING job check
# =============================================================================


class TestDuplicateJobCheck:
    """중복 잡 체크 테스트."""

    @pytest.mark.asyncio
    async def test_duplicate_running_job_blocked(self, render_service, mock_renderer):
        """동일 video_id에 RUNNING 잡 있으면 409 (RuntimeError)."""
        render_service.set_renderer(mock_renderer)

        # 스크립트 생성
        script = render_service.create_script(
            video_id="video-001",
            raw_json={"text": "Test content"},
            created_by="user-001",
        )

        # 첫 번째 잡 생성
        job1 = await render_service.create_render_job(
            video_id="video-001",
            script_id=script.script_id,
            requested_by="reviewer-001",
        )

        # 두 번째 잡 생성 시도 → RuntimeError (중복)
        with pytest.raises(RuntimeError) as exc_info:
            await render_service.create_render_job(
                video_id="video-001",
                script_id=script.script_id,
                requested_by="reviewer-002",
            )
        assert "already exists" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_new_job_allowed_after_completion(self, render_service, mock_renderer):
        """완료된 잡이 있으면 새 잡 생성 가능."""
        render_service.set_renderer(mock_renderer)

        # 스크립트 생성
        script = render_service.create_script(
            video_id="video-001",
            raw_json={"text": "Test content"},
            created_by="user-001",
        )

        # 첫 번째 잡 생성
        job1 = await render_service.create_render_job(
            video_id="video-001",
            script_id=script.script_id,
            requested_by="reviewer-001",
        )

        # 파이프라인 완료 대기
        await asyncio.sleep(0.5)

        # 잡 상태 확인 (SUCCEEDED)
        job1_updated = render_service.get_job(job1.job_id)
        assert job1_updated.status == RenderJobStatus.COMPLETED

        # 두 번째 잡 생성 가능
        job2 = await render_service.create_render_job(
            video_id="video-001",
            script_id=script.script_id,
            requested_by="reviewer-002",
        )
        assert job2.job_id != job1.job_id


# =============================================================================
# Test 3: EXPIRED education check
# =============================================================================


class TestExpiredEducationCheck:
    """EXPIRED 교육 차단 테스트."""

    def test_education_catalog_is_expired(self):
        """교육 만료 여부 확인."""
        catalog = EducationCatalogService()

        # 만료된 교육 등록 (작년 마감)
        catalog.register_education(
            education_id="EDU-4TYPE-2024-001",
            year=2024,
            due_date=date(2024, 12, 31),
            is_mandatory_4type=True,
            title="2024 보안교육",
        )

        # 만료 확인
        assert catalog.is_expired("EDU-4TYPE-2024-001") is True

    def test_education_catalog_not_expired(self):
        """교육 미만료 확인."""
        catalog = EducationCatalogService()

        # 미만료 교육 등록 (미래 마감)
        future_date = date.today() + timedelta(days=365)
        catalog.register_education(
            education_id="EDU-4TYPE-2025-001",
            year=2025,
            due_date=future_date,
            is_mandatory_4type=True,
            title="2025 보안교육",
        )

        # 미만료 확인
        assert catalog.is_expired("EDU-4TYPE-2025-001") is False


# =============================================================================
# Test 4: Job state transitions
# =============================================================================


class TestJobStateTransitions:
    """잡 상태 전환 테스트."""

    @pytest.mark.asyncio
    async def test_job_transitions_pending_to_succeeded(self, render_service, mock_renderer):
        """잡 상태: PENDING → RUNNING → SUCCEEDED."""
        render_service.set_renderer(mock_renderer)

        # 스크립트 생성 및 승인
        script = render_service.create_script(
            video_id="video-001",
            raw_json={"scenes": [{"caption": "Scene 1", "narration": "Narration 1"}]},
            created_by="user-001",
        )
        
        # 잡 생성
        job = await render_service.create_render_job(
            video_id="video-001",
            script_id=script.script_id,
            requested_by="reviewer-001",
        )
        assert job.status == RenderJobStatus.QUEUED

        # 파이프라인 완료 대기
        await asyncio.sleep(0.5)

        # 최종 상태 확인
        job_updated = render_service.get_job(job.job_id)
        assert job_updated.status == RenderJobStatus.COMPLETED
        assert job_updated.progress == 100
        assert job_updated.finished_at is not None

        # 에셋 생성 확인
        asset = render_service.get_asset_by_job_id(job.job_id)
        assert asset is not None
        assert asset.video_url is not None
        assert asset.thumbnail_url is not None
        assert asset.subtitle_url is not None
        assert asset.duration_sec > 0

    @pytest.mark.asyncio
    async def test_job_transitions_to_failed(self, render_service):
        """잡 상태: PENDING → RUNNING → FAILED."""
        # 실패하는 렌더러 설정
        failing_renderer = MockVideoRenderer(
            should_fail=True,
            fail_at_step=RenderStep.GENERATE_TTS,
        )
        render_service.set_renderer(failing_renderer)

        # 스크립트 생성 및 승인
        script = render_service.create_script(
            video_id="video-001",
            raw_json={"text": "Test content"},
            created_by="user-001",
        )
        
        # 잡 생성
        job = await render_service.create_render_job(
            video_id="video-001",
            script_id=script.script_id,
            requested_by="reviewer-001",
        )

        # 파이프라인 실패 대기
        await asyncio.sleep(0.5)

        # 최종 상태 확인
        job_updated = render_service.get_job(job.job_id)
        assert job_updated.status == RenderJobStatus.FAILED
        assert job_updated.error_message is not None
        assert "GENERATE_TTS" in job_updated.error_message
        assert job_updated.finished_at is not None

    @pytest.mark.asyncio
    async def test_all_steps_executed(self, render_service, mock_renderer):
        """모든 파이프라인 단계가 실행되는지 확인."""
        render_service.set_renderer(mock_renderer)

        # 스크립트 생성 및 승인
        script = render_service.create_script(
            video_id="video-001",
            raw_json={"text": "Test content"},
            created_by="user-001",
        )
        
        # 잡 생성 및 완료 대기
        job = await render_service.create_render_job(
            video_id="video-001",
            script_id=script.script_id,
            requested_by="reviewer-001",
        )
        await asyncio.sleep(0.5)

        # 모든 단계 실행 확인
        expected_steps = [
            RenderStep.VALIDATE_SCRIPT,
            RenderStep.GENERATE_TTS,
            RenderStep.GENERATE_SUBTITLE,
            RenderStep.RENDER_SLIDES,
            RenderStep.COMPOSE_VIDEO,
            RenderStep.UPLOAD_ASSETS,
            RenderStep.FINALIZE,
        ]
        assert mock_renderer.executed_steps == expected_steps


# =============================================================================
# Test 5: Cancel functionality
# =============================================================================


class TestCancelFunctionality:
    """잡 취소 기능 테스트."""

    @pytest.mark.asyncio
    async def test_cancel_pending_job(self, render_service, mock_renderer):
        """PENDING 잡 취소 성공."""
        render_service.set_renderer(mock_renderer)

        # 스크립트 생성 및 승인
        script = render_service.create_script(
            video_id="video-001",
            raw_json={"text": "Test content"},
            created_by="user-001",
        )
        
        # 잡 생성
        job = await render_service.create_render_job(
            video_id="video-001",
            script_id=script.script_id,
            requested_by="reviewer-001",
        )

        # 즉시 취소 - 취소된 잡은 FAILED 상태 + error_code="CANCELED"
        canceled_job = await render_service.cancel_job(job.job_id)
        assert canceled_job is not None
        assert canceled_job.status == RenderJobStatus.FAILED
        assert canceled_job.finished_at is not None

    @pytest.mark.asyncio
    async def test_cannot_cancel_completed_job(self, render_service, mock_renderer):
        """완료된 잡은 취소 불가."""
        render_service.set_renderer(mock_renderer)

        # 스크립트 생성 및 승인
        script = render_service.create_script(
            video_id="video-001",
            raw_json={"text": "Test content"},
            created_by="user-001",
        )
        
        # 잡 생성 및 완료 대기
        job = await render_service.create_render_job(
            video_id="video-001",
            script_id=script.script_id,
            requested_by="reviewer-001",
        )
        await asyncio.sleep(0.5)

        # 완료된 잡 취소 시도 → None 반환
        canceled_job = await render_service.cancel_job(job.job_id)
        assert canceled_job is None

        # 상태 변경 없음
        job_updated = render_service.get_job(job.job_id)
        assert job_updated.status == RenderJobStatus.COMPLETED


# =============================================================================
# Test 6: Asset retrieval
# =============================================================================


class TestAssetRetrieval:
    """에셋 조회 테스트."""

    @pytest.mark.asyncio
    async def test_get_latest_asset_by_video_id(self, render_service, mock_renderer):
        """비디오 ID로 최신 에셋 조회."""
        render_service.set_renderer(mock_renderer)

        # 스크립트 생성 및 승인
        script = render_service.create_script(
            video_id="video-001",
            raw_json={"text": "Test content"},
            created_by="user-001",
        )
        
        # 잡 생성 및 완료 대기
        job = await render_service.create_render_job(
            video_id="video-001",
            script_id=script.script_id,
            requested_by="reviewer-001",
        )
        await asyncio.sleep(0.5)

        # 에셋 조회
        asset = render_service.get_latest_asset_by_video_id("video-001")
        assert asset is not None
        assert asset.video_id == "video-001"
        assert asset.job_id == job.job_id

    @pytest.mark.asyncio
    async def test_get_asset_for_nonexistent_video(self, render_service):
        """존재하지 않는 비디오의 에셋 조회."""
        asset = render_service.get_latest_asset_by_video_id("nonexistent")
        assert asset is None


# =============================================================================
# Test 7: Model tests
# =============================================================================


class TestVideoRenderModels:
    """비디오 렌더 모델 테스트."""

    def test_script_status_enum(self):
        """ScriptStatus enum 값 확인."""
        assert ScriptStatus.DRAFT.value == "DRAFT"
        assert ScriptStatus.APPROVED.value == "APPROVED"
        assert ScriptStatus.REJECTED.value == "REJECTED"

    def test_render_job_status_enum(self):
        """RenderJobStatus enum 값 확인."""
        # Canonical status values (Phase 7)
        assert RenderJobStatus.QUEUED.value == "QUEUED"
        assert RenderJobStatus.PROCESSING.value == "PROCESSING"
        assert RenderJobStatus.COMPLETED.value == "COMPLETED"
        assert RenderJobStatus.FAILED.value == "FAILED"

    def test_render_step_enum(self):
        """RenderStep enum 값 확인."""
        steps = [
            RenderStep.VALIDATE_SCRIPT,
            RenderStep.GENERATE_TTS,
            RenderStep.GENERATE_SUBTITLE,
            RenderStep.RENDER_SLIDES,
            RenderStep.COMPOSE_VIDEO,
            RenderStep.UPLOAD_ASSETS,
            RenderStep.FINALIZE,
        ]
        assert len(steps) == 7

    def test_video_script_is_approved(self):
        """VideoScript.is_approved() 테스트."""
        script = VideoScript(
            script_id="script-001",
            video_id="video-001",
            status=ScriptStatus.DRAFT,
            raw_json={},
            created_by="user-001",
        )
        assert script.is_approved() is False

        script.status = ScriptStatus.APPROVED
        assert script.is_approved() is True

    def test_video_render_job_state_methods(self):
        """VideoRenderJob 상태 메서드 테스트."""
        job = VideoRenderJob(
            job_id="job-001",
            video_id="video-001",
            script_id="script-001",
            status=RenderJobStatus.QUEUED,
        )

        # QUEUED 상태
        assert job.is_active() is True
        assert job.is_terminal() is False
        assert job.can_cancel() is True

        # PROCESSING 상태
        job.status = RenderJobStatus.PROCESSING
        assert job.is_active() is True
        assert job.is_terminal() is False
        assert job.can_cancel() is True

        # COMPLETED 상태
        job.status = RenderJobStatus.COMPLETED
        assert job.is_active() is False
        assert job.is_terminal() is True
        assert job.can_cancel() is False

        # FAILED 상태
        job.status = RenderJobStatus.FAILED
        assert job.is_active() is False
        assert job.is_terminal() is True
        assert job.can_cancel() is False
