"""
Phase 27: Video Render API

영상 생성 파이프라인 API 엔드포인트.

엔드포인트:
- POST /api/videos/{video_id}/render-jobs : 렌더 잡 생성
- GET /api/render-jobs/{job_id} : 잡 상태 조회
- POST /api/render-jobs/{job_id}/cancel : 잡 취소
- GET /api/videos/{video_id}/asset : 결과 비디오 조회

권한:
- 렌더 잡 생성: REVIEWER 역할만 가능

Phase 26 연동:
- EXPIRED 교육에 대해 404 반환
"""

from fastapi import APIRouter, Depends, HTTPException, status
from typing import Optional

from app.core.logging import get_logger
from app.models.video_render import (
    RenderJobCancelResponse,
    RenderJobCreateRequest,
    RenderJobCreateResponse,
    RenderJobStatusResponse,
    ScriptCreateRequest,
    ScriptApproveRequest,
    ScriptResponse,
    VideoAssetResponse,
)
from app.services.education_catalog_service import get_education_catalog_service
from app.services.video_render_service import get_video_render_service
from app.services.video_renderer_mvp import get_mvp_video_renderer

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["Video Render"])


# =============================================================================
# Dependencies
# =============================================================================


def get_render_service():
    """VideoRenderService 의존성."""
    service = get_video_render_service()
    # 렌더러가 없으면 MVP 렌더러 설정
    if service._renderer is None:
        service.set_renderer(get_mvp_video_renderer())
    return service


def ensure_education_not_expired(video_id: str) -> None:
    """교육이 만료되지 않았는지 확인.

    Phase 26 정책: EXPIRED 교육은 404 차단

    Args:
        video_id: 비디오 ID (교육 ID로 매핑)

    Raises:
        HTTPException: 교육이 만료된 경우 404
    """
    catalog = get_education_catalog_service()

    # video_id를 education_id로 매핑 (동일하다고 가정)
    # 실제 환경에서는 video → education 매핑 필요
    education_id = video_id

    if catalog.is_expired(education_id):
        logger.warning(f"Video render blocked: education expired, education_id={education_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason_code": "EDU_EXPIRED",
                "message": f"해당 교육({education_id})은 만료되어 영상 생성이 불가합니다.",
            },
        )


def verify_reviewer_role(user_role: Optional[str] = None) -> None:
    """REVIEWER 역할 검증.

    Args:
        user_role: 사용자 역할

    Raises:
        HTTPException: REVIEWER가 아닌 경우 403
    """
    # MVP: role 검증 간소화 (실제 환경에서는 JWT에서 추출)
    if user_role and user_role.upper() not in ("REVIEWER", "ADMIN"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "reason_code": "PERMISSION_DENIED",
                "message": "영상 생성은 검토자(REVIEWER) 역할만 가능합니다.",
            },
        )


# =============================================================================
# Script Management APIs
# =============================================================================


@router.post(
    "/scripts",
    response_model=ScriptResponse,
    summary="스크립트 생성",
    description="새 스크립트를 생성합니다. (DRAFT 상태)",
)
async def create_script(
    request: ScriptCreateRequest,
    user_id: str = "anonymous",
    service=Depends(get_render_service),
):
    """스크립트 생성."""
    script = service.create_script(
        video_id=request.video_id,
        raw_json=request.raw_json,
        created_by=user_id,
    )
    return ScriptResponse(
        script_id=script.script_id,
        video_id=script.video_id,
        status=script.status.value,
        raw_json=script.raw_json,
        created_by=script.created_by,
        created_at=script.created_at.isoformat(),
    )


@router.post(
    "/scripts/{script_id}/approve",
    response_model=ScriptResponse,
    summary="스크립트 승인",
    description="스크립트를 승인합니다. (APPROVED 상태로 전환)",
)
async def approve_script(
    script_id: str,
    request: ScriptApproveRequest = None,
    user_id: str = "reviewer",
    user_role: str = "REVIEWER",
    service=Depends(get_render_service),
):
    """스크립트 승인."""
    # REVIEWER 역할 검증
    verify_reviewer_role(user_role)

    script = service.approve_script(script_id, user_id)
    if not script:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason_code": "SCRIPT_NOT_FOUND",
                "message": f"스크립트를 찾을 수 없습니다: {script_id}",
            },
        )

    return ScriptResponse(
        script_id=script.script_id,
        video_id=script.video_id,
        status=script.status.value,
        raw_json=script.raw_json,
        created_by=script.created_by,
        created_at=script.created_at.isoformat(),
    )


@router.get(
    "/scripts/{script_id}",
    response_model=ScriptResponse,
    summary="스크립트 조회",
    description="스크립트 정보를 조회합니다.",
)
async def get_script(
    script_id: str,
    service=Depends(get_render_service),
):
    """스크립트 조회."""
    script = service.get_script(script_id)
    if not script:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason_code": "SCRIPT_NOT_FOUND",
                "message": f"스크립트를 찾을 수 없습니다: {script_id}",
            },
        )

    return ScriptResponse(
        script_id=script.script_id,
        video_id=script.video_id,
        status=script.status.value,
        raw_json=script.raw_json,
        created_by=script.created_by,
        created_at=script.created_at.isoformat(),
    )


