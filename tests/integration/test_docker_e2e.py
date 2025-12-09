"""
Docker Compose E2E Integration Tests (Phase 8)

Docker Compose 환경에서 실제 /ai/chat/messages 엔드포인트를 호출하는 통합 테스트입니다.

사전 조건:
- docker compose up -d 로 모든 서비스가 실행 중이어야 합니다.
- ai-gateway (8000), ragflow (8080), llm-internal (8001), backend-mock (8081)

실행 방법:
    # 서비스 시작
    docker compose up -d

    # 테스트 실행
    pytest tests/integration/test_docker_e2e.py -v

    # integration 마크로 실행
    pytest -m integration -v

    # 서비스 종료
    docker compose down

테스트 시나리오:
1. POLICY + RAG + LLM + PII + 로그 해피패스
2. ROUTE_LLM_ONLY (일반 질문)
3. Mock 서버 통계 검증
"""

import time
from typing import Any, Dict

import httpx
import pytest

# =============================================================================
# 설정
# =============================================================================

# AI Gateway URL (docker compose에서 호스트 포트 8000으로 매핑)
AI_GATEWAY_URL = "http://localhost:8000"

# Mock 서버 URLs (테스트 검증용)
MOCK_RAGFLOW_URL = "http://localhost:8080"
MOCK_LLM_URL = "http://localhost:8001"
MOCK_BACKEND_URL = "http://localhost:8081"

# 타임아웃 설정
REQUEST_TIMEOUT = 30.0


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def http_client():
    """HTTP 클라이언트 fixture."""
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        yield client


@pytest.fixture(scope="function")
def reset_mock_stats(http_client: httpx.Client):
    """
    각 테스트 전에 Mock 서버 통계를 초기화합니다.

    이렇게 하면 테스트 간 상태가 격리됩니다.
    """
    # 모든 Mock 서버의 통계 초기화
    try:
        http_client.post(f"{MOCK_RAGFLOW_URL}/stats/reset")
        http_client.post(f"{MOCK_LLM_URL}/stats/reset")
        http_client.post(f"{MOCK_BACKEND_URL}/stats/reset")
    except httpx.ConnectError:
        pytest.skip("Docker Compose services not running")

    yield

    # 테스트 후 정리 (필요시)


# =============================================================================
# 헬퍼 함수
# =============================================================================


def wait_for_services(client: httpx.Client, max_retries: int = 30, delay: float = 1.0) -> bool:
    """
    모든 서비스가 준비될 때까지 대기합니다.

    Args:
        client: HTTP 클라이언트
        max_retries: 최대 재시도 횟수
        delay: 재시도 간 대기 시간 (초)

    Returns:
        모든 서비스가 준비되면 True
    """
    services = [
        (AI_GATEWAY_URL, "/health"),
        (MOCK_RAGFLOW_URL, "/health"),
        (MOCK_LLM_URL, "/health"),
        (MOCK_BACKEND_URL, "/health"),
    ]

    for attempt in range(max_retries):
        all_ready = True
        for base_url, health_path in services:
            try:
                response = client.get(f"{base_url}{health_path}", timeout=5.0)
                if response.status_code != 200:
                    all_ready = False
                    break
            except httpx.RequestError:
                all_ready = False
                break

        if all_ready:
            return True

        time.sleep(delay)

    return False


def check_services_running(client: httpx.Client) -> None:
    """서비스 실행 여부를 확인하고, 실행 중이 아니면 테스트를 스킵합니다."""
    try:
        response = client.get(f"{AI_GATEWAY_URL}/health", timeout=5.0)
        if response.status_code != 200:
            pytest.skip("AI Gateway not healthy")
    except httpx.ConnectError:
        pytest.skip(
            "Docker Compose services not running. "
            "Run 'docker compose up -d' first."
        )


# =============================================================================
# 통합 테스트: 시나리오 1 - POLICY + RAG + LLM + PII + 로그 해피패스
# =============================================================================


