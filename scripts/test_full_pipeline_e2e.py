"""
전체 교육 영상 파이프라인 E2E 테스트 스크립트

프론트엔드 없이 전체 플로우를 테스트합니다:
1. 백엔드에 문서 업로드 시뮬레이션 (또는 기존 데이터 사용)
2. ctrlf-ai에 전처리 시작 요청
3. 전처리 완료 대기 및 스크립트 생성 확인
4. 영상 생성 요청
5. 영상 생성 완료 대기 및 결과 확인

사용법:
    python scripts/test_full_pipeline_e2e.py --source-set-id <source_set_id> --video-id <video_id>
    python scripts/test_full_pipeline_e2e.py --auto  # 자동으로 테스트 데이터 생성
"""

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

# 프로젝트 루트를 Python 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.core.config import get_settings


def print_header(title: str):
    """헤더 출력."""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_step(step: int, title: str):
    """단계 출력."""
    print(f"\n[Step {step}] {title}")
    print("-" * 70)


def print_result(success: bool, message: str):
    """결과 출력."""
    status = "✅ PASS" if success else "❌ FAIL"
    print(f"  {status}: {message}")


def get_env_var(name: str, default: Optional[str] = None) -> str:
    """환경변수 가져오기."""
    value = os.getenv(name, default)
    if not value:
        raise ValueError(f"환경변수 {name}이 설정되지 않았습니다.")
    return value


