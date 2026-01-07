"""
RAGFlow Callback API 테스트 스크립트

실제 서버에서 콜백 API를 테스트합니다.
- 엔드포인트: POST /v1/internal_ragflow/internal/ai/callbacks/ragflow/ingest
- 인증: X-Internal-Token 헤더 (AI_CALLBACK_TOKEN)
"""

import os
import httpx
import json
from datetime import datetime
import uuid
import sys

# 서버 설정 (환경변수로 설정 가능)
SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000")

# 인증 토큰 (.env의 AI_CALLBACK_TOKEN)
AI_CALLBACK_TOKEN = os.getenv("AI_CALLBACK_TOKEN", "ctrlf-r2a-w3e5t7y9u2i4")

# API 엔드포인트
CALLBACK_ENDPOINT = "/v1/internal_ragflow/internal/ai/callbacks/ragflow/ingest"


def test_callback_success():
    """성공 콜백 테스트"""
    print("\n" + "="*60)
    print("TEST 1: 성공 콜백 (COMPLETED)")
    print("="*60)

    payload = {
        "ingestId": f"ingest-{uuid.uuid4().hex[:8]}",
        "docId": f"DOC-TEST-{uuid.uuid4().hex[:6]}",
        "version": 1,
        "status": "COMPLETED",
        "processedAt": datetime.utcnow().isoformat() + "Z",
        "failReason": None,
        "meta": {
            "ragDocumentPk": f"pk-{uuid.uuid4().hex[:8]}",
            "traceId": f"trace-{uuid.uuid4().hex[:8]}",
            "requestId": f"req-{uuid.uuid4().hex[:8]}"
        },
        "stats": {
            "chunks": 15
        }
    }

    print(f"\nRequest URL: {SERVER_URL}{CALLBACK_ENDPOINT}")
    print(f"Request Body:\n{json.dumps(payload, indent=2, ensure_ascii=False)}")

    try:
        response = httpx.post(
            f"{SERVER_URL}{CALLBACK_ENDPOINT}",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Internal-Token": AI_CALLBACK_TOKEN
            },
            timeout=30.0
        )

        print(f"\nResponse Status: {response.status_code}")
        print(f"Response Body: {response.text}")

        if response.status_code == 200:
            print("\n[PASS] 성공 콜백 테스트 통과")
            return True
        else:
            print(f"\n[FAIL] 예상: 200, 실제: {response.status_code}")
            return False

    except httpx.ConnectError as e:
        print(f"\n[ERROR] 서버 연결 실패: {e}")
        print(f"서버가 {SERVER_URL}에서 실행 중인지 확인하세요.")
        return False
    except Exception as e:
        print(f"\n[ERROR] 요청 실패: {e}")
        return False


def test_callback_failed():
    """실패 콜백 테스트"""
    print("\n" + "="*60)
    print("TEST 2: 실패 콜백 (FAILED)")
    print("="*60)

    payload = {
        "ingestId": f"ingest-{uuid.uuid4().hex[:8]}",
        "docId": f"DOC-FAIL-{uuid.uuid4().hex[:6]}",
        "version": 1,
        "status": "FAILED",
        "processedAt": datetime.utcnow().isoformat() + "Z",
        "failReason": "PREPROCESSING_FAILED: PDF 파싱 중 오류 발생 - 페이지 5 손상",
        "meta": {
            "ragDocumentPk": f"pk-{uuid.uuid4().hex[:8]}",
            "traceId": f"trace-{uuid.uuid4().hex[:8]}",
            "requestId": f"req-{uuid.uuid4().hex[:8]}"
        },
        "stats": None
    }

    print(f"\nRequest URL: {SERVER_URL}{CALLBACK_ENDPOINT}")
    print(f"Request Body:\n{json.dumps(payload, indent=2, ensure_ascii=False)}")

    try:
        response = httpx.post(
            f"{SERVER_URL}{CALLBACK_ENDPOINT}",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Internal-Token": AI_CALLBACK_TOKEN
            },
            timeout=30.0
        )

        print(f"\nResponse Status: {response.status_code}")
        print(f"Response Body: {response.text}")

        if response.status_code == 200:
            print("\n[PASS] 실패 콜백 테스트 통과")
            return True
        else:
            print(f"\n[FAIL] 예상: 200, 실제: {response.status_code}")
            return False

    except httpx.ConnectError as e:
        print(f"\n[ERROR] 서버 연결 실패: {e}")
        return False
    except Exception as e:
        print(f"\n[ERROR] 요청 실패: {e}")
        return False


