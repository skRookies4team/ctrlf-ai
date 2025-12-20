"""
렌더 잡 API

영상 렌더링 잡 CRUD 및 실행 API 엔드포인트.

엔드포인트:
- POST /api/v2/videos/{video_id}/render-jobs : 렌더 잡 생성 (idempotent)
- GET /api/v2/videos/{video_id}/render-jobs : 잡 목록 조회
- GET /api/v2/videos/{video_id}/render-jobs/{job_id} : 잡 상세 조회
- POST /api/v2/videos/{video_id}/render-jobs/{job_id}/cancel : 잡 취소
- GET /api/v2/videos/{video_id}/assets/published : 발행된 에셋 조회
- POST /ai/video/job/{job_id}/start : 잡 시작 (Backend → AI)
- POST /ai/video/job/{job_id}/retry : 잡 재시도 (Backend → AI)

정책:
- RUNNING/PENDING 잡이 있으면 기존 잡 반환 (idempotency)
- APPROVED 스크립트만 렌더 가능
- 잡 상태는 DB에 영속화
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.core.logging import get_logger
from app.models.video_render import (
    PublishedAssetsResponse,
    RenderJobCancelResponse,
    RenderJobCreateRequest,
    RenderJobCreateResponseV2,
    RenderJobDetailResponse,
    RenderJobListResponse,
    RenderJobStartResponse,
    RenderJobSummary,
)
from app.services.education_catalog_service import get_education_catalog_service
from app.services.video_render_service import get_video_render_service
from app.services.video_renderer_mvp import get_mvp_video_renderer

logger = get_logger(__name__)

# 렌더 잡 CRUD API (V2)
router = APIRouter(prefix="/api/v2", tags=["Render Jobs"])

# Backend → AI 호출용 라우터 (영상 생성 시작/재시도)
ai_router = APIRouter(prefix="/video/job", tags=["Video Job (Backend → AI)"])


# =============================================================================
# Dependencies
# =============================================================================


def get_render_job_runner():
    """RenderJobRunner 의존성."""
    from app.services.render_job_runner import get_render_job_runner as _get_runner

    runner = _get_runner()
    if runner._renderer is None:
        runner.set_renderer(get_mvp_video_renderer())
    return runner


def get_render_service():
    """VideoRenderService 의존성."""
    service = get_video_render_service()
    if service._renderer is None:
        service.set_renderer(get_mvp_video_renderer())
    return service


def ensure_education_not_expired(video_id: str) -> None:
    """교육이 만료되지 않았는지 확인."""
    catalog = get_education_catalog_service()
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
    """REVIEWER 역할 검증."""
    if user_role and user_role.upper() not in ("REVIEWER", "ADMIN"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "reason_code": "PERMISSION_DENIED",
                "message": "영상 생성은 검토자(REVIEWER) 역할만 가능합니다.",
            },
        )


# =============================================================================
# Render Job CRUD APIs
# =============================================================================


@router.post(
    "/videos/{video_id}/render-jobs",
    response_model=RenderJobCreateResponseV2,
    summary="렌더 잡 생성 (idempotent)",
    description="""
새 렌더 잡을 생성하거나 기존 잡을 반환합니다 (idempotent).

**권한**: REVIEWER만 가능

**검증**:
- 스크립트가 APPROVED 상태여야 함 (400)
- 해당 교육이 EXPIRED면 404

**중복 방지**:
- 동일 video_id에 RUNNING/PENDING 잡이 있으면 기존 잡 반환 (200)
- 없으면 새 잡 생성 (202)

