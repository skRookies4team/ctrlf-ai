"""
Phase 18: 표준 RAG 검색 API 테스트

POST /search 엔드포인트 및 SearchService 테스트
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.models.search import SearchRequest, SearchResponse, SearchResultItem
from app.services.search_service import SearchService, DatasetNotFoundError
from app.core.config import Settings


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


@pytest.fixture
def test_client() -> TestClient:
    """FastAPI 테스트 클라이언트."""
    return TestClient(app)


# =============================================================================
# Model Tests
# =============================================================================


class TestSearchModels:
    """검색 모델 테스트"""

    def test_search_request_creation(self):
        """SearchRequest 생성 테스트"""
        request = SearchRequest(
            query="연차휴가 규정",
            top_k=5,
            dataset="policy",
        )
        assert request.query == "연차휴가 규정"
        assert request.top_k == 5
        assert request.dataset == "policy"

    def test_search_request_default_top_k(self):
        """SearchRequest 기본 top_k 테스트"""
        request = SearchRequest(query="test", dataset="policy")
        assert request.top_k == 5

    def test_search_result_item_creation(self):
        """SearchResultItem 생성 테스트"""
        item = SearchResultItem(
            doc_id="doc-001",
            title="인사규정",
            page=3,
            score=0.87,
            snippet="연차휴가는 1년 근무 시...",
            dataset="policy",
            source="ragflow",
        )
        assert item.doc_id == "doc-001"
        assert item.title == "인사규정"
        assert item.page == 3
        assert item.score == 0.87
        assert item.dataset == "policy"
        assert item.source == "ragflow"

    def test_search_result_item_default_source(self):
        """SearchResultItem 기본 source 테스트"""
        item = SearchResultItem(
            doc_id="doc-001",
            title="인사규정",
            score=0.87,
            dataset="policy",
        )
        assert item.source == "ragflow"

    def test_search_response_creation(self):
        """SearchResponse 생성 테스트"""
        response = SearchResponse(
            results=[
                SearchResultItem(
                    doc_id="doc-001",
                    title="인사규정",
                    score=0.87,
                    dataset="policy",
                )
            ]
        )
        assert len(response.results) == 1
        assert response.results[0].doc_id == "doc-001"

    def test_search_response_empty_results(self):
        """SearchResponse 빈 결과 테스트"""
        response = SearchResponse()
        assert response.results == []


# =============================================================================
# Config Tests
# =============================================================================


class TestSearchConfig:
    """검색 설정 테스트"""

    def test_ragflow_dataset_mapping_parsing(self):
        """Dataset 매핑 파싱 테스트"""
        settings = Settings(
            RAGFLOW_DATASET_MAPPING="policy:kb_001,training:kb_002,incident:kb_003"
        )
        mapping = settings.ragflow_dataset_to_kb_mapping
        assert mapping["policy"] == "kb_001"
        assert mapping["training"] == "kb_002"
        assert mapping["incident"] == "kb_003"

    def test_ragflow_dataset_mapping_case_insensitive(self):
        """Dataset 매핑 대소문자 무시 테스트"""
        settings = Settings(
            RAGFLOW_DATASET_MAPPING="POLICY:kb_001,Training:kb_002"
        )
        mapping = settings.ragflow_dataset_to_kb_mapping
        assert "policy" in mapping
        assert "training" in mapping

    def test_ragflow_dataset_mapping_empty(self):
        """Dataset 매핑 빈 값 테스트"""
        settings = Settings(RAGFLOW_DATASET_MAPPING="")
        mapping = settings.ragflow_dataset_to_kb_mapping
        assert mapping == {}

    def test_ragflow_timeout_default(self):
        """RAGFlow 타임아웃 기본값 테스트"""
        settings = Settings()
        assert settings.RAGFLOW_TIMEOUT_SEC == 10.0


# =============================================================================
# Service Tests
# =============================================================================


class TestSearchService:
    """SearchService 테스트"""

    def test_get_kb_id_success(self):
        """kb_id 조회 성공 테스트"""
        service = SearchService(
            base_url="http://ragflow:8080",
            dataset_mapping={"policy": "kb_policy_001", "training": "kb_training_001"},
        )
        assert service.get_kb_id("policy") == "kb_policy_001"
        assert service.get_kb_id("POLICY") == "kb_policy_001"  # 대소문자 무시
        assert service.get_kb_id("training") == "kb_training_001"

    def test_get_kb_id_not_found(self):
        """kb_id 조회 실패 테스트"""
        service = SearchService(
            base_url="http://ragflow:8080",
            dataset_mapping={"policy": "kb_policy_001"},
        )
        with pytest.raises(DatasetNotFoundError) as exc_info:
            service.get_kb_id("unknown")

        assert exc_info.value.dataset == "unknown"
        assert "policy" in exc_info.value.available_datasets

    @pytest.mark.anyio
    async def test_search_no_base_url(self):
        """base_url 미설정 시 빈 결과 반환 테스트"""
        service = SearchService(
            base_url=None,
            dataset_mapping={"policy": "kb_001"},
        )
        request = SearchRequest(query="test", dataset="policy")
        response = await service.search(request)
        assert response.results == []

    @pytest.mark.anyio
    async def test_search_success(self):
        """검색 성공 테스트"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "doc_id": "chunk-001",
                    "title": "인사규정",
                    "page": 3,
                    "score": 0.87,
                    "snippet": "연차휴가는...",
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        service = SearchService(
            base_url="http://ragflow:8080",
            dataset_mapping={"policy": "kb_policy_001"},
            client=mock_client,
        )

        request = SearchRequest(query="연차휴가", top_k=5, dataset="policy")
        response = await service.search(request)

        assert len(response.results) == 1
        assert response.results[0].doc_id == "chunk-001"
        assert response.results[0].title == "인사규정"
        assert response.results[0].score == 0.87
        assert response.results[0].dataset == "policy"
        assert response.results[0].source == "ragflow"

        # kb_id가 올바르게 전송되었는지 확인
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["dataset"] == "kb_policy_001"

    @pytest.mark.anyio
    async def test_search_timeout(self):
        """타임아웃 테스트"""
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TimeoutException("timeout")

        service = SearchService(
            base_url="http://ragflow:8080",
            dataset_mapping={"policy": "kb_001"},
            client=mock_client,
        )

        request = SearchRequest(query="test", dataset="policy")
        response = await service.search(request)
        assert response.results == []

    @pytest.mark.anyio
    async def test_search_http_error(self):
        """HTTP 에러 테스트"""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 error", request=MagicMock(), response=mock_response
        )

        service = SearchService(
            base_url="http://ragflow:8080",
            dataset_mapping={"policy": "kb_001"},
            client=mock_client,
        )

        request = SearchRequest(query="test", dataset="policy")
        response = await service.search(request)
        assert response.results == []

    @pytest.mark.anyio
    async def test_search_request_error(self):
        """요청 에러 테스트"""
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.RequestError("Connection failed")

        service = SearchService(
            base_url="http://ragflow:8080",
            dataset_mapping={"policy": "kb_001"},
            client=mock_client,
        )

        request = SearchRequest(query="test", dataset="policy")
        response = await service.search(request)
        assert response.results == []


