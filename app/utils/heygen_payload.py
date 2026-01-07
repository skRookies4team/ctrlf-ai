"""
HeyGen V2 Payload Builder (STRICT)

- ONLY supported fields for /v2/video/generate
- Any extra field will cause 400 BAD REQUEST
"""

from typing import Any, Dict, List


def build_heygen_video_inputs(
    video_script: Dict[str, Any],
    avatar_id: str,
) -> List[Dict[str, Any]]:
    """
    VideoScript chapters/scenes -> HeyGen v2 video_inputs
    """

    scenes: List[Dict[str, str]] = []

    for ch in video_script.get("chapters", []):
        for sc in ch.get("scenes", []):
            narration = (sc.get("narration") or sc.get("on_screen_text") or "").strip()
            if not narration:
                continue

            # HeyGen v2 text limit safety (< 5000 chars)
            scenes.append({"text": narration[:4900]})

    if not scenes:
        raise ValueError("No valid narration scenes found for HeyGen")

    return [
        {
            "avatar_id": avatar_id,
            "scenes": scenes,
        }
    ]


def build_heygen_generate_payload(
    video_inputs: List[Dict[str, Any]],
    width: int = 1280,
    height: int = 720,
) -> Dict[str, Any]:
    """
    Final payload for POST /v2/video/generate
    """
    return {
        "video_inputs": video_inputs,
        "dimension": {
            "width": width,
            "height": height,
        },
    }
