"""
백엔드-AI 서버 통신 통합 테스트 스크립트

이 스크립트는 백엔드에서 AI 서버로 보내는 요청을 시뮬레이션합니다.

테스트 항목:
1. SourceSet 오케스트레이션 시작 (POST /internal/ai/source-sets/{sourceSetId}/start)
2. Render Job 시작 (POST /ai/video/job/{jobId}/start)

사용법:
    python scripts/test_backend_ai_integration.py --base-url http://localhost:8000
    python scripts/test_backend_ai_integration.py --base-url http://localhost:8000 --source-set-id <실제_source_set_id>
"""

import argparse
import asyncio
import os
import sys
import time
import uuid
from typing import Optional

import httpx

# 색상 출력을 위한 ANSI 코드
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"
BOLD = "\033[1m"


def print_header(title: str):
    """섹션 헤더 출력."""
    print(f"\n{BOLD}{BLUE}{'=' * 70}{RESET}")
    print(f"{BOLD}{BLUE}{title:^70}{RESET}")
    print(f"{BOLD}{BLUE}{'=' * 70}{RESET}\n")


def print_result(success: bool, message: str):
    """테스트 결과 출력."""
    status = f"{GREEN}✓ PASS{RESET}" if success else f"{RED}✗ FAIL{RESET}"
    print(f"  {status} {message}")


def print_info(message: str):
    """정보 메시지 출력."""
    print(f"  {BLUE}ℹ{RESET} {message}")


def print_warning(message: str):
    """경고 메시지 출력."""
    print(f"  {YELLOW}⚠{RESET} {message}")


def print_error(message: str):
    """에러 메시지 출력."""
    print(f"  {RED}✗{RESET} {message}")


def get_internal_token() -> Optional[str]:
    """환경변수에서 내부 토큰 가져오기."""
    token = os.getenv("BACKEND_INTERNAL_TOKEN")
    if not token:
        print_warning("BACKEND_INTERNAL_TOKEN 환경변수가 설정되지 않았습니다.")
        print_info("토큰 없이 테스트를 진행합니다 (개발 환경에서만 가능).")
    return token


def test_health_check(base_url: str) -> bool:
    """AI 서버 헬스체크."""
    print_header("1. AI 서버 헬스체크")
    
    try:
        response = httpx.get(f"{base_url}/health", timeout=5.0)
        if response.status_code == 200:
            print_result(True, f"AI 서버 응답: {response.json()}")
            return True
        else:
            print_result(False, f"예상치 못한 상태 코드: {response.status_code}")
            return False
    except httpx.ConnectError:
        print_result(False, "AI 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.")
        return False
    except Exception as e:
        print_result(False, f"에러: {e}")
        return False


def test_source_set_start(
    base_url: str,
    source_set_id: Optional[str] = None,
    video_id: Optional[str] = None,
) -> bool:
    """SourceSet 오케스트레이션 시작 테스트."""
    print_header("2. SourceSet 오케스트레이션 시작")
    
    # 테스트 데이터 생성
    test_source_set_id = source_set_id or f"test-source-set-{uuid.uuid4().hex[:8]}"
    test_video_id = video_id or f"test-video-{uuid.uuid4().hex[:8]}"
    
    payload = {
        "videoId": test_video_id,
        "educationId": f"test-edu-{uuid.uuid4().hex[:8]}",
        "requestId": f"test-request-{uuid.uuid4().hex[:8]}",
        "traceId": f"test-trace-{uuid.uuid4().hex[:8]}",
    }
    
    print_info(f"SourceSetId: {test_source_set_id}")
    print_info(f"VideoId: {test_video_id}")
    print_info(f"Endpoint: POST /internal/ai/source-sets/{test_source_set_id}/start")
    
    # 헤더 설정
    headers = {}
    internal_token = get_internal_token()
    if internal_token:
        headers["X-Internal-Token"] = internal_token
    
    try:
        start_time = time.time()
        response = httpx.post(
            f"{base_url}/internal/ai/source-sets/{test_source_set_id}/start",
            json=payload,
            headers=headers,
            timeout=30.0,
        )
        elapsed = time.time() - start_time
        
        print_info(f"응답 시간: {elapsed:.2f}초")
        print_info(f"상태 코드: {response.status_code}")
        
        if response.status_code == 202:
            data = response.json()
            print_result(True, f"요청 접수됨: {data}")
            print_info(f"SourceSet 상태: {data.get('status', 'N/A')}")
            return True
        elif response.status_code == 401:
            print_result(False, "인증 실패: X-Internal-Token 헤더가 필요하거나 유효하지 않습니다.")
            print_info("BACKEND_INTERNAL_TOKEN 환경변수를 확인하세요.")
            return False
        elif response.status_code == 409:
            data = response.json()
            print_warning(f"이미 처리 중이거나 완료된 SourceSet: {data.get('detail', {})}")
            return True  # 충돌이지만 정상적인 응답
        else:
            print_result(False, f"예상치 못한 상태 코드: {response.status_code}")
            print_error(f"응답: {response.text[:200]}")
            return False
            
    except httpx.TimeoutException:
        print_result(False, "요청 타임아웃 (30초 초과)")
        return False
    except httpx.ConnectError:
        print_result(False, "AI 서버에 연결할 수 없습니다.")
        return False
    except Exception as e:
        print_result(False, f"에러: {e}")
        return False