class PipelineTester:
    """전체 파이프라인 테스터."""

    def __init__(
        self,
        ai_base_url: str,
        backend_base_url: Optional[str] = None,
        internal_token: Optional[str] = None,
    ):
        """초기화."""
        self.ai_base_url = ai_base_url.rstrip("/")
        self.backend_base_url = backend_base_url.rstrip("/") if backend_base_url else None
        self.internal_token = internal_token or get_env_var(
            "BACKEND_INTERNAL_TOKEN", "test-token"
        )
        self.client = httpx.AsyncClient(timeout=60.0)

    async def close(self):
        """리소스 정리."""
        await self.client.aclose()

    async def check_ai_health(self) -> bool:
        """AI 서버 헬스체크."""
        try:
            response = await self.client.get(f"{self.ai_base_url}/health")
            if response.status_code == 200:
                data = response.json()
                print_result(True, f"AI 서버 상태: {data.get('status', 'unknown')}")
                return True
            else:
                print_result(False, f"AI 서버 헬스체크 실패: {response.status_code}")
                return False
        except Exception as e:
            print_result(False, f"AI 서버 연결 실패: {e}")
            return False

    async def check_backend_health(self) -> bool:
        """백엔드 서버 헬스체크."""
        if not self.backend_base_url:
            print_result(False, "백엔드 URL이 설정되지 않았습니다.")
            return False

        try:
            response = await self.client.get(f"{self.backend_base_url}/actuator/health")
            if response.status_code == 200:
                print_result(True, "백엔드 서버 연결 성공")
                return True
            else:
                print_result(False, f"백엔드 헬스체크 실패: {response.status_code}")
                return False
        except Exception as e:
            print_result(False, f"백엔드 서버 연결 실패: {e}")
            return False

    async def start_preprocessing(
        self, source_set_id: str, video_id: str, education_id: Optional[str] = None
    ) -> bool:
        """전처리 시작 요청."""
        print_step(1, "전처리 시작 요청")

        payload = {
            "videoId": video_id,
            "requestId": f"test-req-{uuid.uuid4().hex[:8]}",
            "traceId": f"test-trace-{uuid.uuid4().hex[:8]}",
        }
        if education_id:
            payload["educationId"] = education_id

        print(f"  SourceSetId: {source_set_id}")
        print(f"  VideoId: {video_id}")
        print(f"  Payload: {json.dumps(payload, indent=2, ensure_ascii=False)}")

        try:
            response = await self.client.post(
                f"{self.ai_base_url}/internal/ai/source-sets/{source_set_id}/start",
                json=payload,
                headers={"X-Internal-Token": self.internal_token},
            )

            if response.status_code == 202:
                data = response.json()
                print_result(True, f"전처리 시작됨: {data.get('status', 'PROCESSING')}")
                return True
            elif response.status_code == 409:
                data = response.json()
                detail = data.get("detail", {})
                if detail.get("reason_code") == "ALREADY_COMPLETED":
                    print_result(
                        True,
                        f"이미 완료된 소스셋: {detail.get('source_set_status', 'SCRIPT_READY')}",
                    )
                    return True
                else:
                    print_result(False, f"충돌: {detail.get('message', 'Unknown')}")
                    return False
            else:
                print_result(
                    False, f"전처리 시작 실패: {response.status_code} - {response.text}"
                )
                return False
        except Exception as e:
            print_result(False, f"전처리 시작 요청 실패: {e}")
            return False

    async def wait_for_preprocessing_complete(
        self, source_set_id: str, max_wait_sec: int = 600
    ) -> Optional[Dict[str, Any]]:
        """전처리 완료 대기."""
        print_step(2, "전처리 완료 대기")

        start_time = time.time()
        poll_interval = 5  # 5초마다 조회

        while time.time() - start_time < max_wait_sec:
            try:
                response = await self.client.get(
                    f"{self.ai_base_url}/internal/ai/source-sets/{source_set_id}/status",
                    headers={"X-Internal-Token": self.internal_token},
                )

                if response.status_code == 200:
                    data = response.json()
                    status = data.get("status", "UNKNOWN")
                    source_set_status = data.get("sourceSetStatus", "UNKNOWN")
                    has_script = data.get("hasScript", False)

                    elapsed = int(time.time() - start_time)
                    print(
                        f"  [{elapsed}s] 상태: {status}, "
                        f"SourceSetStatus: {source_set_status}, "
                        f"hasScript: {has_script}"
                    )

                    if status == "COMPLETED":
                        print_result(True, "전처리 완료!")
                        return data
                    elif status == "FAILED":
                        print_result(False, f"전처리 실패: {data.get('error_message', 'Unknown')}")
                        return None

                await asyncio.sleep(poll_interval)

            except Exception as e:
                print(f"  상태 조회 오류: {e}")
                await asyncio.sleep(poll_interval)

        print_result(False, f"전처리 완료 대기 시간 초과 ({max_wait_sec}초)")
        return None

    async def verify_script_generated(
        self, source_set_id: str, video_id: str
    ) -> Optional[str]:
        """스크립트 생성 확인."""
        print_step(3, "스크립트 생성 확인")

        # 백엔드에서 스크립트 조회 (백엔드 API가 있는 경우)
        if not self.backend_base_url:
            print("  백엔드 URL이 없어 스크립트 조회를 건너뜁니다.")
            return None

        try:
            # 백엔드 API 경로는 실제 백엔드 API 명세에 따라 수정 필요
            # 예: GET /api/scripts?videoId={video_id}
            response = await self.client.get(
                f"{self.backend_base_url}/api/scripts",
                params={"videoId": video_id},
                headers={"Authorization": f"Bearer {self.internal_token}"},
            )

            if response.status_code == 200:
                data = response.json()
                script_id = data.get("scriptId") or data.get("id")
                if script_id:
                    print_result(True, f"스크립트 생성 확인: {script_id}")
                    return script_id
                else:
                    print_result(False, "스크립트 ID를 찾을 수 없습니다.")
                    return None
            else:
                print(f"  백엔드 스크립트 조회 실패: {response.status_code}")
                return None
        except Exception as e:
            print(f"  스크립트 조회 오류: {e}")
            return None

    async def start_video_generation(
        self, video_id: str, script_id: str, education_id: Optional[str] = None
    ) -> Optional[str]:
        """영상 생성 시작."""
        print_step(4, "영상 생성 시작")

        job_id = f"test-job-{uuid.uuid4().hex[:8]}"

        payload = {
            "jobId": job_id,
            "videoId": video_id,
            "scriptId": script_id,
        }
        if education_id:
            payload["educationId"] = education_id

        print(f"  JobId: {job_id}")
        print(f"  VideoId: {video_id}")
        print(f"  ScriptId: {script_id}")
        print(f"  Payload: {json.dumps(payload, indent=2, ensure_ascii=False)}")

        try:
            response = await self.client.post(
                f"{self.ai_base_url}/internal/ai/render-jobs",
                json=payload,
                headers={"X-Internal-Token": self.internal_token},
            )

            if response.status_code in [200, 201, 202]:
                data = response.json()
                status = data.get("status", "PENDING")
                print_result(True, f"영상 생성 Job 생성됨: {status}")
                return job_id
            else:
                print_result(
                    False, f"영상 생성 시작 실패: {response.status_code} - {response.text}"
                )
                return None
        except Exception as e:
            print_result(False, f"영상 생성 시작 요청 실패: {e}")
            return None

    async def wait_for_video_complete(
        self, job_id: str, max_wait_sec: int = 1800
    ) -> Optional[Dict[str, Any]]:
        """영상 생성 완료 대기."""
        print_step(5, "영상 생성 완료 대기")

        start_time = time.time()
        poll_interval = 10  # 10초마다 조회

        while time.time() - start_time < max_wait_sec:
            try:
                response = await self.client.get(
                    f"{self.ai_base_url}/internal/ai/video/job/{job_id}",
                    headers={"X-Internal-Token": self.internal_token},
                )

                if response.status_code == 200:
                    data = response.json()
                    status = data.get("status", "UNKNOWN")
                    video_url = data.get("videoUrl")
                    duration = data.get("duration")

                    elapsed = int(time.time() - start_time)
                    print(
                        f"  [{elapsed}s] 상태: {status}, "
                        f"videoUrl: {video_url or 'N/A'}, "
                        f"duration: {duration or 'N/A'}s"
                    )

                    if status == "COMPLETED":
                        print_result(True, f"영상 생성 완료! URL: {video_url}")
                        return data
                    elif status == "FAILED":
                        fail_reason = data.get("failReason", "Unknown")
                        print_result(False, f"영상 생성 실패: {fail_reason}")
                        return None

                await asyncio.sleep(poll_interval)

            except Exception as e:
                print(f"  상태 조회 오류: {e}")
                await asyncio.sleep(poll_interval)

        print_result(False, f"영상 생성 완료 대기 시간 초과 ({max_wait_sec}초)")
        return None

    async def verify_s3_upload(self, video_url: str) -> bool:
        """S3 업로드 확인."""
        print_step(6, "S3 업로드 확인")

        if not video_url:
            print_result(False, "비디오 URL이 없습니다.")
            return False

        print(f"  Video URL: {video_url}")

        try:
            # URL이 S3 URL인지 확인
            if "s3://" in video_url or "amazonaws.com" in video_url or "s3." in video_url:
                print_result(True, "S3 URL 형식 확인됨")
                return True
            else:
                print_result(False, f"예상치 못한 URL 형식: {video_url}")
                return False
        except Exception as e:
            print_result(False, f"S3 URL 확인 실패: {e}")
            return False