class TestSearchServiceParseResults:
    """SearchService 결과 파싱 테스트"""

    def test_parse_results_standard_format(self):
        """표준 응답 형식 파싱 테스트"""
        service = SearchService(
            base_url="http://ragflow:8080",
            dataset_mapping={"policy": "kb_001"},
        )
        data = {
            "results": [
                {
                    "doc_id": "doc-001",
                    "title": "문서1",
                    "page": 1,
                    "score": 0.9,
                    "snippet": "내용...",
                }
            ]
        }
        results = service._parse_results(data, "policy")
        assert len(results) == 1
        assert results[0].doc_id == "doc-001"

    def test_parse_results_alternative_fields(self):
        """대체 필드명 파싱 테스트"""
        service = SearchService(
            base_url="http://ragflow:8080",
            dataset_mapping={"policy": "kb_001"},
        )
        data = {
            "results": [
                {
                    "chunk_id": "chunk-001",  # doc_id 대신 chunk_id
                    "doc_name": "문서1",       # title 대신 doc_name
                    "page_num": 1,            # page 대신 page_num
                    "similarity": 0.9,        # score 대신 similarity
                    "content": "내용...",     # snippet 대신 content
                }
            ]
        }
        results = service._parse_results(data, "policy")
        assert len(results) == 1
        assert results[0].doc_id == "chunk-001"
        assert results[0].title == "문서1"
        assert results[0].page == 1
        assert results[0].score == 0.9
        assert results[0].snippet == "내용..."

    def test_parse_results_empty(self):
        """빈 결과 파싱 테스트"""
        service = SearchService(
            base_url="http://ragflow:8080",
            dataset_mapping={"policy": "kb_001"},
        )
        data = {"results": []}
        results = service._parse_results(data, "policy")
        assert results == []

    def test_parse_results_missing_results_key(self):
        """results 키 없음 테스트"""
        service = SearchService(
            base_url="http://ragflow:8080",
            dataset_mapping={"policy": "kb_001"},
        )
        data = {}
        results = service._parse_results(data, "policy")
        assert results == []


