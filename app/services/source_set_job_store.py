"""
SourceSet Job 상태 저장소

SourceSet 처리 Job 상태를 파일 기반으로 영속화합니다.
서버 재시작 후에도 Job 상태를 유지할 수 있습니다.

구조:
- 파일 기반: `{data_dir}/source_set_jobs/{source_set_id}.json`
- VideoJobStore와 동일한 패턴 사용
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.source_set import (
    DocumentResult,
    GeneratedScript,
    SourceSetDocument,
)

logger = get_logger(__name__)


class ProcessingStatus(str, Enum):
    """내부 처리 상태."""
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class ProcessingJob:
    """소스셋 처리 작업 상태."""
    source_set_id: str
    video_id: str
    education_id: Optional[str]
    request_id: Optional[str]
    trace_id: Optional[str]
    script_policy_id: Optional[str]
    llm_model_hint: Optional[str]
    status: ProcessingStatus = ProcessingStatus.PENDING
    documents: List[SourceSetDocument] = field(default_factory=list)
    document_results: List[DocumentResult] = field(default_factory=list)
    generated_script: Optional[GeneratedScript] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
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
        # Pydantic 모델들을 dict로 변환
        if self.documents:
            data["documents"] = [doc.model_dump() if hasattr(doc, "model_dump") else doc for doc in self.documents]
        if self.document_results:
            data["document_results"] = [
                result.model_dump() if hasattr(result, "model_dump") else result
                for result in self.document_results
            ]
        if self.generated_script:
            data["generated_script"] = (
                self.generated_script.model_dump()
                if hasattr(self.generated_script, "model_dump")
                else self.generated_script
            )
        return data

    @classmethod
    def from_dict(cls, data: Dict) -> "ProcessingJob":
        """Dict에서 생성 (JSON 역직렬화용)."""
        # datetime 문자열을 datetime 객체로 변환
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
        if isinstance(data.get("updated_at"), str):
            data["updated_at"] = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
        # 문자열을 Enum으로 변환
        if isinstance(data.get("status"), str):
            data["status"] = ProcessingStatus(data["status"])
        # Pydantic 모델들 복원
        if "documents" in data and data["documents"]:
            data["documents"] = [
                SourceSetDocument(**doc) if isinstance(doc, dict) else doc
                for doc in data["documents"]
            ]
        if "document_results" in data and data["document_results"]:
            data["document_results"] = [
                DocumentResult(**result) if isinstance(result, dict) else result
                for result in data["document_results"]
            ]
        if "generated_script" in data and data["generated_script"]:
            if isinstance(data["generated_script"], dict):
                data["generated_script"] = GeneratedScript(**data["generated_script"])
        return cls(**data)


class SourceSetJobStore:
    """
    SourceSet Job 상태 저장소 (파일 기반).

    서버 재시작 후에도 Job 상태를 유지합니다.

    Usage:
        store = SourceSetJobStore()
        
        # Job 저장
        job = ProcessingJob(...)
        store.save(job)
        
        # Job 조회
        job = store.get("source-set-123")
        
        # Job 삭제
        store.delete("source-set-123")
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
            # 기본 디렉토리: RENDER_OUTPUT_DIR/source_set_jobs 또는 ./data/source_set_jobs
            base_dir = (
                Path(settings.RENDER_OUTPUT_DIR)
                if hasattr(settings, "RENDER_OUTPUT_DIR") and settings.RENDER_OUTPUT_DIR
                else Path("./data")
            )
            self._data_dir = base_dir / "source_set_jobs"

        # 디렉토리 생성
        self._data_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"SourceSetJobStore initialized: data_dir={self._data_dir}")

    def _get_job_path(self, source_set_id: str) -> Path:
        """Job 파일 경로 반환."""
        return self._data_dir / f"{source_set_id}.json"

    def save(self, job: ProcessingJob) -> None:
        """
        Job을 저장합니다.

        Args:
            job: 저장할 Job
        """
        job_path = self._get_job_path(job.source_set_id)
        
        try:
            # Dict로 변환 후 JSON 저장
            data = job.to_dict()
            with open(job_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            logger.debug(
                f"SourceSetJob saved: source_set_id={job.source_set_id}, "
                f"status={job.status.value}, path={job_path}"
            )
        
        except Exception as e:
            logger.error(
                f"Failed to save SourceSetJob: source_set_id={job.source_set_id}, error={e}",
                exc_info=True
            )
            raise

    def get(self, source_set_id: str) -> Optional[ProcessingJob]:
        """
        Job을 조회합니다.

        Args:
            source_set_id: 소스셋 ID

        Returns:
            ProcessingJob 또는 None
        """
        job_path = self._get_job_path(source_set_id)
        
        if not job_path.exists():
            return None

        try:
            with open(job_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            job = ProcessingJob.from_dict(data)
            logger.debug(
                f"SourceSetJob loaded: source_set_id={source_set_id}, status={job.status.value}"
            )
            return job

        except Exception as e:
            logger.error(
                f"Failed to load SourceSetJob: source_set_id={source_set_id}, error={e}",
                exc_info=True
            )
            return None

    def delete(self, source_set_id: str) -> None:
        """
        Job을 삭제합니다.

        Args:
            source_set_id: 소스셋 ID
        """
        job_path = self._get_job_path(source_set_id)
        
        if job_path.exists():
            try:
                job_path.unlink()
                logger.debug(f"SourceSetJob deleted: source_set_id={source_set_id}")
            except Exception as e:
                logger.error(
                    f"Failed to delete SourceSetJob: source_set_id={source_set_id}, error={e}",
                    exc_info=True
                )

    def list_all(self) -> List[ProcessingJob]:
        """
        모든 Job을 조회합니다.

        Returns:
            ProcessingJob 목록
        """
        jobs = []
        
        if not self._data_dir.exists():
            return jobs

        for job_file in self._data_dir.glob("*.json"):
            try:
                source_set_id = job_file.stem
                job = self.get(source_set_id)
                if job:
                    jobs.append(job)
            except Exception as e:
                logger.warning(f"Failed to load job from {job_file}: {e}")
        
        return jobs

    def list_by_status(self, status: ProcessingStatus) -> List[ProcessingJob]:
        """
        특정 상태의 Job 목록을 조회합니다.

        Args:
            status: Job 상태

        Returns:
            ProcessingJob 목록
        """
        all_jobs = self.list_all()
        return [job for job in all_jobs if job.status == status]


# 싱글톤 인스턴스
_store: Optional[SourceSetJobStore] = None


def get_source_set_job_store() -> SourceSetJobStore:
    """SourceSet Job 저장소 싱글톤 인스턴스 반환."""
    global _store
    if _store is None:
        _store = SourceSetJobStore()
    return _store


def clear_source_set_job_store() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _store
    _store = None

