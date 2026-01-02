import json
from pathlib import Path
from typing import Dict, Any, List


# =========================
# 입력 / 출력 경로
# =========================
INPUT_PATH = Path("test_output_script/generated_script_직장내괴롭힘교육.cleaned.json")
OUTPUT_PATH = Path("test_output_script/backend_contract_script.json")

VIDEO_TITLE = "직장 내 괴롭힘 예방 교육"


# =========================
# 유틸
# =========================
def estimate_confidence(scene: Dict[str, Any]) -> float:
    """
    confidenceScore는 현재 모델 추정치이므로
    임시로 안정적인 고정값 사용
    """
    return 0.93


def build_source_refs(scene: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    sourceRefs가 있으면 변환,
    없으면 빈 배열
    """
    refs = []

    for r in scene.get("source_chunks", []) or scene.get("sourceRefs", []) or []:
        refs.append(
            {
                "documentId": r.get("documentId") or r.get("doc_id") or "unknown-doc",
                "chunkIndex": r.get("chunkIndex") or r.get("chunk_id") or 0,
            }
        )

    return refs


def convert(script: Dict[str, Any]) -> Dict[str, Any]:
    backend = {
        "title": VIDEO_TITLE,
        "totalDurationSec": 0,
        "chapters": [],
    }

    total_duration = 0

    for ch_idx, ch in enumerate(script.get("chapters", [])):
        chapter_duration = 0

        backend_chapter = {
            "chapterIndex": ch_idx,
            "title": ch.get("title", ""),
            "durationSec": 0,
            "scenes": [],
        }

        for sc in ch.get("scenes", []):
            narration = (sc.get("narration") or "").strip()
            if not narration:
                continue

            duration = int(sc.get("duration_sec") or sc.get("durationSec") or 0)
            chapter_duration += duration

            scene_index = sc.get("scene_id", sc.get("sceneIndex", 0))

            backend_scene = {
                "sceneIndex": scene_index,
                "purpose": "hook" if scene_index == 0 else "concept",
                "narration": narration,
                "caption": sc.get("on_screen_text") or sc.get("caption"),
                "visual": sc.get("visual") or "avatar_talking",
                "durationSec": duration,
                "confidenceScore": estimate_confidence(sc),
                "sourceRefs": build_source_refs(sc),
            }

            backend_chapter["scenes"].append(backend_scene)

        backend_chapter["durationSec"] = chapter_duration
        total_duration += chapter_duration

        backend["chapters"].append(backend_chapter)

    backend["totalDurationSec"] = total_duration
    return backend


# =========================
# main
# =========================
def main():
    raw = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    backend_script = convert(raw)

    OUTPUT_PATH.write_text(
        json.dumps(backend_script, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"✅ 백엔드 계약 스키마 변환 완료: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
