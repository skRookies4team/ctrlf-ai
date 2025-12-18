"""
Job Service (Phase 25)

인덱싱/삭제 작업의 상태를 추적하고 관리하는 서비스입니다.
백엔드에서 작업 상태를 폴링할 수 있도록 지원합니다.

작업 상태:
- QUEUED: 대기 중
- RUNNING: 실행 중
- COMPLETED: 완료
- FAILED: 실패
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.internal_rag import JobStatus, JobStatusResponse

logger = get_logger(__name__)


# =============================================================================
# Job Entry
# =============================================================================


class JobEntry:
    """작업 엔트리."""

    def __init__(
        self,
        job_id: str,
        document_id: Optional[str] = None,
        version_no: Optional[int] = None,
        job_type: str = "index",  # "index" or "delete"
    ):
        self.job_id = job_id
        self.document_id = document_id
        self.version_no = version_no
        self.job_type = job_type
        self.status = JobStatus.QUEUED
        self.progress: Optional[str] = None
        self.chunks_processed: int = 0
        self.error_message: Optional[str] = None
        self.created_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
        self.logs: List[Dict[str, Any]] = []

    def update_status(
        self,
        status: JobStatus,
        progress: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """작업 상태를 업데이트합니다."""
        self.status = status
        if progress:
            self.progress = progress
        if error_message:
            self.error_message = error_message
        self.updated_at = datetime.now(timezone.utc)

    def add_log(self, stage: str, message: str, level: str = "INFO") -> None:
        """작업 로그를 추가합니다."""
        self.logs.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            "level": level,
            "message": message,
        })
        self.updated_at = datetime.now(timezone.utc)

    def to_response(self) -> JobStatusResponse:
        """JobStatusResponse로 변환합니다."""
        return JobStatusResponse(
            job_id=self.job_id,
            status=self.status,
            document_id=self.document_id,
            version_no=self.version_no,
            progress=self.progress,
            chunks_processed=self.chunks_processed if self.chunks_processed > 0 else None,
            error_message=self.error_message,
            created_at=self.created_at.isoformat(),
            updated_at=self.updated_at.isoformat(),
        )


# =============================================================================
# Job Service
# =============================================================================


class JobService:
    """
    작업 관리 서비스.

    인메모리로 작업 상태를 관리합니다.
    프로덕션에서는 Redis나 DB 기반으로 확장 가능합니다.

    Example:
        job_service = JobService()
        job = job_service.create_job("job-123", "DOC-001", 1)
        job_service.update_job("job-123", JobStatus.RUNNING, "downloading")
        status = job_service.get_job_status("job-123")
    """

    # 작업 보관 시간 (초) - 완료/실패 후 이 시간이 지나면 삭제
    JOB_RETENTION_SECONDS = 3600  # 1시간

    def __init__(self) -> None:
        """JobService 초기화."""
        self._jobs: Dict[str, JobEntry] = {}
        self._lock = asyncio.Lock()

        logger.info("JobService initialized (in-memory storage)")

    async def create_job(
        self,
        job_id: str,
        document_id: Optional[str] = None,
        version_no: Optional[int] = None,
        job_type: str = "index",
    ) -> JobEntry:
        """
        새 작업을 생성합니다.

        Args:
            job_id: 작업 ID
            document_id: 문서 ID
            version_no: 버전 번호
            job_type: 작업 유형 ("index" or "delete")

        Returns:
            JobEntry: 생성된 작업 엔트리
        """
        async with self._lock:
            # 기존 작업이 있으면 삭제 후 새로 생성 (idempotency)
            if job_id in self._jobs:
                logger.warning(f"Job {job_id} already exists, replacing")
                del self._jobs[job_id]

            job = JobEntry(
                job_id=job_id,
                document_id=document_id,
                version_no=version_no,
                job_type=job_type,
            )
            job.add_log("create", f"Job created: type={job_type}")
            self._jobs[job_id] = job

            logger.info(
                f"Job created: job_id={job_id}, document_id={document_id}, "
                f"version_no={version_no}, type={job_type}"
            )

            return job

    async def get_job(self, job_id: str) -> Optional[JobEntry]:
        """
        작업 엔트리를 반환합니다.

        Args:
            job_id: 작업 ID

        Returns:
            Optional[JobEntry]: 작업 엔트리 또는 None
        """
        return self._jobs.get(job_id)

    async def get_job_status(self, job_id: str) -> Optional[JobStatusResponse]:
        """
        작업 상태를 반환합니다.

        Args:
            job_id: 작업 ID

        Returns:
            Optional[JobStatusResponse]: 작업 상태 응답 또는 None
        """
        job = self._jobs.get(job_id)
        if job:
            return job.to_response()
        return None

    async def update_job(
        self,
        job_id: str,
        status: Optional[JobStatus] = None,
        progress: Optional[str] = None,
        error_message: Optional[str] = None,
        chunks_processed: Optional[int] = None,
        log_message: Optional[str] = None,
    ) -> Optional[JobEntry]:
        """
        작업 상태를 업데이트합니다.

        Args:
            job_id: 작업 ID
            status: 새 상태
            progress: 진행 단계
            error_message: 에러 메시지
            chunks_processed: 처리된 청크 수
            log_message: 로그 메시지

        Returns:
            Optional[JobEntry]: 업데이트된 작업 엔트리 또는 None
        """
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                logger.warning(f"Job not found for update: {job_id}")
                return None

            if status:
                job.update_status(status, progress, error_message)

            if progress and not status:
                job.progress = progress
                job.updated_at = datetime.now(timezone.utc)

            if chunks_processed is not None:
                job.chunks_processed = chunks_processed

            if log_message:
                level = "ERROR" if status == JobStatus.FAILED else "INFO"
                job.add_log(progress or "update", log_message, level)

            logger.debug(
                f"Job updated: job_id={job_id}, status={job.status.value}, "
                f"progress={job.progress}"
            )

            return job

    async def mark_running(
        self, job_id: str, progress: str = "starting"
    ) -> Optional[JobEntry]:
        """작업을 실행 중으로 표시합니다."""
        return await self.update_job(
            job_id,
            status=JobStatus.RUNNING,
            progress=progress,
            log_message=f"Job started: {progress}",
        )

    async def mark_completed(
        self, job_id: str, chunks_processed: int = 0
    ) -> Optional[JobEntry]:
        """작업을 완료로 표시합니다."""
        return await self.update_job(
            job_id,
            status=JobStatus.COMPLETED,
            progress="completed",
            chunks_processed=chunks_processed,
            log_message=f"Job completed: {chunks_processed} chunks processed",
        )

    async def mark_failed(
        self, job_id: str, error_message: str, progress: Optional[str] = None
    ) -> Optional[JobEntry]:
        """작업을 실패로 표시합니다."""
        return await self.update_job(
            job_id,
            status=JobStatus.FAILED,
            progress=progress or "failed",
            error_message=error_message,
            log_message=f"Job failed: {error_message}",
        )

    async def cleanup_old_jobs(self) -> int:
        """
        오래된 완료/실패 작업을 정리합니다.

        Returns:
            int: 정리된 작업 수
        """
        async with self._lock:
            now = datetime.now(timezone.utc)
            to_delete = []

            for job_id, job in self._jobs.items():
                if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                    age_seconds = (now - job.updated_at).total_seconds()
                    if age_seconds > self.JOB_RETENTION_SECONDS:
                        to_delete.append(job_id)

            for job_id in to_delete:
                del self._jobs[job_id]

            if to_delete:
                logger.info(f"Cleaned up {len(to_delete)} old jobs")

            return len(to_delete)

    async def get_all_jobs(
        self, status_filter: Optional[JobStatus] = None
    ) -> List[JobStatusResponse]:
        """
        모든 작업 상태를 반환합니다.

        Args:
            status_filter: 특정 상태만 필터링

        Returns:
            List[JobStatusResponse]: 작업 상태 목록
        """
        jobs = []
        for job in self._jobs.values():
            if status_filter is None or job.status == status_filter:
                jobs.append(job.to_response())
        return jobs


# =============================================================================
# 싱글턴 인스턴스
# =============================================================================

_job_service: Optional[JobService] = None


def get_job_service() -> JobService:
    """JobService 싱글턴 인스턴스를 반환합니다."""
    global _job_service
    if _job_service is None:
        _job_service = JobService()
    return _job_service


def clear_job_service() -> None:
    """JobService 싱글턴 인스턴스를 제거합니다 (테스트용)."""
    global _job_service
    _job_service = None