# =============================================================================
# Render Job APIs
# =============================================================================


@router.post(
    "/videos/{video_id}/render-jobs",
    response_model=RenderJobCreateResponse,
    summary="렌더 잡 생성",
    description="""
새 렌더 잡을 생성합니다.

**권한**: REVIEWER만 가능

**검증**:
- 스크립트가 APPROVED 상태여야 함
- 해당 교육이 EXPIRED면 404
- 동일 video_id에 대해 RUNNING/PENDING 잡이 있으면 409
""",
)
async def create_render_job(
    video_id: str,
    request: RenderJobCreateRequest,
    user_id: str = "reviewer",
    user_role: str = "REVIEWER",
    service=Depends(get_render_service),
):
    """렌더 잡 생성."""
    # REVIEWER 역할 검증
    verify_reviewer_role(user_role)

    # EXPIRED 교육 차단
    ensure_education_not_expired(video_id)

    try:
        job = await service.create_render_job(
            video_id=video_id,
            script_id=request.script_id,
            requested_by=user_id,
        )
        return RenderJobCreateResponse(
            job_id=job.job_id,
            status=job.status.value,
        )

    except ValueError as e:
        # 스크립트 관련 오류 (없음, 미승인, video_id 불일치)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "reason_code": "INVALID_SCRIPT",
                "message": str(e),
            },
        )

    except RuntimeError as e:
        # 중복 잡 오류
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason_code": "DUPLICATE_JOB",
                "message": str(e),
            },
        )


@router.get(
    "/render-jobs/{job_id}",
    response_model=RenderJobStatusResponse,
    summary="렌더 잡 상태 조회",
    description="렌더 잡의 상태를 조회합니다.",
)
async def get_render_job_status(
    job_id: str,
    service=Depends(get_render_service),
):
    """렌더 잡 상태 조회."""
    job, asset = service.get_job_with_asset(job_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason_code": "JOB_NOT_FOUND",
                "message": f"렌더 잡을 찾을 수 없습니다: {job_id}",
            },
        )

    response = RenderJobStatusResponse(
        job_id=job.job_id,
        video_id=job.video_id,
        script_id=job.script_id,
        status=job.status.value,
        step=job.step.value if job.step else None,
        progress=job.progress,
        error_message=job.error_message,
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
        asset=VideoAssetResponse(
            video_asset_id=asset.video_asset_id,
            video_id=asset.video_id,
            video_url=asset.video_url,
            thumbnail_url=asset.thumbnail_url,
            subtitle_url=asset.subtitle_url,
            duration_sec=asset.duration_sec,
            created_at=asset.created_at.isoformat(),
        ) if asset else None,
    )
    return response


@router.post(
    "/render-jobs/{job_id}/cancel",
    response_model=RenderJobCancelResponse,
    summary="렌더 잡 취소",
    description="진행 중인 렌더 잡을 취소합니다. (PENDING/RUNNING만 가능)",
)
async def cancel_render_job(
    job_id: str,
    service=Depends(get_render_service),
):
    """렌더 잡 취소."""
    job = await service.cancel_job(job_id)

    if not job:
        # 잡이 없거나 취소 불가
        existing_job = service.get_job(job_id)
        if not existing_job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "reason_code": "JOB_NOT_FOUND",
                    "message": f"렌더 잡을 찾을 수 없습니다: {job_id}",
                },
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "reason_code": "CANNOT_CANCEL",
                    "message": f"잡을 취소할 수 없습니다 (현재 상태: {existing_job.status.value})",
                },
            )

    return RenderJobCancelResponse(
        job_id=job.job_id,
        status=job.status.value,
        message="렌더 잡이 취소되었습니다.",
    )


@router.get(
    "/videos/{video_id}/asset",
    response_model=VideoAssetResponse,
    summary="비디오 에셋 조회",
    description="""
비디오의 최신 에셋(영상/썸네일/자막)을 조회합니다.

**Phase 26 정책**: EXPIRED 교육은 404 반환
""",
)
async def get_video_asset(
    video_id: str,
    service=Depends(get_render_service),
):
    """비디오 에셋 조회."""
    # EXPIRED 교육 차단
    ensure_education_not_expired(video_id)

    asset = service.get_latest_asset_by_video_id(video_id)

    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason_code": "ASSET_NOT_FOUND",
                "message": f"비디오 에셋을 찾을 수 없습니다: {video_id}",
            },
        )

    return VideoAssetResponse(
        video_asset_id=asset.video_asset_id,
        video_id=asset.video_id,
        video_url=asset.video_url,
        thumbnail_url=asset.thumbnail_url,
        subtitle_url=asset.subtitle_url,
        duration_sec=asset.duration_sec,
        created_at=asset.created_at.isoformat(),
    )
