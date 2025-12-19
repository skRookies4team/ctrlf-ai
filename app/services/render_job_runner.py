"""
Phase 34: Render Job Runner

렌더 잡 실행기 - DB 영속화, 진행률 브로드캐스트, 에러 처리를 담당.

주요 기능:
- 잡 생성 및 중복 방지 (idempotency)
- 잡 실행 및 상태 관리 (DB 동기화)
- 진행률 실시간 알림 (WebSocket)
- 성공/실패 처리 및 에셋 저장
- 임시 파일 정리 (cleanup)

정책:
- 같은 video_id에 RUNNING/PENDING 잡이 있으면 기존 잡 반환
- APPROVED 스크립트만 렌더 가능
- 성공 시 assets 저장, 실패 시 error 저장

Phase 34 변경사항:
- StorageUploadError 예외 처리 (error_code=STORAGE_UPLOAD_FAILED)
- 업로드 실패 시 임시 파일 정리
"""

import asyncio
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from app.api.v1.ws_render_progress import (
    RenderProgressEvent,
    get_step_progress,
    notify_render_progress,
)
from app.clients.backend_script_client import (
    BackendScriptClient,
    EmptyRenderSpecError,
    ScriptFetchError,
    get_backend_script_client,
)
from app.clients.storage_adapter import StorageUploadError
from app.core.logging import get_logger
from app.models.render_spec import RenderSpec, validate_render_spec
from app.models.video_render import (
    RenderJobStatus,
    RenderStep,
    ScriptStatus,
    VideoScript,
)
from app.repositories.render_job_repository import (
    RenderJobEntity,
    get_render_job_repository,
    RenderJobRepository,
)
from app.services.video_render_service import VideoRenderer

logger = get_logger(__name__)


# =============================================================================
# Job Creation Result
# =============================================================================


class JobCreationResult:
    """잡 생성 결과.

    Attributes:
        job: 생성/조회된 잡
        created: 새로 생성되었는지 여부
        message: 결과 메시지
    """

    def __init__(
        self,
        job: RenderJobEntity,
        created: bool,
        message: str = "",
    ):
        self.job = job
        self.created = created
        self.message = message


class JobStartResult:
    """Phase 38: Job 시작 결과.

    Attributes:
        job: 시작된 잡
        started: 새로 시작되었는지 여부 (False면 이미 시작됨)
        message: 결과 메시지
        error_code: 에러 코드 (실패 시)
    """

    def __init__(
        self,
        job: Optional[RenderJobEntity],
        started: bool,
        message: str = "",
        error_code: Optional[str] = None,
    ):
        self.job = job
        self.started = started
        self.message = message
        self.error_code = error_code


# =============================================================================
# Render Job Runner
# =============================================================================


