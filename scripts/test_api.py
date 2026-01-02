#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
API 통합 테스트 스크립트

백엔드에서 AI Gateway로 보내는 요청을 시뮬레이션합니다.
- Chat API (채팅 + Milvus RAG)
- FAQ Generate API (FAQ 초안 생성)
- Quiz Generate API (퀴즈 생성)

사용법:
    python scripts/test_api.py [--base-url http://localhost:8000]
"""

import argparse
import os
import sys
import time

try:
    import httpx
except ImportError:
    print("httpx 패키지가 필요합니다: pip install httpx")
    sys.exit(1)


def print_header(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def print_result(success: bool, message: str):
    status = "PASS" if success else "FAIL"
    symbol = "[v]" if success else "[x]"
    print(f"  {symbol} {status}: {message}")


def test_health(base_url: str) -> bool:
    """헬스체크 테스트"""
    print_header("1. Health Check")
    try:
        response = httpx.get(f"{base_url}/health", timeout=10.0)
        if response.status_code == 200:
            data = response.json()
            print_result(True, f"Server OK - {data.get('app', 'unknown')}")
            return True
        else:
            print_result(False, f"Status: {response.status_code}")
            return False
    except Exception as e:
        print_result(False, f"Connection error: {e}")
        return False


def test_chat_api(base_url: str) -> bool:
    """Chat API 테스트 (Milvus 검색 포함)"""
    print_header("2. Chat API Test (EDUCATION Domain + Milvus)")

    payload = {
        "session_id": "test-session-001",
        "user_id": "test-user-001",
        "user_role": "EMPLOYEE",
        "domain": "EDUCATION",
        "messages": [
            {"role": "user", "content": "직장 내 성희롱 예방 교육 내용이 뭐야?"}
        ]
    }

    print(f"  Query: {payload['messages'][0]['content']}")
    print(f"  Domain: {payload['domain']}")

    try:
        start = time.time()
        response = httpx.post(
            f"{base_url}/ai/chat/messages",
            json=payload,
            timeout=120.0
        )
        elapsed = time.time() - start

        if response.status_code == 200:
            data = response.json()
            answer = data.get("answer", "")[:150]
            meta = data.get("metadata", {})
            sources = data.get("sources", [])

            print_result(True, f"Response received ({elapsed:.1f}s)")
            print(f"\n  Answer: {answer}...")
            print(f"\n  Metadata:")
            print(f"    - retriever_used: {meta.get('retriever_used', 'N/A')}")
            print(f"    - sources_count: {len(sources)}")

            if sources:
                print(f"\n  Top Sources:")
                for i, src in enumerate(sources[:3]):
                    sim = src.get("similarity", "N/A")
                    doc = src.get("doc_name", "N/A")
                    if isinstance(sim, (int, float)):
                        print(f"    {i+1}. {doc} (sim={sim:.4f})")
                    else:
                        print(f"    {i+1}. {doc}")

            # Milvus 사용 여부 확인
            if meta.get('retriever_used') == 'MILVUS':
                print(f"\n  [v] Milvus search confirmed!")

            return True
        else:
            print_result(False, f"Status: {response.status_code}")
            print(f"  Response: {response.text[:200]}")
            return False

    except Exception as e:
        print_result(False, f"Error: {e}")
        return False


def test_faq_generate_api(base_url: str) -> bool:
    """FAQ Generate API 테스트"""
    print_header("3. FAQ Generate API Test")

    payload = {
        "domain": "SEC_POLICY",
        "cluster_id": "test-cluster-001",
        "canonical_question": "USB 메모리 반출 시 어떤 절차가 필요한가요?",
        "sample_questions": [
            "USB 반출 어떻게 해요?",
            "외부 저장장치 반출 승인 절차가 뭔가요?"
        ]
    }

    print(f"  Domain: {payload['domain']}")
    print(f"  Question: {payload['canonical_question']}")

    try:
        start = time.time()
        response = httpx.post(
            f"{base_url}/ai/faq/generate",
            json=payload,
            timeout=120.0
        )
        elapsed = time.time() - start

        if response.status_code == 200:
            data = response.json()
            status = data.get("status", "N/A")
            faq_draft = data.get("faq_draft", {})

            print_result(True, f"Response received ({elapsed:.1f}s)")
            print(f"\n  Status: {status}")

            if faq_draft:
                print(f"  Generated Q: {faq_draft.get('question', 'N/A')[:80]}...")
                answer = faq_draft.get('answer_markdown', '')[:100]
                print(f"  Generated A: {answer}...")
                print(f"  Confidence: {faq_draft.get('ai_confidence', 'N/A')}")

            return status == "SUCCESS"
        elif response.status_code == 404:
            print_result(False, "Endpoint not found")
            return False
        else:
            print_result(False, f"Status: {response.status_code}")
            error = response.json().get("detail", response.text[:100])
            print(f"  Error: {error}")
            return False

    except Exception as e:
        print_result(False, f"Error: {e}")
        return False


def test_chat_policy_domain(base_url: str) -> bool:
    """Chat API 테스트 - POLICY 도메인"""
    print_header("4. Chat API Test (POLICY Domain)")

    payload = {
        "session_id": "test-session-002",
        "user_id": "test-user-001",
        "user_role": "EMPLOYEE",
        "domain": "POLICY",
        "messages": [
            {"role": "user", "content": "연차 사용 신청은 어떻게 하나요?"}
        ]
    }

    print(f"  Query: {payload['messages'][0]['content']}")
    print(f"  Domain: {payload['domain']}")

    try:
        start = time.time()
        response = httpx.post(
            f"{base_url}/ai/chat/messages",
            json=payload,
            timeout=120.0
        )
        elapsed = time.time() - start

        if response.status_code == 200:
            data = response.json()
            answer = data.get("answer", "")[:150]
            meta = data.get("metadata", {})

            print_result(True, f"Response received ({elapsed:.1f}s)")
            print(f"\n  Answer: {answer}...")
            print(f"  retriever_used: {meta.get('retriever_used', 'N/A')}")
            return True
        else:
            print_result(False, f"Status: {response.status_code}")
            return False

    except Exception as e:
        print_result(False, f"Error: {e}")
        return False


def test_quiz_generate_api(base_url: str) -> bool:
    """Quiz Generate API 테스트"""
    print_header("6. Quiz Generate API Test")

    payload = {
        "numQuestions": 3,
        "language": "ko",
        "maxOptions": 4,
        "quizCandidateBlocks": [
            {
                "blockId": "block-001",
                "docId": "doc-test-001",
                "text": "정보보안의 3요소는 기밀성, 무결성, 가용성입니다. 기밀성은 인가된 사용자만 정보에 접근할 수 있도록 하는 것이고, 무결성은 정보가 무단으로 변경되지 않도록 보호하는 것이며, 가용성은 필요할 때 정보에 접근할 수 있도록 보장하는 것입니다."
            },
            {
                "blockId": "block-002",
                "docId": "doc-test-001",
                "text": "패스워드는 최소 8자 이상, 영문 대소문자, 숫자, 특수문자를 조합하여 설정해야 합니다. 90일마다 변경해야 하며, 이전 3회 사용한 패스워드는 재사용할 수 없습니다."
            }
        ],
        "excludePreviousQuestions": []
    }

    print(f"  Blocks: {len(payload['quizCandidateBlocks'])}개")
    print(f"  numQuestions: {payload['numQuestions']}")

    try:
        start = time.time()
        response = httpx.post(
            f"{base_url}/ai/quiz/generate",
            json=payload,
            timeout=180.0
        )
        elapsed = time.time() - start

        if response.status_code == 200:
            data = response.json()
            generated = data.get("generatedCount", 0)
            questions = data.get("questions", [])

            print_result(True, f"Response received ({elapsed:.1f}s)")
            print(f"\n  Generated: {generated}문항")

            if questions:
                print(f"\n  Sample Questions:")
                for i, q in enumerate(questions[:2]):
                    stem = q.get("stem", "N/A")[:60]
                    diff = q.get("difficulty", "N/A")
                    print(f"    {i+1}. [{diff}] {stem}...")

            return generated > 0
        elif response.status_code == 404:
            print_result(False, "Endpoint not found")
            return False
        else:
            print_result(False, f"Status: {response.status_code}")
            try:
                error = response.json().get("detail", response.text[:100])
                print(f"  Error: {error}")
            except Exception:
                print(f"  Response: {response.text[:100]}")
            return False

    except Exception as e:
        print_result(False, f"Error: {e}")
        return False


def test_source_set_api(base_url: str) -> bool:
    """SourceSet API 테스트 (Internal API)"""
    print_header("7. SourceSet API Test (Internal)")

    source_set_id = "test-source-set-001"
    payload = {
        "videoId": "test-video-001",
        "requestId": "test-request-001",
        "traceId": "test-trace-001"
    }

    print(f"  SourceSetId: {source_set_id}")
    print(f"  VideoId: {payload['videoId']}")
    print(f"  Endpoint: /internal/ai/source-sets/{{sourceSetId}}/start")

    try:
        start = time.time()
        # Internal API는 X-Internal-Token이 필요할 수 있음
        internal_token = os.getenv("BACKEND_INTERNAL_TOKEN", "")
        headers = {
            "X-Internal-Token": internal_token
        }
        response = httpx.post(
            f"{base_url}/internal/ai/source-sets/{source_set_id}/start",
            json=payload,
            headers=headers,
            timeout=30.0
        )
        elapsed = time.time() - start

        # 202 Accepted가 정상 응답
        if response.status_code in (200, 202):
            data = response.json()
            status_val = data.get("status", "N/A")
            message = data.get("message", "N/A")

            print_result(True, f"Response received ({elapsed:.1f}s)")
            print(f"\n  Status: {status_val}")
            print(f"  Message: {message}")
            return True
        elif response.status_code == 401:
            print_result(False, "Unauthorized (X-Internal-Token required)")
            print(f"  Hint: 개발환경에서는 BACKEND_INTERNAL_TOKEN 미설정 시 통과됨")
            return False
        elif response.status_code == 403:
            print_result(False, "Forbidden (Invalid token)")
            return False
        elif response.status_code == 404:
            print_result(False, "Endpoint not found")
            return False
        else:
            print_result(False, f"Status: {response.status_code}")
            try:
                error = response.json().get("detail", response.text[:100])
                print(f"  Error: {error}")
            except Exception:
                print(f"  Response: {response.text[:100]}")
            return False

    except Exception as e:
        print_result(False, f"Error: {e}")
        return False


def test_chat_stream_api(base_url: str) -> bool:
    """Chat Stream API 테스트"""
    print_header("5. Chat Stream API Test")

    payload = {
        "session_id": "test-stream-001",
        "user_id": "test-user-001",
        "user_role": "EMPLOYEE",
        "domain": "EDUCATION",
        "messages": [
            {"role": "user", "content": "정보보안 교육 요약해줘"}
        ]
    }

    print(f"  Query: {payload['messages'][0]['content']}")
    print(f"  Endpoint: /ai/chat/stream")

    try:
        start = time.time()
        # Stream 엔드포인트 체크 (SSE)
        response = httpx.post(
            f"{base_url}/ai/chat/stream",
            json=payload,
            timeout=30.0
        )
        elapsed = time.time() - start

        if response.status_code == 200:
            # SSE 응답이면 성공
            content_type = response.headers.get("content-type", "")
            if "text/event-stream" in content_type or response.text:
                print_result(True, f"Stream started ({elapsed:.1f}s)")
                print(f"  Content-Type: {content_type[:50]}")
                # 첫 몇 글자만 출력
                preview = response.text[:100].replace('\n', ' ')
                print(f"  Preview: {preview}...")
                return True
            else:
                print_result(True, f"Response received ({elapsed:.1f}s)")
                return True
        elif response.status_code == 404:
            print_result(False, "Endpoint not found")
            return False
        else:
            print_result(False, f"Status: {response.status_code}")
            return False

    except Exception as e:
        print_result(False, f"Error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="API 통합 테스트")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--skip-health", action="store_true", help="Skip health check")
    parser.add_argument("--chat-only", action="store_true", help="Only test chat API")
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  AI Gateway API Integration Test")
    print("=" * 60)
    print(f"  Base URL: {args.base_url}")
    print("=" * 60)

    results = {}

    # Health Check
    if not args.skip_health:
        results["health"] = test_health(args.base_url)
        if not results["health"]:
            print("\n[!] Server is not running. Start the server first:")
            print("    python -m uvicorn app.main:app --port 8000")
            sys.exit(1)

    # Chat API (필수)
    results["chat"] = test_chat_api(args.base_url)

    if not args.chat_only:
        # FAQ Generate API
        results["faq_generate"] = test_faq_generate_api(args.base_url)

        # POLICY domain test
        results["chat_policy"] = test_chat_policy_domain(args.base_url)

        # Stream API test
        results["chat_stream"] = test_chat_stream_api(args.base_url)

        # Quiz Generate API
        results["quiz_generate"] = test_quiz_generate_api(args.base_url)

        # SourceSet API (Internal)
        results["source_set"] = test_source_set_api(args.base_url)

    # Summary
    print_header("Test Summary")
    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, success in results.items():
        status = "PASS" if success else "FAIL"
        print(f"  {name:15} : {status}")

    print(f"\n  Total: {passed}/{total} passed")
    print("=" * 60)

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