**응답**:
- created=true: 새 잡 생성됨
- created=false: 기존 잡 반환됨
""",
    responses={
        200: {"description": "기존 활성 잡 반환"},
        202: {"description": "새 잡 생성됨"},
        400: {"description": "스크립트가 APPROVED가 아님"},
        404: {"description": "교육 만료 또는 스크립트 없음"},
    },
)
async def create_render_job(
    video_id: str,
    request: RenderJobCreateRequest,
    response: Response,
    user_id: str = "reviewer",
    user_role: str = "REVIEWER",
):
    """렌더 잡 생성 (idempotent)."""
    # REVIEWER 역할 검증
    verify_reviewer_role(user_role)

    # EXPIRED 교육 차단
    ensure_education_not_expired(video_id)

    # 스크립트 조회
    service = get_render_service()
    script = service.get_script(request.script_id)
    if not script:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason_code": "SCRIPT_NOT_FOUND",
                "message": f"스크립트를 찾을 수 없습니다: {request.script_id}",
            },
        )

    # RenderJobRunner 사용
    runner = get_render_job_runner()

    try:
        result = await runner.create_job(
            video_id=video_id,
            script_id=request.script_id,
            script=script,
            created_by=user_id,
        )

        # 응답 상태 코드 설정
        if result.created:
            response.status_code = status.HTTP_202_ACCEPTED
        else:
            response.status_code = status.HTTP_200_OK

        return RenderJobCreateResponseV2(
            job_id=result.job.job_id,
            status=result.job.status,
            progress=result.job.progress,
            step=result.job.step,
            message=result.message,
            created=result.created,
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "reason_code": "INVALID_SCRIPT",
                "message": str(e),
            },
        )


@router.get(
    "/videos/{video_id}/render-jobs/{job_id}",
    response_model=RenderJobDetailResponse,
    summary="렌더 잡 상세 조회",
    description="특정 렌더 잡의 상세 정보를 조회합니다.",
)
async def get_render_job_detail(
    video_id: str,
    job_id: str,
):
    """렌더 잡 상세 조회."""
    runner = get_render_job_runner()
    job = runner.get_job(job_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason_code": "JOB_NOT_FOUND",
                "message": f"렌더 잡을 찾을 수 없습니다: {job_id}",
            },
        )

    # video_id 일치 확인
    if job.video_id != video_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason_code": "JOB_NOT_FOUND",
                "message": f"해당 비디오에 속한 잡이 아닙니다: {job_id}",
            },
        )

    return RenderJobDetailResponse(
        job_id=job.job_id,
        video_id=job.video_id,
        script_id=job.script_id,
        status=job.status,
        step=job.step,
        progress=job.progress,
        message=job.message,
        error_code=job.error_code,
        error_message=job.error_message,
        assets=job.assets if job.assets else None,
        created_by=job.created_by,
        created_at=job.created_at.isoformat() if job.created_at else None,
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
    )


@router.get(
    "/videos/{video_id}/render-jobs",
    response_model=RenderJobListResponse,
    summary="렌더 잡 목록 조회",
    description="비디오의 렌더 잡 목록을 조회합니다 (최신순).",
)
async def list_render_jobs(
    video_id: str,
    limit: int = Query(default=20, ge=1, le=100, description="조회 개수"),
    offset: int = Query(default=0, ge=0, description="오프셋"),
):
    """렌더 잡 목록 조회."""
    from app.repositories.render_job_repository import get_render_job_repository

    runner = get_render_job_runner()
    repo = get_render_job_repository()

    jobs = runner.list_jobs(video_id, limit=limit, offset=offset)
    total = repo.count_by_video_id(video_id)

    return RenderJobListResponse(
        video_id=video_id,
        jobs=[
            RenderJobSummary(
                job_id=job.job_id,
                status=job.status,
                progress=job.progress,
                created_at=job.created_at.isoformat() if job.created_at else None,
                finished_at=job.finished_at.isoformat() if job.finished_at else None,
            )
            for job in jobs
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/videos/{video_id}/assets/published",
    response_model=PublishedAssetsResponse,
    summary="발행된 에셋 조회",
    description="""
비디오의 발행된(SUCCEEDED) 에셋을 조회합니다.

**FE 사용 시나리오**:
1. POST /render-jobs로 잡 생성
2. WS로 진행률 수신
3. 완료 후 이 API로 URL 획득
""",
)
async def get_published_assets(
    video_id: str,
):
    """발행된 에셋 조회."""
    runner = get_render_job_runner()
    assets = runner.get_published_assets(video_id)

    if not assets:
        return PublishedAssetsResponse(
            video_id=video_id,
            published=False,
        )

    return PublishedAssetsResponse(
        video_id=video_id,
        published=True,
        video_url=assets.get("video_url"),
        subtitle_url=assets.get("subtitle_url"),
        thumbnail_url=assets.get("thumbnail_url"),
        duration_sec=assets.get("duration_sec"),
        published_at=assets.get("published_at"),
        script_id=assets.get("script_id"),
        job_id=assets.get("job_id"),
    )


@router.post(
    "/videos/{video_id}/render-jobs/{job_id}/cancel",
    response_model=RenderJobCancelResponse,
    summary="렌더 잡 취소",
    description="진행 중인 렌더 잡을 취소합니다.",
)
async def cancel_render_job(
    video_id: str,
    job_id: str,
):
    """렌더 잡 취소."""
    runner = get_render_job_runner()

    # 잡 조회
    job = runner.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason_code": "JOB_NOT_FOUND",
                "message": f"렌더 잡을 찾을 수 없습니다: {job_id}",
            },
        )

    # video_id 확인
    if job.video_id != video_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason_code": "JOB_NOT_FOUND",
                "message": f"해당 비디오에 속한 잡이 아닙니다: {job_id}",
            },
        )

    # 취소
    canceled = await runner.cancel_job(job_id)
    if not canceled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason_code": "CANNOT_CANCEL",
                "message": f"잡을 취소할 수 없습니다 (현재 상태: {job.status})",
            },
        )

    return RenderJobCancelResponse(
        job_id=canceled.job_id,
        status=canceled.status,
        message="렌더 잡이 취소되었습니다.",
    )


# =============================================================================
# Backend → AI APIs (Job Execution)
# =============================================================================


@ai_router.post(
    "/{job_id}/start",
    response_model=RenderJobStartResponse,
    summary="영상 생성 시작 (Backend → AI)",
    description="""
