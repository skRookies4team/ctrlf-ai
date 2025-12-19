"""
Phase 42: Script Editor Models

영상 스크립트 편집을 위한 API 모델.

주요 모델:
- SceneEditorItem: 씬 편집용 항목
- ScriptEditorView: 편집용 스크립트 뷰
- ScriptEditorPatchRequest: 씬 업데이트 요청
- ScriptEditorPatchResponse: 씬 업데이트 응답
"""

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.models.video_render import ScriptStatus, VideoScript


# =============================================================================
# Phase 42: Script Editor API Models
# =============================================================================


class SceneEditorItem(BaseModel):
    """씬 편집용 항목.

    EditorView에서 씬 정보를 표현합니다.
    """
    scene_id: int = Field(..., description="씬 ID")
    order: int = Field(..., description="씬 순서 (1-based)")
    chapter_id: int = Field(..., description="소속 챕터 ID")
    chapter_title: Optional[str] = Field(None, description="챕터 제목")
    scene_title: Optional[str] = Field(None, description="씬 제목 (on_screen_text)")
    narration_text: str = Field(..., description="나레이션 텍스트")
    subtitle_text: Optional[str] = Field(None, description="자막 텍스트 (없으면 narration 기반)")
    # 렌더 산출물 상태
    has_audio: bool = Field(default=False, description="오디오 생성 여부")
    has_captions: bool = Field(default=False, description="캡션 타임라인 생성 여부")


class ScriptEditorView(BaseModel):
    """스크립트 편집용 뷰.

    프론트엔드/백엔드가 raw_json을 직접 다루지 않고
    씬 중심의 편집 인터페이스를 사용할 수 있도록 합니다.

    GET /api/scripts/{script_id}/editor
    """
    script_id: str = Field(..., description="스크립트 ID")
    video_id: str = Field(..., description="비디오 ID")
    title: Optional[str] = Field(None, description="스크립트 제목 (첫 챕터 제목)")
    language: str = Field(default="ko", description="언어 코드")
    status: str = Field(..., description="스크립트 상태 (DRAFT, APPROVED, etc.)")
    scenes: List[SceneEditorItem] = Field(default_factory=list, description="씬 목록")
    total_scenes: int = Field(default=0, description="전체 씬 수")
    editable: bool = Field(default=True, description="편집 가능 여부 (DRAFT만 true)")


class SceneUpdateItem(BaseModel):
    """씬 업데이트 항목.

    PATCH 요청에서 개별 씬 업데이트를 표현합니다.
    """
    scene_id: int = Field(..., description="수정할 씬 ID")
    narration_text: Optional[str] = Field(None, description="새 나레이션 텍스트 (null이면 변경 안함)")
    subtitle_text: Optional[str] = Field(None, description="새 자막 텍스트 (null이면 변경 안함)")


class ScriptEditorPatchRequest(BaseModel):
    """스크립트 편집 PATCH 요청.

    PATCH /api/scripts/{script_id}/editor

    씬별로 narration_text, subtitle_text를 부분 수정합니다.
    """
    updates: List[SceneUpdateItem] = Field(..., min_length=1, description="업데이트할 씬 목록")


class ScriptEditorPatchResponse(BaseModel):
    """스크립트 편집 PATCH 응답.

    PATCH /api/scripts/{script_id}/editor
    """
    success: bool = Field(..., description="수정 성공 여부")
    updated_scene_ids: List[int] = Field(default_factory=list, description="수정된 씬 ID 목록")
    invalidated_audio_count: int = Field(default=0, description="오디오 무효화된 씬 수")
    invalidated_caption_count: int = Field(default=0, description="캡션 무효화된 씬 수")
    message: str = Field(default="", description="결과 메시지")


# =============================================================================
# Conversion Functions
# =============================================================================


