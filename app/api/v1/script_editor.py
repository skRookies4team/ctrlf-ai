"""
Phase 42: Script Editor API

영상 스크립트 편집 API 엔드포인트.

엔드포인트:
- GET /api/scripts/{script_id}/editor: 편집용 뷰 조회
- PATCH /api/scripts/{script_id}/editor: 씬 부분 수정

권한:
- DRAFT 상태의 스크립트만 편집 가능
- APPROVED/PUBLISHED는 409 에러

무효화 규칙:
- narration_text 변경 시: 오디오 + 캡션 산출물 무효화
- subtitle_text만 변경 시: 캡션 산출물만 무효화
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.logging import get_logger
from app.models.script_editor import (
    ScriptEditorView,
    ScriptEditorPatchRequest,
    ScriptEditorPatchResponse,
    script_to_editor_view,
    apply_scene_updates,
)
from app.models.video_render import ScriptStatus
from app.services.video_render_service import get_video_render_service

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["Script Editor"])


# =============================================================================
# Dependencies
# =============================================================================


def get_render_service():
    """VideoRenderService 의존성."""
    return get_video_render_service()


# =============================================================================
# Phase 42: Script Editor APIs
# =============================================================================


@router.get(
    "/scripts/{script_id}/editor",
    response_model=ScriptEditorView,
    summary="Phase 42: 스크립트 편집용 뷰 조회",
    description="""
스크립트를 편집용 DTO로 반환합니다.

**응답**:
- script_id, video_id, title, language, status
- scenes[]: scene_id, order, chapter_id, chapter_title, scene_title, narration_text, subtitle_text, has_audio, has_captions
- total_scenes: 전체 씬 수
- editable: 편집 가능 여부 (DRAFT만 true)

**사용 예시**:
프론트엔드에서 raw_json을 직접 다루지 않고, 씬 중심의 편집 인터페이스를 사용할 수 있습니다.
""",
)
async def get_script_editor_view(
    script_id: str,
    service=Depends(get_render_service),
):
    """스크립트를 편집용 DTO로 반환 (Phase 42)."""
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
    summary="Phase 42: 스크립트 씬 편집",
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

**동작**:
- 실제 재렌더(FFmpeg)는 자동 실행하지 않음
- 다음 Render Job 실행 시 자동으로 재생성됨 (Phase 40)
""",
)
async def patch_script_editor(
    script_id: str,
    request: ScriptEditorPatchRequest,
    service=Depends(get_render_service),
):
    """스크립트 씬 편집 (Phase 42)."""
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