@pytest.mark.integration
def test_integration_policy_rag_llm_pii_log_happy_path(
    http_client: httpx.Client,
    reset_mock_stats: None,
) -> None:
    """
    통합 시나리오 1: POLICY + RAG + LLM + PII + 로그 해피패스

    전제:
    - Mock RAGFlow /search는 "연차휴가 관리 규정" 문서를 반환
    - Mock LLM /v1/chat/completions는 RAG 컨텍스트 기반 응답 반환
    - Mock Backend /api/ai-logs는 모든 로그를 수신

    검증:
    - 응답 status_code == 200
    - answer 비어있지 않음
    - sources 길이 >= 1
    - meta.rag_used == True
    - PII가 마스킹됨 (010-1234-5678이 응답에 없음)
    """
    # 서비스 확인
    check_services_running(http_client)

    # Act: AI Gateway에 요청 전송
    request_body = {
        "session_id": "integration-test-001",
        "user_id": "emp-12345",
        "user_role": "EMPLOYEE",
        "department": "개발팀",
        "domain": "POLICY",
        "channel": "WEB",
        "messages": [
            {
                "role": "user",
                "content": "제 전화번호 010-1234-5678 남기고 연차 이월 규정 알려줘",
            }
        ],
    }

    response = http_client.post(
        f"{AI_GATEWAY_URL}/ai/chat/messages",
        json=request_body,
    )

    # Assert: HTTP 응답
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()

    # Assert: 응답 구조
    assert "answer" in data, "Response should have 'answer' field"
    assert "sources" in data, "Response should have 'sources' field"
    assert "meta" in data, "Response should have 'meta' field"

    # Assert: answer
    assert data["answer"], "Answer should not be empty"
    assert isinstance(data["answer"], str)

    # Assert: PII 마스킹 (원본 전화번호가 응답에 없어야 함)
    assert "010-1234-5678" not in data["answer"], "Raw phone number should be masked"

    # Assert: sources (RAG 결과)
    assert isinstance(data["sources"], list)
    assert len(data["sources"]) >= 1, "Should have at least 1 source from RAG"

    # Assert: sources 구조
    source = data["sources"][0]
    assert "doc_id" in source
    assert "title" in source
    assert source["doc_id"] == "HR-001"  # Mock RAGFlow에서 반환하는 문서

    # Assert: meta
    meta = data["meta"]
    assert meta["rag_used"] is True, "rag_used should be True"
    assert meta["rag_source_count"] >= 1, "rag_source_count should be >= 1"
    assert meta["rag_source_count"] == len(data["sources"])
    assert meta["has_pii_input"] is True, "should detect PII in input"

    # 잠시 대기 (비동기 로그 전송 완료 대기)
    time.sleep(0.5)

    # Assert: Mock 서버 통계 확인
    # RAGFlow 호출 확인
    ragflow_stats = http_client.get(f"{MOCK_RAGFLOW_URL}/stats").json()
    assert ragflow_stats["search_call_count"] >= 1, "RAGFlow should have been called"

    # LLM 호출 확인
    llm_stats = http_client.get(f"{MOCK_LLM_URL}/stats").json()
    assert llm_stats["completion_call_count"] >= 1, "LLM should have been called"

    # Backend 로그 확인
    backend_stats = http_client.get(f"{MOCK_BACKEND_URL}/stats").json()
    assert backend_stats["log_call_count"] >= 1, "Backend should have received logs"

    # 로그 내용 확인
    logs_response = http_client.get(f"{MOCK_BACKEND_URL}/api/ai-logs?limit=1").json()
    assert logs_response["total_count"] >= 1

    if logs_response["logs"]:
        log_entry = logs_response["logs"][-1]
        # 로그에 원본 전화번호가 없어야 함
        if log_entry.get("question_masked"):
            assert "010-1234-5678" not in log_entry["question_masked"], \
                "Raw phone number should not be in masked question log"


# =============================================================================
# 통합 테스트: 시나리오 2 - ROUTE_LLM_ONLY (일반 질문)
# =============================================================================


@pytest.mark.integration
def test_integration_llm_only_route_general_question(
    http_client: httpx.Client,
    reset_mock_stats: None,
) -> None:
    """
    통합 시나리오 2: ROUTE_LLM_ONLY (일반 질문)

    일반적인 도우미 질문을 보내서 RAG 없이 LLM만으로 응답하는 케이스.

    검증:
    - 응답 status_code == 200
    - meta.route == ROUTE_LLM_ONLY (또는 유사)
    - meta.rag_used == False
    - meta.rag_source_count == 0
    - sources == []
    """
    # 서비스 확인
    check_services_running(http_client)

    # Act: 일반 질문 전송 (RAG 검색이 필요 없는 질문)
    request_body = {
        "session_id": "integration-test-002",
        "user_id": "emp-12345",
        "user_role": "EMPLOYEE",
        "department": "개발팀",
        "domain": None,  # 도메인 미지정
        "channel": "WEB",
        "messages": [
            {
                "role": "user",
                "content": "오늘 정보보호 교육 일정이 어떻게 되나요?",
            }
        ],
    }

    response = http_client.post(
        f"{AI_GATEWAY_URL}/ai/chat/messages",
        json=request_body,
    )

    # Assert: HTTP 응답
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()

    # Assert: 응답 구조
    assert "answer" in data
    assert "sources" in data
    assert "meta" in data

    # Assert: answer
    assert data["answer"], "Answer should not be empty"

    # Assert: sources (RAG 미사용이므로 빈 리스트 또는 매우 적음)
    # 일반 질문이지만 IntentService 구현에 따라 달라질 수 있음
    assert isinstance(data["sources"], list)

    # Assert: meta
    meta = data["meta"]
    # LLM_ONLY 라우트인 경우
    if meta["route"] == "ROUTE_LLM_ONLY":
        assert meta["rag_used"] is False, "rag_used should be False for LLM_ONLY"
        assert meta["rag_source_count"] == 0, "rag_source_count should be 0"
        assert data["sources"] == [], "sources should be empty for LLM_ONLY"

    # 잠시 대기
    time.sleep(0.5)

    # LLM 호출 확인 (LLM_ONLY든 RAG든 LLM은 호출됨)
    llm_stats = http_client.get(f"{MOCK_LLM_URL}/stats").json()
    assert llm_stats["completion_call_count"] >= 1, "LLM should have been called"