# =============================================================================
# API Tests
# =============================================================================


class TestSearchAPI:
    """검색 API 엔드포인트 테스트"""

    def test_search_endpoint_success(self, test_client: TestClient):
        """검색 엔드포인트 성공 테스트"""
        from app.api.v1 import search as search_module

        # Mock service
        mock_service = MagicMock()
        mock_service.search = AsyncMock(
            return_value=SearchResponse(
                results=[
                    SearchResultItem(
                        doc_id="doc-001",
                        title="테스트 문서",
                        score=0.9,
                        dataset="policy",
                    )
                ]
            )
        )

        # Replace service
        original_fn = search_module.get_search_service
        search_module.get_search_service = lambda: mock_service
        search_module._search_service = None

        try:
            response = test_client.post(
                "/search",
                json={
                    "query": "테스트 쿼리",
                    "top_k": 5,
                    "dataset": "policy",
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert len(data["results"]) == 1
            assert data["results"][0]["doc_id"] == "doc-001"
        finally:
            search_module.get_search_service = original_fn
            search_module._search_service = None

    def test_search_endpoint_dataset_not_found(self, test_client: TestClient):
        """검색 엔드포인트 dataset 없음 테스트"""
        from app.api.v1 import search as search_module

        # Mock service
        mock_service = MagicMock()
        mock_service.search = AsyncMock(
            side_effect=DatasetNotFoundError("unknown", ["policy", "training"])
        )

        # Replace service
        original_fn = search_module.get_search_service
        search_module.get_search_service = lambda: mock_service
        search_module._search_service = None

        try:
            response = test_client.post(
                "/search",
                json={
                    "query": "테스트",
                    "dataset": "unknown",
                },
            )
            assert response.status_code == 400
            assert "unknown" in response.json()["detail"]
        finally:
            search_module.get_search_service = original_fn
            search_module._search_service = None

    def test_search_endpoint_validation_error(self, test_client: TestClient):
        """검색 엔드포인트 유효성 검사 오류 테스트"""
        # query 누락
        response = test_client.post(
            "/search",
            json={"dataset": "policy"},
        )
        assert response.status_code == 422

        # dataset 누락
        response = test_client.post(
            "/search",
            json={"query": "test"},
        )
        assert response.status_code == 422

        # 빈 query
        response = test_client.post(
            "/search",
            json={"query": "", "dataset": "policy"},
        )
        assert response.status_code == 422


# =============================================================================
# Integration Tests (with mocked HTTP)
# =============================================================================


class TestSearchIntegration:
    """검색 통합 테스트"""

    @pytest.mark.anyio
    async def test_full_search_flow(self):
        """전체 검색 플로우 테스트"""
        # Mock RAGFlow response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "doc_id": "chunk-001",
                    "title": "인사규정 v2.0",
                    "page": 5,
                    "score": 0.92,
                    "snippet": "연차휴가는 입사 1년 후 15일이 부여됩니다...",
                },
                {
                    "doc_id": "chunk-002",
                    "title": "인사규정 v2.0",
                    "page": 6,
                    "score": 0.85,
                    "snippet": "연차휴가 이월은 최대 5일까지 가능합니다...",
                },
            ]
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        service = SearchService(
            base_url="http://ragflow:8080",
            timeout=10.0,
            dataset_mapping={
                "policy": "kb_policy_001",
                "training": "kb_training_001",
            },
            client=mock_client,
        )

        # Execute search
        request = SearchRequest(
            query="연차휴가 이월 규정은?",
            top_k=5,
            dataset="policy",
        )
        response = await service.search(request)

        # Verify results
        assert len(response.results) == 2
        assert response.results[0].doc_id == "chunk-001"
        assert response.results[0].score == 0.92
        assert response.results[0].dataset == "policy"
        assert response.results[0].source == "ragflow"
        assert response.results[1].doc_id == "chunk-002"

        # Verify RAGFlow was called correctly
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "/v1/chunk/search" in call_args[0][0]
        assert call_args[1]["json"]["query"] == "연차휴가 이월 규정은?"
        assert call_args[1]["json"]["top_k"] == 5
        assert call_args[1]["json"]["dataset"] == "kb_policy_001"  # kb_id로 변환됨
