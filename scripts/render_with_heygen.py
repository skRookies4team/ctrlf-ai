import sys
import os
import json
import argparse
import asyncio
from pathlib import Path

# ============================================================
# 프로젝트 루트 세팅
# ============================================================
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

from app.utils.script_enhance import enhance_video_script_for_video
from app.utils.heygen_payload import (
    build_heygen_video_inputs,
    build_heygen_generate_payload,
)
from app.clients.heygen_client import HeyGenClient


# ============================================================
# 설정
# ============================================================
INPUT_SCRIPT_PATH = Path(
    "test_output_script/generated_script_직장내괴롭힘교육.cleaned.json"
)
OUT_DIR = Path("test_output_script/chapters")
OUT_DIR.mkdir(parents=True, exist_ok=True)

POLL_INTERVAL_SEC = 10
MAX_POLLS = 180  # 약 30분


# ============================================================
# argparse
# ============================================================
def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--chapter",
        type=int,
        required=True,
        help="렌더링할 chapter index (1부터 시작)",
    )
    return parser.parse_args()


# ============================================================
# 챕터 단일 렌더링
# ============================================================
async def render_single_chapter(
    client: HeyGenClient,
    chapter_index_1based: int,
    avatar_id: str,
    voice_id: str,
    bg_type: str,
    bg_value: str,
    width: int,
    height: int,
):
    raw_script = json.loads(INPUT_SCRIPT_PATH.read_text(encoding="utf-8"))

    chapters = raw_script.get("chapters", [])
    ch_idx = chapter_index_1based - 1

    if ch_idx < 0 or ch_idx >= len(chapters):
        raise ValueError(f"Invalid chapter index: {chapter_index_1based}")

    chapter = chapters[ch_idx]

    print(f"\n🎬 [CHAPTER {chapter_index_1based:02d}] {chapter.get('title')}")

    # 👉 챕터 하나만 스크립트 형태로 감싸기
    chapter_script = {
        "chapters": [chapter]
    }

    # 1️⃣ 인트로 + duration 보정
    enhanced = enhance_video_script_for_video(
        chapter_script,
        safe_intro=True,   # ← IndexError 방지용
        max_total_sec=170  # ← HeyGen 180초 안전선
    )

    enhanced_path = OUT_DIR / f"chapter_{chapter_index_1based:02d}.enhanced.json"
    enhanced_path.write_text(
        json.dumps(enhanced, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 2️⃣ HeyGen payload
    video_inputs = build_heygen_video_inputs(
        enhanced,
        avatar_id=avatar_id,
        voice_id=voice_id,
        bg_type=bg_type,
        bg_value=bg_value,
    )

    payload = build_heygen_generate_payload(
        video_inputs,
        width=width,
        height=height,
    )

    payload_path = OUT_DIR / f"chapter_{chapter_index_1based:02d}.heygen.payload.json"
    payload_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 3️⃣ 생성 요청
    print("🚀 HeyGen 렌더링 요청...")
    video_id = await client.generate_video(payload)
    print(f"✅ video_id = {video_id}")

    # 4️⃣ 상태 폴링
    status_path = OUT_DIR / f"chapter_{chapter_index_1based:02d}.status.json"

    for i in range(MAX_POLLS):
        try:
            status = await client.get_video_status(video_id)
        except Exception as e:
            print(f"⚠️ status 조회 실패 (재시도): {e}")
            await asyncio.sleep(POLL_INTERVAL_SEC)
            continue

        status_path.write_text(
            json.dumps(status, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        data = status.get("data", {})
        s = (data.get("status") or "").lower()

        print(f"[{i+1}/{MAX_POLLS}] status = {s or 'unknown'}")

        if s == "completed":
            video_url = data.get("video_url")
            print("🎉 완료!")
            print(f"📌 video_url = {video_url}")

            result_path = OUT_DIR / f"chapter_{chapter_index_1based:02d}.result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "chapter": chapter_index_1based,
                        "video_id": video_id,
                        "video_url": video_url,
                        "raw": status,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            return

        if s == "failed":
            print("❌ 렌더링 실패")
            print(status)
            return

        await asyncio.sleep(POLL_INTERVAL_SEC)

    print("⚠️ 폴링 횟수 초과. 상태 파일 확인:", status_path)


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

    client = HeyGenClient(api_key=api_key)

    await render_single_chapter(
        client=client,
        chapter_index_1based=args.chapter,
        avatar_id=avatar_id,
        voice_id=voice_id,
        bg_type=bg_type,
        bg_value=bg_value,
        width=width,
        height=height,
    )


if __name__ == "__main__":
    asyncio.run(main())