def script_to_editor_view(script: VideoScript) -> ScriptEditorView:
    """VideoScript를 ScriptEditorView로 변환합니다.

    Args:
        script: 원본 스크립트

    Returns:
        ScriptEditorView: 편집용 뷰
    """
    raw_json = script.raw_json
    chapters = raw_json.get("chapters", [])

    scenes: List[SceneEditorItem] = []
    title = None
    scene_order = 1

    for chapter in chapters:
        chapter_id = chapter.get("chapter_id", 0)
        chapter_title = chapter.get("title", "")

        if title is None and chapter_title:
            title = chapter_title

        for scene in chapter.get("scenes", []):
            scene_id = scene.get("scene_id", scene_order)
            narration = scene.get("narration", "")
            on_screen_text = scene.get("on_screen_text", "")
            subtitle = scene.get("subtitle_text") or on_screen_text

            # 렌더 산출물 상태 체크
            has_audio = bool(scene.get("tts_audio_path") or scene.get("audio_path"))
            has_captions = bool(scene.get("captions") or scene.get("caption_timeline"))

            scenes.append(
                SceneEditorItem(
                    scene_id=scene_id,
                    order=scene_order,
                    chapter_id=chapter_id,
                    chapter_title=chapter_title,
                    scene_title=on_screen_text or None,
                    narration_text=narration,
                    subtitle_text=subtitle or None,
                    has_audio=has_audio,
                    has_captions=has_captions,
                )
            )
            scene_order += 1

    # DRAFT만 편집 가능
    editable = script.status == ScriptStatus.DRAFT

    return ScriptEditorView(
        script_id=script.script_id,
        video_id=script.video_id,
        title=title,
        language=raw_json.get("language", "ko"),
        status=script.status.value,
        scenes=scenes,
        total_scenes=len(scenes),
        editable=editable,
    )


def apply_scene_updates(
    raw_json: Dict[str, Any],
    updates: List[SceneUpdateItem],
) -> Tuple[Dict[str, Any], List[int], int, int]:
    """씬 업데이트를 적용하고 산출물을 무효화합니다.

    Args:
        raw_json: 원본 스크립트 JSON
        updates: 업데이트할 씬 목록

    Returns:
        Tuple[Dict, List[int], int, int]:
            - 수정된 raw_json
            - 수정된 씬 ID 목록
            - 오디오 무효화된 씬 수
            - 캡션 무효화된 씬 수
    """
    # 업데이트 맵 생성
    update_map = {u.scene_id: u for u in updates}

    updated_scene_ids = []
    invalidated_audio_count = 0
    invalidated_caption_count = 0

    chapters = raw_json.get("chapters", [])

    for chapter in chapters:
        for scene in chapter.get("scenes", []):
            scene_id = scene.get("scene_id")
            if scene_id not in update_map:
                continue

            update = update_map[scene_id]
            narration_changed = False
            subtitle_only_changed = False

            # narration_text 변경
            if update.narration_text is not None:
                old_narration = scene.get("narration", "")
                if old_narration != update.narration_text:
                    scene["narration"] = update.narration_text
                    narration_changed = True

            # subtitle_text 변경
            if update.subtitle_text is not None:
                old_subtitle = scene.get("subtitle_text") or scene.get("on_screen_text", "")
                if old_subtitle != update.subtitle_text:
                    scene["subtitle_text"] = update.subtitle_text
                    if not narration_changed:
                        subtitle_only_changed = True

            # 무효화 처리
            if narration_changed:
                # narration 변경: 오디오 + 캡션 모두 무효화
                _invalidate_audio_outputs(scene)
                _invalidate_caption_outputs(scene)
                invalidated_audio_count += 1
                invalidated_caption_count += 1
                updated_scene_ids.append(scene_id)

            elif subtitle_only_changed:
                # subtitle만 변경: 캡션만 무효화
                _invalidate_caption_outputs(scene)
                invalidated_caption_count += 1
                updated_scene_ids.append(scene_id)

    return raw_json, updated_scene_ids, invalidated_audio_count, invalidated_caption_count


def _invalidate_audio_outputs(scene: Dict[str, Any]) -> None:
    """씬의 오디오 산출물을 무효화합니다.

    Phase 40 기준 필드:
    - tts_audio_path: TTS 오디오 파일 경로
    - audio_path: 합성 오디오 파일 경로
    - audio_duration_sec: 오디오 길이
    - duration_sec: 씬 duration (오디오 기반)
    """
    audio_fields = [
        "tts_audio_path",
        "audio_path",
        "audio_duration_sec",
        "duration_sec",
    ]
    for field in audio_fields:
        if field in scene:
            scene[field] = None


def _invalidate_caption_outputs(scene: Dict[str, Any]) -> None:
    """씬의 캡션 산출물을 무효화합니다.

    Phase 40 기준 필드:
    - captions: 캡션 타임라인 JSON
    - caption_timeline: 캡션 타임라인 (대체 필드명)
    - srt_path: SRT 파일 경로
    """
    caption_fields = [
        "captions",
        "caption_timeline",
        "srt_path",
    ]
    for field in caption_fields:
        if field in scene:
            scene[field] = None
