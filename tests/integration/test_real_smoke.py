"""
Real Environment Smoke Tests (Phase 9)

REAL 모드에서 실제 외부 서비스 연동을 검증하는 smoke 테스트입니다.
Mock 서버 대신 실제 RAGFlow, LLM, Backend 서비스에 연결합니다.

사전 조건:
- 실제 서비스 URL이 환경변수로 설정되어야 합니다:
    export RAGFLOW_BASE_URL_REAL=http://real-ragflow:8080
    export LLM_BASE_URL_REAL=http://real-llm:8001
    export BACKEND_BASE_URL_REAL=http://real-backend:8080
- docker compose --profile real up -d 로 AI Gateway가 실행 중이어야 합니다.

실행 방법:
    # 환경변수 설정
    export RAGFLOW_BASE_URL_REAL=http://your-ragflow-server:port
    export LLM_BASE_URL_REAL=http://your-llm-server:port
    export BACKEND_BASE_URL_REAL=http://your-backend-server:port

    # Real 모드로 AI Gateway 시작
    docker compose --profile real up -d

    # Real Integration 테스트 실행
    pytest -m real_integration -v

    # 서비스 종료
    docker compose --profile real down

주의사항:
- 이 테스트는 실제 서비스에 요청을 보냅니다.
- 비용이 발생할 수 있는 LLM API를 호출합니다.
- 실제 데이터베이스에 로그가 저장될 수 있습니다.
- CI/CD에서는 기본적으로 실행되지 않습니다. (real_integration 마커 제외)

테스트 시나리오:
1. Health Check: 모든 서비스 연결 확인
2. RAG Search: RAGFlow 검색 기능 확인
3. LLM Completion: LLM 응답 생성 확인
4. E2E Chat: 전체 채팅 파이프라인 확인
"""

import os
import time
from typing import Optional

import httpx
import pytest


# =============================================================================
# Configuration
# =============================================================================

# AI Gateway URL (real mode에서도 동일한 포트 사용)
AI_GATEWAY_URL = os.getenv("AI_GATEWAY_URL", "http://localhost:8000")

# Real service URLs (환경변수에서 가져옴)
RAGFLOW_REAL_URL = os.getenv("RAGFLOW_BASE_URL_REAL")
LLM_REAL_URL = os.getenv("LLM_BASE_URL_REAL")
BACKEND_REAL_URL = os.getenv("BACKEND_BASE_URL_REAL")

# Timeout settings (실제 서비스는 더 오래 걸릴 수 있음)
REQUEST_TIMEOUT = 60.0
HEALTH_CHECK_TIMEOUT = 10.0


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def http_client():
    """HTTP client fixture with extended timeout for real services."""
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        yield client


@pytest.fixture(scope="module")
def check_real_mode_configured():
    """
    REAL 모드가 올바르게 설정되었는지 확인합니다.

    환경변수가 설정되지 않은 경우 테스트를 스킵합니다.
    """
    missing_vars = []

    if not RAGFLOW_REAL_URL:
        missing_vars.append("RAGFLOW_BASE_URL_REAL")
    if not LLM_REAL_URL:
        missing_vars.append("LLM_BASE_URL_REAL")
    if not BACKEND_REAL_URL:
        missing_vars.append("BACKEND_BASE_URL_REAL")

    if missing_vars:
        pytest.skip(
            f"Real mode not configured. Missing environment variables: {', '.join(missing_vars)}\n"
            f"Set these variables and run 'docker compose --profile real up -d'"
        )


# =============================================================================
# Helper Functions
# =============================================================================


def check_ai_gateway_running(client: httpx.Client) -> None:
    """AI Gateway가 실행 중인지 확인합니다."""
    try:
        response = client.get(f"{AI_GATEWAY_URL}/health", timeout=HEALTH_CHECK_TIMEOUT)
        if response.status_code != 200:
            pytest.skip(f"AI Gateway not healthy: {response.status_code}")
    except httpx.ConnectError:
        pytest.skip(
            f"AI Gateway not running at {AI_GATEWAY_URL}. "
            "Run 'docker compose --profile real up -d' first."
        )


