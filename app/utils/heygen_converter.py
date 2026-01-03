"""
HeyGen JSON Converter

- Backend 표준 VideoScript JSON → HeyGen Scene JSON
- narration / on_screen_text / duration 매핑
"""

from typing import Dict, Any, List


# ============================================================
# 기본 설정 (프로젝트에 맞게 수정 가능)
# ============================================================

DEFAULT_AVATAR_ID = "avatar_anna"
DEFAULT_VOICE_ID = "ko_female_1"
DEFAULT_BACKGROUND = "office_modern"
DEFAULT_LAYOUT = "avatar_left_text_right"


# ============================================================
# Scene 변환
# ============================================================

def convert_scene_to_heygen(
    scene: Dict[str, Any],
    chapter_meta: Dict[str, Any],
    scene_order: int,
) -> Dict[str, Any]:
    """
    단일 Scene → HeyGen Scene 변환
    """
    return {
        "scene_order": scene_order,
        "avatar": {
            "avatar_id": DEFAULT_AVATAR_ID,
            "voice_id": DEFAULT_VOICE_ID,
        },
        "script": {
            "type": "text",
            "content": scene.get("narration", ""),
        },
        "on_screen_text": scene.get("on_screen_text"),
        "duration_sec": scene.get("duration_sec", 60),
        "background": DEFAULT_BACKGROUND,
        "layout": DEFAULT_LAYOUT,
        # 🔹 backend 추적용 metadata
        "metadata": {
            "chapter_id": chapter_meta.get("chapter_id"),
            "chapter_title": chapter_meta.get("title"),
            "scene_id": scene.get("scene_id"),
        },
    }


# ============================================================
# 전체 VideoScript → HeyGen 변환
# ============================================================

def convert_video_script_to_heygen(
    video_script: Dict[str, Any],
) -> Dict[str, Any]:
    """
    VideoScript JSON → HeyGen JSON
    """
    heygen_scenes: List[Dict[str, Any]] = []

    scene_order = 1

    for chapter in video_script.get("chapters", []):
        chapter_meta = {
            "chapter_id": chapter.get("chapter_id"),
            "title": chapter.get("title"),
        }

        for scene in chapter.get("scenes", []):
            heygen_scene = convert_scene_to_heygen(
                scene=scene,
                chapter_meta=chapter_meta,
                scene_order=scene_order,
            )
            heygen_scenes.append(heygen_scene)
            scene_order += 1

    return {
        "project_type": "heygen_video",
        "scenes": heygen_scenes,
    }
