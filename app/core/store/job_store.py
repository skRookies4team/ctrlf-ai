"""
교육 영상 파이프라인 Job 상태 저장소

Redis 우선, File fallback 지원.
서버 재시작 후에도 Job 상태를 유지합니다.

상태 모델:
- sourceSetStatus: 전처리/스크립트 생성 상태
- scriptStatus: 스크립트 생성 상태
- videoJobStatus: 영상 생성 상태
"""

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class SourceSetStatus(str, Enum):
    """소스셋 처리 상태."""
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"  # RAGFLOW 전처리 중
    PREPROCESSING_COMPLETED = "PREPROCESSING_COMPLETED"  # 전처리 완료
    SCRIPT_GENERATING = "SCRIPT_GENERATING"  # 스크립트 생성 중
    SCRIPT_READY = "SCRIPT_READY"  # 스크립트 생성 완료
    FAILED = "FAILED"


class ScriptStatus(str, Enum):
    """스크립트 생성 상태."""
    PENDING = "PENDING"
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class VideoJobStatus(str, Enum):
    """영상 생성 Job 상태."""
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class PipelineJob:
    """교육 영상 파이프라인 Job 상태."""
    # 식별자
    source_set_id: str
    video_id: str
    education_id: str
    
    # 상태
    source_set_status: SourceSetStatus = SourceSetStatus.PENDING
    script_status: ScriptStatus = ScriptStatus.PENDING
    video_job_status: Optional[VideoJobStatus] = None
    
    # 진행률 (0-100)
    progress: int = 0
    
    # 결과
    script_backend: Optional[Dict[str, Any]] = None  # 백엔드 저장용 스크립트
    script_heygen: Optional[Dict[str, Any]] = None  # Heygen용 스크립트
    script_s3_key: Optional[str] = None  # 스크립트 저장 S3 key
    video_s3_key: Optional[str] = None  # 영상 저장 S3 key
    video_url: Optional[str] = None  # 영상 재생 URL
    
    # 에러
    fail_reason: Optional[str] = None
    error_code: Optional[str] = None
    
    # 메타데이터
    heygen_video_id: Optional[str] = None
    heygen_job_id: Optional[str] = None
    request_id: Optional[str] = None
    trace_id: Optional[str] = None
    
    # 타임스탬프
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        """Dict로 변환 (JSON 직렬화용)."""
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat() + "Z"
        data["updated_at"] = self.updated_at.isoformat() + "Z"
        data["source_set_status"] = self.source_set_status.value
        data["script_status"] = self.script_status.value
        if self.video_job_status:
            data["video_job_status"] = self.video_job_status.value
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PipelineJob":
        """Dict에서 생성 (JSON 역직렬화용)."""
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
        if isinstance(data.get("updated_at"), str):
            data["updated_at"] = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
        if isinstance(data.get("source_set_status"), str):
            data["source_set_status"] = SourceSetStatus(data["source_set_status"])
        if isinstance(data.get("script_status"), str):
            data["script_status"] = ScriptStatus(data["script_status"])
        if data.get("video_job_status") and isinstance(data["video_job_status"], str):
            data["video_job_status"] = VideoJobStatus(data["video_job_status"])
        return cls(**data)


class BaseJobStore(ABC):
    """Job 저장소 추상 클래스."""
    
    @abstractmethod
    async def get(self, source_set_id: str) -> Optional[PipelineJob]:
        """Job 조회."""
        pass
    
    @abstractmethod
    async def save(self, job: PipelineJob) -> None:
        """Job 저장."""
        pass
    
    @abstractmethod
    async def delete(self, source_set_id: str) -> None:
        """Job 삭제."""
        pass
    
    @abstractmethod
    async def list_by_video_id(self, video_id: str) -> list[PipelineJob]:
        """video_id로 Job 목록 조회."""
        pass


