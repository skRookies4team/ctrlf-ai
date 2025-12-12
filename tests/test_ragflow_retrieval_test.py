"""
RagflowClient /retrieval_test 엔드포인트 통합 테스트 (Phase 9)

ctrlf-ragflow의 /retrieval_test API에 맞춰 수정된 RagflowClient를 검증합니다.

테스트 목표:
1. /retrieval_test로 요청이 나가는지 확인
2. query/top_k/dataset이 question/size/kb_id로 제대로 매핑되는지 확인
3. 응답의 chunks가 RagDocument 리스트로 잘 변환되는지 확인
4. _dataset_to_kb_id() 헬퍼 메서드 동작 확인

테스트 방법:
- httpx.MockTransport를 사용하여 HTTP 요청/응답을 모킹
- 실제 외부 서비스 호출 없이 요청 페이로드 검증
"""

import json
from typing import Any, Dict, List

import httpx
import pytest

from app.clients.ragflow_client import RagflowClient
from app.models.rag import RagDocument


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


@pytest.fixture(autouse=True)
def disable_search_wrapper(monkeypatch):
    """이 테스트 파일에서는 retrieval_test 직접 호출을 테스트하므로 래퍼 비활성화."""
    monkeypatch.setattr(RagflowClient, "USE_SEARCH_WRAPPER", False)


# =============================================================================
# Mock Response Data
# =============================================================================


