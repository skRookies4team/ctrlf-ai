"""
RagflowSearchClient 단위 테스트 (Phase 19-AI-1)

테스트 케이스:
1. 정상 200 + results 반환
2. No chunk found (results=[]) 처리
3. 400/500 에러 처리
4. 타임아웃 처리
5. 매핑 없는 dataset 처리 (RagflowConfigError)
"""

import os
import pytest
import httpx
from unittest.mock import patch, MagicMock, AsyncMock

from app.clients.ragflow_search_client import (
    RagflowSearchClient,
    RagflowSearchError,
    RagflowConfigError,
    clear_rag_cache,
)
from app.core.config import clear_settings_cache


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


@pytest.fixture(autouse=True)
def reset_caches():
    """
    테스트 격리를 위해 settings 캐시와 RAG 캐시를 초기화합니다.

    다른 테스트에서 변경된 환경변수의 영향을 받지 않도록 합니다.
    """
    clear_settings_cache()
    clear_rag_cache()
    yield
    clear_settings_cache()
    clear_rag_cache()


@pytest.fixture
def mock_settings():
    """테스트용 설정 mock"""
    settings = MagicMock()
    settings.ragflow_base_url = "http://ragflow-test:8080"
    settings.RAGFLOW_API_KEY = "test-api-key"
    settings.ragflow_dataset_to_kb_mapping = {
        "policy": "kb_policy_001",
        "training": "kb_training_001",
    }
    settings.RAGFLOW_KB_ID_POLICY = None
    settings.RAGFLOW_KB_ID_TRAINING = None
    settings.RAGFLOW_KB_ID_SECURITY = None
    settings.RAGFLOW_KB_ID_INCIDENT = None
    settings.RAGFLOW_KB_ID_EDUCATION = None
    return settings


@pytest.fixture
def mock_http_client():
    """테스트용 HTTP 클라이언트 mock"""
    return AsyncMock(spec=httpx.AsyncClient)


def create_client_with_mocks(mock_settings, mock_http_client):
    """Mock이 적용된 RagflowSearchClient 생성"""
    with patch("app.clients.ragflow_search_client.get_settings", return_value=mock_settings):
        with patch("app.clients.ragflow_search_client.get_async_http_client", return_value=mock_http_client):
            return RagflowSearchClient()


# =============================================================================
# 테스트 클래스
# =============================================================================


