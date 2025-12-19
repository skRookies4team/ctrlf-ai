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
    PUBLISHED = "PUBLISHED"  # Phase 28: KB 적재 완료


class KBIndexStatus(str, Enum):
    """KB 인덱스 상태 (Phase 28)."""
    NOT_INDEXED = "NOT_INDEXED"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class KBDocumentStatus(str, Enum):
    """KB 문서 상태 (Phase 28)."""
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


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
        kb_index_status: KB 인덱스 상태 (Phase 28)
        kb_indexed_at: KB 인덱싱 완료 시각
        kb_last_error: 마지막 KB 인덱싱 에러
        kb_document_id: KB 문서 ID
        kb_document_status: KB 문서 상태 (ACTIVE/ARCHIVED)
    """
    script_id: str
    video_id: str
    status: ScriptStatus
    raw_json: Dict[str, Any]
    created_by: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    # Phase 28: KB 인덱스 관련 필드
    kb_index_status: KBIndexStatus = KBIndexStatus.NOT_INDEXED
    kb_indexed_at: Optional[datetime] = None
    kb_last_error: Optional[str] = None
    kb_document_id: Optional[str] = None
    kb_document_status: KBDocumentStatus = KBDocumentStatus.ACTIVE

    def is_approved(self) -> bool:
        """스크립트가 승인되었는지 확인."""
        return self.status in (ScriptStatus.APPROVED, ScriptStatus.PUBLISHED)

    def is_published(self) -> bool:
        """스크립트가 발행되었는지 확인."""
        return self.status == ScriptStatus.PUBLISHED

    def is_kb_indexed(self) -> bool:
        """KB에 인덱싱되었는지 확인."""
        return self.kb_index_status == KBIndexStatus.SUCCEEDED


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


@dataclass
class KBChunk:
    """KB 청크 모델 (Phase 28/29).

    승인된 스크립트에서 생성된 청크입니다.
    씬 단위로 청크를 생성하되, 긴 내용은 토큰 기반으로 분할합니다.

    Attributes:
        chunk_id: 청크 ID (script_id:chapter:scene 또는 script_id:chapter:scene:part 형태)
        video_id: 비디오 ID
        script_id: 스크립트 ID
        chapter_order: 챕터 순서
        scene_order: 씬 순서
        chapter_title: 챕터 제목
        scene_purpose: 씬 목적
        content: 청크 내용 (narration + caption)
        source_refs: 원본 참조 (doc_id, chunk_id 등)
        metadata: 추가 메타데이터
        part_index: 분할 파트 인덱스 (Phase 29, None이면 분할되지 않음)
        source_type: 소스 타입 (Phase 29, "TRAINING_SCRIPT" 등)
    """
    chunk_id: str
    video_id: str
    script_id: str
    chapter_order: int
    scene_order: int
    chapter_title: str
    scene_purpose: str
    content: str
    source_refs: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    # Phase 29: 토큰 기반 분할 지원
    part_index: Optional[int] = None
    source_type: str = "TRAINING_SCRIPT"


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


# =============================================================================
# Phase 28: Publish API Models
# =============================================================================


class PublishRequest(BaseModel):
    """발행 요청 (Phase 28).

    POST /api/videos/{video_id}/publish
    """
    pass  # No body needed, video_id from path


class PublishResponse(BaseModel):
    """발행 응답 (Phase 28).

    POST /api/videos/{video_id}/publish
    """
    video_id: str = Field(..., description="비디오 ID")
    script_id: str = Field(..., description="스크립트 ID")
    status: str = Field(..., description="스크립트 상태 (PUBLISHED)")
    kb_index_status: str = Field(..., description="KB 인덱스 상태 (PENDING)")
    message: str = Field(..., description="결과 메시지")


class KBIndexStatusResponse(BaseModel):
    """KB 인덱스 상태 응답 (Phase 28).

    GET /api/videos/{video_id}/kb-status
    """
    video_id: str = Field(..., description="비디오 ID")
    script_id: Optional[str] = Field(None, description="스크립트 ID")
    kb_index_status: str = Field(..., description="KB 인덱스 상태")
    kb_indexed_at: Optional[str] = Field(None, description="인덱싱 완료 시각")
    kb_document_status: Optional[str] = Field(None, description="KB 문서 상태")
    kb_last_error: Optional[str] = Field(None, description="마지막 에러")


# =============================================================================
# Phase 31: Script Generation API Models
# =============================================================================


class ScriptGenerateRequest(BaseModel):
    """스크립트 자동 생성 요청 (Phase 31).

    POST /api/videos/{video_id}/scripts/generate
    """
    source_text: str = Field(..., min_length=10, description="교육 원문 텍스트")
    language: str = Field(default="ko", description="언어 코드 (ko, en 등)")
    target_minutes: float = Field(default=3, ge=1, le=30, description="목표 영상 길이 (분)")
    max_chapters: int = Field(default=5, ge=1, le=10, description="최대 챕터 수")
    max_scenes_per_chapter: int = Field(default=6, ge=1, le=15, description="챕터당 최대 씬 수")
    style: str = Field(default="friendly_security_training", description="스크립트 스타일")


class ScriptGenerateResponse(BaseModel):
    """스크립트 자동 생성 응답 (Phase 31).

    POST /api/videos/{video_id}/scripts/generate
    """
    script_id: str = Field(..., description="생성된 스크립트 ID")
    video_id: str = Field(..., description="비디오 ID")
    status: str = Field(..., description="스크립트 상태 (DRAFT)")
    raw_json: Dict[str, Any] = Field(..., description="생성된 스크립트 JSON")


# Forward reference 해결
RenderJobStatusResponse.model_rebuild()
