"""
Phase 27: Video Render Service

영상 생성 파이프라인 관리 서비스.

주요 기능:
- 렌더 잡 생성/조회/취소
- 스크립트 관리 (생성/승인)
- 에셋 관리
- 렌더링 파이프라인 실행

안정성 규칙:
- 동일 video_id에 대해 RUNNING/PENDING 잡이 있으면 중복 생성 불가
- 이미 SUCCEEDED job이 있으면 idempotent 처리
- FAILED 시 error_message + step 저장
"""

import asyncio
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from app.core.logging import get_logger
from app.models.video_render import (
    RenderedAssets,
    RenderJobStatus,
    RenderStep,
    ScriptStatus,
    VideoAsset,
    VideoRenderJob,
    VideoScript,
)

logger = get_logger(__name__)


# =============================================================================
# In-Memory Stores (MVP - replace with DB in production)
# =============================================================================


class VideoScriptStore:
    """스크립트 저장소 (MVP: 인메모리)."""

    def __init__(self) -> None:
        self._scripts: Dict[str, VideoScript] = {}

    def save(self, script: VideoScript) -> None:
        """스크립트 저장."""
        self._scripts[script.script_id] = script

    def get(self, script_id: str) -> Optional[VideoScript]:
        """스크립트 조회."""
        return self._scripts.get(script_id)

    def get_by_video_id(self, video_id: str) -> List[VideoScript]:
        """비디오 ID로 스크립트 목록 조회."""
        return [s for s in self._scripts.values() if s.video_id == video_id]

    def delete(self, script_id: str) -> bool:
        """스크립트 삭제."""
        if script_id in self._scripts:
            del self._scripts[script_id]
            return True
        return False


class VideoRenderJobStore:
    """렌더 잡 저장소 (MVP: 인메모리)."""

    def __init__(self) -> None:
        self._jobs: Dict[str, VideoRenderJob] = {}

    def save(self, job: VideoRenderJob) -> None:
        """잡 저장."""
        self._jobs[job.job_id] = job

    def get(self, job_id: str) -> Optional[VideoRenderJob]:
        """잡 조회."""
        return self._jobs.get(job_id)

    def get_by_video_id(self, video_id: str) -> List[VideoRenderJob]:
        """비디오 ID로 잡 목록 조회."""
        return [j for j in self._jobs.values() if j.video_id == video_id]

    def get_active_by_video_id(self, video_id: str) -> Optional[VideoRenderJob]:
        """비디오 ID로 활성 잡(PENDING/RUNNING) 조회."""
        for job in self._jobs.values():
            if job.video_id == video_id and job.is_active():
                return job
        return None

    def get_succeeded_by_video_id(self, video_id: str) -> Optional[VideoRenderJob]:
        """비디오 ID로 최신 성공 잡 조회."""
        succeeded_jobs = [
            j for j in self._jobs.values()
            if j.video_id == video_id and j.status == RenderJobStatus.SUCCEEDED
        ]
        if not succeeded_jobs:
            return None
        return max(succeeded_jobs, key=lambda j: j.finished_at or j.created_at)

    def delete(self, job_id: str) -> bool:
        """잡 삭제."""
        if job_id in self._jobs:
            del self._jobs[job_id]
            return True
        return False


class VideoAssetStore:
    """비디오 에셋 저장소 (MVP: 인메모리)."""

    def __init__(self) -> None:
        self._assets: Dict[str, VideoAsset] = {}

    def save(self, asset: VideoAsset) -> None:
        """에셋 저장."""
        self._assets[asset.video_asset_id] = asset

    def get(self, asset_id: str) -> Optional[VideoAsset]:
        """에셋 조회."""
        return self._assets.get(asset_id)

    def get_by_job_id(self, job_id: str) -> Optional[VideoAsset]:
        """잡 ID로 에셋 조회."""
        for asset in self._assets.values():
            if asset.job_id == job_id:
                return asset
        return None

    def get_by_video_id(self, video_id: str) -> List[VideoAsset]:
        """비디오 ID로 에셋 목록 조회."""
        return [a for a in self._assets.values() if a.video_id == video_id]

    def get_latest_by_video_id(self, video_id: str) -> Optional[VideoAsset]:
        """비디오 ID로 최신 에셋 조회."""
        assets = self.get_by_video_id(video_id)
        if not assets:
            return None
        return max(assets, key=lambda a: a.created_at)


# =============================================================================
# Video Render Service
# =============================================================================


