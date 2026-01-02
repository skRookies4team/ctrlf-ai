import sys
import os
import json
import asyncio
import subprocess
import argparse
from pathlib import Path

# ============================================================
# 프로젝트 루트 세팅
# ============================================================
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv
import httpx

from app.utils.script_enhance import enhance_video_script_for_video
from app.utils.heygen_payload import (
    build_heygen_video_inputs,
    build_heygen_generate_payload,
)
from app.clients.heygen_client import HeyGenClient


# ============================================================
# 설정
# ============================================================
INPUT_SCRIPT_PATH = Path("test_output_script/generated_script_직장내괴롭힘교육.cleaned.json")
OUTPUT_DIR = Path("test_output_script/chapters")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

POLL_INTERVAL_SEC = 10
MAX_POLLS = 180  # 약 30분
THUMB_AT_SEC = 1.0


# ============================================================
# argparse
# ============================================================
def parse_args():
    parser = argparse.ArgumentParser(description="HeyGen 챕터별 영상 생성")
    parser.add_argument(
        "--chapter",
        type=int,
        help="생성할 챕터 번호 (1부터 시작)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="이미 생성된 챕터는 스킵하고 나머지만 생성",
    )
    return parser.parse_args()


# ============================================================
# 유틸
# ============================================================
def build_chapter_script(chapter: dict) -> dict:
    return {
        "chapters": [
            {
                "chapter_id": chapter["chapter_id"],
                "title": chapter["title"],
                "scenes": chapter["scenes"],
            }
        ]
    }


async def download_file(url: str, out_path: Path, timeout: float = 300.0):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        out_path.write_bytes(r.content)


def make_thumbnail_with_ffmpeg(video_path: Path, thumb_path: Path, at_sec: float):
    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(at_sec),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        str(thumb_path),
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


# ============================================================
# 단일 챕터 렌더링
# ============================================================
async def render_single_chapter(
    client: HeyGenClient,
    chapter: dict,
    idx: int,
    avatar_id: str,
    voice_id: str,
    bg_type: str,
    bg_value: str,
    width: int,
    height: int,
):
    chapter_no = f"{idx:02d}"
    chapter_title = chapter.get("title", f"Chapter {idx}")

    print(f"\n🎬 [CHAPTER {chapter_no}] {chapter_title}")

    # 1️⃣ 챕터 스크립트 → 인트로 강화
    chapter_script = build_chapter_script(chapter)
    enhanced = enhance_video_script_for_video(chapter_script)

    enhanced_path = OUTPUT_DIR / f"chapter_{chapter_no}.enhanced.json"
    enhanced_path.write_text(json.dumps(enhanced, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2️⃣ HeyGen payload
    video_inputs = build_heygen_video_inputs(
        enhanced,
        avatar_id=avatar_id,
        voice_id=voice_id,
        bg_type=bg_type,
        bg_value=bg_value,
    )
    payload = build_heygen_generate_payload(video_inputs, width=width, height=height)

    payload_path = OUTPUT_DIR / f"chapter_{chapter_no}.heygen_payload.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 3️⃣ 렌더링 요청
    video_id = await client.generate_video(payload)
    print(f"✅ [CHAPTER {chapter_no}] video_id = {video_id}")

    status_path = OUTPUT_DIR / f"chapter_{chapter_no}.status.json"

    # 4️⃣ 상태 폴링
    for i in range(MAX_POLLS):
        try:
            status = await client.get_video_status(video_id)
        except Exception as e:
            print(f"⚠️ status error (retry): {e}")
            await asyncio.sleep(POLL_INTERVAL_SEC)
            continue

        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

        data = status.get("data", {})
        s = (data.get("status") or "").lower()
        print(f"[CHAPTER {chapter_no}] [{i+1}/{MAX_POLLS}] status = {s}")

        if s == "completed":
            video_url = data.get("video_url")
            thumbnail_url = data.get("thumbnail_url")

            print(f"🎉 [CHAPTER {chapter_no}] 완료")
            print(f"📌 video_url = {video_url}")

            # 결과 저장
            result_path = OUTPUT_DIR / f"chapter_{chapter_no}.result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "chapter": chapter_title,
                        "video_id": video_id,
                        "video_url": video_url,
                        "thumbnail_url": thumbnail_url,
                        "raw": status,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            # =========================
            # 썸네일 생성
            # =========================
            thumb_path = OUTPUT_DIR / f"chapter_{chapter_no}.thumbnail.png"

            # (A) HeyGen 썸네일 우선
            if thumbnail_url:
                try:
                    await download_file(thumbnail_url, thumb_path)
                    print(f"🖼️ thumbnail 저장: {thumb_path}")
                    return
                except Exception:
                    pass

            # (B) mp4 다운로드 후 ffmpeg
            if video_url:
                mp4_path = OUTPUT_DIR / f"chapter_{chapter_no}.mp4"
                await download_file(video_url, mp4_path)
                make_thumbnail_with_ffmpeg(mp4_path, thumb_path, THUMB_AT_SEC)
                print(f"🖼️ thumbnail 생성: {thumb_path}")
            return

        if s == "failed":
            print(f"❌ [CHAPTER {chapter_no}] 실패")
            return

        await asyncio.sleep(POLL_INTERVAL_SEC)

    print(f"⚠️ [CHAPTER {chapter_no}] polling timeout")


# ============================================================
# main
# ============================================================
async def main():
    args = parse_args()
    load_dotenv(ROOT_DIR / ".env")

    api_key = os.getenv("HEYGEN_API_KEY", "").strip()
    avatar_id = os.getenv("HEYGEN_AVATAR_ID", "").strip()
    voice_id = os.getenv("HEYGEN_VOICE_ID", "").strip()

    if not api_key or not avatar_id or not voice_id:
        raise RuntimeError("HEYGEN_API_KEY / AVATAR_ID / VOICE_ID 누락")

    bg_type = os.getenv("HEYGEN_BG_TYPE", "color")
    bg_value = os.getenv("HEYGEN_BG_VALUE", "#FAFAFA")
    width = int(os.getenv("HEYGEN_DIM_W", "1280"))
    height = int(os.getenv("HEYGEN_DIM_H", "720"))

    script = json.loads(INPUT_SCRIPT_PATH.read_text(encoding="utf-8"))
    chapters = script.get("chapters", [])
    if not chapters:
        raise RuntimeError("chapters 없음")

    client = HeyGenClient(api_key=api_key)

    for idx, chapter in enumerate(chapters, start=1):
        chapter_no = f"{idx:02d}"
        result_path = OUTPUT_DIR / f"chapter_{chapter_no}.result.json"

        # 특정 챕터만
        if args.chapter is not None and idx != args.chapter:
            continue

        # resume 모드
        if args.resume and result_path.exists():
            print(f"⏭️ [CHAPTER {chapter_no}] 이미 생성됨 → 스킵")
            continue

        await render_single_chapter(
            client=client,
            chapter=chapter,
            idx=idx,
            avatar_id=avatar_id,
            voice_id=voice_id,
            bg_type=bg_type,
            bg_value=bg_value,
            width=width,
            height=height,
        )


if __name__ == "__main__":
    asyncio.run(main())
