"""
영상 생성 API 테스트 스크립트

사용법:
    python scripts/test_video_creation.py \
        --source-set-id <source_set_id> \
        --video-id <video_id> \
        --backend-url http://localhost:8080 \
        --ai-url http://localhost:8000 \
        --token <internal_token>
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx


async def test_video_creation(
    source_set_id: str,
    video_id: str,
    ai_url: str,
    token: str,
    education_id: str = None,
    request_id: str = None,
    trace_id: str = None,
):
    """영상 생성 API 테스트."""
    url = f"{ai_url}/api/v1/videos/create-from-source-set"

    headers = {
        "X-Internal-Token": token,
        "Content-Type": "application/json",
    }

    body = {
        "sourceSetId": source_set_id,
        "videoId": video_id,
    }

    if education_id:
        body["educationId"] = education_id
    if request_id:
        body["requestId"] = request_id
    if trace_id:
        body["traceId"] = trace_id

    print(f"🚀 영상 생성 요청 시작")
    print(f"   URL: {url}")
    print(f"   Source Set ID: {source_set_id}")
    print(f"   Video ID: {video_id}")
    print()

    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            response = await client.post(url, headers=headers, json=body)

            print(f"📊 응답 상태: {response.status_code}")

            if response.status_code == 200:
                result = response.json()
                print(f"✅ 영상 생성 성공!")
                print()
                print("📋 결과:")
                print(json.dumps(result, indent=2, ensure_ascii=False))
                print()
                print(f"🎬 영상 URL: {result.get('video_url', 'N/A')}")
                print(f"☁️  S3 URI: {result.get('s3_uri', 'N/A')}")
                print(f"⏱️  길이: {result.get('duration_sec', 0)}초")
                return True
            else:
                print(f"❌ 영상 생성 실패")
                print(f"   상태 코드: {response.status_code}")
                print(f"   응답: {response.text}")
                return False

    except httpx.TimeoutException:
        print(f"⏱️  타임아웃: 요청이 10분을 초과했습니다.")
        return False
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(description="영상 생성 API 테스트")
    parser.add_argument(
        "--source-set-id",
        required=True,
        help="소스셋 ID",
    )
    parser.add_argument(
        "--video-id",
        required=True,
        help="영상 ID",
    )
    parser.add_argument(
        "--ai-url",
        default="http://localhost:8000",
        help="AI 서버 URL (기본: http://localhost:8000)",
    )
    parser.add_argument(
        "--token",
        required=True,
        help="내부 API 토큰 (X-Internal-Token)",
    )
    parser.add_argument(
        "--education-id",
        help="교육 ID (선택)",
    )
    parser.add_argument(
        "--request-id",
        help="요청 ID (멱등성, 선택)",
    )
    parser.add_argument(
        "--trace-id",
        help="추적 ID (선택)",
    )

    args = parser.parse_args()

    success = asyncio.run(
        test_video_creation(
            source_set_id=args.source_set_id,
            video_id=args.video_id,
            ai_url=args.ai_url,
            token=args.token,
            education_id=args.education_id,
            request_id=args.request_id,
            trace_id=args.trace_id,
        )
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

