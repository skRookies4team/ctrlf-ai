"""
API 통합 테스트 스크립트

구현된 API들을 테스트하는 통합 테스트 스크립트입니다.
"""

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

# 프로젝트 루트를 경로에 추가
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 환경변수 로드
load_dotenv(PROJECT_ROOT / ".env")

# 설정
BASE_URL = os.getenv("AI_BASE_URL")
if not BASE_URL:
    print("⚠️  AI_BASE_URL 환경변수가 설정되지 않았습니다.")
    print("   .env 파일에 AI_BASE_URL을 설정하세요 (예: http://localhost:8000)")
    sys.exit(1)

INTERNAL_TOKEN = os.getenv("BACKEND_INTERNAL_TOKEN", "")
BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL")
if not BACKEND_BASE_URL:
    print("⚠️  BACKEND_BASE_URL 환경변수가 설정되지 않았습니다.")
    print("   .env 파일에 BACKEND_BASE_URL을 설정하세요 (예: http://localhost:8080)")
    sys.exit(1)


class Colors:
    """터미널 색상"""
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


def print_header(text: str):
    """헤더 출력"""
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 60}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 60}{Colors.ENDC}\n")


def print_success(text: str):
    """성공 메시지 출력"""
    print(f"{Colors.OKGREEN}✅ {text}{Colors.ENDC}")


def print_error(text: str):
    """에러 메시지 출력"""
    print(f"{Colors.FAIL}❌ {text}{Colors.ENDC}")


def print_info(text: str):
    """정보 메시지 출력"""
    print(f"{Colors.OKCYAN}ℹ️  {text}{Colors.ENDC}")


def print_warning(text: str):
    """경고 메시지 출력"""
    print(f"{Colors.WARNING}⚠️  {text}{Colors.ENDC}")


async def test_script_generation():
    """Milvus 기반 스크립트 생성 테스트"""
    print_header("1. Milvus 기반 스크립트 생성 테스트")

    if not INTERNAL_TOKEN:
        print_error("BACKEND_INTERNAL_TOKEN이 설정되지 않았습니다.")
        return None

    url = f"{BASE_URL}/internal/ai/scripts/generate-from-milvus"
    
    # 서버 연결 확인
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            health_check = await client.get(f"{BASE_URL}/health")
            if health_check.status_code != 200:
                print_error(f"서버가 응답하지 않습니다. 서버가 실행 중인지 확인하세요: {BASE_URL}")
                return None
            print_success(f"서버 연결 확인: {BASE_URL}")
    except Exception as e:
        print_error(f"서버에 연결할 수 없습니다: {BASE_URL}")
        print_error(f"에러: {e}")
        print_info("\n도커를 사용하는 경우:")
        print_info("  1. 컨테이너가 실행 중인지 확인: docker ps")
        print_info("  2. 컨테이너 재빌드: docker compose build ai-gateway")
        print_info("  3. 컨테이너 재시작: docker compose restart ai-gateway-real")
        print_info("\n로컬에서 실행하는 경우:")
        print_info("  python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000")
        return None
    
    headers = {
        "Content-Type": "application/json",
        "X-Internal-Token": INTERNAL_TOKEN,
    }
    payload = {
        "videoId": f"test-video-{uuid.uuid4()}",
        "domain": "직장내괴롭힘교육",
        "language": "ko",
        "targetMinutes": 4,
        "maxChapters": 2,
        "maxScenesPerChapter": 5,
        "style": "friendly_security_training",
        "topK": 50,
    }

    print_info(f"요청 URL: {url}")
    print_info(f"도메인: {payload['domain']}")
    print_info(f"Video ID: {payload['videoId']}")

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code == 200:
                data = response.json()
                print_success("스크립트 생성 완료")
                print_info(f"Script ID: {data.get('scriptId')}")
                print_info(f"Video ID: {data.get('videoId')}")
                print_info(f"Source Text Length: {data.get('sourceTextLength')}")
                return data
            else:
                print_error(f"스크립트 생성 실패: {response.status_code}")
                print_error(f"응답: {response.text}")
                return None

    except Exception as e:
        print_error(f"스크립트 생성 중 에러: {e}")
        return None


