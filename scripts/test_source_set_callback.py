#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SourceSet 콜백 API 테스트 스크립트

RAGFlow 없이 백엔드 콜백 API만 직접 테스트합니다.
UUID 형식 검증을 위한 테스트입니다.

사용법:
    # 기본 (localhost:8080 백엔드)
    python scripts/test_source_set_callback.py

    # 백엔드 URL 지정
    python scripts/test_source_set_callback.py --backend-url http://localhost:8080

    # 특정 source_set_id로 테스트
    python scripts/test_source_set_callback.py --source-set-id "실제-소스셋-UUID"

    # 토큰 지정
    python scripts/test_source_set_callback.py --token "your-internal-token"
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime

try:
    import httpx
except ImportError:
    print("httpx 패키지가 필요합니다: pip install httpx")
    sys.exit(1)


def print_header(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_result(success: bool, message: str):
    status = "PASS" if success else "FAIL"
    symbol = "[v]" if success else "[x]"
    print(f"  {symbol} {status}: {message}")


def print_json(data: dict, indent: int = 4):
    """JSON을 예쁘게 출력"""
    print(json.dumps(data, indent=indent, ensure_ascii=False, default=str))


def generate_uuid() -> str:
    """UUID 생성"""
    return str(uuid.uuid4())


def test_callback_success(
    backend_url: str,
    source_set_id: str,
    video_id: str,
    education_id: str,
    document_id: str,
    request_id: str,
    token: str | None,
) -> bool:
    """성공 콜백 테스트 (COMPLETED + SCRIPT_READY)"""
    print_header("1. Success Callback Test (COMPLETED)")

    url = f"{backend_url}/internal/callbacks/source-sets/{source_set_id}/complete"

    # 테스트용 스크립트 데이터 (API 스펙 참조: docs/source_set_callback_api_spec.md)
    payload = {
        "videoId": video_id,  # UUID 형식
        "status": "COMPLETED",
        "sourceSetStatus": "SCRIPT_READY",
        "documents": [
            {
                "documentId": document_id,  # 실제 document_id 사용
                "status": "COMPLETED",
                "failReason": None,
            }
        ],
        "script": {
            # scriptId는 백엔드에서 자동 생성 (JPA @GeneratedValue)
            "educationId": education_id,  # ✅ 필수 필드!
            "sourceSetId": source_set_id,
            "title": "테스트 교육 스크립트",
            "totalDurationSec": 180,
            "version": 1,
            "llmModel": "test-model",
            "chapters": [
                {
                    "chapterIndex": 0,
                    "title": "테스트 챕터",
                    "durationSec": 180,
                    "scenes": [
                        {
                            "sceneIndex": 0,
                            "purpose": "hook",
                            "narration": "안녕하세요, 테스트 교육입니다.",
                            "caption": "테스트 교육",
                            "visual": "타이틀 슬라이드",
                            "durationSec": 30,
                            "confidenceScore": 0.9,
                            "sourceRefs": [
                                {
                                    "documentId": document_id,
                                    "chunkIndex": 0
                                }
                            ],
                        }
                    ],
                }
            ],
        },
        "requestId": request_id,  # UUID 형식 (선택)
        "traceId": f"trace-{uuid.uuid4().hex[:8]}",
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if token:
        headers["X-Internal-Token"] = token

    print(f"  URL: {url}")
    print(f"  videoId: {video_id}")
    print(f"  requestId: {request_id}")
    print(f"  status: {payload['status']}")
    print(f"  sourceSetStatus: {payload['sourceSetStatus']}")
    print("\n  Request Body:")
    print_json(payload)

    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=30.0)

        print(f"\n  Response Status: {response.status_code}")
        print(f"  Response Body: {response.text[:500]}")

        if response.status_code in (200, 201, 204):
            print_result(True, "Success callback sent successfully")
            return True
        elif response.status_code == 400:
            print_result(False, f"Bad Request - UUID 형식 오류 가능성: {response.text[:200]}")
            return False
        elif response.status_code == 401:
            print_result(False, "Unauthorized - X-Internal-Token 필요")
            return False
        elif response.status_code == 404:
            print_result(False, "Not Found - source_set_id가 존재하지 않음")
            return False
        else:
            print_result(False, f"Unexpected status: {response.status_code}")
            return False

    except httpx.ConnectError as e:
        print_result(False, f"Connection failed: {e}")
        print(f"\n  Hint: 백엔드 서버({backend_url})가 실행 중인지 확인하세요.")
        return False
    except Exception as e:
        print_result(False, f"Error: {e}")
        return False


def test_callback_failure(
    backend_url: str,
    source_set_id: str,
    video_id: str,
    request_id: str,
    token: str | None,
) -> bool:
    """실패 콜백 테스트 (FAILED)"""
    print_header("2. Failure Callback Test (FAILED)")

    url = f"{backend_url}/internal/callbacks/source-sets/{source_set_id}/complete"

    payload = {
        "videoId": video_id,  # UUID 형식
        "status": "FAILED",
        "sourceSetStatus": "FAILED",
        "documents": [
            {
                "documentId": generate_uuid(),
                "status": "FAILED",
                "failReason": "테스트 실패 사유",
            }
        ],
        "script": None,
        "errorCode": "TEST_ERROR",
        "errorMessage": "테스트용 실패 메시지입니다.",
        "requestId": request_id,  # UUID 형식 (선택)
        "traceId": f"trace-{uuid.uuid4().hex[:8]}",
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if token:
        headers["X-Internal-Token"] = token

    print(f"  URL: {url}")
    print(f"  videoId: {video_id}")
    print(f"  status: {payload['status']}")
    print(f"  errorCode: {payload['errorCode']}")
    print("\n  Request Body:")
    print_json(payload)

    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=30.0)

        print(f"\n  Response Status: {response.status_code}")
        print(f"  Response Body: {response.text[:500]}")

        if response.status_code in (200, 201, 204):
            print_result(True, "Failure callback sent successfully")
            return True
        elif response.status_code == 400:
            print_result(False, f"Bad Request: {response.text[:200]}")
            return False
        else:
            print_result(False, f"Unexpected status: {response.status_code}")
            return False

    except httpx.ConnectError as e:
        print_result(False, f"Connection failed: {e}")
        return False
    except Exception as e:
        print_result(False, f"Error: {e}")
        return False


def test_invalid_uuid(
    backend_url: str,
    source_set_id: str,
    token: str | None,
) -> bool:
    """잘못된 UUID 형식 테스트 (400 에러 예상)"""
    print_header("3. Invalid UUID Test (Expect 400 Error)")

    url = f"{backend_url}/internal/callbacks/source-sets/{source_set_id}/complete"

    payload = {
        "videoId": "not-a-valid-uuid",  # 잘못된 UUID
        "status": "COMPLETED",
        "sourceSetStatus": "SCRIPT_READY",
        "documents": [],
        "requestId": "also-not-uuid",  # 잘못된 UUID
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if token:
        headers["X-Internal-Token"] = token

    print(f"  URL: {url}")
    print(f"  videoId: {payload['videoId']} (INVALID)")
    print(f"  requestId: {payload['requestId']} (INVALID)")
    print("\n  Request Body:")
    print_json(payload)

    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=30.0)

        print(f"\n  Response Status: {response.status_code}")
        print(f"  Response Body: {response.text[:500]}")

        if response.status_code == 400:
            print_result(True, "Backend correctly rejected invalid UUID (400)")
            return True
        elif response.status_code in (200, 201, 204):
            print_result(False, "Backend accepted invalid UUID - should validate!")
            return False
        else:
            print_result(False, f"Unexpected status: {response.status_code}")
            return False

    except httpx.ConnectError as e:
        print_result(False, f"Connection failed: {e}")
        return False
    except Exception as e:
        print_result(False, f"Error: {e}")
        return False


def test_null_request_id(
    backend_url: str,
    source_set_id: str,
    video_id: str,
    token: str | None,
) -> bool:
    """requestId가 null인 경우 테스트 (정상 동작 예상)"""
    print_header("4. Null requestId Test (Should Work)")

    url = f"{backend_url}/internal/callbacks/source-sets/{source_set_id}/complete"

    payload = {
        "videoId": video_id,  # UUID 형식
        "status": "COMPLETED",
        "sourceSetStatus": "SCRIPT_READY",
        "documents": [],
        # requestId 없음 (null)
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if token:
        headers["X-Internal-Token"] = token

    print(f"  URL: {url}")
    print(f"  videoId: {video_id}")
    print(f"  requestId: (not provided / null)")
    print("\n  Request Body:")
    print_json(payload)

    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=30.0)

        print(f"\n  Response Status: {response.status_code}")
        print(f"  Response Body: {response.text[:500]}")

        if response.status_code in (200, 201, 204):
            print_result(True, "Null requestId accepted correctly")
            return True
        elif response.status_code == 400:
            print_result(False, f"Bad Request for null requestId: {response.text[:200]}")
            return False
        else:
            print_result(False, f"Unexpected status: {response.status_code}")
            return False

    except httpx.ConnectError as e:
        print_result(False, f"Connection failed: {e}")
        return False
    except Exception as e:
        print_result(False, f"Error: {e}")
        return False


def main():
    # =========================================================================
    # 실제 테스트 데이터 (백엔드 DB에 존재하는 값으로 변경하세요)
    # =========================================================================
    TEST_DATA = {
        "source_set_id": "da21f531-6e87-47dc-b359-5e0a742b59c3",
        "video_id": "f3cec586-b6db-4ad9-bd62-8ce85ec26640",
        "education_id": "64915d4e-aa78-4032-987e-dcd38fc90830",
        "document_id": "2cf84b64-ed70-4349-8ce3-9d1ef9449351",
    }
    # =========================================================================

    parser = argparse.ArgumentParser(description="SourceSet 콜백 API 테스트")
    parser.add_argument(
        "--backend-url",
        default=os.getenv("BACKEND_BASE_URL", "http://localhost:8080"),
        help="백엔드 서버 URL (default: BACKEND_BASE_URL env or localhost:8080)",
    )
    parser.add_argument(
        "--source-set-id",
        default=TEST_DATA["source_set_id"],
        help="테스트할 source_set_id",
    )
    parser.add_argument(
        "--video-id",
        default=TEST_DATA["video_id"],
        help="테스트할 video_id",
    )
    parser.add_argument(
        "--document-id",
        default=TEST_DATA["document_id"],
        help="테스트할 document_id",
    )
    parser.add_argument(
        "--education-id",
        default=TEST_DATA["education_id"],
        help="테스트할 education_id",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("BACKEND_INTERNAL_TOKEN"),
        help="X-Internal-Token (default: BACKEND_INTERNAL_TOKEN env)",
    )
    parser.add_argument(
        "--skip-invalid",
        action="store_true",
        help="잘못된 UUID 테스트 건너뛰기",
    )
    args = parser.parse_args()

    # 실제 테스트 데이터 사용
    source_set_id = args.source_set_id
    video_id = args.video_id
    education_id = args.education_id
    document_id = args.document_id
    request_id = generate_uuid()

    print("\n" + "=" * 70)
    print("  SourceSet Callback API Test")
    print("=" * 70)
    print(f"  Backend URL:   {args.backend_url}")
    print(f"  Source Set ID: {source_set_id}")
    print(f"  Video ID:      {video_id}")
    print(f"  Education ID:  {education_id}")
    print(f"  Document ID:   {document_id}")
    print(f"  Request ID:    {request_id}")
    print(f"  Token:         {'(set)' if args.token else '(not set)'}")
    print(f"  Time:          {datetime.now().isoformat()}")
    print("=" * 70)

    results = {}

    # 1. 성공 콜백 테스트
    results["success_callback"] = test_callback_success(
        args.backend_url, source_set_id, video_id, education_id, document_id, request_id, args.token
    )

    # 2. 실패 콜백 테스트 (새 source_set_id 사용)
    results["failure_callback"] = test_callback_failure(
        args.backend_url, generate_uuid(), video_id, generate_uuid(), args.token
    )

    # 3. 잘못된 UUID 테스트
    if not args.skip_invalid:
        results["invalid_uuid"] = test_invalid_uuid(
            args.backend_url, generate_uuid(), args.token
        )

    # 4. null requestId 테스트
    results["null_request_id"] = test_null_request_id(
        args.backend_url, generate_uuid(), generate_uuid(), args.token
    )

    # Summary
    print_header("Test Summary")
    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, success in results.items():
        status = "PASS" if success else "FAIL"
        symbol = "[v]" if success else "[x]"
        print(f"  {symbol} {name:20} : {status}")

    print(f"\n  Total: {passed}/{total} passed")
    print("=" * 70)

    # 힌트 출력
    if passed < total:
        print("\n  Hints:")
        if not results.get("success_callback"):
            print("    - 백엔드 서버가 실행 중인지 확인하세요")
            print("    - source_set_id가 백엔드 DB에 존재하는지 확인하세요")
            print("    - X-Internal-Token이 올바른지 확인하세요")
        print()

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