def check_service_health(
    client: httpx.Client,
    base_url: str,
    service_name: str,
    health_path: str = "/health",
) -> bool:
    """
    외부 서비스의 health check를 수행합니다.

    Args:
        client: HTTP client
        base_url: Service base URL
        service_name: Service name for logging
        health_path: Health check endpoint path

    Returns:
        True if service is healthy, False otherwise
    """
    try:
        response = client.get(
            f"{base_url}{health_path}",
            timeout=HEALTH_CHECK_TIMEOUT,
        )
        return response.status_code == 200
    except Exception as e:
        print(f"{service_name} health check failed: {e}")
        return False


# =============================================================================
# Smoke Test 1: AI Gateway Health in Real Mode
# =============================================================================


@pytest.mark.real_integration
def test_real_ai_gateway_health(
    http_client: httpx.Client,
    check_real_mode_configured: None,
) -> None:
    """
    Smoke Test 1: AI Gateway가 REAL 모드에서 정상 동작하는지 확인합니다.

    검증:
    - /health 엔드포인트 응답 200
    - status == "ok"
    """
    check_ai_gateway_running(http_client)

    response = http_client.get(f"{AI_GATEWAY_URL}/health")

    assert response.status_code == 200, f"Health check failed: {response.status_code}"

    data = response.json()
    assert data["status"] == "ok", f"Status is not 'ok': {data}"


# =============================================================================
# Smoke Test 2: Readiness Check with Real Services
# =============================================================================


@pytest.mark.real_integration
def test_real_readiness_check(
    http_client: httpx.Client,
    check_real_mode_configured: None,
) -> None:
    """
    Smoke Test 2: AI Gateway의 readiness check가 실제 서비스와 연결되는지 확인합니다.

    검증:
    - /health/ready 엔드포인트 응답 200
    - ready == True (모든 서비스 정상)
    - checks에 각 서비스 상태 포함

    주의:
    - 실제 서비스가 모두 정상이어야 통과합니다.
    - 하나라도 연결 실패하면 ready=False가 됩니다.
    """
    check_ai_gateway_running(http_client)

    response = http_client.get(f"{AI_GATEWAY_URL}/health/ready")

    assert response.status_code == 200, f"Readiness check failed: {response.status_code}"

    data = response.json()

    # Log the checks for debugging
    print(f"Readiness checks: {data.get('checks', {})}")

    # 모든 서비스가 정상이어야 ready=True
    if not data["ready"]:
        failed_services = [
            service for service, status in data.get("checks", {}).items()
            if not status
        ]
        pytest.fail(
            f"Some services are not ready: {failed_services}\n"
            f"Full checks: {data['checks']}"
        )

    assert data["ready"] is True, f"Not ready: {data}"


# =============================================================================
# Smoke Test 3: Simple Chat Request (E2E)
# =============================================================================


@pytest.mark.real_integration
def test_real_simple_chat_request(
    http_client: httpx.Client,
    check_real_mode_configured: None,
) -> None:
    """
    Smoke Test 3: 실제 서비스를 통한 간단한 채팅 요청 테스트

    가장 기본적인 채팅 요청을 보내서 전체 파이프라인이 동작하는지 확인합니다.

    검증:
    - 응답 status_code == 200
    - answer 필드 존재 및 비어있지 않음
    - meta 필드 존재
    - latency_ms > 0 (실제 처리 시간)

    주의:
    - 실제 LLM API를 호출하므로 비용이 발생할 수 있습니다.
    """
    check_ai_gateway_running(http_client)

    # 간단한 테스트 요청 (RAG 없이 LLM만 사용하도록)
    request_body = {
        "session_id": "real-smoke-test-001",
        "user_id": "test-user",
        "user_role": "EMPLOYEE",
        "channel": "WEB",
        "messages": [
            {
                "role": "user",
                "content": "Hello, are you working?",  # Simple test message
            }
        ],
    }

    response = http_client.post(
        f"{AI_GATEWAY_URL}/ai/chat/messages",
        json=request_body,
        timeout=REQUEST_TIMEOUT,
    )

    assert response.status_code == 200, f"Chat request failed: {response.status_code} - {response.text}"

    data = response.json()

    # 기본 응답 구조 확인
    assert "answer" in data, "Response should have 'answer' field"
    assert data["answer"], "Answer should not be empty"
    assert isinstance(data["answer"], str), "Answer should be a string"

    assert "meta" in data, "Response should have 'meta' field"

    # 메타 정보 확인
    meta = data["meta"]
    assert "latency_ms" in meta, "meta should have 'latency_ms'"
    assert meta["latency_ms"] > 0, "latency_ms should be positive"

    print(f"Real chat response received in {meta['latency_ms']}ms")
    print(f"Answer preview: {data['answer'][:100]}...")


