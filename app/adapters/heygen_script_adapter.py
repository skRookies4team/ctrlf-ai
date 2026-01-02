# app/adapters/heygen_script_adapter.py

from typing import Dict, List


def convert_to_heygen_script(video_script: Dict) -> Dict:
    """
    내부 Video Script(JSON)를 HeyGen 입력용 스크립트로 변환
    """

    heygen_scenes: List[Dict] = []
    scene_counter = 1

    video_title = video_script.get("title", "교육 영상")

    for chapter_idx, chapter in enumerate(video_script.get("chapters", []), start=1):
        chapter_title = chapter.get("title", f"Chapter {chapter_idx}")

        for scene in chapter.get("scenes", []):
            narration = (scene.get("narration") or "").strip()
            if not narration:
                continue

            heygen_scene = {
                "scene_id": scene_counter,

                # HeyGen 기본 아바타 / 나중에 옵션화 가능
                "avatar_id": "default_avatar",

                # 실제 TTS로 읽힐 본문
                "script": narration,

                # 선택: 화면 자막
                "subtitle": scene.get("caption") or scene.get("on_screen_text"),

                # 선택: 타이밍 힌트
                "duration_sec": scene.get("duration_sec", 0),

                # 디버깅 / 추적용 메타
                "meta": {
                    "chapter": chapter_title,
                    "purpose": scene.get("purpose"),
                    "source_chunks": scene.get("source_chunks", []),
                },
            }

            heygen_scenes.append(heygen_scene)
            scene_counter += 1

    return {
        "video_title": video_title,
        "total_scenes": len(heygen_scenes),
        "scenes": heygen_scenes,
    }
