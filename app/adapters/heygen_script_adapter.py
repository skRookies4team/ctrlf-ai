"""
HeyGen 스크립트 어댑터

내부 Video Script(JSON)를 HeyGen API 형식으로 변환합니다.
"""

from typing import Any, Dict, List, Optional

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def convert_to_heygen_script(video_script: Dict) -> Dict:
    """
    내부 Video Script(JSON)를 HeyGen 입력용 스크립트로 변환 (레거시 호환).

    Args:
        video_script: 내부 스크립트 JSON

    Returns:
        Dict: HeyGen 스크립트 형식
    """
    return convert_script_to_heygen_format(video_script)


def convert_script_to_heygen_format(
    script_data: Dict[str, Any],
    voice_id: Optional[str] = None,
    avatar_id: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    background_color: Optional[str] = None,
) -> Dict[str, Any]:
    """
    내부 스크립트 데이터를 HeyGen API 페이로드 형식으로 변환합니다.

    HeyGen API v2 형식:
    {
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": "...",
                    "voice": {
                        "type": "text",
                        "input_text": "...",
                        "voice_id": "..."
                    }
                },
                "background": {
                    "type": "color",
                    "value": "#FFFFFF"
                },
                "ratio": "16:9",
                "caption": {
                    "type": "text",
                    "text": "..."
                }
            }
        ]
    }

    Args:
        script_data: 내부 스크립트 데이터
        voice_id: HeyGen Voice ID (None이면 설정에서 가져옴)
        avatar_id: HeyGen Avatar ID (None이면 기본값 사용)
        width: 비디오 너비 (None이면 설정에서 가져옴)
        height: 비디오 높이 (None이면 설정에서 가져옴)
        background_color: 배경 색상 (None이면 기본값 사용)

    Returns:
        Dict: HeyGen API 페이로드
    """
    settings = get_settings()

    # 기본값 설정
    if voice_id is None:
        voice_id = settings.HEYGEN_VOICE_ID or "default_voice"
    
    if avatar_id is None:
        avatar_id = "default_avatar"
    
    if width is None:
        width = settings.VIDEO_WIDTH
    
    if height is None:
        height = settings.VIDEO_HEIGHT
    
    if background_color is None:
        background_color = "#FFFFFF"

    video_inputs: List[Dict[str, Any]] = []
    scene_counter = 0

    # 스크립트 구조 파싱
    chapters = script_data.get("chapters", [])
    if not chapters:
        # 레거시 형식 지원
        scenes = script_data.get("scenes", [])
        if scenes:
            chapters = [{"scenes": scenes}]

    for chapter_idx, chapter in enumerate(chapters, start=1):
        chapter_title = chapter.get("title", f"Chapter {chapter_idx}")
        scenes = chapter.get("scenes", [])

        for scene in scenes:
            narration = (scene.get("narration") or scene.get("script") or "").strip()
            if not narration:
                logger.warning(
                    f"Skipping empty scene: chapter={chapter_title}, "
                    f"scene_index={scene.get('sceneIndex', scene_counter)}"
                )
                continue

            caption = (
                scene.get("caption") or
                scene.get("subtitle") or
                scene.get("on_screen_text") or
                narration
            )

            # HeyGen v2 API 형식
            video_input = {
                "character": {
                    "type": "avatar",
                    "avatar_id": avatar_id,
                    "voice": {
                        "type": "text",
                        "input_text": narration,
                        "voice_id": voice_id,
                    },
                },
                "background": {
                    "type": "color",
                    "value": background_color,
                },
                "ratio": f"{width}:{height}",
                "caption": {
                    "type": "text",
                    "text": caption,
                },
            }

            # 선택적 필드 추가
            duration_sec = scene.get("duration_sec") or scene.get("durationSec")
            if duration_sec and duration_sec > 0:
                video_input["duration"] = duration_sec

            video_inputs.append(video_input)
            scene_counter += 1

    if not video_inputs:
        raise ValueError("No valid scenes found in script data")

    logger.info(
        f"Converted script to Heygen format: chapters={len(chapters)}, "
        f"scenes={len(video_inputs)}, voice_id={voice_id}"
    )

    return {
        "video_inputs": video_inputs,
        "ratio": f"{width}:{height}",
        "test": False,  # 프로덕션 모드
    }
