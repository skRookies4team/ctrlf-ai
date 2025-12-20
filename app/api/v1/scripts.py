"""
스크립트 API

영상 스크립트 CRUD 및 편집 API 엔드포인트.

엔드포인트:
- POST /api/scripts : 스크립트 생성
- GET /api/scripts/{script_id} : 스크립트 조회
- POST /api/videos/{video_id}/scripts/generate : 스크립트 자동 생성
- GET /api/scripts/{script_id}/editor : 편집용 뷰 조회
- PATCH /api/scripts/{script_id}/editor : 씬 부분 수정
"""

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, status

from app.clients.backend_client import get_backend_callback_client
from app.core.logging import get_logger
from app.models.script_editor import (
    ScriptEditorPatchRequest,
    ScriptEditorPatchResponse,
    ScriptEditorView,
    apply_scene_updates,
    script_to_editor_view,
)
from app.models.video_render import (
    ScriptCreateRequest,
    ScriptGenerateRequest,
    ScriptGenerateResponse,
    ScriptResponse,
    ScriptStatus,
)
from app.services.education_catalog_service import get_education_catalog_service
from app.services.video_render_service import get_video_render_service
from app.services.video_renderer_mvp import get_mvp_video_renderer
from app.services.video_script_generation_service import (
    ScriptGenerationOptions,
    get_video_script_generation_service,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["Scripts"])


# =============================================================================
# Dependencies
# =============================================================================


def get_render_service():
    """VideoRenderService 의존성."""
    service = get_video_render_service()
    if service._renderer is None:
        service.set_renderer(get_mvp_video_renderer())
    return service


def ensure_education_not_expired(video_id: str) -> None:
    """교육이 만료되지 않았는지 확인.

    Args:
        video_id: 비디오 ID (교육 ID로 매핑)

    Raises:
        HTTPException: 교육이 만료된 경우 404
    """
    catalog = get_education_catalog_service()
    education_id = video_id

    if catalog.is_expired(education_id):
        logger.warning(f"Script operation blocked: education expired, education_id={education_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason_code": "EDU_EXPIRED",
                "message": f"해당 교육({education_id})은 만료되어 작업이 불가합니다.",
            },
        )


# =============================================================================
# Script CRUD APIs
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
# Script Generation API
# =============================================================================


@router.post(
    "/videos/{video_id}/scripts/generate",
    response_model=ScriptGenerateResponse,
    summary="스크립트 자동 생성",
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
    """교육 원문에서 스크립트 자동 생성."""
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
# Script Editor APIs
# =============================================================================


@router.get(
    "/scripts/{script_id}/editor",
    response_model=ScriptEditorView,
    summary="스크립트 편집용 뷰 조회",
    description="""
스크립트를 편집용 DTO로 반환합니다.

**응답**:
- script_id, video_id, title, language, status
- scenes[]: scene_id, order, chapter_id, chapter_title, scene_title, narration_text, subtitle_text, has_audio, has_captions
- total_scenes: 전체 씬 수
- editable: 편집 가능 여부 (DRAFT만 true)
""",
)
async def get_script_editor_view(
    script_id: str,
    service=Depends(get_render_service),
):
    """스크립트를 편집용 DTO로 반환."""
    script = service.get_script(script_id)
    if not script:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason_code": "SCRIPT_NOT_FOUND",
                "message": f"스크립트를 찾을 수 없습니다: {script_id}",
            },
        )

    return script_to_editor_view(script)


@router.patch(
    "/scripts/{script_id}/editor",
    response_model=ScriptEditorPatchResponse,
    summary="스크립트 씬 편집",
    description="""
특정 씬의 narration/subtitle을 부분 수정합니다.

**요청**:
- updates[]: scene_id, narration_text (optional), subtitle_text (optional)

**무효화 규칙**:
- narration_text 변경 시: 해당 씬의 오디오 + 캡션 산출물 무효화
- subtitle_text만 변경 시: 해당 씬의 캡션 산출물만 무효화

**권한 제약**:
- status == DRAFT인 스크립트만 편집 가능
- APPROVED/PUBLISHED는 409 에러
""",
)
async def patch_script_editor(
    script_id: str,
    request: ScriptEditorPatchRequest,
    service=Depends(get_render_service),
):
    """스크립트 씬 편집."""
    # 스크립트 조회
    script = service.get_script(script_id)
    if not script:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason_code": "SCRIPT_NOT_FOUND",
                "message": f"스크립트를 찾을 수 없습니다: {script_id}",
            },
        )

    # 상태 제약: DRAFT만 편집 가능
    if script.status != ScriptStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason_code": "NOT_EDITABLE",
                "message": f"DRAFT 상태의 스크립트만 편집할 수 있습니다. 현재 상태: {script.status.value}",
            },
        )

    # 업데이트 적용
    updated_json, updated_ids, audio_count, caption_count = apply_scene_updates(
        script.raw_json,
        request.updates,
    )

    if not updated_ids:
        return ScriptEditorPatchResponse(
            success=True,
            updated_scene_ids=[],
            invalidated_audio_count=0,
            invalidated_caption_count=0,
            message="변경된 씬이 없습니다.",
        )

    # raw_json 업데이트 후 저장
    script.raw_json = updated_json
    service._script_store.save(script)

    logger.info(
        f"Script editor patch applied: script_id={script_id}, "
        f"updated_scenes={updated_ids}, "
        f"invalidated_audio={audio_count}, invalidated_caption={caption_count}"
    )

    return ScriptEditorPatchResponse(
        success=True,
        updated_scene_ids=updated_ids,
        invalidated_audio_count=audio_count,
        invalidated_caption_count=caption_count,
        message=f"{len(updated_ids)}개 씬이 수정되었습니다. 다음 렌더링 시 오디오/캡션이 재생성됩니다.",
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