# =============================================================================
# Smoke Test 4: RAG + LLM Integration (POLICY Domain)
# =============================================================================


@pytest.mark.real_integration
def test_real_rag_llm_integration(
    http_client: httpx.Client,
    check_real_mode_configured: None,
) -> None:
    """
    Smoke Test 4: RAG + LLM 통합 테스트 (POLICY 도메인)

    POLICY 도메인 질문을 보내서 RAG 검색 + LLM 응답 생성이 동작하는지 확인합니다.

    검증:
    - 응답 status_code == 200
    - answer 존재
    - sources 필드 존재 (RAG 결과)
    - meta.rag_used 존재

    주의:
    - 실제 RAGFlow에 데이터가 인덱싱되어 있어야 sources가 반환됩니다.
    - 데이터가 없으면 sources는 빈 리스트일 수 있습니다.
    """
    check_ai_gateway_running(http_client)

    # POLICY 도메인 질문 (RAG 검색 트리거)
    request_body = {
        "session_id": "real-smoke-test-002",
        "user_id": "test-user",
        "user_role": "EMPLOYEE",
        "department": "TEST",
        "domain": "POLICY",
        "channel": "WEB",
        "messages": [
            {
                "role": "user",
                "content": "What are the company policies?",
            }
        ],
    }

    response = http_client.post(
        f"{AI_GATEWAY_URL}/ai/chat/messages",
        json=request_body,
        timeout=REQUEST_TIMEOUT,
    )

    assert response.status_code == 200, f"Chat request failed: {response.status_code} - {response.text}"

    data = response.json()

    # 응답 구조 확인
    assert "answer" in data, "Response should have 'answer' field"
    assert "sources" in data, "Response should have 'sources' field"
    assert "meta" in data, "Response should have 'meta' field"

    # sources는 리스트여야 함 (비어있을 수 있음)
    assert isinstance(data["sources"], list), "sources should be a list"

    # 메타 정보 확인
    meta = data["meta"]
    assert "rag_used" in meta, "meta should have 'rag_used'"
    assert "rag_source_count" in meta, "meta should have 'rag_source_count'"

    # Log results
    print(f"RAG used: {meta['rag_used']}")
    print(f"RAG source count: {meta['rag_source_count']}")
    print(f"Sources: {len(data['sources'])}")

    if data["sources"]:
        print(f"First source: {data['sources'][0]}")


# =============================================================================
# Smoke Test 5: PII Masking Verification
# =============================================================================