async def test_render_job(script_id: str, video_id: str):
    """렌더 잡 생성 테스트"""
    print_header("2. HeyGen 렌더링 테스트")

    if not INTERNAL_TOKEN:
        print_error("BACKEND_INTERNAL_TOKEN이 설정되지 않았습니다.")
        return None

    # HeyGen 활성화 확인
    heygen_enabled = os.getenv("HEYGEN_ENABLED", "true").lower() == "true"
    if not heygen_enabled:
        print_warning("HEYGEN_ENABLED가 false입니다. HeyGen 렌더러를 사용하려면 true로 설정하세요.")
        print_info("현재 MVP 렌더러가 사용됩니다.")

    url = f"{BASE_URL}/internal/ai/render-jobs"
    headers = {
        "Content-Type": "application/json",
        "X-Internal-Token": INTERNAL_TOKEN,
    }
    payload = {
        "jobId": str(uuid.uuid4()),
        "videoId": video_id,
        "scriptId": script_id,
        "scriptVersion": 1,
        "renderPolicyId": "RP-DEFAULT-01",
        "requestId": str(uuid.uuid4()),
    }

    print_info(f"요청 URL: {url}")
    print_info(f"Job ID: {payload['jobId']}")
    print_info(f"Script ID: {payload['scriptId']}")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code == 202:
                data = response.json()
                print_success("렌더링 시작됨")
                print_info(f"Job ID: {data.get('jobId')}")
                print_info(f"Status: {data.get('status')}")
                print_info(f"\n렌더링은 백그라운드에서 진행됩니다.")
                print_info(f"진행 상황은 백엔드 API 또는 WebSocket으로 확인할 수 있습니다.")
                return data
            else:
                print_error(f"렌더링 시작 실패: {response.status_code}")
                print_error(f"응답: {response.text}")
                return None

    except Exception as e:
        print_error(f"렌더링 시작 중 에러: {e}")
        return None


def check_environment():
    """환경 설정 확인"""
    print_header("환경 설정 확인")

    required_vars = [
        "BACKEND_INTERNAL_TOKEN",
        "HEYGEN_API_KEY",
        "HEYGEN_AVATAR_ID",
        "HEYGEN_VOICE_ID",
        "S3_BUCKET_NAME",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ]

    optional_vars = [
        "HEYGEN_ENABLED",
        "BACKEND_BASE_URL",
        "MILVUS_ENABLED",
        "LLM_BASE_URL",
    ]

    all_ok = True

    print_info("필수 환경변수:")
    for var in required_vars:
        value = os.getenv(var)
        if value:
            masked = value[:4] + "****" if len(value) > 8 else "****"
            print_success(f"  {var}: {masked}")
        else:
            print_error(f"  {var}: 설정되지 않음")
            all_ok = False

    print_info("\n선택 환경변수:")
    for var in optional_vars:
        value = os.getenv(var)
        if value:
            print_success(f"  {var}: {value}")
        else:
            print_warning(f"  {var}: 설정되지 않음 (기본값 사용)")

    return all_ok


async def main():
    """메인 테스트 함수"""
    print_header("API 통합 테스트 시작")

    # 환경 설정 확인
    if not check_environment():
        print_error("\n필수 환경변수가 설정되지 않았습니다.")
        print_info("`.env` 파일을 확인하고 필요한 환경변수를 설정하세요.")
        return

    # 1. 스크립트 생성 테스트
    script_data = await test_script_generation()
    if not script_data:
        print_error("\n스크립트 생성에 실패했습니다. 테스트를 중단합니다.")
        return

    script_id = script_data.get("scriptId")
    video_id = script_data.get("videoId")

    if not script_id or not video_id:
        print_error("\n스크립트 데이터가 올바르지 않습니다.")
        return

    # 2. 렌더링 테스트 (선택적)
    print_warning("\n⚠️  렌더링 테스트를 진행하시겠습니까?")
    print_warning("   (HeyGen API 사용량이 발생합니다)")
    print_info("   Enter를 눌러 계속하거나 Ctrl+C로 중단하세요...")

    try:
        input()
    except KeyboardInterrupt:
        print_info("\n테스트를 중단했습니다.")
        return

    render_data = await test_render_job(script_id, video_id)
    if render_data:
        print_success("\n✅ 모든 테스트가 완료되었습니다!")
        print_info(f"Job ID: {render_data.get('jobId')}")
        print_info(f"렌더링 진행 상황은 백엔드에서 확인할 수 있습니다.")
    else:
        print_warning("\n⚠️  렌더링 테스트는 실패했지만 스크립트 생성은 성공했습니다.")


if __name__ == "__main__":
    # Windows 콘솔 인코딩 설정
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print_info("\n\n테스트가 중단되었습니다.")

