"""
Video Job 상태 저장소

Video Job 상태를 파일 기반으로 영속화합니다.
서버 재시작 후에도 Job 상태를 유지할 수 있습니다.

구조:
- 파일 기반: `{data_dir}/video_jobs/{job_id}.json`
- Redis 기반: 향후 지원 가능 (선택사항)
"""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class VideoJobStatus(str, Enum):
    """영상 생성 Job 상태."""
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class VideoJob:
    """영상 생성 Job 정보."""
    job_id: str
    video_id: str
    script_id: str
    education_id: str
    status: VideoJobStatus = VideoJobStatus.PENDING
    heygen_video_id: Optional[str] = None
    video_url: Optional[str] = None
    s3_key: Optional[str] = None
    duration_sec: Optional[int] = None
    fail_reason: Optional[str] = None
    retry_count: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict:
        """Dict로 변환 (JSON 직렬화용)."""
        data = asdict(self)
        # datetime을 ISO 문자열로 변환
        data["created_at"] = self.created_at.isoformat() + "Z"
        data["updated_at"] = self.updated_at.isoformat() + "Z"
        # Enum을 문자열로 변환
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: Dict) -> "VideoJob":
        """Dict에서 생성 (JSON 역직렬화용)."""
        # datetime 문자열을 datetime 객체로 변환
        def parse_datetime(dt_str: str) -> datetime:
            """datetime 문자열 파싱 (Z 또는 timezone 정보 처리)."""
            if not dt_str:
                return datetime.utcnow()
            
            # Z로 끝나면 UTC로 변환
            if dt_str.endswith("Z"):
                dt_str = dt_str[:-1] + "+00:00"
            # 중복된 +00:00 제거 (버그로 인한 중복 방지)
            while "+00:00+00:00" in dt_str:
                dt_str = dt_str.replace("+00:00+00:00", "+00:00")
            
            try:
                return datetime.fromisoformat(dt_str)
            except ValueError:
                # fallback: Z만 제거하고 재시도
                dt_str_clean = dt_str.replace("Z", "")
                if "+00:00" not in dt_str_clean and dt_str_clean.count("+") == 0:
                    dt_str_clean += "+00:00"
                return datetime.fromisoformat(dt_str_clean)
        
        if isinstance(data.get("created_at"), str):
            data["created_at"] = parse_datetime(data["created_at"])
        
        if isinstance(data.get("updated_at"), str):
            data["updated_at"] = parse_datetime(data["updated_at"])
        
        # 문자열을 Enum으로 변환
        if isinstance(data.get("status"), str):
            data["status"] = VideoJobStatus(data["status"])
        return cls(**data)


class VideoJobStore:
    """
    Video Job 상태 저장소 (파일 기반).

    서버 재시작 후에도 Job 상태를 유지합니다.

    Usage:
        store = VideoJobStore()
        
        # Job 저장
        job = VideoJob(...)
        store.save(job)
        
        # Job 조회
        job = store.get("job-123")
        
        # Job 삭제
        store.delete("job-123")
    """

    def __init__(self, data_dir: Optional[Path] = None):
        """
        저장소 초기화.

        Args:
            data_dir: 데이터 디렉토리 (None이면 설정에서 가져옴)
        """
        if data_dir:
            self._data_dir = Path(data_dir)
        else:
            settings = get_settings()
            # 기본 디렉토리: RENDER_OUTPUT_DIR/video_jobs
            base_dir = Path(settings.RENDER_OUTPUT_DIR) if hasattr(settings, 'RENDER_OUTPUT_DIR') else Path("./data")
            self._data_dir = base_dir / "video_jobs"

        # 디렉토리 생성
        self._data_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"VideoJobStore initialized: data_dir={self._data_dir}")

    def _get_job_path(self, job_id: str) -> Path:
        """Job 파일 경로 반환."""
        return self._data_dir / f"{job_id}.json"

    def save(self, job: VideoJob) -> None:
        """
        Job을 저장합니다.

        Args:
            job: 저장할 Job
        """
        job_path = self._get_job_path(job.job_id)
        
        try:
            # Dict로 변환 후 JSON 저장
            data = job.to_dict()
            with open(job_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            logger.debug(f"VideoJob saved: job_id={job.job_id}, status={job.status.value}, path={job_path}")
        
        except Exception as e:
            logger.error(f"Failed to save VideoJob: job_id={job.job_id}, error={e}", exc_info=True)
            raise

    def get(self, job_id: str) -> Optional[VideoJob]:
        """
        Job을 조회합니다.

        Args:
            job_id: Job ID

        Returns:
            VideoJob 또는 None
        """
        job_path = self._get_job_path(job_id)
        
        if not job_path.exists():
            return None

        try:
            with open(job_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            job = VideoJob.from_dict(data)
            logger.debug(f"VideoJob loaded: job_id={job_id}, status={job.status.value}")
            return job

        except Exception as e:
            logger.error(f"Failed to load VideoJob: job_id={job_id}, error={e}", exc_info=True)
            return None

    def delete(self, job_id: str) -> None:
        """
        Job을 삭제합니다.

        Args:
            job_id: Job ID
        """
        job_path = self._get_job_path(job_id)
        
        if job_path.exists():
            try:
                job_path.unlink()
                logger.debug(f"VideoJob deleted: job_id={job_id}")
            except Exception as e:
                logger.error(f"Failed to delete VideoJob: job_id={job_id}, error={e}", exc_info=True)

    def list_all(self) -> list[VideoJob]:
        """
        모든 Job을 조회합니다.

        Returns:
            VideoJob 목록
        """
        jobs = []
        
        if not self._data_dir.exists():
            return jobs

        for job_file in self._data_dir.glob("*.json"):
            try:
                job_id = job_file.stem
                job = self.get(job_id)
                if job:
                    jobs.append(job)
            except Exception as e:
                logger.warning(f"Failed to load job from {job_file}: {e}")
        
        return jobs

    def list_by_status(self, status: VideoJobStatus) -> list[VideoJob]:
        """
        특정 상태의 Job 목록을 조회합니다.

        Args:
            status: Job 상태

        Returns:
            VideoJob 목록
        """
        all_jobs = self.list_all()
        return [job for job in all_jobs if job.status == status]


# 싱글톤 인스턴스
_store: Optional[VideoJobStore] = None


def get_video_job_store() -> VideoJobStore:
    """Video Job 저장소 싱글톤 인스턴스 반환."""
    global _store
    if _store is None:
        _store = VideoJobStore()
    return _store


def clear_video_job_store() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _store
    _store = None