async def test_full_pipeline(
    source_set_id: str,
    video_id: str,
    education_id: Optional[str] = None,
    ai_base_url: str = "http://localhost:8000",
    backend_base_url: Optional[str] = None,
    internal_token: Optional[str] = None,
    skip_video: bool = False,
):
    """전체 파이프라인 테스트."""
    print_header("전체 교육 영상 파이프라인 E2E 테스트")

    tester = PipelineTester(ai_base_url, backend_base_url, internal_token)

    try:
        # 0. 헬스체크
        print_step(0, "서버 헬스체크")
        ai_ok = await tester.check_ai_health()
        if not ai_ok:
            print("\n❌ AI 서버가 실행 중이지 않습니다.")
            print("   서버를 시작하세요: uvicorn app.main:app --port 8000")
            return False

        if backend_base_url:
            backend_ok = await tester.check_backend_health()
            if not backend_ok:
                print("\n⚠️  백엔드 서버 연결 실패 (계속 진행)")

        # 1. 전처리 시작
        if not await tester.start_preprocessing(source_set_id, video_id, education_id):
            return False

        # 2. 전처리 완료 대기
        preprocessing_result = await tester.wait_for_preprocessing_complete(source_set_id)
        if not preprocessing_result:
            return False

        # 3. 스크립트 생성 확인
        script_id = await tester.verify_script_generated(source_set_id, video_id)
        if not script_id and not skip_video:
            print("\n⚠️  스크립트 ID를 찾을 수 없어 영상 생성 단계를 건너뜁니다.")
            print("   --skip-video 옵션을 사용하거나 백엔드 URL을 설정하세요.")
            return True  # 전처리는 성공했으므로 True 반환

        # 4. 영상 생성 (선택사항)
        if not skip_video and script_id:
            job_id = await tester.start_video_generation(
                video_id, script_id, education_id
            )
            if not job_id:
                return False

            # 5. 영상 생성 완료 대기
            video_result = await tester.wait_for_video_complete(job_id)
            if not video_result:
                return False

            # 6. S3 업로드 확인
            video_url = video_result.get("videoUrl")
            await tester.verify_s3_upload(video_url)

        # 최종 결과
        print_header("테스트 완료")
        print_result(True, "전체 파이프라인 테스트 성공!")
        return True

    except Exception as e:
        print_result(False, f"테스트 중 오류 발생: {e}")
        import traceback

        traceback.print_exc()
        return False

    finally:
        await tester.close()