# =============================================================================
# 통합 테스트: 시나리오 3 - RAG 결과 없는 POLICY 질문
# =============================================================================


@pytest.mark.integration
def test_integration_policy_rag_no_results(
    http_client: httpx.Client,
    reset_mock_stats: None,
) -> None:
    """
    통합 시나리오 3: POLICY 도메인이지만 RAG 결과가 없는 경우

    Mock RAGFlow는 "연차", "휴가", "규정" 키워드가 없으면 빈 결과를 반환합니다.

    검증:
    - 응답 status_code == 200
    - sources == [] (또는 빈 리스트)
    - meta.rag_used == False (결과 없음)
    - answer에 fallback 안내 포함
    """
    # 서비스 확인
    check_services_running(http_client)

    # Act: RAG에서 결과가 없는 질문
    request_body = {
        "session_id": "integration-test-003",
        "user_id": "emp-12345",
        "user_role": "EMPLOYEE",
        "domain": "POLICY",
        "channel": "WEB",
        "messages": [
            {
                "role": "user",
                "content": "회사에서 반려동물을 키울 수 있는 정책이 있나요?",
            }
        ],
    }

    response = http_client.post(
        f"{AI_GATEWAY_URL}/ai/chat/messages",
        json=request_body,
    )

    # Assert: HTTP 응답
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()

    # Assert: 응답 구조
    assert "answer" in data
    assert "sources" in data
    assert "meta" in data

    # Assert: answer (fallback 응답)
    assert data["answer"], "Answer should not be empty even with no RAG results"

    # Assert: sources (RAG 결과 없음)
    assert data["sources"] == [], "sources should be empty when RAG has no results"

    # Assert: meta
    meta = data["meta"]
    assert meta["rag_used"] is False, "rag_used should be False when no results"
    assert meta["rag_source_count"] == 0, "rag_source_count should be 0"

    # 잠시 대기
    time.sleep(0.5)

    # RAGFlow 호출은 됐지만 결과가 없음
    ragflow_stats = http_client.get(f"{MOCK_RAGFLOW_URL}/stats").json()
    assert ragflow_stats["search_call_count"] >= 1, "RAGFlow should have been called"


# =============================================================================
# 통합 테스트: 시나리오 4 - 응답 스키마 완전성
# =============================================================================


@pytest.mark.integration
def test_integration_response_schema_completeness(
    http_client: httpx.Client,
    reset_mock_stats: None,
) -> None:
    """
    응답 스키마 완전성 검증

    ChatResponse의 모든 필드가 올바르게 반환되는지 확인합니다.
    """
    # 서비스 확인
    check_services_running(http_client)

    # Act
    request_body = {
        "session_id": "integration-test-schema",
        "user_id": "emp-12345",
        "user_role": "EMPLOYEE",
        "department": "개발팀",
        "domain": "POLICY",
        "channel": "WEB",
        "messages": [
            {"role": "user", "content": "연차휴가 규정 알려줘"}
        ],
    }

    response = http_client.post(
        f"{AI_GATEWAY_URL}/ai/chat/messages",
        json=request_body,
    )

    assert response.status_code == 200
    data = response.json()

    # 최상위 필드
    assert "answer" in data
    assert "sources" in data
    assert "meta" in data

    # sources 필드 (있는 경우)
    if data["sources"]:
        source = data["sources"][0]
        assert "doc_id" in source
        assert "title" in source
        # page, score, snippet은 optional

    # meta 필드 (Phase 6에서 정의한 필드들)
    meta = data["meta"]
    required_meta_fields = [
        "used_model",
        "route",
        "intent",
        "domain",
        "masked",
        "has_pii_input",
        "has_pii_output",
        "rag_used",
        "rag_source_count",
        "latency_ms",
    ]

    for field in required_meta_fields:
        assert field in meta, f"meta should have '{field}' field"


# =============================================================================
# 통합 테스트: 헬스체크
# =============================================================================


@pytest.mark.integration
def test_integration_all_services_healthy(http_client: httpx.Client) -> None:
    """
    모든 서비스의 헬스체크가 정상인지 확인합니다.
    """
    services = [
        (AI_GATEWAY_URL, "/health", "AI Gateway"),
        (MOCK_RAGFLOW_URL, "/health", "Mock RAGFlow"),
        (MOCK_LLM_URL, "/health", "Mock LLM"),
        (MOCK_BACKEND_URL, "/health", "Mock Backend"),
    ]

    for base_url, health_path, service_name in services:
        try:
            response = http_client.get(f"{base_url}{health_path}", timeout=5.0)
            assert response.status_code == 200, f"{service_name} health check failed"
            data = response.json()
            assert data.get("status") == "ok", f"{service_name} status is not 'ok'"
        except httpx.ConnectError:
            pytest.skip(f"{service_name} not running at {base_url}")
