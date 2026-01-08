"""
영상 생성 Job API (Backend → AI)

백엔드에서 호출하여 영상 생성 Job을 시작하는 내부 API입니다.

엔드포인트:
- POST /internal/ai/video/job : 영상 생성 Job 시작
- GET /internal/ai/video/job/{jobId} : Job 상태 조회

인증:
- X-Internal-Token 헤더 필수
"""

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.heygen_video_generation_service import (
    HeyGenVideoGenerationService,
    VideoJob,
    VideoJobStatus,
    get_heygen_video_generation_service,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/internal/ai", tags=["Video Job (HeyGen)"])


# =============================================================================
# Dependencies
# =============================================================================


async def verify_internal_token(
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
) -> None:
    """내부 API 인증 토큰 검증."""
    settings = get_settings()
    expected_token = settings.BACKEND_INTERNAL_TOKEN

    if not expected_token:
        logger.warning("BACKEND_INTERNAL_TOKEN not configured, skipping auth")
        return

    if not x_internal_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "reason_code": "MISSING_TOKEN",
                "message": "X-Internal-Token 헤더가 필요합니다.",
            },
        )

    if x_internal_token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "reason_code": "INVALID_TOKEN",
                "message": "유효하지 않은 인증 토큰입니다.",
            },
        )


# =============================================================================
# Request/Response Models
# =============================================================================


class VideoJobCreateRequest(BaseModel):
    """영상 생성 Job 시작 요청.

    POST /internal/ai/video/job

    back-docs: POST /video/job
    """

    edu_id: str = Field(..., alias="eduId", description="교육 ID")
    script_id: str = Field(..., alias="scriptId", description="스크립트 ID")
    video_id: str = Field(..., alias="videoId", description="영상 ID")
    job_id: Optional[str] = Field(None, alias="jobId", description="Job ID (선택, 없으면 자동 생성)")

    class Config:
        populate_by_name = True


class VideoJobCreateResponse(BaseModel):
    """영상 생성 Job 시작 응답.

    back-docs: POST /video/job Response
    """

    job_id: str = Field(..., alias="jobId", description="Job ID")
    status: str = Field(..., description="Job 상태 (PENDING)")

    class Config:
        populate_by_name = True


class VideoJobStatusResponse(BaseModel):
    """영상 생성 Job 상태 조회 응답.

    back-docs: GET /video/job/{jobId} Response
    """

    job_id: str = Field(..., alias="jobId", description="Job ID")
    script_id: str = Field(..., alias="scriptId", description="스크립트 ID")
    edu_id: str = Field(..., alias="eduId", description="교육 ID")
    status: str = Field(..., description="Job 상태")
    retry_count: int = Field(0, alias="retryCount", description="재시도 횟수")
    video_url: Optional[str] = Field(None, alias="videoUrl", description="생성된 영상 URL")
    duration: Optional[int] = Field(None, description="영상 길이(초)")
    created_at: str = Field(..., alias="createdAt", description="생성 시각 (ISO8601)")
    updated_at: str = Field(..., alias="updatedAt", description="수정 시각 (ISO8601)")
    fail_reason: Optional[str] = Field(None, alias="failReason", description="실패 사유")

    class Config:
        populate_by_name = True


# =============================================================================
# Video Job APIs
# =============================================================================