def test_render_job_start(
    base_url: str,
    job_id: Optional[str] = None,
) -> bool:
    """Render Job 시작 테스트.
    
    Note: 이 API는 백엔드에서 render-spec을 조회하므로,
    실제 백엔드가 실행 중이고 jobId가 백엔드 DB에 존재해야 합니다.
    """
    print_header("3. Render Job 시작")
    
    # 테스트 데이터 생성
    test_job_id = job_id or f"test-job-{uuid.uuid4().hex[:8]}"
    
    print_info(f"JobId: {test_job_id}")
    print_info(f"Endpoint: POST /ai/video/job/{test_job_id}/start")
    print_warning("이 API는 백엔드에서 render-spec을 조회합니다.")
    print_warning("실제 백엔드가 실행 중이고 jobId가 존재해야 합니다.")
    
    # 헤더 설정
    headers = {}
    internal_token = get_internal_token()
    if internal_token:
        headers["X-Internal-Token"] = internal_token
    
    try:
        start_time = time.time()
        # Render Job 시작 API는 body가 없음 (jobId만 path parameter)
        response = httpx.post(
            f"{base_url}/ai/video/job/{test_job_id}/start",
            headers=headers,
            timeout=30.0,
        )
        elapsed = time.time() - start_time
        
        print_info(f"응답 시간: {elapsed:.2f}초")
        print_info(f"상태 코드: {response.status_code}")
        
        if response.status_code == 202:
            data = response.json()
            print_result(True, f"요청 접수됨: {data}")
            return True
        elif response.status_code == 401:
            print_result(False, "인증 실패: X-Internal-Token 헤더가 필요하거나 유효하지 않습니다.")
            return False
        elif response.status_code == 404:
            print_warning(f"Job을 찾을 수 없음: {test_job_id}")
            print_info("실제 백엔드 DB에 존재하는 jobId를 사용하세요.")
            return False
        elif response.status_code == 502:
            data = response.json()
            print_warning(f"백엔드 render-spec 조회 실패: {data.get('detail', {})}")
            print_info("백엔드 서버가 실행 중이고 BACKEND_BASE_URL이 올바르게 설정되었는지 확인하세요.")
            return False
        else:
            print_result(False, f"예상치 못한 상태 코드: {response.status_code}")
            print_error(f"응답: {response.text[:200]}")
            return False
            
    except httpx.TimeoutException:
        print_result(False, "요청 타임아웃 (30초 초과)")
        return False
    except httpx.ConnectError:
        print_result(False, "AI 서버에 연결할 수 없습니다.")
        return False
    except Exception as e:
        print_result(False, f"에러: {e}")
        return False


