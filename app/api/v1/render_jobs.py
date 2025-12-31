"""
렌더 잡 API (Internal Only)

영상 렌더링 잡 실행 API 엔드포인트.
FE는 백엔드 경유하므로 FE용 API는 모두 제거됨.

엔드포인트 (Backend → AI):
- POST /internal/ai/render-jobs : 렌더 잡 생성/시작 (백엔드 발급 jobId 사용)
- POST /ai/video/job/{job_id}/start : 잡 시작 (레거시, 호환성)
- POST /ai/video/job/{job_id}/retry : 잡 재시도 (레거시, 호환성)

상태 머신 (DB 정렬):
- QUEUED → PROCESSING → COMPLETED | FAILED
- 취소: FAILED + error_code="CANCELED"

정책:
- jobId는 백엔드가 발급 (AI는 생성하지 않음)
- 상태값은 PROCESSING 사용 (RENDERING 금지)
- 렌더 스펙은 백엔드에서 조회
"""

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.video_render import RenderJobStartResponse
from app.services.video_renderer_mvp import get_mvp_video_renderer

logger = get_logger(__name__)

# Internal API 라우터 (Backend → AI)
internal_router = APIRouter(prefix="/internal/ai", tags=["Internal Render Jobs"])

# Backend → AI 호출용 라우터 (영상 생성 시작/재시도) - 레거시 호환
ai_router = APIRouter(prefix="/video/job", tags=["Video Job (Backend → AI)"])


# =============================================================================
# Internal API Authentication
# =============================================================================


async def verify_internal_token(
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
) -> None:
    """내부 API 인증 토큰 검증.

    Args:
        x_internal_token: X-Internal-Token 헤더 값

    Raises:
        HTTPException: 인증 실패 시 401/403
    """
    settings = get_settings()
    expected_token = settings.BACKEND_INTERNAL_TOKEN

    # 토큰이 설정되지 않은 경우 (개발 환경)
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
# Internal API Request/Response Models
# =============================================================================


class InternalRenderJobRequest(BaseModel):
    """내부 렌더 잡 생성 요청 (Backend → AI).

    POST /internal/ai/render-jobs
    """

    job_id: str = Field(
        ...,
        alias="jobId",
        description="백엔드가 발급한 잡 ID",
    )
    video_id: str = Field(
        ...,
        alias="videoId",
        description="영상 ID",
    )
    script_id: str = Field(
        ...,
        alias="scriptId",
        description="스크립트 ID",
    )
    script_version: Optional[int] = Field(
        None,
        alias="scriptVersion",
        description="스크립트 버전 (선택)",
    )
    render_policy_id: Optional[str] = Field(
        None,
        alias="renderPolicyId",
        description="렌더 정책 ID (선택)",
    )
    request_id: Optional[str] = Field(
        None,
        alias="requestId",
        description="멱등 키 (권장)",
    )

    class Config:
        populate_by_name = True


class InternalRenderJobResponse(BaseModel):
    """내부 렌더 잡 생성 응답.

    202 Accepted
    """

    received: bool = Field(
        ...,
        description="접수 여부",
    )
    job_id: str = Field(
        ...,
        alias="jobId",
        description="잡 ID (백엔드 발급값 그대로)",
    )
    status: str = Field(
        ...,
        description="현재 상태 (PROCESSING)",
    )

    class Config:
        populate_by_name = True


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


# =============================================================================
# Internal API (Backend → AI)
# =============================================================================


@internal_router.post(
    "/render-jobs",
    response_model=InternalRenderJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="렌더 잡 생성/시작 (Backend → AI)",
    description="""
백엔드가 호출하여 렌더 잡을 생성하고 시작합니다.

**인증**: X-Internal-Token 헤더 필수

**요청**:
- jobId: 백엔드가 발급한 잡 ID (AI는 생성하지 않음)
- videoId: 영상 ID
- scriptId: 스크립트 ID
- scriptVersion: 스크립트 버전 (선택)
- renderPolicyId: 렌더 정책 ID (선택)
- requestId: 멱등 키 (권장)

**동작**:
1. 백엔드에서 render-spec 조회 (GET /internal/scripts/{scriptId}/render-spec)
2. 렌더 파이프라인 시작
3. 완료 시 콜백 전송 (POST /internal/callbacks/render-jobs/{jobId}/complete)

**응답**: 202 Accepted
- received: true
- jobId: 백엔드 발급값 그대로
- status: "PROCESSING"
""",
    dependencies=[Depends(verify_internal_token)],
)
async def create_internal_render_job(
    request: InternalRenderJobRequest,
):
    """내부 렌더 잡 생성/시작."""
    runner = get_render_job_runner()

    logger.info(
        f"Internal render job received: job_id={request.job_id}, "
        f"video_id={request.video_id}, script_id={request.script_id}"
    )

    # 잡 생성 (백엔드 발급 jobId 사용)
    try:
        result = await runner.create_job_with_id(
            job_id=request.job_id,
            video_id=request.video_id,
            script_id=request.script_id,
            request_id=request.request_id,
        )

        # 바로 시작
        if result.created:
            await runner.start_job(request.job_id)

        return InternalRenderJobResponse(
            received=True,
            job_id=request.job_id,
            status="PROCESSING",
        )

    except Exception as e:
        logger.error(f"Internal render job failed: job_id={request.job_id}, error={e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "reason_code": "RENDER_JOB_FAILED",
                "message": str(e)[:200],
            },
        )


# =============================================================================
# Backend → AI APIs (Job Execution) - Legacy Compatibility
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
- 이미 render_spec_json이 있고 PROCESSING/COMPLETED/FAILED 상태면 no-op
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
    runner = get_render_job_runner()

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
- PROCESSING 상태가 아니어야 함
""",
)
async def retry_render_job(
    job_id: str,
):
    """영상 생성 재시도 (Backend → AI)."""
    runner = get_render_job_runner()

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
