"""
HeyGen V2 Payload Builder

- Backend VideoScript(JSON) -> HeyGen API payload (v2/video/generate)
- Keeps on_screen_text as metadata (overlay is template-level in many cases)
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional


def build_heygen_video_inputs(
    video_script: Dict[str, Any],
    avatar_id: str,
    voice_id: str,
    avatar_style: str = "normal",
    bg_type: str = "color",
    bg_value: str = "#FAFAFA",
    max_input_text_chars: int = 4900,  # docs: < 5000 chars :contentReference[oaicite:5]{index=5}
) -> List[Dict[str, Any]]:
    """
    VideoScript chapters/scenes -> HeyGen video_inputs list
    """
    video_inputs: List[Dict[str, Any]] = []

    for ch in video_script.get("chapters", []):
        chapter_id = ch.get("chapter_id")
        chapter_title = ch.get("title")

        for sc in ch.get("scenes", []):
            narration = (sc.get("narration") or "").strip()
            if not narration:
                continue

            # 안전: input_text 5000자 제한 대응 :contentReference[oaicite:6]{index=6}
            if len(narration) > max_input_text_chars:
                narration = narration[:max_input_text_chars]

            video_inputs.append(
                {
                    "character": {
                        "type": "avatar",
                        "avatar_id": avatar_id,
                        "avatar_style": avatar_style,
                    },
                    "voice": {
                        "type": "text",
                        "input_text": narration,
                        "voice_id": voice_id,
                        # speed/pitch 같은 속성은 필요 시 여기에 추가 가능 (문서에서 조정 가능 언급) :contentReference[oaicite:7]{index=7}
                    },
                    "background": {
                        "type": bg_type,
                        "value": bg_value if bg_type == "color" else bg_value,
                    },
                    # 오버레이 필드는 문서 예시 기준으로 명확히 보이지 않아 metadata로만 보존 :contentReference[oaicite:8]{index=8}
                    "metadata": {
                        "chapter_id": chapter_id,
                        "chapter_title": chapter_title,
                        "scene_id": sc.get("scene_id"),
                        "scene_type": sc.get("scene_type"),
                        "on_screen_text": sc.get("on_screen_text"),
                        "duration_sec": sc.get("duration_sec"),
                    },
                }
            )

    return video_inputs


def build_heygen_generate_payload(
    video_inputs: List[Dict[str, Any]],
    width: int = 1280,
    height: int = 720,
) -> Dict[str, Any]:
    """
    Final payload for POST /v2/video/generate
    """
    payload: Dict[str, Any] = {"video_inputs": video_inputs}

    # 해상도 옵션(문서 가이드에 dimension 예시) :contentReference[oaicite:9]{index=9}
    payload["dimension"] = {"width": width, "height": height}
    return payload