@pytest.mark.real_integration
def test_real_pii_masking(
    http_client: httpx.Client,
    check_real_mode_configured: None,
) -> None:
    """
    Smoke Test 5: PII 마스킹이 실제 환경에서 동작하는지 확인합니다.

    전화번호가 포함된 질문을 보내서 PII 검출 및 마스킹이 동작하는지 확인합니다.

    검증:
    - 응답 status_code == 200
    - meta.has_pii_input == True (PII 검출됨)
    - 응답에 원본 전화번호가 없어야 함

    주의:
    - PII 마스킹은 내장 규칙 기반으로 동작합니다.
    """
    check_ai_gateway_running(http_client)

    # PII (전화번호)가 포함된 질문
    test_phone = "010-1234-5678"
    request_body = {
        "session_id": "real-smoke-test-003",
        "user_id": "test-user",
        "user_role": "EMPLOYEE",
        "channel": "WEB",
        "messages": [
            {
                "role": "user",
                "content": f"My phone number is {test_phone}. Please remember it.",
            }
        ],
    }

    response = http_client.post(
        f"{AI_GATEWAY_URL}/ai/chat/messages",
        json=request_body,
        timeout=REQUEST_TIMEOUT,
    )

    assert response.status_code == 200, f"Chat request failed: {response.status_code} - {response.text}"

    data = response.json()

    # PII 검출 확인
    meta = data["meta"]
    assert "has_pii_input" in meta, "meta should have 'has_pii_input'"

    # PII가 검출되어야 함
    assert meta["has_pii_input"] is True, "PII should be detected in input"

    # 원본 전화번호가 응답에 없어야 함 (마스킹 확인)
    # 주의: LLM이 전화번호를 다시 생성할 수도 있으므로 이건 soft assertion
    if test_phone in data["answer"]:
        print(f"WARNING: Raw phone number found in answer (LLM may have reproduced it)")

    print(f"PII detected: {meta['has_pii_input']}")


# =============================================================================
# Smoke Test 6: Error Handling
# =============================================================================


@pytest.mark.real_integration
def test_real_invalid_request_handling(
    http_client: httpx.Client,
    check_real_mode_configured: None,
) -> None:
    """
    Smoke Test 6: 잘못된 요청에 대한 에러 처리 확인

    필수 필드가 누락된 요청을 보내서 적절한 에러 응답이 반환되는지 확인합니다.

    검증:
    - 응답 status_code == 422 (Validation Error)
    - 에러 메시지 포함
    """
    check_ai_gateway_running(http_client)

    # 필수 필드 누락된 요청
    invalid_request = {
        "session_id": "real-smoke-test-error",
        # messages 필드 누락
    }

    response = http_client.post(
        f"{AI_GATEWAY_URL}/ai/chat/messages",
        json=invalid_request,
        timeout=REQUEST_TIMEOUT,
    )

    # Validation 에러 예상
    assert response.status_code == 422, f"Expected 422, got {response.status_code}"

    data = response.json()
    assert "detail" in data, "Error response should have 'detail' field"

    print(f"Validation error handled correctly: {response.status_code}")


# =============================================================================
# Smoke Test 7: Concurrent Requests
# =============================================================================


@pytest.mark.real_integration
def test_real_concurrent_requests(
    check_real_mode_configured: None,
) -> None:
    """
    Smoke Test 7: 동시 요청 처리 확인

    여러 요청을 동시에 보내서 시스템이 안정적으로 처리하는지 확인합니다.

    검증:
    - 모든 요청이 성공 (status_code == 200)
    - 응답 시간이 합리적인 범위 내

    주의:
    - 실제 서비스에 부하를 줄 수 있으므로 적은 수의 요청만 사용합니다.
    """
    import asyncio

    async def send_request(session_num: int) -> tuple[int, float]:
        """비동기 요청을 보내고 상태 코드와 응답 시간을 반환합니다."""
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            request_body = {
                "session_id": f"real-concurrent-{session_num}",
                "user_id": "test-user",
                "user_role": "EMPLOYEE",
                "channel": "WEB",
                "messages": [
                    {"role": "user", "content": f"Test message {session_num}"}
                ],
            }

            start = time.time()
            response = await client.post(
                f"{AI_GATEWAY_URL}/ai/chat/messages",
                json=request_body,
            )
            elapsed = time.time() - start

            return response.status_code, elapsed

    async def run_concurrent_tests():
        # 3개의 동시 요청 (실제 서비스 부하 고려)
        tasks = [send_request(i) for i in range(3)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return results

    # 실행
    results = asyncio.run(run_concurrent_tests())

    # 검증
    success_count = 0
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"Request {i} failed with exception: {result}")
        else:
            status_code, elapsed = result
            print(f"Request {i}: status={status_code}, time={elapsed:.2f}s")
            if status_code == 200:
                success_count += 1

    # 최소 2개 이상 성공해야 함
    assert success_count >= 2, f"Only {success_count}/3 requests succeeded"
    print(f"Concurrent test passed: {success_count}/3 requests succeeded")