class RenderJobRunner:
    """렌더 잡 실행기.

    렌더 잡의 생성, 실행, 상태 관리를 담당합니다.
    DB에 영속화하고 WebSocket으로 진행률을 전송합니다.

    Usage:
        runner = RenderJobRunner(renderer)

        # 잡 생성 (idempotent)
        result = await runner.create_job(video_id, script_id, user_id)
        if result.created:
            print(f"New job created: {result.job.job_id}")
        else:
            print(f"Existing job returned: {result.job.job_id}")

        # 잡 조회
        job = runner.get_job("job-xxx")

        # 잡 목록
        jobs = runner.list_jobs("video-001", limit=20)
    """

    def __init__(
        self,
        renderer: Optional[VideoRenderer] = None,
        repository: Optional[RenderJobRepository] = None,
        script_client: Optional[BackendScriptClient] = None,
        output_dir: Optional[str] = None,
    ):
        """실행기 초기화.

        Args:
            renderer: 렌더러 (없으면 lazy-load)
            repository: 잡 저장소 (없으면 싱글톤 사용)
            script_client: Phase 38 - 백엔드 스크립트 클라이언트
            output_dir: 렌더링 출력 디렉토리
        """
        self._renderer = renderer
        self._repository = repository or get_render_job_repository()
        self._script_client = script_client or get_backend_script_client()
        self._output_dir = Path(output_dir or "./video_output")
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._running_jobs: Dict[str, asyncio.Task] = {}

    def set_renderer(self, renderer: VideoRenderer) -> None:
        """렌더러 설정."""
        self._renderer = renderer

    # =========================================================================
    # Job Creation
    # =========================================================================

    async def create_job(
        self,
        video_id: str,
        script_id: str,
        script: VideoScript,
        created_by: str,
    ) -> JobCreationResult:
        """렌더 잡 생성 (idempotent).

        - APPROVED 스크립트만 렌더 가능
        - 기존 RUNNING/PENDING 잡이 있으면 그 잡을 반환
        - 새 잡 생성 시 백그라운드에서 실행 시작

        Args:
            video_id: 비디오 ID
            script_id: 스크립트 ID
            script: 스크립트 객체
            created_by: 생성자 ID

        Returns:
            JobCreationResult: 생성 결과 (job, created, message)

        Raises:
            ValueError: 스크립트가 APPROVED가 아닌 경우
        """
        # 1. 스크립트 상태 검증
        if not script.is_approved():
            raise ValueError(
                f"Script is not approved: script_id={script_id}, "
                f"status={script.status.value}"
            )

        if script.video_id != video_id:
            raise ValueError(
                f"Script video_id mismatch: script={script.video_id}, "
                f"request={video_id}"
            )

        # 2. 기존 활성 잡 확인 (idempotency)
        existing_job = self._repository.get_active_by_video_id(video_id)
        if existing_job:
            logger.info(
                f"Existing active job found: job_id={existing_job.job_id}, "
                f"status={existing_job.status}"
            )
            return JobCreationResult(
                job=existing_job,
                created=False,
                message=f"Existing {existing_job.status} job returned",
            )

        # 3. 새 잡 생성
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        job = RenderJobEntity(
            job_id=job_id,
            video_id=video_id,
            script_id=script_id,
            status="PENDING",
            progress=0,
            message="대기 중...",
            created_by=created_by,
            created_at=datetime.utcnow(),
        )

        self._repository.save(job)
        logger.info(f"New render job created: job_id={job_id}, video_id={video_id}")

        # 4. 백그라운드에서 실행 시작
        task = asyncio.create_task(self._execute_job(job_id, script))
        self._running_jobs[job_id] = task

        return JobCreationResult(
            job=job,
            created=True,
            message="New job created and started",
        )

    # =========================================================================
    # Phase 38: Job Start (Snapshot-based)
    # =========================================================================

    async def start_job(self, job_id: str) -> JobStartResult:
        """Phase 38: 잡 시작 (render-spec 스냅샷 기반).

        백엔드에서 최신 render-spec을 조회하여 스냅샷으로 저장하고,
        이후 파이프라인을 실행합니다.

        Idempotent:
        - 이미 render_spec_json이 있고 RUNNING/SUCCEEDED/FAILED면 no-op

        Args:
            job_id: 잡 ID

        Returns:
            JobStartResult: 시작 결과
        """
        # 1. 잡 조회
        job = self._repository.get(job_id)
        if not job:
            logger.warning(f"Job not found: {job_id}")
            return JobStartResult(
                job=None,
                started=False,
                message="Job not found",
                error_code="JOB_NOT_FOUND",
            )

        # 2. Idempotency 체크: 이미 시작된 경우
        if job.has_render_spec() and job.status in ("RUNNING", "SUCCEEDED", "FAILED"):
            logger.info(
                f"Job already started: job_id={job_id}, status={job.status}"
            )
            return JobStartResult(
                job=job,
                started=False,
                message=f"Job already {job.status}",
            )

        # 3. render_spec_json이 없으면 백엔드에서 조회
        if not job.has_render_spec():
            try:
                logger.info(
                    f"Fetching render-spec from backend: "
                    f"job_id={job_id}, script_id={job.script_id}"
                )

                # 백엔드에서 render-spec 조회
                render_spec = await self._script_client.get_render_spec(job.script_id)

                # 검증 및 정규화
                normalized_spec, warnings = validate_render_spec(render_spec)
                for warning in warnings:
                    logger.warning(f"Render spec validation: {warning}")

                # render_spec_json으로 저장 (스냅샷)
                spec_json = normalized_spec.model_dump()
                self._repository.update_render_spec(job_id, spec_json)

                logger.info(
                    f"Render-spec snapshot saved: job_id={job_id}, "
                    f"scenes={normalized_spec.get_scene_count()}"
                )

                # 메모리 상의 job 갱신
                job = self._repository.get(job_id)

            except ScriptFetchError as e:
                logger.error(
                    f"Failed to fetch render-spec: job_id={job_id}, "
                    f"script_id={job.script_id}, error={e}"
                )
                # FAILED 상태로 전환
                self._repository.update_error(
                    job_id=job_id,
                    error_code=e.error_code,
                    error_message=e.message,
                )
                return JobStartResult(
                    job=self._repository.get(job_id),
                    started=False,
                    message=e.message,
                    error_code=e.error_code,
                )

            except EmptyRenderSpecError as e:
                logger.error(
                    f"Empty render-spec: job_id={job_id}, script_id={job.script_id}"
                )
                self._repository.update_error(
                    job_id=job_id,
                    error_code=e.error_code,
                    error_message=str(e),
                )
                return JobStartResult(
                    job=self._repository.get(job_id),
                    started=False,
                    message="Render-spec has no scenes",
                    error_code=e.error_code,
                )

        # 4. 파이프라인 시작
        task = asyncio.create_task(self._execute_job_with_spec(job_id))
        self._running_jobs[job_id] = task

        logger.info(f"Job started: job_id={job_id}")

        return JobStartResult(
            job=self._repository.get(job_id),
            started=True,
            message="Job started",
        )

    async def retry_job(self, job_id: str) -> JobStartResult:
        """Phase 38: 잡 재시도 (기존 스냅샷 사용).

        이전에 저장된 render_spec_json을 재사용하여 재시도합니다.
        백엔드를 다시 호출하지 않습니다.

        Args:
            job_id: 잡 ID

        Returns:
            JobStartResult: 시작 결과
        """
        # 1. 잡 조회
        job = self._repository.get(job_id)
        if not job:
            return JobStartResult(
                job=None,
                started=False,
                message="Job not found",
                error_code="JOB_NOT_FOUND",
            )

        # 2. render_spec_json이 없으면 재시도 불가
        if not job.has_render_spec():
            logger.error(f"No render-spec for retry: job_id={job_id}")
            return JobStartResult(
                job=job,
                started=False,
                message="No render-spec snapshot for retry. Use start_job first.",
                error_code="NO_RENDER_SPEC_FOR_RETRY",
            )

        # 3. RUNNING 중이면 재시도 불가
        if job.status == "RUNNING":
            return JobStartResult(
                job=job,
                started=False,
                message="Job is already running",
            )

        # 4. 상태 초기화 및 재시작
        self._repository.update_status(
            job_id=job_id,
            status="PENDING",
            step=None,
            progress=0,
            message="재시도 대기 중...",
        )

        # 5. 파이프라인 시작 (기존 스냅샷 사용)
        task = asyncio.create_task(self._execute_job_with_spec(job_id))
        self._running_jobs[job_id] = task

        logger.info(f"Job retry started (using existing snapshot): job_id={job_id}")

        return JobStartResult(
            job=self._repository.get(job_id),
            started=True,
            message="Job retry started",
        )

    # =========================================================================
    # Job Retrieval
    # =========================================================================

    def get_job(self, job_id: str) -> Optional[RenderJobEntity]:
        """잡 조회."""
        return self._repository.get(job_id)

    def list_jobs(
        self,
        video_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list:
        """잡 목록 조회 (최신순)."""
        return self._repository.get_by_video_id(video_id, limit, offset)

    def get_active_job(self, video_id: str) -> Optional[RenderJobEntity]:
        """활성 잡 조회."""
        return self._repository.get_active_by_video_id(video_id)

    def get_succeeded_job(self, video_id: str) -> Optional[RenderJobEntity]:
        """최신 성공 잡 조회."""
        return self._repository.get_succeeded_by_video_id(video_id)

    def get_published_assets(self, video_id: str) -> Optional[Dict[str, Any]]:
        """발행된 에셋 조회.

        Args:
            video_id: 비디오 ID

        Returns:
            dict: {video_url, subtitle_url, thumbnail_url, published_at, script_id}
            None: 발행된 에셋이 없는 경우
        """
        job = self._repository.get_latest_published_by_video_id(video_id)
        if not job or not job.assets:
            return None

        return {
            "video_url": job.assets.get("video_url"),
            "subtitle_url": job.assets.get("subtitle_url"),
            "thumbnail_url": job.assets.get("thumbnail_url"),
            "duration_sec": job.assets.get("duration_sec"),
            "published_at": job.finished_at.isoformat() if job.finished_at else None,
            "script_id": job.script_id,
            "job_id": job.job_id,
        }

    # =========================================================================
    # Job Cancellation
    # =========================================================================

    async def cancel_job(self, job_id: str) -> Optional[RenderJobEntity]:
        """잡 취소.

        Args:
            job_id: 잡 ID

        Returns:
            취소된 잡 (취소 불가하면 None)
        """
        job = self._repository.get(job_id)
        if not job or not job.is_active():
            return None

        # DB 상태 업데이트
        self._repository.update_status(
            job_id=job_id,
            status="CANCELED",
            message="사용자에 의해 취소됨",
        )

        # 실행 중인 태스크 취소
        if job_id in self._running_jobs:
            task = self._running_jobs[job_id]
            task.cancel()
            del self._running_jobs[job_id]

        # WebSocket 알림
        await notify_render_progress(
            job_id=job_id,
            video_id=job.video_id,
            status=RenderJobStatus.CANCELED,
            progress=job.progress,
            message="취소됨",
        )

        logger.info(f"Render job canceled: job_id={job_id}")

        return self._repository.get(job_id)

    # =========================================================================
    # Job Execution
    # =========================================================================

    async def _execute_job(self, job_id: str, script: VideoScript) -> None:
        """잡 실행 (백그라운드).

        Args:
            job_id: 잡 ID
            script: 스크립트 객체
        """
        job = self._repository.get(job_id)
        if not job:
            logger.error(f"Job not found for execution: {job_id}")
            return

        # 취소된 경우 스킵
        if job.status == "CANCELED":
            logger.info(f"Job already canceled, skipping: {job_id}")
            return

        # RUNNING 상태로 전환
        self._repository.update_status(
            job_id=job_id,
            status="RUNNING",
            step=RenderStep.VALIDATE_SCRIPT.value,
            progress=0,
            message="실행 중...",
        )

        await notify_render_progress(
            job_id=job_id,
            video_id=job.video_id,
            status=RenderJobStatus.RUNNING,
            step=RenderStep.VALIDATE_SCRIPT,
            progress=0,
            message="렌더링 시작...",
        )

        try:
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

            for step in steps:
                # 취소 확인
                job = self._repository.get(job_id)
                if job and job.status == "CANCELED":
                    logger.info(f"Job canceled during pipeline: {job_id}")
                    return

                # 진행률 계산
                progress, message = get_step_progress(step, 0.0)

                # DB 업데이트
                self._repository.update_status(
                    job_id=job_id,
                    status="RUNNING",
                    step=step.value,
                    progress=progress,
                    message=message,
                )

                # WebSocket 알림
                await notify_render_progress(
                    job_id=job_id,
                    video_id=job.video_id,
                    status=RenderJobStatus.RUNNING,
                    step=step,
                    progress=progress,
                    message=message,
                )

                logger.info(f"Render step: job_id={job_id}, step={step.value}")

                # 실제 렌더링 수행
                await self._renderer.execute_step(step, script.raw_json, job_id)

            # 에셋 조회
            rendered = await self._renderer.get_rendered_assets(job_id)

            # 에셋 저장
            assets = {
                "video_url": rendered.mp4_path,
                "subtitle_url": rendered.subtitle_path,
                "thumbnail_url": rendered.thumbnail_path,
                "duration_sec": rendered.duration_sec,
            }
            self._repository.update_assets(job_id, assets)

            # 성공 상태로 전환
            self._repository.update_status(
                job_id=job_id,
                status="SUCCEEDED",
                step=RenderStep.FINALIZE.value,
                progress=100,
                message="렌더링 완료!",
            )

            # WebSocket 알림
            await notify_render_progress(
                job_id=job_id,
                video_id=job.video_id,
                status=RenderJobStatus.SUCCEEDED,
                step=RenderStep.FINALIZE,
                progress=100,
                message="렌더링 완료!",
            )

            logger.info(f"Render job succeeded: job_id={job_id}")

        except asyncio.CancelledError:
            # 취소됨
            logger.info(f"Render job task cancelled: {job_id}")
            raise

        except StorageUploadError as e:
            # Phase 34: Storage 업로드 실패 시 STORAGE_UPLOAD_FAILED 에러 코드
            error_code = "STORAGE_UPLOAD_FAILED"
            error_message = f"Storage upload failed for key '{e.key}': {e.message}"[:500]

            self._repository.update_error(
                job_id=job_id,
                error_code=error_code,
                error_message=error_message,
            )

            # WebSocket 알림
            job = self._repository.get(job_id)
            if job:
                await notify_render_progress(
                    job_id=job_id,
                    video_id=job.video_id,
                    status=RenderJobStatus.FAILED,
                    step=RenderStep.UPLOAD_ASSETS,
                    progress=job.progress,
                    message=f"스토리지 업로드 실패: {e.message[:100]}",
                )

            logger.error(f"Render job storage upload failed: job_id={job_id}, key={e.key}, error={e.message}")

            # 임시 파일 정리
            await self._cleanup_job_files(job_id)

        except Exception as e:
            # 실패 처리
            error_code = type(e).__name__
            error_message = str(e)[:500]  # 메시지 길이 제한

            self._repository.update_error(
                job_id=job_id,
                error_code=error_code,
                error_message=error_message,
            )

            # WebSocket 알림
            job = self._repository.get(job_id)
            if job:
                await notify_render_progress(
                    job_id=job_id,
                    video_id=job.video_id,
                    status=RenderJobStatus.FAILED,
                    step=RenderStep(job.step) if job.step else None,
                    progress=job.progress,
                    message=f"실패: {error_message[:100]}",
                )

            logger.error(f"Render job failed: job_id={job_id}, error={e}")

            # 임시 파일 정리
            await self._cleanup_job_files(job_id)

        finally:
            # 실행 중인 잡 목록에서 제거
            if job_id in self._running_jobs:
                del self._running_jobs[job_id]

    async def _execute_job_with_spec(self, job_id: str) -> None:
        """Phase 38: 스냅샷 기반 잡 실행 (백그라운드).

        job.render_spec_json에 저장된 스냅샷을 사용하여 렌더링을 수행합니다.

        Args:
            job_id: 잡 ID
        """
        job = self._repository.get(job_id)
        if not job:
            logger.error(f"Job not found for execution: {job_id}")
            return

        # render_spec_json이 없으면 실행 불가
        if not job.has_render_spec():
            logger.error(f"No render_spec_json for job: {job_id}")
            self._repository.update_error(
                job_id=job_id,
                error_code="NO_RENDER_SPEC",
                error_message="No render-spec snapshot found",
            )
            return

        # 취소된 경우 스킵
        if job.status == "CANCELED":
            logger.info(f"Job already canceled, skipping: {job_id}")
            return

        # render_spec_json에서 RenderSpec으로 변환
        render_spec = RenderSpec(**job.render_spec_json)
        # raw_json 형식으로 변환 (기존 파이프라인 호환)
        raw_json = render_spec.to_raw_json()

        # RUNNING 상태로 전환
        self._repository.update_status(
            job_id=job_id,
            status="RUNNING",
            step=RenderStep.VALIDATE_SCRIPT.value,
            progress=0,
            message="실행 중...",
        )

        await notify_render_progress(
            job_id=job_id,
            video_id=job.video_id,
            status=RenderJobStatus.RUNNING,
            step=RenderStep.VALIDATE_SCRIPT,
            progress=0,
            message="렌더링 시작...",
        )

        try:
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

            for step in steps:
                # 취소 확인
                job = self._repository.get(job_id)
                if job and job.status == "CANCELED":
                    logger.info(f"Job canceled during pipeline: {job_id}")
                    return

                # 진행률 계산
                progress, message = get_step_progress(step, 0.0)

                # DB 업데이트
                self._repository.update_status(
                    job_id=job_id,
                    status="RUNNING",
                    step=step.value,
                    progress=progress,
                    message=message,
                )

                # WebSocket 알림
                await notify_render_progress(
                    job_id=job_id,
                    video_id=job.video_id,
                    status=RenderJobStatus.RUNNING,
                    step=step,
                    progress=progress,
                    message=message,
                )

                logger.info(f"Render step: job_id={job_id}, step={step.value}")

                # 실제 렌더링 수행 (스냅샷 기반 raw_json 사용)
                await self._renderer.execute_step(step, raw_json, job_id)

            # 에셋 조회
            rendered = await self._renderer.get_rendered_assets(job_id)

            # 에셋 저장
            assets = {
                "video_url": rendered.mp4_path,
                "subtitle_url": rendered.subtitle_path,
                "thumbnail_url": rendered.thumbnail_path,
                "duration_sec": rendered.duration_sec,
            }
            self._repository.update_assets(job_id, assets)

            # 성공 상태로 전환
            self._repository.update_status(
                job_id=job_id,
                status="SUCCEEDED",
                step=RenderStep.FINALIZE.value,
                progress=100,
                message="렌더링 완료!",
            )

            # WebSocket 알림
            await notify_render_progress(
                job_id=job_id,
                video_id=job.video_id,
                status=RenderJobStatus.SUCCEEDED,
                step=RenderStep.FINALIZE,
                progress=100,
                message="렌더링 완료!",
            )

            logger.info(f"Render job succeeded: job_id={job_id}")

        except asyncio.CancelledError:
            logger.info(f"Render job task cancelled: {job_id}")
            raise

        except StorageUploadError as e:
            error_code = "STORAGE_UPLOAD_FAILED"
            error_message = f"Storage upload failed for key '{e.key}': {e.message}"[:500]

            self._repository.update_error(
                job_id=job_id,
                error_code=error_code,
                error_message=error_message,
            )

            job = self._repository.get(job_id)
            if job:
                await notify_render_progress(
                    job_id=job_id,
                    video_id=job.video_id,
                    status=RenderJobStatus.FAILED,
                    step=RenderStep.UPLOAD_ASSETS,
                    progress=job.progress,
                    message=f"스토리지 업로드 실패: {e.message[:100]}",
                )

            logger.error(
                f"Render job storage upload failed: job_id={job_id}, "
                f"key={e.key}, error={e.message}"
            )
            await self._cleanup_job_files(job_id)

        except Exception as e:
            error_code = type(e).__name__
            error_message = str(e)[:500]

            self._repository.update_error(
                job_id=job_id,
                error_code=error_code,
                error_message=error_message,
            )

            job = self._repository.get(job_id)
            if job:
                await notify_render_progress(
                    job_id=job_id,
                    video_id=job.video_id,
                    status=RenderJobStatus.FAILED,
                    step=RenderStep(job.step) if job.step else None,
                    progress=job.progress,
                    message=f"실패: {error_message[:100]}",
                )

            logger.error(f"Render job failed: job_id={job_id}, error={e}")
            await self._cleanup_job_files(job_id)

        finally:
            if job_id in self._running_jobs:
                del self._running_jobs[job_id]

    async def _cleanup_job_files(self, job_id: str) -> None:
        """잡 임시 파일 정리.

        Args:
            job_id: 잡 ID
        """
        job_dir = self._output_dir / job_id
        if job_dir.exists():
            try:
                shutil.rmtree(job_dir)
                logger.info(f"Cleaned up job files: {job_dir}")
            except Exception as e:
                logger.warning(f"Failed to cleanup job files: {job_dir}, error={e}")


# =============================================================================
# Singleton Instance
# =============================================================================


_runner: Optional[RenderJobRunner] = None


def get_render_job_runner() -> RenderJobRunner:
    """RenderJobRunner 싱글톤 인스턴스 반환."""
    global _runner
    if _runner is None:
        _runner = RenderJobRunner()
    return _runner


def clear_render_job_runner() -> None:
    """RenderJobRunner 싱글톤 초기화 (테스트용)."""
    global _runner
    _runner = None
