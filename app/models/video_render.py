"""
Phase 27: Video Render Models

영상 생성 파이프라인을 위한 데이터 모델 정의.

주요 모델:
- VideoScript: 승인된 스크립트 (APPROVED 상태만 렌더링 가능)
- VideoRenderJob: 렌더 잡 상태 머신
- VideoAsset: 생성된 에셋 (mp4, thumbnail, subtitle)
- RenderedAssets: 렌더러 출력 결과
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Enums
# =============================================================================


class ScriptStatus(str, Enum):
    """스크립트 상태."""
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RenderJobStatus(str, Enum):
    """렌더 잡 상태 (상태 머신).

    PENDING → RUNNING → (SUCCEEDED | FAILED | CANCELED)
    """
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class RenderStep(str, Enum):
    """렌더 파이프라인 단계."""
    VALIDATE_SCRIPT = "VALIDATE_SCRIPT"
    GENERATE_TTS = "GENERATE_TTS"
    GENERATE_SUBTITLE = "GENERATE_SUBTITLE"
    RENDER_SLIDES = "RENDER_SLIDES"
    COMPOSE_VIDEO = "COMPOSE_VIDEO"
    UPLOAD_ASSETS = "UPLOAD_ASSETS"
    FINALIZE = "FINALIZE"


# =============================================================================
# Data Classes (Internal Models)
# =============================================================================


@dataclass
class VideoScript:
    """영상 스크립트 모델.

    Attributes:
        script_id: 스크립트 고유 ID
        video_id: 연관된 비디오 ID
        status: 스크립트 상태 (APPROVED만 렌더링 가능)
        raw_json: 스크립트 JSON (chapters/scenes/narration/caption/source_refs)
        created_by: 생성자 ID
        created_at: 생성 시각
    """
    script_id: str
    video_id: str
    status: ScriptStatus
    raw_json: Dict[str, Any]
    created_by: str
    created_at: datetime = field(default_factory=datetime.utcnow)

    def is_approved(self) -> bool:
        """스크립트가 승인되었는지 확인."""
        return self.status == ScriptStatus.APPROVED


@dataclass
class VideoRenderJob:
    """렌더 잡 모델 (상태 머신).

    Attributes:
        job_id: 잡 고유 ID
        video_id: 비디오 ID
        script_id: 스크립트 ID
        status: 잡 상태
        step: 현재 진행 단계
        progress: 진행률 (0-100)
        error_message: 에러 메시지 (FAILED 시)
        started_at: 시작 시각
        finished_at: 종료 시각
        requested_by: 요청자 ID
        created_at: 생성 시각
    """
    job_id: str
    video_id: str
    script_id: str
    status: RenderJobStatus = RenderJobStatus.PENDING
    step: Optional[RenderStep] = None
    progress: int = 0
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    requested_by: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)

    def is_terminal(self) -> bool:
        """종료 상태인지 확인."""
        return self.status in (
            RenderJobStatus.SUCCEEDED,
            RenderJobStatus.FAILED,
            RenderJobStatus.CANCELED,
        )

    def is_active(self) -> bool:
        """활성 상태(PENDING/RUNNING)인지 확인."""
        return self.status in (
            RenderJobStatus.PENDING,
            RenderJobStatus.RUNNING,
        )

    def can_cancel(self) -> bool:
        """취소 가능한지 확인."""
        return self.is_active()


@dataclass
class VideoAsset:
    """생성된 비디오 에셋.

    Attributes:
        video_asset_id: 에셋 고유 ID
        video_id: 비디오 ID
        job_id: 생성한 잡 ID
        video_url: MP4 파일 URL
        thumbnail_url: 썸네일 URL
        subtitle_url: 자막 파일 URL (SRT/VTT)
        duration_sec: 영상 길이 (초)
        created_at: 생성 시각
    """
    video_asset_id: str
    video_id: str
    job_id: str
    video_url: str
    thumbnail_url: str
    subtitle_url: str
    duration_sec: float
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class RenderedAssets:
    """렌더러 출력 결과.

    VideoRenderer가 반환하는 결과물입니다.

    Attributes:
        mp4_path: 생성된 MP4 파일 경로
        thumbnail_path: 썸네일 이미지 경로
        subtitle_path: 자막 파일 경로
        duration_sec: 영상 길이 (초)
    """
    mp4_path: str
    thumbnail_path: str
    subtitle_path: str
    duration_sec: float


# =============================================================================
# API Request/Response Models (Pydantic)
# =============================================================================


class RenderJobCreateRequest(BaseModel):
    """렌더 잡 생성 요청.

    POST /api/videos/{video_id}/render-jobs
    """
    script_id: str = Field(..., description="렌더링할 스크립트 ID")


class RenderJobCreateResponse(BaseModel):
    """렌더 잡 생성 응답."""
    job_id: str = Field(..., description="생성된 잡 ID")
    status: str = Field(..., description="잡 상태 (PENDING)")


class RenderJobStatusResponse(BaseModel):
    """렌더 잡 상태 조회 응답.

    GET /api/render-jobs/{job_id}
    """
    job_id: str = Field(..., description="잡 ID")
    video_id: str = Field(..., description="비디오 ID")
    script_id: str = Field(..., description="스크립트 ID")
    status: str = Field(..., description="잡 상태")
    step: Optional[str] = Field(None, description="현재 진행 단계")
    progress: int = Field(..., description="진행률 (0-100)")
    error_message: Optional[str] = Field(None, description="에러 메시지")
    started_at: Optional[str] = Field(None, description="시작 시각 (ISO 8601)")
    finished_at: Optional[str] = Field(None, description="종료 시각 (ISO 8601)")
    asset: Optional["VideoAssetResponse"] = Field(None, description="생성된 에셋 (SUCCEEDED 시)")


class RenderJobCancelResponse(BaseModel):
    """렌더 잡 취소 응답.

    POST /api/render-jobs/{job_id}/cancel
    """
    job_id: str = Field(..., description="잡 ID")
    status: str = Field(..., description="변경된 상태 (CANCELED)")
    message: str = Field(..., description="결과 메시지")


class VideoAssetResponse(BaseModel):
    """비디오 에셋 응답.

    GET /api/videos/{video_id}/asset
    """
    video_asset_id: str = Field(..., description="에셋 ID")
    video_id: str = Field(..., description="비디오 ID")
    video_url: str = Field(..., description="MP4 파일 URL")
    thumbnail_url: str = Field(..., description="썸네일 URL")
    subtitle_url: str = Field(..., description="자막 파일 URL")
    duration_sec: float = Field(..., description="영상 길이 (초)")
    created_at: str = Field(..., description="생성 시각 (ISO 8601)")


# =============================================================================
# Script API Models
# =============================================================================


class ScriptCreateRequest(BaseModel):
    """스크립트 생성 요청."""
    video_id: str = Field(..., description="비디오 ID")
    raw_json: Dict[str, Any] = Field(..., description="스크립트 JSON")


class ScriptApproveRequest(BaseModel):
    """스크립트 승인 요청."""
    pass  # No body needed


class ScriptResponse(BaseModel):
    """스크립트 응답."""
    script_id: str = Field(..., description="스크립트 ID")
    video_id: str = Field(..., description="비디오 ID")
    status: str = Field(..., description="스크립트 상태")
    raw_json: Dict[str, Any] = Field(..., description="스크립트 JSON")
    created_by: str = Field(..., description="생성자 ID")
    created_at: str = Field(..., description="생성 시각 (ISO 8601)")


# Forward reference 해결
RenderJobStatusResponse.model_rebuild()
