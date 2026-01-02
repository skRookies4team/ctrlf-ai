"""
Video Script Narration Cleanup Utility

- LLM 생성 스크립트를 영상/TTS 친화적으로 정규화
- JSON 구조 유지 (chapter / scene / id 보존)
"""

import re
from typing import Dict, Any


# ============================================================
# 정규식 패턴
# ============================================================

# (약 1분), (약 30초) 등 메타 시간 표현
TIME_META_PATTERN = re.compile(
    r"\(\s*약\s*\d+\s*(분|초)\s*\)", re.IGNORECASE
)

# 불필요한 인삿말 (문장 맨 앞)
GREETING_PREFIX_PATTERN = re.compile(
    r"^(안녕하세요[!！.]?\s*)",
    re.IGNORECASE,
)

# 중복 공백
MULTI_SPACE_PATTERN = re.compile(r"\s{2,}")

# 문장 끝 불필요한 공백
TRAILING_SPACE_PATTERN = re.compile(r"\s+([.!?])")


# ============================================================
# narration 클린업
# ============================================================

def clean_narration(text: str) -> str:
    """
    narration 텍스트 정규화

    Rules:
    - 시간 메타 문구 제거
    - 불필요한 인삿말 제거
    - 공백 정리
    """
    if not text:
        return text

    cleaned = text.strip()

    # 1. 시간 메타 제거
    cleaned = TIME_META_PATTERN.sub("", cleaned)

    # 2. 인삿말 제거
    cleaned = GREETING_PREFIX_PATTERN.sub("", cleaned)

    # 3. 문장부호 앞 공백 제거
    cleaned = TRAILING_SPACE_PATTERN.sub(r"\1", cleaned)

    # 4. 중복 공백 정리
    cleaned = MULTI_SPACE_PATTERN.sub(" ", cleaned)

    return cleaned.strip()


# ============================================================
# 전체 VideoScript JSON 클린업
# ============================================================

def cleanup_video_script(script_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    VideoScript JSON 전체 클린업

    - narration만 정규화
    - 나머지 필드는 그대로 유지
    """
    chapters = script_json.get("chapters", [])

    for chapter in chapters:
        scenes = chapter.get("scenes", [])
        for scene in scenes:
            narration = scene.get("narration")
            if isinstance(narration, str):
                scene["narration"] = clean_narration(narration)

    return script_json
