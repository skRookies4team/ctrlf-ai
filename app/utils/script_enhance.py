from __future__ import annotations

import re
from typing import Dict, Any, List


# ============================================================
# 공통 유틸
# ============================================================
def _clean_text(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s


# ============================================================
# 챕터에서 bullet 후보 추출
# ============================================================
def _pick_bullets_from_chapter(
    chapter: Dict[str, Any],
    max_items: int = 3,
) -> List[str]:
    """
    챕터 초반 씬들을 훑어서
    on_screen_text / narration 기반으로 bullet 문장 추출
    """
    bullets: List[str] = []
    scenes = chapter.get("scenes", []) or []

    for sc in scenes[:6]:
        ost = _clean_text(sc.get("on_screen_text") or "")
        nar = _clean_text(sc.get("narration") or "")

        cand = ost if ost else nar
        if not cand:
            continue

        # (약 1분) 같은 설명 제거
        cand = re.sub(r"\(.*?\)", "", cand).strip()

        # 너무 길면 자르기
        if len(cand) > 36:
            cand = cand[:36].rstrip() + "…"

        if cand and cand not in bullets:
            bullets.append(cand)

        if len(bullets) >= max_items:
            break

    # 하나도 못 뽑았으면 기본값
    if not bullets:
        bullets = [
            "핵심 개념 정리",
            "주요 사례 이해",
            "대응 절차 확인",
        ]

    return bullets[:max_items]


# ============================================================
# 챕터 인트로 Scene 생성 (교육 슬라이드 느낌)
# ============================================================
def build_chapter_intro_scene(
    chapter_title: str,
    chapter: Dict[str, Any],
) -> Dict[str, Any]:
    title = _clean_text(chapter_title) or "챕터"
    bullets = _pick_bullets_from_chapter(chapter, max_items=3)

    # 🔒 bullet 개수 안전 처리
    bullet_lines: List[str] = []
    for b in bullets:
        bullet_lines.append(f"• {b}")

    # 그래도 비면 fallback
    if not bullet_lines:
        bullet_lines = [
            "• 핵심 개념 정리",
            "• 주요 사례 이해",
            "• 대응 절차 확인",
        ]

    on_screen_text = (
        f"📌 {title}\n"
        f"오늘 배울 내용\n"
        + "\n".join(bullet_lines)
        + "\n\n지금부터 시작합니다."
    )

    narration = (
        f"이번 챕터에서는 {title}에 대해 핵심 내용을 정리합니다. "
        f"지금부터 함께 살펴보겠습니다."
    )

    return {
        "scene_id": 0,
        "narration": narration,
        "on_screen_text": on_screen_text,
        "duration_sec": 7.0,  # 인트로는 6~8초 권장
    }


# ============================================================
# narration 기반 duration 자동 추정 (한국어 기준)
# ============================================================
def _estimate_duration_sec_ko(narration: str) -> float:
    """
    한국어 기준 대략 150자/분 ≈ 2.5자/초
    """
    t = _clean_text(narration)
    if not t:
        return 4.0

    sec = len(t) / 2.5
    sec = max(4.0, min(60.0, sec))

    # 보기 좋게 0.5초 단위
    return round(sec * 2) / 2


# ============================================================
# 메인: VideoScript 강화
# ============================================================
def enhance_video_script_for_video(
    script: Dict[str, Any],
) -> Dict[str, Any]:
    """
    - 챕터 인트로 Scene 자동 삽입 (교육 슬라이드 느낌)
    - scene_id 재정렬
    - duration_sec 없는 씬 자동 보정
    """
    out: Dict[str, Any] = {"chapters": []}
    chapters = script.get("chapters", []) or []

    for ch in chapters:
        ch_id = ch.get("chapter_id")
        title = ch.get("title", "")
        scenes = ch.get("scenes", []) or []

        # 1️⃣ 인트로 씬 생성
        intro_scene = build_chapter_intro_scene(title, ch)

        new_scenes: List[Dict[str, Any]] = [intro_scene]

        # 2️⃣ 기존 씬 처리
        next_scene_id = 1
        for sc in scenes:
            narration = sc.get("narration", "") or ""
            on_screen_text = sc.get("on_screen_text", None)
            duration_sec = sc.get("duration_sec")

            if duration_sec is None:
                duration_sec = _estimate_duration_sec_ko(narration)

            new_scenes.append(
                {
                    "scene_id": next_scene_id,
                    "narration": narration,
                    "on_screen_text": on_screen_text,
                    "duration_sec": duration_sec,
                }
            )
            next_scene_id += 1

        out["chapters"].append(
            {
                "chapter_id": ch_id,
                "title": title,
                "scenes": new_scenes,
            }
        )

    return out
