"""
Phase 27: Video Render API
Phase 28: Publish & KB Indexing API
Phase 31: Script Generation API
Phase 38: Render Job Start/Retry API

영상 생성 파이프라인 API 엔드포인트.

엔드포인트:
- POST /api/scripts : 스크립트 생성
- GET /api/scripts/{script_id} : 스크립트 조회
- POST /api/videos/{video_id}/scripts/generate : 스크립트 자동 생성 (Phase 31)
- POST /api/render-jobs/{job_id}/start : 렌더 잡 시작 (Phase 38)
- POST /api/render-jobs/{job_id}/retry : 렌더 잡 재시도 (Phase 38)
- POST /api/videos/{video_id}/publish : 영상 발행 + KB 적재 (Phase 28)
- GET /api/videos/{video_id}/kb-status : KB 인덱스 상태 조회 (Phase 28)

V2로 이전된 API (Phase 33 - video_render_phase33.py):
- POST /api/v2/videos/{video_id}/render-jobs : 렌더 잡 생성 (idempotent)
- GET /api/v2/videos/{video_id}/render-jobs : 잡 목록 조회
- GET /api/v2/videos/{video_id}/render-jobs/{job_id} : 잡 상세 조회
- POST /api/v2/videos/{video_id}/render-jobs/{job_id}/cancel : 잡 취소
- GET /api/v2/videos/{video_id}/assets/published : 발행된 에셋 조회

권한:
- 발행: REVIEWER 역할만 가능

Phase 26 연동:
- EXPIRED 교육에 대해 404 반환

Phase 28 정책:
- KB 적재는 PUBLISHED 상태의 영상만 대상
- 렌더 SUCCEEDED 후에만 발행 가능
"""

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.clients.backend_callback_client import get_backend_callback_client
from app.core.logging import get_logger
from app.models.video_render import (
    KBIndexStatus,
    KBIndexStatusResponse,
    PublishResponse,
    RenderJobStartResponse,
    ScriptCreateRequest,
    ScriptGenerateRequest,
    ScriptGenerateResponse,
    ScriptResponse,
    ScriptStatus,
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

        # 백엔드로 스크립트 생성 완료 콜백 (비동기, 실패해도 응답에 영향 없음)
        asyncio.create_task(
            _notify_script_complete(
                material_id=video_id,
                script_id=script.script_id,
                script_json=raw_json,
            )
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
# Phase 38: Job Start API
# =============================================================================


@router.post(
    "/render-jobs/{job_id}/start",
    response_model=RenderJobStartResponse,
    summary="Phase 38: 렌더 잡 시작 (스냅샷 기반)",
    description="""
렌더 잡을 시작합니다.

**Phase 38 동작**:
1. 백엔드에서 최신 render-spec 조회
2. render-spec을 잡에 스냅샷으로 저장
3. 파이프라인 실행 시작

**Idempotent**:
- 이미 render_spec_json이 있고 RUNNING/SUCCEEDED/FAILED 상태면 no-op
- 같은 잡에 여러 번 호출해도 안전

**재시도 정책**:
- retry 시에는 기존 스냅샷을 재사용
- 백엔드를 다시 호출하지 않음

**에러 코드**:
- JOB_NOT_FOUND: 잡이 존재하지 않음
- SCRIPT_FETCH_FAILED: 백엔드 render-spec 조회 실패
- EMPTY_RENDER_SPEC: render-spec에 씬이 없음
""",
)
async def start_render_job(
    job_id: str,
    service=Depends(get_render_service),
):
    """Phase 38: 렌더 잡 시작."""
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


@router.post(
    "/render-jobs/{job_id}/retry",
    response_model=RenderJobStartResponse,
    summary="Phase 38: 렌더 잡 재시도",
    description="""
실패한 렌더 잡을 재시도합니다.

**Phase 38 정책**:
- 기존에 저장된 render-spec 스냅샷을 재사용
- 백엔드를 다시 호출하지 않음

**조건**:
- render_spec_json이 있어야 함 (start_job 이후)
- RUNNING 상태가 아니어야 함
""",
)
async def retry_render_job(
    job_id: str,
    service=Depends(get_render_service),
):
    """Phase 38: 렌더 잡 재시도."""
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

    # 2. 스크립트 확인
    script = service.get_script(succeeded_job.script_id)
    if not script:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason_code": "SCRIPT_NOT_FOUND",
                "message": f"스크립트를 찾을 수 없습니다: script_id={succeeded_job.script_id}",
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


# =============================================================================
# Helper Functions
# =============================================================================


async def _notify_script_complete(
    material_id: str,
    script_id: str,
    script_json: dict,
) -> None:
    """스크립트 생성 완료를 백엔드에 알립니다 (백그라운드).

    Args:
        material_id: 자료 ID (video_id)
        script_id: 생성된 스크립트 ID
        script_json: 생성된 스크립트 JSON (raw_json)
    """
    import json
    from app.clients.backend_callback_client import get_backend_callback_client

    try:
        callback_client = get_backend_callback_client()
        await callback_client.notify_script_complete(
            material_id=material_id,
            script_id=script_id,
            script=json.dumps(script_json, ensure_ascii=False),
            version=1,
        )
    except Exception as e:
        # 콜백 실패는 로그만 남기고 무시 (스크립트 생성 자체는 성공)
        logger.warning(
            f"Script complete callback failed (non-blocking): "
            f"material_id={material_id}, script_id={script_id}, error={e}"
        )
