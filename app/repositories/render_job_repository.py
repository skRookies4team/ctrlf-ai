"""
Phase 33: Render Job Repository

렌더 잡의 영속성을 위한 저장소 구현.

SQLite 기반으로 서버 재시작 후에도 잡 상태를 유지합니다.

환경변수:
- RENDER_JOB_DB_PATH: DB 파일 경로 (기본: ./data/render_jobs.db)
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger
from app.models.video_render import RenderJobStatus, RenderStep

logger = get_logger(__name__)


# =============================================================================
# Data Classes
# =============================================================================


class RenderJobEntity:
    """렌더 잡 엔티티 (DB 모델).

    Attributes:
        job_id: 잡 고유 ID (UUID)
        video_id: 비디오 ID (FK)
        script_id: 스크립트 ID (FK or string)
        status: 잡 상태 (PENDING | RUNNING | SUCCEEDED | FAILED | CANCELED)
        step: 현재 진행 단계
        progress: 진행률 (0-100)
        message: 진행 메시지
        error_code: 에러 코드 (nullable)
        error_message: 에러 메시지 (nullable)
        assets: 에셋 JSON (video_url, subtitle_url, thumbnail_url)
        created_by: 생성자 ID
        created_at: 생성 시각
        updated_at: 수정 시각
        started_at: 시작 시각
        finished_at: 종료 시각
    """

    def __init__(
        self,
        job_id: str,
        video_id: str,
        script_id: str,
        status: str = "PENDING",
        step: Optional[str] = None,
        progress: int = 0,
        message: str = "",
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        assets: Optional[Dict[str, Any]] = None,
        created_by: str = "",
        created_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
    ):
        self.job_id = job_id
        self.video_id = video_id
        self.script_id = script_id
        self.status = status
        self.step = step
        self.progress = progress
        self.message = message
        self.error_code = error_code
        self.error_message = error_message
        self.assets = assets or {}
        self.created_by = created_by
        self.created_at = created_at or datetime.utcnow()
        self.updated_at = updated_at or datetime.utcnow()
        self.started_at = started_at
        self.finished_at = finished_at

    def is_active(self) -> bool:
        """활성 상태(PENDING/RUNNING)인지 확인."""
        return self.status in ("PENDING", "RUNNING")

    def is_terminal(self) -> bool:
        """종료 상태인지 확인."""
        return self.status in ("SUCCEEDED", "FAILED", "CANCELED")

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "job_id": self.job_id,
            "video_id": self.video_id,
            "script_id": self.script_id,
            "status": self.status,
            "step": self.step,
            "progress": self.progress,
            "message": self.message,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "assets": self.assets,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }

    @classmethod
    def from_row(cls, row: tuple) -> "RenderJobEntity":
        """DB 행에서 엔티티 생성."""
        (
            job_id, video_id, script_id, status, step,
            progress, message, error_code, error_message, assets_json,
            created_by, created_at, updated_at, started_at, finished_at
        ) = row

        return cls(
            job_id=job_id,
            video_id=video_id,
            script_id=script_id,
            status=status,
            step=step,
            progress=progress,
            message=message or "",
            error_code=error_code,
            error_message=error_message,
            assets=json.loads(assets_json) if assets_json else {},
            created_by=created_by or "",
            created_at=datetime.fromisoformat(created_at) if created_at else None,
            updated_at=datetime.fromisoformat(updated_at) if updated_at else None,
            started_at=datetime.fromisoformat(started_at) if started_at else None,
            finished_at=datetime.fromisoformat(finished_at) if finished_at else None,
        )


# =============================================================================
# Repository
# =============================================================================


class RenderJobRepository:
    """렌더 잡 저장소 (SQLite).

    서버 재시작 후에도 잡 상태를 유지합니다.
    Thread-safe하게 구현되어 있습니다.

    Usage:
        repo = RenderJobRepository()

        # 잡 저장
        repo.save(job)

        # 잡 조회
        job = repo.get("job-xxx")

        # 활성 잡 조회
        active = repo.get_active_by_video_id("video-001")
    """

    def __init__(self, db_path: Optional[str] = None):
        """저장소 초기화.

        Args:
            db_path: DB 파일 경로 (None이면 환경변수 또는 기본값 사용)
        """
        self._db_path = db_path or os.getenv(
            "RENDER_JOB_DB_PATH",
            "./data/render_jobs.db"
        )
        self._local = threading.local()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Thread-local DB 연결 반환."""
        if not hasattr(self._local, "connection"):
            self._local.connection = sqlite3.connect(self._db_path)
            self._local.connection.row_factory = sqlite3.Row
        return self._local.connection

    @contextmanager
    def _get_cursor(self):
        """커서 컨텍스트 매니저."""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

    def _init_db(self) -> None:
        """DB 초기화 (테이블 생성)."""
        # 디렉토리 생성
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        create_table_sql = """
        CREATE TABLE IF NOT EXISTS render_jobs (
            job_id TEXT PRIMARY KEY,
            video_id TEXT NOT NULL,
            script_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            step TEXT,
            progress INTEGER DEFAULT 0,
            message TEXT,
            error_code TEXT,
            error_message TEXT,
            assets TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        )
        """

        create_index_sql = """
        CREATE INDEX IF NOT EXISTS idx_render_jobs_video_id
        ON render_jobs(video_id)
        """

        create_status_index_sql = """
        CREATE INDEX IF NOT EXISTS idx_render_jobs_status
        ON render_jobs(status)
        """

        with self._get_cursor() as cursor:
            cursor.execute(create_table_sql)
            cursor.execute(create_index_sql)
            cursor.execute(create_status_index_sql)

        logger.info(f"RenderJobRepository initialized: {self._db_path}")

    def save(self, job: RenderJobEntity) -> None:
        """잡 저장 (upsert)."""
        job.updated_at = datetime.utcnow()

        sql = """
        INSERT OR REPLACE INTO render_jobs (
            job_id, video_id, script_id, status, step,
            progress, message, error_code, error_message, assets,
            created_by, created_at, updated_at, started_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        with self._get_cursor() as cursor:
            cursor.execute(sql, (
                job.job_id,
                job.video_id,
                job.script_id,
                job.status,
                job.step,
                job.progress,
                job.message,
                job.error_code,
                job.error_message,
                json.dumps(job.assets) if job.assets else None,
                job.created_by,
                job.created_at.isoformat() if job.created_at else None,
                job.updated_at.isoformat() if job.updated_at else None,
                job.started_at.isoformat() if job.started_at else None,
                job.finished_at.isoformat() if job.finished_at else None,
            ))

        logger.debug(f"RenderJob saved: job_id={job.job_id}, status={job.status}")

    def get(self, job_id: str) -> Optional[RenderJobEntity]:
        """잡 조회."""
        sql = """
        SELECT job_id, video_id, script_id, status, step,
               progress, message, error_code, error_message, assets,
               created_by, created_at, updated_at, started_at, finished_at
        FROM render_jobs
        WHERE job_id = ?
        """

        with self._get_cursor() as cursor:
            cursor.execute(sql, (job_id,))
            row = cursor.fetchone()
            if row:
                return RenderJobEntity.from_row(tuple(row))
        return None

    def get_by_video_id(
        self,
        video_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> List[RenderJobEntity]:
        """비디오 ID로 잡 목록 조회 (최신순)."""
        sql = """
        SELECT job_id, video_id, script_id, status, step,
               progress, message, error_code, error_message, assets,
               created_by, created_at, updated_at, started_at, finished_at
        FROM render_jobs
        WHERE video_id = ?
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """

        jobs = []
        with self._get_cursor() as cursor:
            cursor.execute(sql, (video_id, limit, offset))
            for row in cursor.fetchall():
                jobs.append(RenderJobEntity.from_row(tuple(row)))
        return jobs

    def get_active_by_video_id(self, video_id: str) -> Optional[RenderJobEntity]:
        """비디오 ID로 활성 잡(PENDING/RUNNING) 조회."""
        sql = """
        SELECT job_id, video_id, script_id, status, step,
               progress, message, error_code, error_message, assets,
               created_by, created_at, updated_at, started_at, finished_at
        FROM render_jobs
        WHERE video_id = ? AND status IN ('PENDING', 'RUNNING')
        ORDER BY created_at DESC
        LIMIT 1
        """

        with self._get_cursor() as cursor:
            cursor.execute(sql, (video_id,))
            row = cursor.fetchone()
            if row:
                return RenderJobEntity.from_row(tuple(row))
        return None

    def get_succeeded_by_video_id(self, video_id: str) -> Optional[RenderJobEntity]:
        """비디오 ID로 최신 성공 잡 조회."""
        sql = """
        SELECT job_id, video_id, script_id, status, step,
               progress, message, error_code, error_message, assets,
               created_by, created_at, updated_at, started_at, finished_at
        FROM render_jobs
        WHERE video_id = ? AND status = 'SUCCEEDED'
        ORDER BY finished_at DESC
        LIMIT 1
        """

        with self._get_cursor() as cursor:
            cursor.execute(sql, (video_id,))
            row = cursor.fetchone()
            if row:
                return RenderJobEntity.from_row(tuple(row))
        return None

    def get_latest_published_by_video_id(self, video_id: str) -> Optional[RenderJobEntity]:
        """비디오 ID로 최신 발행 잡 조회 (assets 있음)."""
        sql = """
        SELECT job_id, video_id, script_id, status, step,
               progress, message, error_code, error_message, assets,
               created_by, created_at, updated_at, started_at, finished_at
        FROM render_jobs
        WHERE video_id = ? AND status = 'SUCCEEDED' AND assets IS NOT NULL
        ORDER BY finished_at DESC
        LIMIT 1
        """

        with self._get_cursor() as cursor:
            cursor.execute(sql, (video_id,))
            row = cursor.fetchone()
            if row:
                job = RenderJobEntity.from_row(tuple(row))
                # assets에 video_url이 있는지 확인
                if job.assets and job.assets.get("video_url"):
                    return job
        return None

    def update_status(
        self,
        job_id: str,
        status: str,
        step: Optional[str] = None,
        progress: Optional[int] = None,
        message: Optional[str] = None,
    ) -> bool:
        """잡 상태 업데이트 (부분 업데이트)."""
        updates = ["status = ?", "updated_at = ?"]
        params = [status, datetime.utcnow().isoformat()]

        if step is not None:
            updates.append("step = ?")
            params.append(step)
        if progress is not None:
            updates.append("progress = ?")
            params.append(progress)
        if message is not None:
            updates.append("message = ?")
            params.append(message)

        # RUNNING 시작 시 started_at 설정
        if status == "RUNNING":
            updates.append("started_at = COALESCE(started_at, ?)")
            params.append(datetime.utcnow().isoformat())

        # 종료 상태 시 finished_at 설정
        if status in ("SUCCEEDED", "FAILED", "CANCELED"):
            updates.append("finished_at = ?")
            params.append(datetime.utcnow().isoformat())

        sql = f"UPDATE render_jobs SET {', '.join(updates)} WHERE job_id = ?"
        params.append(job_id)

        with self._get_cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.rowcount > 0

    def update_assets(
        self,
        job_id: str,
        assets: Dict[str, Any],
    ) -> bool:
        """잡 에셋 업데이트."""
        sql = """
        UPDATE render_jobs
        SET assets = ?, updated_at = ?
        WHERE job_id = ?
        """

        with self._get_cursor() as cursor:
            cursor.execute(sql, (
                json.dumps(assets),
                datetime.utcnow().isoformat(),
                job_id,
            ))
            return cursor.rowcount > 0

    def update_error(
        self,
        job_id: str,
        error_code: str,
        error_message: str,
    ) -> bool:
        """잡 에러 정보 업데이트."""
        sql = """
        UPDATE render_jobs
        SET error_code = ?, error_message = ?, status = 'FAILED',
            updated_at = ?, finished_at = ?
        WHERE job_id = ?
        """

        now = datetime.utcnow().isoformat()
        with self._get_cursor() as cursor:
            cursor.execute(sql, (error_code, error_message, now, now, job_id))
            return cursor.rowcount > 0

    def delete(self, job_id: str) -> bool:
        """잡 삭제."""
        sql = "DELETE FROM render_jobs WHERE job_id = ?"

        with self._get_cursor() as cursor:
            cursor.execute(sql, (job_id,))
            return cursor.rowcount > 0

    def count_by_video_id(self, video_id: str) -> int:
        """비디오 ID로 잡 수 조회."""
        sql = "SELECT COUNT(*) FROM render_jobs WHERE video_id = ?"

        with self._get_cursor() as cursor:
            cursor.execute(sql, (video_id,))
            return cursor.fetchone()[0]

    def close(self) -> None:
        """DB 연결 종료."""
        if hasattr(self._local, "connection"):
            self._local.connection.close()
            del self._local.connection


# =============================================================================
# Singleton Instance
# =============================================================================


_repository: Optional[RenderJobRepository] = None


def get_render_job_repository() -> RenderJobRepository:
    """RenderJobRepository 싱글톤 인스턴스 반환."""
    global _repository
    if _repository is None:
        _repository = RenderJobRepository()
    return _repository


def clear_render_job_repository() -> None:
    """RenderJobRepository 싱글톤 초기화 (테스트용)."""
    global _repository
    if _repository:
        _repository.close()
    _repository = None