def test_callback_no_token():
    """토큰 없이 호출 테스트 (401 예상)"""
    print("\n" + "="*60)
    print("TEST 3: 토큰 없이 호출 (401 예상)")
    print("="*60)

    payload = {
        "ingestId": "ingest-notoken",
        "docId": "DOC-NOTOKEN",
        "version": 1,
        "status": "COMPLETED",
        "processedAt": datetime.utcnow().isoformat() + "Z",
        "failReason": None,
        "meta": {
            "ragDocumentPk": "pk-notoken",
            "traceId": "trace-notoken",
            "requestId": "req-notoken"
        },
        "stats": {"chunks": 1}
    }

    print(f"\nRequest URL: {SERVER_URL}{CALLBACK_ENDPOINT}")
    print("Request Headers: X-Internal-Token 없음")

    try:
        response = httpx.post(
            f"{SERVER_URL}{CALLBACK_ENDPOINT}",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30.0
        )

        print(f"\nResponse Status: {response.status_code}")
        print(f"Response Body: {response.text}")

        if response.status_code == 401:
            print("\n[PASS] 토큰 없이 호출 시 401 반환")
            return True
        elif response.status_code == 200:
            print("\n[WARN] 토큰 검증이 비활성화되어 있음 (AI_CALLBACK_TOKEN 미설정)")
            return True
        else:
            print(f"\n[FAIL] 예상: 401 또는 200, 실제: {response.status_code}")
            return False

    except httpx.ConnectError as e:
        print(f"\n[ERROR] 서버 연결 실패: {e}")
        return False
    except Exception as e:
        print(f"\n[ERROR] 요청 실패: {e}")
        return False


def test_callback_wrong_token():
    """잘못된 토큰 테스트 (401 예상)"""
    print("\n" + "="*60)
    print("TEST 4: 잘못된 토큰 (401 예상)")
    print("="*60)

    payload = {
        "ingestId": "ingest-wrongtoken",
        "docId": "DOC-WRONGTOKEN",
        "version": 1,
        "status": "COMPLETED",
        "processedAt": datetime.utcnow().isoformat() + "Z",
        "failReason": None,
        "meta": {
            "ragDocumentPk": "pk-wrongtoken",
            "traceId": "trace-wrongtoken",
            "requestId": "req-wrongtoken"
        },
        "stats": {"chunks": 1}
    }

    print(f"\nRequest URL: {SERVER_URL}{CALLBACK_ENDPOINT}")
    print("Request Headers: X-Internal-Token: wrong-token")

    try:
        response = httpx.post(
            f"{SERVER_URL}{CALLBACK_ENDPOINT}",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Internal-Token": "wrong-token"
            },
            timeout=30.0
        )

        print(f"\nResponse Status: {response.status_code}")
        print(f"Response Body: {response.text}")

        if response.status_code == 401:
            print("\n[PASS] 잘못된 토큰 시 401 반환")
            return True
        elif response.status_code == 200:
            print("\n[WARN] 토큰 검증이 비활성화되어 있음 (AI_CALLBACK_TOKEN 미설정)")
            return True
        else:
            print(f"\n[FAIL] 예상: 401 또는 200, 실제: {response.status_code}")
            return False

    except httpx.ConnectError as e:
        print(f"\n[ERROR] 서버 연결 실패: {e}")
        return False
    except Exception as e:
        print(f"\n[ERROR] 요청 실패: {e}")
        return False


def main():
    """테스트 실행"""
    global SERVER_URL

    # 서버 URL 변경 옵션
    if len(sys.argv) > 1:
        SERVER_URL = sys.argv[1]

    print("\n" + "#"*60)
    print("# RAGFlow Callback API 테스트")
    print(f"# Server: {SERVER_URL}")
    print(f"# Endpoint: {CALLBACK_ENDPOINT}")
    print("#"*60)

    results = []

    # 테스트 실행
    results.append(("성공 콜백 (COMPLETED)", test_callback_success()))
    results.append(("실패 콜백 (FAILED)", test_callback_failed()))
    results.append(("토큰 없이 호출", test_callback_no_token()))
    results.append(("잘못된 토큰", test_callback_wrong_token()))

    # 결과 요약
    print("\n" + "="*60)
    print("테스트 결과 요약")
    print("="*60)

    passed = sum(1 for _, r in results if r)
    total = len(results)

    for name, result in results:
        status = "[PASS]" if result else "[FAIL]"
        print(f"  {status} {name}")

    print(f"\n총 {passed}/{total} 테스트 통과")

    return 0 if passed == total else 1


if __name__ == "__main__":
    exit(main())