class VideoRenderService:
    """영상 렌더링 서비스.

    렌더 잡 생성, 상태 관리, 파이프라인 실행을 담당합니다.

    Usage:
        service = VideoRenderService()

        # 스크립트 생성 및 승인
        script = service.create_script(video_id, raw_json, user_id)
        script = service.approve_script(script_id, reviewer_id)

        # 렌더 잡 생성 및 실행
        job = await service.create_render_job(video_id, script_id, user_id)
        # 백그라운드에서 실행됨
    """

    def __init__(
        self,
        script_store: Optional[VideoScriptStore] = None,
        job_store: Optional[VideoRenderJobStore] = None,
        asset_store: Optional[VideoAssetStore] = None,
    ) -> None:
        """서비스 초기화."""
        self._script_store = script_store or VideoScriptStore()
        self._job_store = job_store or VideoRenderJobStore()
        self._asset_store = asset_store or VideoAssetStore()
        self._renderer: Optional["VideoRenderer"] = None

    def set_renderer(self, renderer: "VideoRenderer") -> None:
        """렌더러 설정."""
        self._renderer = renderer

    # =========================================================================
    # Script Management
    # =========================================================================

    def create_script(
        self,
        video_id: str,
        raw_json: dict,
        created_by: str,
    ) -> VideoScript:
        """스크립트 생성.

        Args:
            video_id: 비디오 ID
            raw_json: 스크립트 JSON
            created_by: 생성자 ID

        Returns:
            생성된 스크립트
        """
        script = VideoScript(
            script_id=f"script-{uuid.uuid4().hex[:12]}",
            video_id=video_id,
            status=ScriptStatus.DRAFT,
            raw_json=raw_json,
            created_by=created_by,
            created_at=datetime.utcnow(),
        )
        self._script_store.save(script)
        logger.info(f"Script created: script_id={script.script_id}, video_id={video_id}")
        return script

    def get_script(self, script_id: str) -> Optional[VideoScript]:
        """스크립트 조회."""
        return self._script_store.get(script_id)

    def approve_script(self, script_id: str, reviewer_id: str) -> Optional[VideoScript]:
        """스크립트 승인.

        Args:
            script_id: 스크립트 ID
            reviewer_id: 검토자 ID

        Returns:
            승인된 스크립트 (없으면 None)
        """
        script = self._script_store.get(script_id)
        if not script:
            return None

        script.status = ScriptStatus.APPROVED
        self._script_store.save(script)
        logger.info(f"Script approved: script_id={script_id}, reviewer={reviewer_id}")
        return script

    # =========================================================================
    # Render Job Management
    # =========================================================================

    async def create_render_job(
        self,
        video_id: str,
        script_id: str,
        requested_by: str,
    ) -> VideoRenderJob:
        """렌더 잡 생성.

        Args:
            video_id: 비디오 ID
            script_id: 스크립트 ID
            requested_by: 요청자 ID

        Returns:
            생성된 렌더 잡

        Raises:
            ValueError: 스크립트가 없거나 APPROVED가 아닌 경우
            RuntimeError: 이미 활성 잡이 있는 경우
        """
        # 스크립트 검증
        script = self._script_store.get(script_id)
        if not script:
            raise ValueError(f"Script not found: {script_id}")
        if not script.is_approved():
            raise ValueError(f"Script is not approved: {script_id} (status={script.status.value})")
        if script.video_id != video_id:
            raise ValueError(f"Script video_id mismatch: {script.video_id} != {video_id}")

        # 중복 잡 체크
        active_job = self._job_store.get_active_by_video_id(video_id)
        if active_job:
            raise RuntimeError(
                f"Active job already exists for video_id={video_id}: "
                f"job_id={active_job.job_id}, status={active_job.status.value}"
            )

        # 잡 생성
        job = VideoRenderJob(
            job_id=f"job-{uuid.uuid4().hex[:12]}",
            video_id=video_id,
            script_id=script_id,
            status=RenderJobStatus.PENDING,
            requested_by=requested_by,
            created_at=datetime.utcnow(),
        )
        self._job_store.save(job)
        logger.info(f"Render job created: job_id={job.job_id}, video_id={video_id}")

        # 백그라운드에서 렌더링 실행
        asyncio.create_task(self._execute_render_pipeline(job.job_id))

        return job

    def get_job(self, job_id: str) -> Optional[VideoRenderJob]:
        """잡 조회."""
        return self._job_store.get(job_id)

    def get_job_with_asset(self, job_id: str) -> tuple[Optional[VideoRenderJob], Optional[VideoAsset]]:
        """잡과 에셋 함께 조회."""
        job = self._job_store.get(job_id)
        if not job:
            return None, None
        asset = self._asset_store.get_by_job_id(job_id) if job.status == RenderJobStatus.SUCCEEDED else None
        return job, asset

    async def cancel_job(self, job_id: str) -> Optional[VideoRenderJob]:
        """잡 취소.

        Args:
            job_id: 잡 ID

        Returns:
            취소된 잡 (없거나 취소 불가하면 None)
        """
        job = self._job_store.get(job_id)
        if not job:
            return None
        if not job.can_cancel():
            return None

        job.status = RenderJobStatus.CANCELED
        job.finished_at = datetime.utcnow()
        self._job_store.save(job)
        logger.info(f"Render job canceled: job_id={job_id}")
        return job

    # =========================================================================
    # Asset Management
    # =========================================================================

    def get_asset(self, asset_id: str) -> Optional[VideoAsset]:
        """에셋 조회."""
        return self._asset_store.get(asset_id)

    def get_latest_asset_by_video_id(self, video_id: str) -> Optional[VideoAsset]:
        """비디오 ID로 최신 에셋 조회."""
        return self._asset_store.get_latest_by_video_id(video_id)

    def get_asset_by_job_id(self, job_id: str) -> Optional[VideoAsset]:
        """잡 ID로 에셋 조회."""
        return self._asset_store.get_by_job_id(job_id)

    # =========================================================================
    # Render Pipeline Execution
    # =========================================================================

    async def _execute_render_pipeline(self, job_id: str) -> None:
        """렌더링 파이프라인 실행 (백그라운드).

        Args:
            job_id: 잡 ID
        """
        job = self._job_store.get(job_id)
        if not job:
            logger.error(f"Job not found for pipeline execution: {job_id}")
            return

        # 이미 취소된 경우 스킵
        if job.status == RenderJobStatus.CANCELED:
            logger.info(f"Job already canceled, skipping: {job_id}")
            return

        # RUNNING 상태로 전환
        job.status = RenderJobStatus.RUNNING
        job.started_at = datetime.utcnow()
        self._job_store.save(job)

        try:
            # 스크립트 조회
            script = self._script_store.get(job.script_id)
            if not script:
                raise ValueError(f"Script not found: {job.script_id}")

            # 렌더러 확인
            if not self._renderer:
                raise RuntimeError("Renderer not configured")

            # 파이프라인 단계별 실행
            steps = [
                RenderStep.VALIDATE_SCRIPT,
                RenderStep.GENERATE_TTS,
                RenderStep.GENERATE_SUBTITLE,
                RenderStep.RENDER_SLIDES,
                RenderStep.COMPOSE_VIDEO,
                RenderStep.UPLOAD_ASSETS,
                RenderStep.FINALIZE,
            ]

            for i, step in enumerate(steps):
                # 취소 확인
                job = self._job_store.get(job_id)
                if job and job.status == RenderJobStatus.CANCELED:
                    logger.info(f"Job canceled during pipeline: {job_id}, step={step.value}")
                    return

                # 단계 업데이트
                job.step = step
                job.progress = int((i / len(steps)) * 100)
                self._job_store.save(job)
                logger.info(f"Render step: job_id={job_id}, step={step.value}, progress={job.progress}%")

                # 실제 렌더링 수행
                await self._renderer.execute_step(step, script.raw_json, job_id)

            # 최종 에셋 생성
            rendered = await self._renderer.get_rendered_assets(job_id)

            # 에셋 저장
            asset = VideoAsset(
                video_asset_id=f"asset-{uuid.uuid4().hex[:12]}",
                video_id=job.video_id,
                job_id=job.job_id,
                video_url=rendered.mp4_path,
                thumbnail_url=rendered.thumbnail_path,
                subtitle_url=rendered.subtitle_path,
                duration_sec=rendered.duration_sec,
                created_at=datetime.utcnow(),
            )
            self._asset_store.save(asset)

            # 성공 상태로 전환
            job.status = RenderJobStatus.SUCCEEDED
            job.step = RenderStep.FINALIZE
            job.progress = 100
            job.finished_at = datetime.utcnow()
            self._job_store.save(job)

            logger.info(f"Render job succeeded: job_id={job_id}, asset_id={asset.video_asset_id}")

            # 퍼블리시 훅 호출
            await self._on_video_published(job.video_id, job.script_id)

        except Exception as e:
            # 실패 상태로 전환
            job = self._job_store.get(job_id)
            if job:
                job.status = RenderJobStatus.FAILED
                job.error_message = str(e)
                job.finished_at = datetime.utcnow()
                self._job_store.save(job)

            logger.error(f"Render job failed: job_id={job_id}, error={e}")

    async def _on_video_published(self, video_id: str, script_id: str) -> None:
        """영상 퍼블리시 훅 (Phase 28 RAG 적재용).

        영상 생성이 성공하면 호출됩니다.
        추후 KB에 승인된 교육 내용을 적재할 때 사용합니다.

        Args:
            video_id: 비디오 ID
            script_id: 스크립트 ID
        """
        logger.info(f"Video published hook: video_id={video_id}, script_id={script_id}")
        # TODO: Phase 28에서 RAG 인덱싱 구현


# =============================================================================
# Video Renderer Interface
# =============================================================================


class VideoRenderer:
    """영상 렌더러 인터페이스.

    구현체는 이 인터페이스를 상속하여 실제 렌더링 로직을 구현합니다.
    """

    async def execute_step(
        self,
        step: RenderStep,
        script_json: dict,
        job_id: str,
    ) -> None:
        """파이프라인 단계 실행.

        Args:
            step: 실행할 단계
            script_json: 스크립트 JSON
            job_id: 잡 ID
        """
        raise NotImplementedError

    async def get_rendered_assets(self, job_id: str) -> RenderedAssets:
        """렌더링된 에셋 조회.

        Args:
            job_id: 잡 ID

        Returns:
            렌더링된 에셋 정보
        """
        raise NotImplementedError


# =============================================================================
# Singleton Instance
# =============================================================================


_render_service: Optional[VideoRenderService] = None


def get_video_render_service() -> VideoRenderService:
    """VideoRenderService 싱글톤 인스턴스 반환."""
    global _render_service
    if _render_service is None:
        _render_service = VideoRenderService()
    return _render_service