def create_mock_chunks_response(chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Create mock /retrieval_test response."""
    return {"chunks": chunks}


SAMPLE_CHUNKS = [
    {
        "chunk_id": "chunk-001",
        "doc_name": "연차휴가 관리 규정",
        "page_num": 12,
        "similarity": 0.92,
        "content": "연차휴가는 최대 10일까지 이월할 수 있다...",
    },
    {
        "chunk_id": "chunk-002",
        "doc_name": "근태관리 지침",
        "page_num": 5,
        "similarity": 0.85,
        "content": "지각 3회는 결근 1일로 간주되며...",
    },
    {
        "chunk_id": "chunk-003",
        "doc_name": "보안 사고 대응 매뉴얼",
        "page_num": 20,
        "similarity": 0.78,
        "content": "보안 사고 발생 시 즉시 보고해야 한다...",
    },
]


# =============================================================================
# 테스트 1: 요청 페이로드 매핑 검증
# =============================================================================


@pytest.mark.anyio
async def test_retrieval_test_request_payload_mapping() -> None:
    """
    테스트 1: /retrieval_test 요청 페이로드 매핑 검증

    query → question
    top_k → size
    dataset → kb_id (via _dataset_to_kb_id)
    page → 1 (고정)
    """
    captured_request = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_request["url"] = str(request.url)
        captured_request["method"] = request.method
        captured_request["payload"] = json.loads(request.content)
        return httpx.Response(
            status_code=200,
            json=create_mock_chunks_response(SAMPLE_CHUNKS),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ragflow = RagflowClient(
            base_url="http://test-ragflow:8080",
            client=client,
        )

        await ragflow.search(
            query="연차휴가 이월 규정이 어떻게 되나요?",
            top_k=5,
            dataset="POLICY",
        )

    # Assert: URL
    assert "/v1/chunk/retrieval_test" in captured_request["url"]
    assert captured_request["method"] == "POST"

    # Assert: Payload mapping
    payload = captured_request["payload"]
    assert payload["question"] == "연차휴가 이월 규정이 어떻게 되나요?"
    assert payload["size"] == 5
    assert payload["kb_id"] == "kb_policy_001"
    assert payload["page"] == 1


@pytest.mark.anyio
async def test_retrieval_test_different_datasets() -> None:
    """
    테스트 2: 다양한 dataset → kb_id 매핑 검증

    POLICY → kb_policy_001
    INCIDENT → kb_incident_001
    EDUCATION → kb_education_001
    """
    captured_payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured_payloads.append(payload)
        return httpx.Response(
            status_code=200,
            json=create_mock_chunks_response([]),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ragflow = RagflowClient(
            base_url="http://test-ragflow:8080",
            client=client,
        )

        # Test POLICY
        await ragflow.search(query="test", dataset="POLICY")
        assert captured_payloads[-1]["kb_id"] == "kb_policy_001"

        # Test INCIDENT
        await ragflow.search(query="test", dataset="INCIDENT")
        assert captured_payloads[-1]["kb_id"] == "kb_incident_001"

        # Test EDUCATION
        await ragflow.search(query="test", dataset="EDUCATION")
        assert captured_payloads[-1]["kb_id"] == "kb_education_001"

        # Test lowercase (should be converted to uppercase)
        await ragflow.search(query="test", dataset="policy")
        assert captured_payloads[-1]["kb_id"] == "kb_policy_001"


@pytest.mark.anyio
async def test_retrieval_test_default_dataset() -> None:
    """
    테스트 3: dataset이 None인 경우 기본값 POLICY 사용
    """
    captured_payload = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payload.update(json.loads(request.content))
        return httpx.Response(
            status_code=200,
            json=create_mock_chunks_response([]),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ragflow = RagflowClient(
            base_url="http://test-ragflow:8080",
            client=client,
        )

        await ragflow.search(query="test query", dataset=None)

    assert captured_payload["kb_id"] == "kb_policy_001"


@pytest.mark.anyio
async def test_retrieval_test_unknown_dataset_fallback() -> None:
    """
    테스트 4: 알 수 없는 dataset인 경우 POLICY로 fallback
    """
    captured_payload = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payload.update(json.loads(request.content))
        return httpx.Response(
            status_code=200,
            json=create_mock_chunks_response([]),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ragflow = RagflowClient(
            base_url="http://test-ragflow:8080",
            client=client,
        )

        await ragflow.search(query="test", dataset="UNKNOWN_DOMAIN")

    # Unknown dataset should fall back to POLICY
    assert captured_payload["kb_id"] == "kb_policy_001"


# =============================================================================
# 테스트 5: 응답 파싱 (chunks → RagDocument)
# =============================================================================


@pytest.mark.anyio
async def test_retrieval_test_response_parsing() -> None:
    """
    테스트 5: /retrieval_test 응답을 RagDocument 리스트로 변환 검증

    chunks[].chunk_id → RagDocument.doc_id
    chunks[].doc_name → RagDocument.title
    chunks[].page_num → RagDocument.page
    chunks[].similarity → RagDocument.score
    chunks[].content → RagDocument.snippet
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json=create_mock_chunks_response(SAMPLE_CHUNKS),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ragflow = RagflowClient(
            base_url="http://test-ragflow:8080",
            client=client,
        )

        documents = await ragflow.search(query="연차휴가 규정", top_k=3)

    # Assert: 3개 문서 반환
    assert len(documents) == 3
    assert all(isinstance(doc, RagDocument) for doc in documents)

    # Assert: 첫 번째 문서 상세 검증
    doc1 = documents[0]
    assert doc1.doc_id == "chunk-001"
    assert doc1.title == "연차휴가 관리 규정"
    assert doc1.page == 12
    assert doc1.score == 0.92
    assert "연차휴가는 최대 10일" in doc1.snippet

    # Assert: 두 번째 문서
    doc2 = documents[1]
    assert doc2.doc_id == "chunk-002"
    assert doc2.title == "근태관리 지침"
    assert doc2.score == 0.85


@pytest.mark.anyio
async def test_retrieval_test_empty_chunks() -> None:
    """
    테스트 6: 빈 chunks 응답 처리
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={"chunks": []},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ragflow = RagflowClient(
            base_url="http://test-ragflow:8080",
            client=client,
        )

        documents = await ragflow.search(query="존재하지 않는 내용")

    assert documents == []


@pytest.mark.anyio
async def test_retrieval_test_partial_chunk_fields() -> None:
    """
    테스트 7: 일부 필드만 있는 chunk 처리 (graceful fallback)
    """
    partial_chunks = [
        {
            "chunk_id": "partial-001",
            # doc_name 없음
            # page_num 없음
            "similarity": 0.75,
            "content": "부분적인 데이터...",
        },
        {
            # chunk_id 없음 → "unknown" 사용
            "doc_name": "문서명만 있는 경우",
            "content": "내용만 있음",
            # similarity 없음 → 0.0 사용
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={"chunks": partial_chunks},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ragflow = RagflowClient(
            base_url="http://test-ragflow:8080",
            client=client,
        )

        documents = await ragflow.search(query="test")

    assert len(documents) == 2

    # 첫 번째: chunk_id 있음, doc_name 없음
    doc1 = documents[0]
    assert doc1.doc_id == "partial-001"
    assert doc1.title == "Untitled"  # fallback
    assert doc1.page is None
    assert doc1.score == 0.75

    # 두 번째: chunk_id 없음
    doc2 = documents[1]
    assert doc2.doc_id == "unknown"  # fallback
    assert doc2.title == "문서명만 있는 경우"
    assert doc2.score == 0.0  # fallback


# =============================================================================
# 테스트 8: 에러 처리
# =============================================================================


@pytest.mark.anyio
async def test_retrieval_test_http_error() -> None:
    """
    테스트 8: HTTP 에러 시 빈 리스트 반환 (예외 발생 안 함)
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=500,
            json={"error": "Internal Server Error"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ragflow = RagflowClient(
            base_url="http://test-ragflow:8080",
            client=client,
        )

        # Should not raise exception
        documents = await ragflow.search(query="test")

    assert documents == []


@pytest.mark.anyio
async def test_retrieval_test_timeout() -> None:
    """
    테스트 9: 타임아웃 시 빈 리스트 반환
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Connection timed out")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ragflow = RagflowClient(
            base_url="http://test-ragflow:8080",
            client=client,
            timeout=1.0,
        )

        documents = await ragflow.search(query="test")

    assert documents == []


@pytest.mark.anyio
async def test_retrieval_test_connection_error() -> None:
    """
    테스트 10: 연결 에러 시 빈 리스트 반환
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ragflow = RagflowClient(
            base_url="http://test-ragflow:8080",
            client=client,
        )

        documents = await ragflow.search(query="test")

    assert documents == []


# =============================================================================
# 테스트 11: _dataset_to_kb_id 헬퍼 메서드 직접 테스트
# =============================================================================


def test_dataset_to_kb_id_helper() -> None:
    """
    테스트 11: _dataset_to_kb_id() 헬퍼 메서드 단위 테스트
    """
    ragflow = RagflowClient(base_url="http://test:8080")

    # 정상 매핑
    assert ragflow._dataset_to_kb_id("POLICY") == "kb_policy_001"
    assert ragflow._dataset_to_kb_id("INCIDENT") == "kb_incident_001"
    assert ragflow._dataset_to_kb_id("EDUCATION") == "kb_education_001"

    # 소문자 → 대문자 변환
    assert ragflow._dataset_to_kb_id("policy") == "kb_policy_001"
    assert ragflow._dataset_to_kb_id("incident") == "kb_incident_001"
    assert ragflow._dataset_to_kb_id("Policy") == "kb_policy_001"

    # None → POLICY 기본값
    assert ragflow._dataset_to_kb_id(None) == "kb_policy_001"

    # 알 수 없는 값 → POLICY fallback
    assert ragflow._dataset_to_kb_id("UNKNOWN") == "kb_policy_001"
    assert ragflow._dataset_to_kb_id("") == "kb_policy_001"


# =============================================================================
# 테스트 12: top_k 매핑 검증
# =============================================================================


@pytest.mark.anyio
async def test_retrieval_test_top_k_mapping() -> None:
    """
    테스트 12: top_k → size 매핑 검증
    """
    captured_payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payloads.append(json.loads(request.content))
        return httpx.Response(
            status_code=200,
            json={"chunks": []},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ragflow = RagflowClient(
            base_url="http://test-ragflow:8080",
            client=client,
        )

        # Test different top_k values
        await ragflow.search(query="test", top_k=3)
        assert captured_payloads[-1]["size"] == 3

        await ragflow.search(query="test", top_k=10)
        assert captured_payloads[-1]["size"] == 10

        # Default top_k = 5
        await ragflow.search(query="test")
        assert captured_payloads[-1]["size"] == 5


# =============================================================================
# 테스트 13: base_url 미설정 시 동작
# =============================================================================


@pytest.mark.anyio
async def test_retrieval_test_no_base_url() -> None:
    """
    테스트 13: base_url이 빈 문자열인 경우 빈 리스트 반환
    """
    ragflow = RagflowClient(base_url="")

    documents = await ragflow.search(query="test")

    assert documents == []
