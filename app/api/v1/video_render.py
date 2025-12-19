"""
Phase 27: Video Render API
Phase 28: Publish & KB Indexing API

영상 생성 파이프라인 API 엔드포인트.

엔드포인트:
- POST /api/videos/{video_id}/render-jobs : 렌더 잡 생성
- GET /api/render-jobs/{job_id} : 잡 상태 조회
- POST /api/render-jobs/{job_id}/cancel : 잡 취소
- GET /api/videos/{video_id}/asset : 결과 비디오 조회
- POST /api/videos/{video_id}/publish : 영상 발행 + KB 적재 (Phase 28)
- GET /api/videos/{video_id}/kb-status : KB 인덱스 상태 조회 (Phase 28)

권한:
- 렌더 잡 생성: REVIEWER 역할만 가능
- 발행: REVIEWER 역할만 가능

Phase 26 연동:
- EXPIRED 교육에 대해 404 반환

Phase 28 정책:
- KB 적재는 PUBLISHED 상태의 영상만 대상
- APPROVED 스크립트 + 렌더 SUCCEEDED 후에만 발행 가능
"""

from fastapi import APIRouter, Depends, HTTPException, status
from typing import Optional

from app.core.logging import get_logger
from app.models.video_render import (
    KBIndexStatus,
    KBIndexStatusResponse,
    PublishResponse,
    RenderJobCancelResponse,
    RenderJobCreateRequest,
    RenderJobCreateResponse,
    RenderJobStatusResponse,
    RenderJobStatus,
    ScriptCreateRequest,
    ScriptApproveRequest,
    ScriptGenerateRequest,
    ScriptGenerateResponse,
    ScriptResponse,
    ScriptStatus,
    VideoAssetResponse,
)
from app.services.education_catalog_service import get_education_catalog_service
from app.services.kb_index_service import get_kb_index_service
from app.services.video_render_service import get_video_render_service
from app.services.video_renderer_mvp import get_mvp_video_renderer
from app.services.video_script_generation_service import (
    get_video_script_generation_service,
    ScriptGenerationOptions,
)

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
# Phase 31: Script Generation API
# =============================================================================


@router.post(
    "/videos/{video_id}/scripts/generate",
    response_model=ScriptGenerateResponse,
    summary="스크립트 자동 생성 (Phase 31)",
    description="""
교육 원문을 LLM으로 분석하여 VideoScript JSON을 자동 생성합니다.

**입력**:
- source_text: 교육 원문 텍스트
- language: 언어 코드 (기본: ko)
- target_minutes: 목표 영상 길이 (분, 기본: 3)
- max_chapters: 최대 챕터 수 (기본: 5)
- max_scenes_per_chapter: 챕터당 최대 씬 수 (기본: 6)
- style: 스크립트 스타일 (기본: friendly_security_training)

**출력**:
- 생성된 스크립트 (DRAFT 상태)
- 생성된 raw_json (chapters/scenes 구조)

**정책**:
- EXPIRED 교육(video_id 기준)은 404 차단
- 생성 실패 시 422 반환 (reason_code: SCRIPT_GENERATION_FAILED)
""",
)
async def generate_script(
    video_id: str,
    request: ScriptGenerateRequest,
    user_id: str = "anonymous",
    service=Depends(get_render_service),
):
    """교육 원문에서 스크립트 자동 생성 (Phase 31)."""
    # EXPIRED 교육 차단
    ensure_education_not_expired(video_id)

    # 생성 옵션 구성
    options = ScriptGenerationOptions(
        language=request.language,
        target_minutes=request.target_minutes,
        max_chapters=request.max_chapters,
        max_scenes_per_chapter=request.max_scenes_per_chapter,
        style=request.style,
    )

    try:
        # LLM으로 스크립트 생성
        gen_service = get_video_script_generation_service()
        raw_json = await gen_service.generate_script(
            video_id=video_id,
            source_text=request.source_text,
            options=options,
        )

        # 생성된 스크립트를 DRAFT로 저장
        script = service.create_script(
            video_id=video_id,
            raw_json=raw_json,
            created_by=user_id,
        )

        logger.info(
            f"Script generated and saved: video_id={video_id}, "
            f"script_id={script.script_id}"
        )

        return ScriptGenerateResponse(
            script_id=script.script_id,
            video_id=script.video_id,
            status=script.status.value,
            raw_json=script.raw_json,
        )

    except ValueError as e:
        # 스크립트 생성 실패
        error_args = e.args
        detail = {
            "reason_code": "SCRIPT_GENERATION_FAILED",
            "message": str(error_args[0]) if error_args else "Script generation failed",
        }
        if len(error_args) > 1 and isinstance(error_args[1], dict):
            detail.update(error_args[1])

        logger.error(f"Script generation failed: video_id={video_id}, error={e}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        )

    except Exception as e:
        # 예상치 못한 에러
        logger.exception(f"Unexpected error in script generation: video_id={video_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "reason_code": "INTERNAL_ERROR",
                "message": f"Unexpected error: {type(e).__name__}",
            },
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


# =============================================================================
# Phase 28: Publish & KB Indexing APIs
# =============================================================================


@router.post(
    "/videos/{video_id}/publish",
    response_model=PublishResponse,
    summary="영상 발행 + KB 적재",
    description="""
영상을 발행하고 KB(Knowledge Base)에 적재합니다.

**권한**: REVIEWER만 가능

**검증**:
- 해당 video_id에 SUCCEEDED 렌더 잡이 있어야 함 (409)
- 최신 스크립트가 APPROVED 상태여야 함 (409)
- 해당 교육이 EXPIRED면 404

**동작**:
1. 검증 통과 시 스크립트 상태를 PUBLISHED로 변경
2. KB Index Job 실행 (비동기)
3. kb_index_status=PENDING 반환

**재발행 시**:
- 이전 버전은 KB에서 삭제되어 검색 제외
- 최신 버전 1개만 ACTIVE
""",
)
async def publish_video(
    video_id: str,
    user_id: str = "reviewer",
    user_role: str = "REVIEWER",
    service=Depends(get_render_service),
):
    """영상 발행 + KB 적재."""
    # REVIEWER 역할 검증
    verify_reviewer_role(user_role)

    # EXPIRED 교육 차단
    ensure_education_not_expired(video_id)

    # 1. 렌더 잡 SUCCEEDED 확인
    succeeded_job = service._job_store.get_succeeded_by_video_id(video_id)
    if not succeeded_job:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason_code": "RENDER_NOT_SUCCEEDED",
                "message": f"SUCCEEDED 상태의 렌더 잡이 없습니다: video_id={video_id}",
            },
        )

    # 2. APPROVED 스크립트 확인
    script = service.get_script(succeeded_job.script_id)
    if not script:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason_code": "SCRIPT_NOT_FOUND",
                "message": f"스크립트를 찾을 수 없습니다: script_id={succeeded_job.script_id}",
            },
        )

    if not script.is_approved():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason_code": "SCRIPT_NOT_APPROVED",
                "message": f"스크립트가 승인되지 않았습니다: status={script.status.value}",
            },
        )

    # 3. 스크립트 상태를 PUBLISHED로 변경
    script.status = ScriptStatus.PUBLISHED
    script.kb_index_status = KBIndexStatus.PENDING
    service._script_store.save(script)

    logger.info(
        f"Video publish initiated: video_id={video_id}, script_id={script.script_id}"
    )

    # 4. KB Index Job 실행 (비동기)
    import asyncio
    asyncio.create_task(_run_kb_indexing(video_id, script))

    return PublishResponse(
        video_id=video_id,
        script_id=script.script_id,
        status=script.status.value,
        kb_index_status=script.kb_index_status.value,
        message="영상 발행이 시작되었습니다. KB 인덱싱이 진행 중입니다.",
    )