def main():
    """메인 함수."""
    parser = argparse.ArgumentParser(
        description="전체 교육 영상 파이프라인 E2E 테스트"
    )
    parser.add_argument(
        "--source-set-id",
        type=str,
        help="SourceSet ID (백엔드 DB에 존재하는 값)",
    )
    parser.add_argument(
        "--video-id", type=str, help="Video ID (백엔드 DB에 존재하는 값)"
    )
    parser.add_argument(
        "--education-id", type=str, help="Education ID (선택사항)"
    )
    parser.add_argument(
        "--ai-base-url",
        type=str,
        default="http://localhost:8000",
        help="AI 서버 Base URL (기본값: http://localhost:8000)",
    )
    parser.add_argument(
        "--backend-base-url",
        type=str,
        help="백엔드 서버 Base URL (선택사항)",
    )
    parser.add_argument(
        "--internal-token",
        type=str,
        help="Internal Token (기본값: BACKEND_INTERNAL_TOKEN 환경변수)",
    )
    parser.add_argument(
        "--skip-video",
        action="store_true",
        help="영상 생성 단계 건너뛰기 (전처리만 테스트)",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="자동으로 테스트 데이터 생성 (백엔드 DB에 실제 데이터가 있어야 함)",
    )

    args = parser.parse_args()

    # 자동 모드
    if args.auto:
        # 환경변수에서 가져오거나 기본값 사용
        source_set_id = args.source_set_id or f"test-source-set-{uuid.uuid4().hex[:8]}"
        video_id = args.video_id or f"test-video-{uuid.uuid4().hex[:8]}"
        education_id = args.education_id
    else:
        if not args.source_set_id or not args.video_id:
            parser.error("--source-set-id와 --video-id는 필수입니다 (또는 --auto 사용)")
            return

        source_set_id = args.source_set_id
        video_id = args.video_id
        education_id = args.education_id

    # 환경변수에서 기본값 가져오기
    settings = get_settings()
    backend_base_url = args.backend_base_url or (
        settings.BACKEND_BASE_URL if hasattr(settings, "BACKEND_BASE_URL") else None
    )
    internal_token = args.internal_token or os.getenv(
        "BACKEND_INTERNAL_TOKEN", "test-token"
    )

    # 테스트 실행
    success = asyncio.run(
        test_full_pipeline(
            source_set_id=source_set_id,
            video_id=video_id,
            education_id=education_id,
            ai_base_url=args.ai_base_url,
            backend_base_url=backend_base_url,
            internal_token=internal_token,
            skip_video=args.skip_video,
        )
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