백엔드가 호출하여 영상 생성을 시작합니다.

**URL**: POST /ai/video/job/{jobId}/start

**동작**:
1. 백엔드에서 최신 render-spec 조회
2. render-spec을 잡에 스냅샷으로 저장
3. 파이프라인 실행 시작

**Idempotent**:
- 이미 render_spec_json이 있고 RUNNING/SUCCEEDED/FAILED 상태면 no-op
- 같은 잡에 여러 번 호출해도 안전

**에러 코드**:
- JOB_NOT_FOUND: 잡이 존재하지 않음
- SCRIPT_FETCH_FAILED: 백엔드 render-spec 조회 실패
- EMPTY_RENDER_SPEC: render-spec에 씬이 없음
""",
)
async def start_render_job(
    job_id: str,
):
    """영상 생성 시작 (Backend → AI)."""
    from app.services.render_job_runner import get_render_job_runner

    runner = get_render_job_runner()

    # 렌더러 설정 확인
    if runner._renderer is None:
        runner.set_renderer(get_mvp_video_renderer())

    result = await runner.start_job(job_id)

    if result.error_code == "JOB_NOT_FOUND":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason_code": "JOB_NOT_FOUND",
                "message": f"렌더 잡을 찾을 수 없습니다: {job_id}",
            },
        )

    if result.error_code and result.error_code.startswith("SCRIPT_FETCH"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "reason_code": result.error_code,
                "message": result.message,
            },
        )

    if result.error_code == "EMPTY_RENDER_SPEC":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "reason_code": "EMPTY_RENDER_SPEC",
                "message": "Render-spec에 씬이 없습니다.",
            },
        )

    return RenderJobStartResponse(
        job_id=job_id,
        status=result.job.status if result.job else "UNKNOWN",
        started=result.started,
        message=result.message,
        error_code=result.error_code,
    )


@ai_router.post(
    "/{job_id}/retry",
    response_model=RenderJobStartResponse,
    summary="영상 생성 재시도 (Backend → AI)",
    description="""
백엔드가 호출하여 실패한 영상 생성을 재시도합니다.

**URL**: POST /ai/video/job/{jobId}/retry

**정책**:
- 기존에 저장된 render-spec 스냅샷을 재사용
- 백엔드를 다시 호출하지 않음

**조건**:
- render_spec_json이 있어야 함 (start 이후)
- RUNNING 상태가 아니어야 함
""",
)
async def retry_render_job(
    job_id: str,
):
    """영상 생성 재시도 (Backend → AI)."""
    from app.services.render_job_runner import get_render_job_runner

    runner = get_render_job_runner()

    if runner._renderer is None:
        runner.set_renderer(get_mvp_video_renderer())

    result = await runner.retry_job(job_id)

    if result.error_code == "JOB_NOT_FOUND":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason_code": "JOB_NOT_FOUND",
                "message": f"렌더 잡을 찾을 수 없습니다: {job_id}",
            },
        )

    if result.error_code == "NO_RENDER_SPEC_FOR_RETRY":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason_code": "NO_RENDER_SPEC_FOR_RETRY",
                "message": "재시도하려면 먼저 /start를 호출해야 합니다.",
            },
        )

    return RenderJobStartResponse(
        job_id=job_id,
        status=result.job.status if result.job else "UNKNOWN",
        started=result.started,
        message=result.message,
        error_code=result.error_code,
    )
