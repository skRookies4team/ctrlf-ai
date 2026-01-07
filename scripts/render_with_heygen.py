import sys
import os
import json
import asyncio
import argparse
from pathlib import Path

import boto3
import httpx
from dotenv import load_dotenv

# ============================================================
# 프로젝트 루트
# ============================================================
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

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
OUTPUT_DIR = Path("test_output_script/chapters")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

POLL_INTERVAL_SEC = 10
MAX_POLLS = 180

# ============================================================
# argparse
# ============================================================
def parse_args():
    parser = argparse.ArgumentParser(
        description="HeyGen 챕터 영상 생성 → mp4 → S3 저장"
    )
    parser.add_argument("--chapter", type=int, help="실행할 챕터 번호 (ex: 1)")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()

# ============================================================
# Utils
# ============================================================
async def download_file(url: str, out_path: Path):
    async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        out_path.write_bytes(r.content)


def upload_to_s3(file_path: Path, s3_key: str) -> str:
    s3 = boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION", "ap-northeast-2"),
    )

    bucket = os.getenv("S3_BUCKET_NAME")
    s3.upload_file(
        Filename=str(file_path),
        Bucket=bucket,
        Key=s3_key,
        ExtraArgs={"ContentType": "video/mp4"},
    )

    return f"s3://{bucket}/{s3_key}"

# ============================================================
# 단일 챕터 렌더링
# ============================================================
async def render_single_chapter_to_s3(
    client: HeyGenClient,
    chapter: dict,
    idx: int,
):
    chapter_no = f"{idx:02d}"
    chapter_title = chapter.get("title", f"Chapter {idx}")

    print(f"\n🎬 [CHAPTER {chapter_no}] {chapter_title}")

    # 1️⃣ 기존에 성공하던 방식 그대로
    enhanced = enhance_video_script_for_video({"chapters": [chapter]})

    # narration 필수 보정 (400 방지)
    for sc in enhanced["chapters"][0]["scenes"]:
        if not sc.get("narration"):
            sc["narration"] = sc.get("on_screen_text") or "설명입니다."

    video_inputs = build_heygen_video_inputs(
        enhanced,
        avatar_id=os.getenv("HEYGEN_AVATAR_ID"),
        voice_id=os.getenv("HEYGEN_VOICE_ID"),
        bg_type=os.getenv("HEYGEN_BG_TYPE", "color"),
        bg_value=os.getenv("HEYGEN_BG_VALUE", "#FFFFFF"),
    )

    payload = build_heygen_generate_payload(
        video_inputs,
        width=int(os.getenv("HEYGEN_DIM_W", "1280")),
        height=int(os.getenv("HEYGEN_DIM_H", "720")),
    )

    # DEBUG payload
    debug_path = OUTPUT_DIR / f"chapter_{chapter_no}.DEBUG.payload.json"
    debug_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"🧪 DEBUG payload saved → {debug_path}")

    # 2️⃣ HeyGen 요청
    video_id = await client.generate_video(payload)
    print(f"✅ HeyGen video_id = {video_id}")

    # 3️⃣ 상태 폴링
    for _ in range(MAX_POLLS):
        status = await client.get_video_status(video_id)
        data = status.get("data", {})
        s = (data.get("status") or "").lower()
        print(f"⏳ status = {s}")

        if s == "completed":
            video_url = data.get("video_url")
            if not video_url:
                raise RuntimeError("completed but video_url missing")

            mp4_path = OUTPUT_DIR / f"chapter_{chapter_no}.mp4"
            await download_file(video_url, mp4_path)
            print(f"🎞️ mp4 saved → {mp4_path}")

            s3_key = (
                f"education_videos/{INPUT_SCRIPT_PATH.stem}/chapter_{chapter_no}.mp4"
            )
            s3_uri = upload_to_s3(mp4_path, s3_key)
            print(f"☁️ S3 uploaded → {s3_uri}")

            result_path = OUTPUT_DIR / f"chapter_{chapter_no}.result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "chapter": chapter_title,
                        "chapter_no": idx,
                        "video_id": video_id,
                        "s3_uri": s3_uri,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"📄 result saved → {result_path}")
            return

        if s == "failed":
            raise RuntimeError(f"HeyGen failed: {video_id}")

        await asyncio.sleep(POLL_INTERVAL_SEC)

    raise TimeoutError("HeyGen polling timeout")

# ============================================================
# main
# ============================================================
async def main():
    args = parse_args()
    load_dotenv(ROOT_DIR / ".env")

    client = HeyGenClient(api_key=os.getenv("HEYGEN_API_KEY"))

    script = json.loads(INPUT_SCRIPT_PATH.read_text(encoding="utf-8"))
    chapters = script["chapters"]

    for idx, chapter in enumerate(chapters, start=1):
        if args.chapter and idx != args.chapter:
            continue

        await render_single_chapter_to_s3(
            client=client,
            chapter=chapter,
            idx=idx,
        )

if __name__ == "__main__":
    asyncio.run(main())