@router.post(
    "/video/job",
    response_model=VideoJobCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="영상 생성 Job 시작 (Backend → AI)",
    description="""
백엔드에서 호출하여 영상 생성 Job을 시작합니다.

**URL**: POST /internal/ai/video/job
**back-docs**: POST /video/job

**인증**: X-Internal-Token 헤더 필수

**요청**:
- eduId: 교육 ID (required)
- scriptId: 스크립트 ID (required)
- videoId: 영상 ID (required)
- jobId: Job ID (optional, 없으면 자동 생성)

**동작**:
1. 즉시 201 Created 반환 (비동기 처리)
2. 백그라운드에서:
   - 백엔드에서 스크립트 조회
   - Heygen 형식으로 변환
   - Heygen Job 생성
   - 상태 폴링
   - 결과 다운로드
   - S3 직접 업로드 (presign 금지)
   - 백엔드 콜백 전송

**응답**: 201 Created
- jobId: Job ID
- status: "PENDING"
""",
    responses={
        201: {"description": "Job 생성 성공"},
        400: {"description": "유효성 실패"},
        401: {"description": "인증 토큰 누락"},
        403: {"description": "유효하지 않은 토큰"},
        500: {"description": "Job 등록 실패"},
    },
    dependencies=[Depends(verify_internal_token)],
)
async def create_video_job(
    request: VideoJobCreateRequest,
):
    """영상 생성 Job을 시작합니다."""
    service = get_heygen_video_generation_service()

    if not service.is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "reason_code": "HEYGEN_NOT_CONFIGURED",
                "message": "HeyGen not configured. Set HEYGEN_API_KEY.",
            },
        )

    # Job ID 처리
    # back-docs에 따르면 백엔드가 jobId를 발급하지만, 현재는 선택적 필드로 처리
    # 백엔드가 jobId를 제공하지 않으면 자동 생성
    job_id = request.job_id
    if not job_id:
        import uuid
        job_id = str(uuid.uuid4())
        logger.info(f"Auto-generated job_id (backend should provide jobId): {job_id}")

    # 기존 Job 확인 (멱등성)
    existing_job = service.get_job_status(job_id)
    if existing_job:
        logger.info(
            f"Job already exists: job_id={job_id}, status={existing_job.status}"
        )
        return VideoJobCreateResponse(
            job_id=job_id,
            status=existing_job.status.value,
        )

    try:
        # Job 생성 및 백그라운드 처리 시작
        job = await service.create_video_job(
            job_id=job_id,
            video_id=request.video_id,
            script_id=request.script_id,
            education_id=request.edu_id,
        )

        logger.info(
            f"Video job created: job_id={job_id}, video_id={request.video_id}, "
            f"script_id={request.script_id}, education_id={request.edu_id}"
        )

        return VideoJobCreateResponse(
            job_id=job.job_id,
            status=job.status.value,
        )

    except ValueError as e:
        logger.error(f"Invalid request for video job: job_id={job_id}, error={e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "reason_code": "INVALID_REQUEST",
                "message": str(e)[:200],
            },
        )
    except Exception as e:
        logger.error(f"Failed to create video job: job_id={job_id}, error={e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "reason_code": "JOB_CREATION_FAILED",
                "message": f"Job 등록 실패: {str(e)[:200]}",
            },
        )


@router.get(
    "/video/job/{job_id}",
    response_model=VideoJobStatusResponse,
    summary="영상 생성 Job 상태 조회",
    description="""
영상 생성 Job의 상태를 조회합니다.

**URL**: GET /internal/ai/video/job/{jobId}
**back-docs**: GET /video/job/{jobId}

**인증**: X-Internal-Token 헤더 필수

**응답**: 200 OK
- jobId: Job ID
- scriptId: 스크립트 ID
- eduId: 교육 ID
- status: Job 상태 (PENDING, PROCESSING, COMPLETED, FAILED)
- retryCount: 재시도 횟수
- videoUrl: 생성된 영상 URL (완료 시)
- duration: 영상 길이(초) (완료 시)
- createdAt: 생성 시각
- updatedAt: 수정 시각
- failReason: 실패 사유 (실패 시)
""",
    responses={
        200: {"description": "정상"},
        401: {"description": "인증 토큰 누락"},
        403: {"description": "유효하지 않은 토큰"},
        404: {"description": "Job을 찾을 수 없음"},
    },
    dependencies=[Depends(verify_internal_token)],
)
async def get_video_job_status(
    job_id: str,
):
    """영상 생성 Job 상태를 조회합니다."""
    service = get_heygen_video_generation_service()

    job = service.get_job_status(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason_code": "JOB_NOT_FOUND",
                "message": "Job을 찾을 수 없습니다.",
            },
        )

    return VideoJobStatusResponse(
        job_id=job.job_id,
        script_id=job.script_id,
        edu_id=job.education_id,
        status=job.status.value,
        retry_count=job.retry_count,
        video_url=job.video_url,
        duration=job.duration_sec,
        created_at=job.created_at.isoformat() + "Z",
        updated_at=job.updated_at.isoformat() + "Z",
        fail_reason=job.fail_reason,
    )