def test_source_set_status(
    base_url: str,
    source_set_id: str,
) -> bool:
    """SourceSet 처리 상태 조회 테스트."""
    print_header("4. SourceSet 처리 상태 조회")
    
    print_info(f"SourceSetId: {source_set_id}")
    print_info(f"Endpoint: GET /internal/ai/source-sets/{source_set_id}/status")
    
    # 헤더 설정
    headers = {}
    internal_token = get_internal_token()
    if internal_token:
        headers["X-Internal-Token"] = internal_token
    
    try:
        response = httpx.get(
            f"{base_url}/internal/ai/source-sets/{source_set_id}/status",
            headers=headers,
            timeout=10.0,
        )
        
        if response.status_code == 200:
            data = response.json()
            print_result(True, f"상태 조회 성공: {data}")
            return True
        elif response.status_code == 404:
            print_warning(f"SourceSet을 찾을 수 없음: {source_set_id}")
            return False
        else:
            print_result(False, f"예상치 못한 상태 코드: {response.status_code}")
            return False
            
    except Exception as e:
        print_result(False, f"에러: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="백엔드-AI 서버 통신 통합 테스트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  # 기본 테스트 (랜덤 ID 사용)
  python scripts/test_backend_ai_integration.py --base-url http://localhost:8000
  
  # 실제 SourceSet ID 사용
  python scripts/test_backend_ai_integration.py \\
    --base-url http://localhost:8000 \\
    --source-set-id <실제_source_set_id> \\
    --video-id <실제_video_id>
  
  # Render Job만 테스트
  python scripts/test_backend_ai_integration.py \\
    --base-url http://localhost:8000 \\
    --render-only \\
    --job-id <실제_job_id>
        """,
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="AI 서버 base URL (기본값: http://localhost:8000)",
    )
    parser.add_argument(
        "--source-set-id",
        help="테스트할 SourceSet ID (없으면 랜덤 생성)",
    )
    parser.add_argument(
        "--video-id",
        help="테스트할 Video ID (없으면 랜덤 생성)",
    )
    parser.add_argument(
        "--job-id",
        help="테스트할 Render Job ID (없으면 랜덤 생성)",
    )
    parser.add_argument(
        "--source-set-only",
        action="store_true",
        help="SourceSet 테스트만 실행",
    )
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="Render Job 테스트만 실행",
    )
    parser.add_argument(
        "--check-status",
        metavar="SOURCE_SET_ID",
        help="SourceSet 처리 상태 조회",
    )
    
    args = parser.parse_args()
    
    print(f"\n{BOLD}{'=' * 70}{RESET}")
    print(f"{BOLD}백엔드-AI 서버 통신 통합 테스트{RESET}")
    print(f"{BOLD}{'=' * 70}{RESET}")
    print(f"\n  Base URL: {args.base_url}")
    print(f"  Internal Token: {'설정됨' if get_internal_token() else '미설정'}")
    print(f"{BOLD}{'=' * 70}{RESET}\n")
    
    results = {}
    
    # 헬스체크
    results["health"] = test_health_check(args.base_url)
    if not results["health"]:
        print_error("\nAI 서버가 실행 중이지 않습니다.")
        print_info("서버를 시작하세요: uvicorn app.main:app --reload --port 8000")
        sys.exit(1)
    
    # 상태 조회만 실행
    if args.check_status:
        results["status"] = test_source_set_status(args.base_url, args.check_status)
        sys.exit(0 if results.get("status") else 1)
    
    # SourceSet 테스트
    if not args.render_only:
        results["source_set"] = test_source_set_start(
            args.base_url,
            args.source_set_id,
            args.video_id,
        )
    
    # Render Job 테스트
    if not args.source_set_only:
        results["render_job"] = test_render_job_start(
            args.base_url,
            args.job_id,
        )
    
    # 결과 요약
    print_header("테스트 결과 요약")
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for name, success in results.items():
        status_icon = f"{GREEN}✓{RESET}" if success else f"{RED}✗{RESET}"
        status_text = "PASS" if success else "FAIL"
        print(f"  {status_icon} {name:20} : {status_text}")
    
    print(f"\n  {BOLD}총계: {passed}/{total} 통과{RESET}")
    print(f"{BOLD}{'=' * 70}{RESET}\n")
    
    if passed == total:
        print(f"{GREEN}모든 테스트 통과!{RESET}\n")
        sys.exit(0)
    else:
        print(f"{RED}일부 테스트 실패{RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()