class TestRagflowSearchClient:
    """RagflowSearchClient 단위 테스트"""

    # =========================================================================
    # 테스트: 정상 200 + results 반환
    # =========================================================================

    @pytest.mark.anyio
    async def test_search_chunks_success(self, mock_settings, mock_http_client):
        """정상 검색 - results 반환"""
        # Arrange
        expected_results = [
            {"id": "chunk-001", "content": "연차휴가 규정 내용", "similarity": 0.92},
            {"id": "chunk-002", "content": "연차 이월 조항", "similarity": 0.88},
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": 0,
            "data": {"results": expected_results}
        }
        mock_http_client.post = AsyncMock(return_value=mock_response)

        client = create_client_with_mocks(mock_settings, mock_http_client)

        # Act
        results = await client.search_chunks(
            query="연차휴가 이월 규정",
            dataset="POLICY",
            top_k=5,
        )

        # Assert
        assert results == expected_results
        assert len(results) == 2
        mock_http_client.post.assert_called_once()

        # 요청 검증
        call_args = mock_http_client.post.call_args
        assert "/v1/chunk/search" in call_args[0][0]
        payload = call_args[1]["json"]
        assert payload["query"] == "연차휴가 이월 규정"
        assert payload["dataset"] == "kb_policy_001"  # 매핑된 kb_id
        assert payload["top_k"] == 5

    # =========================================================================
    # 테스트: No chunk found (results=[])
    # =========================================================================

    @pytest.mark.anyio
    async def test_search_chunks_empty_results(self, mock_settings, mock_http_client):
        """검색 결과 없음 - 빈 리스트 반환"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": 0,
            "data": {"results": []}
        }
        mock_http_client.post = AsyncMock(return_value=mock_response)

        client = create_client_with_mocks(mock_settings, mock_http_client)

        # Act
        results = await client.search_chunks(
            query="존재하지 않는 문서",
            dataset="POLICY",
            top_k=5,
        )

        # Assert
        assert results == []
        assert isinstance(results, list)

    # =========================================================================
    # 테스트: 400/500 에러 처리
    # =========================================================================

    @pytest.mark.anyio
    async def test_search_chunks_http_400_error(self, mock_settings, mock_http_client):
        """400 Bad Request - RagflowSearchError 발생"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"error": "Bad request"}'
        mock_http_client.post = AsyncMock(return_value=mock_response)

        client = create_client_with_mocks(mock_settings, mock_http_client)

        # Act & Assert
        with pytest.raises(RagflowSearchError) as exc_info:
            await client.search_chunks(
                query="테스트 쿼리",
                dataset="POLICY",
                top_k=5,
            )

        assert exc_info.value.status_code == 400
        assert "HTTP 400" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_search_chunks_http_500_error(self, mock_settings, mock_http_client):
        """500 Internal Server Error - RagflowSearchError 발생"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = '{"error": "Internal server error"}'
        mock_http_client.post = AsyncMock(return_value=mock_response)

        client = create_client_with_mocks(mock_settings, mock_http_client)

        # Act & Assert
        with pytest.raises(RagflowSearchError) as exc_info:
            await client.search_chunks(
                query="테스트 쿼리",
                dataset="POLICY",
                top_k=5,
            )

        assert exc_info.value.status_code == 500
        assert "HTTP 500" in str(exc_info.value)

    # =========================================================================
    # 테스트: 타임아웃 처리
    # =========================================================================

    @pytest.mark.anyio
    async def test_search_chunks_timeout(self, mock_settings, mock_http_client):
        """타임아웃 - RagflowSearchError 발생"""
        # Arrange
        mock_http_client.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))

        client = create_client_with_mocks(mock_settings, mock_http_client)

        # Act & Assert
        with pytest.raises(RagflowSearchError) as exc_info:
            await client.search_chunks(
                query="테스트 쿼리",
                dataset="POLICY",
                top_k=5,
            )

        assert "timeout" in str(exc_info.value).lower()

    # =========================================================================
    # 테스트: 네트워크 오류 처리
    # =========================================================================

    @pytest.mark.anyio
    async def test_search_chunks_network_error(self, mock_settings, mock_http_client):
        """네트워크 오류 - RagflowSearchError 발생"""
        # Arrange
        mock_http_client.post = AsyncMock(
            side_effect=httpx.RequestError("Connection refused")
        )

        client = create_client_with_mocks(mock_settings, mock_http_client)

        # Act & Assert
        with pytest.raises(RagflowSearchError) as exc_info:
            await client.search_chunks(
                query="테스트 쿼리",
                dataset="POLICY",
                top_k=5,
            )

        assert "request error" in str(exc_info.value).lower()

    # =========================================================================
    # 테스트: 매핑 없는 dataset 처리
    # =========================================================================

    @pytest.mark.anyio
    async def test_search_chunks_unknown_dataset(self, mock_settings, mock_http_client):
        """매핑 없는 dataset - RagflowConfigError 발생"""
        # Arrange
        client = create_client_with_mocks(mock_settings, mock_http_client)

        # Act & Assert
        with pytest.raises(RagflowConfigError) as exc_info:
            await client.search_chunks(
                query="테스트 쿼리",
                dataset="UNKNOWN_DATASET",  # 매핑 없는 dataset
                top_k=5,
            )

        assert "No kb_id mapping" in str(exc_info.value)
        assert "UNKNOWN_DATASET" in str(exc_info.value)

    # =========================================================================
    # 테스트: 대소문자 무관 dataset 매핑
    # =========================================================================

    @pytest.mark.anyio
    async def test_search_chunks_case_insensitive_dataset(self, mock_settings, mock_http_client):
        """dataset 이름 대소문자 무관하게 매핑"""
        # Arrange
        expected_results = [{"id": "chunk-001", "content": "테스트", "similarity": 0.9}]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": 0,
            "data": {"results": expected_results}
        }
        mock_http_client.post = AsyncMock(return_value=mock_response)

        client = create_client_with_mocks(mock_settings, mock_http_client)

        # Act - 소문자로 호출
        results = await client.search_chunks(
            query="테스트",
            dataset="policy",  # 소문자
            top_k=5,
        )

        # Assert
        assert results == expected_results

        # 요청의 dataset이 kb_id로 매핑되었는지 확인
        call_args = mock_http_client.post.call_args
        payload = call_args[1]["json"]
        assert payload["dataset"] == "kb_policy_001"

    # =========================================================================
    # 테스트: 인증 헤더 포함
    # =========================================================================

    @pytest.mark.anyio
    async def test_search_chunks_auth_header(self, mock_settings, mock_http_client):
        """API Key가 설정된 경우 Authorization 헤더 포함"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"code": 0, "data": {"results": []}}
        mock_http_client.post = AsyncMock(return_value=mock_response)

        client = create_client_with_mocks(mock_settings, mock_http_client)

        # Act
        await client.search_chunks(query="테스트", dataset="POLICY", top_k=5)

        # Assert
        call_args = mock_http_client.post.call_args
        headers = call_args[1]["headers"]
        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer test-api-key"

    # =========================================================================
    # 테스트: health_check
    # =========================================================================

    @pytest.mark.anyio
    async def test_health_check_success(self, mock_settings, mock_http_client):
        """헬스체크 성공"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_http_client.get = AsyncMock(return_value=mock_response)

        client = create_client_with_mocks(mock_settings, mock_http_client)

        # Act
        result = await client.health_check()

        # Assert
        assert result is True

    @pytest.mark.anyio
    async def test_health_check_failure(self, mock_settings, mock_http_client):
        """헬스체크 실패"""
        # Arrange
        mock_http_client.get = AsyncMock(side_effect=httpx.RequestError("Connection refused"))

        client = create_client_with_mocks(mock_settings, mock_http_client)

        # Act
        result = await client.health_check()

        # Assert
        assert result is False


# =============================================================================
# 테스트: base_url 미설정 시 RagflowConfigError (동기 테스트)
# =============================================================================


def test_client_init_no_base_url():
    """base_url 미설정 시 초기화 실패"""
    # Arrange
    mock_settings = MagicMock()
    mock_settings.ragflow_base_url = None
    mock_settings.RAGFLOW_API_KEY = None
    mock_settings.ragflow_dataset_to_kb_mapping = {}
    mock_settings.RAGFLOW_KB_ID_POLICY = None
    mock_settings.RAGFLOW_KB_ID_TRAINING = None
    mock_settings.RAGFLOW_KB_ID_SECURITY = None
    mock_settings.RAGFLOW_KB_ID_INCIDENT = None
    mock_settings.RAGFLOW_KB_ID_EDUCATION = None

    # Act & Assert
    with patch("app.clients.ragflow_search_client.get_settings", return_value=mock_settings):
        with patch("app.clients.ragflow_search_client.get_async_http_client"):
            with pytest.raises(RagflowConfigError) as exc_info:
                RagflowSearchClient()

    assert "RAGFLOW_BASE_URL" in str(exc_info.value)