async def _run_kb_indexing(video_id: str, script) -> None:
    """KB 인덱싱 실행 (백그라운드)."""
    from datetime import datetime

    kb_service = get_kb_index_service()

    try:
        # 인덱싱 상태 RUNNING으로 변경
        script.kb_index_status = KBIndexStatus.RUNNING

        # 인덱싱 실행
        result = await kb_service.index_published_video(
            video_id=video_id,
            script=script,
            course_type="TRAINING",
        )

        # 결과 반영
        script.kb_index_status = result
        if result == KBIndexStatus.SUCCEEDED:
            script.kb_indexed_at = datetime.utcnow()
            script.kb_last_error = None
            logger.info(f"KB indexing succeeded: video_id={video_id}")
        else:
            script.kb_last_error = "Indexing failed"
            logger.error(f"KB indexing failed: video_id={video_id}")

    except Exception as e:
        script.kb_index_status = KBIndexStatus.FAILED
        script.kb_last_error = str(e)
        logger.exception(f"KB indexing error: video_id={video_id}, error={e}")


@router.get(
    "/videos/{video_id}/kb-status",
    response_model=KBIndexStatusResponse,
    summary="KB 인덱스 상태 조회",
    description="영상의 KB 인덱스 상태를 조회합니다.",
)
async def get_kb_index_status(
    video_id: str,
    service=Depends(get_render_service),
):
    """KB 인덱스 상태 조회."""
    # 최신 성공 잡 조회
    succeeded_job = service._job_store.get_succeeded_by_video_id(video_id)
    if not succeeded_job:
        return KBIndexStatusResponse(
            video_id=video_id,
            script_id=None,
            kb_index_status=KBIndexStatus.NOT_INDEXED.value,
            kb_indexed_at=None,
            kb_document_status=None,
            kb_last_error=None,
        )

    # 스크립트 조회
    script = service.get_script(succeeded_job.script_id)
    if not script:
        return KBIndexStatusResponse(
            video_id=video_id,
            script_id=succeeded_job.script_id,
            kb_index_status=KBIndexStatus.NOT_INDEXED.value,
            kb_indexed_at=None,
            kb_document_status=None,
            kb_last_error=None,
        )

    return KBIndexStatusResponse(
        video_id=video_id,
        script_id=script.script_id,
        kb_index_status=script.kb_index_status.value,
        kb_indexed_at=script.kb_indexed_at.isoformat() if script.kb_indexed_at else None,
        kb_document_status=script.kb_document_status.value if script.kb_document_status else None,
        kb_last_error=script.kb_last_error,
    )