class FileJobStore(BaseJobStore):
    """파일 기반 Job 저장소."""
    
    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir:
            self._data_dir = Path(data_dir)
        else:
            settings = get_settings()
            base_dir = (
                Path(settings.RENDER_OUTPUT_DIR)
                if hasattr(settings, "RENDER_OUTPUT_DIR") and settings.RENDER_OUTPUT_DIR
                else Path("./data")
            )
            self._data_dir = base_dir / "pipeline_jobs"
        
        self._data_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"FileJobStore initialized: data_dir={self._data_dir}")
    
    def _get_job_path(self, source_set_id: str) -> Path:
        return self._data_dir / f"{source_set_id}.json"
    
    async def get(self, source_set_id: str) -> Optional[PipelineJob]:
        job_path = self._get_job_path(source_set_id)
        if not job_path.exists():
            return None
        
        try:
            with open(job_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return PipelineJob.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to load PipelineJob: source_set_id={source_set_id}, error={e}", exc_info=True)
            return None
    
    async def save(self, job: PipelineJob) -> None:
        job_path = self._get_job_path(job.source_set_id)
        job.updated_at = datetime.utcnow()
        try:
            data = job.to_dict()
            with open(job_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug(f"PipelineJob saved: source_set_id={job.source_set_id}, status={job.source_set_status.value}")
        except Exception as e:
            logger.error(f"Failed to save PipelineJob: source_set_id={job.source_set_id}, error={e}", exc_info=True)
            raise
    
    async def delete(self, source_set_id: str) -> None:
        job_path = self._get_job_path(source_set_id)
        if job_path.exists():
            try:
                job_path.unlink()
                logger.debug(f"PipelineJob deleted: source_set_id={source_set_id}")
            except Exception as e:
                logger.error(f"Failed to delete PipelineJob: source_set_id={source_set_id}, error={e}", exc_info=True)
    
    async def list_by_video_id(self, video_id: str) -> list[PipelineJob]:
        jobs = []
        if not self._data_dir.exists():
            return jobs
        
        for job_file in self._data_dir.glob("*.json"):
            try:
                job = await self.get(job_file.stem)
                if job and job.video_id == video_id:
                    jobs.append(job)
            except Exception as e:
                logger.warning(f"Failed to load job from {job_file}: {e}")
        
        return jobs


class RedisJobStore(BaseJobStore):
    """Redis 기반 Job 저장소."""
    
    def __init__(self, redis_url: str, key_prefix: str = "pipeline_job"):
        self.redis_url = redis_url
        self.key_prefix = key_prefix
        self._redis = None
        self._connected = False
    
    async def _get_redis(self):
        """Redis 클라이언트 획득 (lazy initialization)."""
        if self._redis is not None:
            return self._redis
        
        try:
            import redis.asyncio as redis
            self._redis = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            self._connected = True
            return self._redis
        except ImportError:
            raise RuntimeError("redis package is required. Install with: pip install redis")
    
    def _make_key(self, source_set_id: str) -> str:
        return f"{self.key_prefix}:{source_set_id}"
    
    async def get(self, source_set_id: str) -> Optional[PipelineJob]:
        try:
            redis = await self._get_redis()
            key = self._make_key(source_set_id)
            data = await redis.get(key)
            if data is None:
                return None
            return PipelineJob.from_dict(json.loads(data))
        except Exception as e:
            logger.error(f"Redis get error: source_set_id={source_set_id}, error={e}", exc_info=True)
            return None
    
    async def save(self, job: PipelineJob) -> None:
        try:
            redis = await self._get_redis()
            key = self._make_key(job.source_set_id)
            job.updated_at = datetime.utcnow()
            data = json.dumps(job.to_dict(), ensure_ascii=False)
            # TTL: 7일 (604800초)
            await redis.set(key, data, ex=604800)
            logger.debug(f"PipelineJob saved to Redis: source_set_id={job.source_set_id}")
        except Exception as e:
            logger.error(f"Redis save error: source_set_id={job.source_set_id}, error={e}", exc_info=True)
            raise
    
    async def delete(self, source_set_id: str) -> None:
        try:
            redis = await self._get_redis()
            key = self._make_key(source_set_id)
            await redis.delete(key)
            logger.debug(f"PipelineJob deleted from Redis: source_set_id={source_set_id}")
        except Exception as e:
            logger.error(f"Redis delete error: source_set_id={source_set_id}, error={e}", exc_info=True)
    
    async def list_by_video_id(self, video_id: str) -> list[PipelineJob]:
        jobs = []
        try:
            redis = await self._get_redis()
            pattern = f"{self.key_prefix}:*"
            keys = await redis.keys(pattern)
            for key in keys:
                try:
                    data = await redis.get(key)
                    if data:
                        job = PipelineJob.from_dict(json.loads(data))
                        if job.video_id == video_id:
                            jobs.append(job)
                except Exception as e:
                    logger.warning(f"Failed to load job from Redis key {key}: {e}")
        except Exception as e:
            logger.error(f"Redis list_by_video_id error: video_id={video_id}, error={e}", exc_info=True)
        return jobs
    
    async def close(self) -> None:
        """Redis 연결 종료."""
        if self._redis:
            await self._redis.close()
            self._redis = None
            self._connected = False


# 싱글톤 인스턴스
_job_store: Optional[BaseJobStore] = None


def get_job_store() -> BaseJobStore:
    """Job 저장소 싱글톤 인스턴스 반환."""
    global _job_store
    
    if _job_store is not None:
        return _job_store
    
    settings = get_settings()
    redis_url = getattr(settings, "REDIS_URL", None)
    
    if redis_url:
        try:
            _job_store = RedisJobStore(redis_url=redis_url)
            logger.info(f"Using Redis job store: {redis_url}")
        except Exception as e:
            logger.warning(f"Failed to initialize Redis job store: {e}, falling back to file store")
            _job_store = FileJobStore()
    else:
        _job_store = FileJobStore()
        logger.info("Using file-based job store")
    
    return _job_store


def clear_job_store() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _job_store
    _job_store = None

