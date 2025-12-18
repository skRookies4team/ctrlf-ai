"""
MilvusSearchClient 테스트 (Phase 24)

Milvus 벡터 검색 클라이언트의 단위 테스트입니다.
실제 Milvus 서버 연결 없이 모킹을 통해 테스트합니다.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import httpx

from app.clients.milvus_client import (
    MilvusSearchClient,
    MilvusError,
    MilvusConnectionError,
    MilvusSearchError,
    EmbeddingError,
    get_milvus_client,
    clear_milvus_client,
)
from app.models.chat import ChatSource


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_settings():
    """테스트용 Settings 모킹."""
    with patch("app.clients.milvus_client.get_settings") as mock:
        settings = MagicMock()
        settings.MILVUS_HOST = "localhost"
        settings.MILVUS_PORT = 19530
        settings.MILVUS_COLLECTION_NAME = "test_collection"
        settings.llm_base_url = "http://localhost:8001"
        settings.EMBEDDING_MODEL_NAME = "test-embedding-model"
        settings.EMBEDDING_DIMENSION = 1024
        settings.MILVUS_TOP_K = 5
        settings.MILVUS_SEARCH_PARAMS = '{"metric_type": "COSINE", "params": {"nprobe": 10}}'
        mock.return_value = settings
        yield settings


@pytest.fixture
def milvus_client(mock_settings):
    """테스트용 MilvusSearchClient 인스턴스."""
    clear_milvus_client()
    client = MilvusSearchClient()
    yield client
    clear_milvus_client()


@pytest.fixture
def mock_embedding_response():
    """임베딩 응답 Mock."""
    return {
        "data": [
            {
                "embedding": [0.1] * 1024,  # 1024차원 임베딩 벡터
                "index": 0,
            }
        ],
        "model": "test-embedding-model",
        "usage": {"prompt_tokens": 10, "total_tokens": 10},
    }


# =============================================================================
# 초기화 테스트
# =============================================================================


class TestMilvusSearchClientInit:
    """MilvusSearchClient 초기화 테스트."""

    def test_init_with_defaults(self, mock_settings):
        """기본 설정으로 초기화 테스트."""
        client = MilvusSearchClient()

        assert client._host == "localhost"
        assert client._port == 19530
        assert client._collection_name == "test_collection"
        assert client._embedding_model == "test-embedding-model"
        assert client._connected is False
        assert client._collection is None

    def test_init_with_custom_values(self, mock_settings):
        """커스텀 값으로 초기화 테스트."""
        client = MilvusSearchClient(
            host="custom-host",
            port=19999,
            collection_name="custom_collection",
            llm_base_url="http://custom-llm:8000",
            embedding_model="custom-model",
        )

        assert client._host == "custom-host"
        assert client._port == 19999
        assert client._collection_name == "custom_collection"
        assert client._llm_base_url == "http://custom-llm:8000"
        assert client._embedding_model == "custom-model"


# =============================================================================
# 연결 관리 테스트
# =============================================================================


class TestMilvusConnection:
    """Milvus 연결 관리 테스트."""

    def test_ensure_connection_success(self, milvus_client):
        """연결 성공 테스트."""
        with patch("app.clients.milvus_client.connections") as mock_connections:
            mock_connections.connect = MagicMock()
            mock_connections.disconnect = MagicMock()

            milvus_client._ensure_connection()

            assert milvus_client._connected is True
            mock_connections.connect.assert_called_once()

    def test_ensure_connection_already_connected(self, milvus_client):
        """이미 연결된 상태에서는 재연결하지 않음."""
        milvus_client._connected = True

        with patch("app.clients.milvus_client.connections") as mock_connections:
            milvus_client._ensure_connection()

            mock_connections.connect.assert_not_called()

    def test_ensure_connection_failure(self, milvus_client):
        """연결 실패 시 MilvusConnectionError 발생."""
        with patch("app.clients.milvus_client.connections") as mock_connections:
            mock_connections.connect.side_effect = Exception("Connection refused")
            mock_connections.disconnect = MagicMock()

            with pytest.raises(MilvusConnectionError) as exc_info:
                milvus_client._ensure_connection()

            assert "Failed to connect to Milvus" in str(exc_info.value)

    def test_disconnect(self, milvus_client):
        """연결 해제 테스트."""
        milvus_client._connected = True
        mock_collection = MagicMock()
        milvus_client._collection = mock_collection

        with patch("app.clients.milvus_client.connections") as mock_connections:
            milvus_client.disconnect()

            assert milvus_client._connected is False
            assert milvus_client._collection is None
            mock_collection.release.assert_called_once()
            mock_connections.disconnect.assert_called_once()


# =============================================================================
# 컬렉션 관리 테스트
# =============================================================================


class TestMilvusCollection:
    """Milvus 컬렉션 관리 테스트."""

    def test_get_collection_success(self, milvus_client):
        """컬렉션 가져오기 성공 테스트."""
        with patch("app.clients.milvus_client.connections") as mock_connections, \
             patch("app.clients.milvus_client.utility") as mock_utility, \
             patch("app.clients.milvus_client.Collection") as MockCollection:

            mock_connections.connect = MagicMock()
            mock_connections.disconnect = MagicMock()
            mock_utility.has_collection.return_value = True
            mock_collection = MagicMock()
            MockCollection.return_value = mock_collection

            result = milvus_client._get_collection()

            assert result == mock_collection
            mock_collection.load.assert_called_once()

    def test_get_collection_not_exists(self, milvus_client):
        """존재하지 않는 컬렉션 테스트."""
        with patch("app.clients.milvus_client.connections") as mock_connections, \
             patch("app.clients.milvus_client.utility") as mock_utility:

            mock_connections.connect = MagicMock()
            mock_connections.disconnect = MagicMock()
            mock_utility.has_collection.return_value = False

            with pytest.raises(MilvusError) as exc_info:
                milvus_client._get_collection()

            assert "does not exist" in str(exc_info.value)


# =============================================================================
# 임베딩 생성 테스트
# =============================================================================


class TestEmbeddingGeneration:
    """임베딩 생성 테스트."""

    @pytest.mark.anyio
    async def test_generate_embedding_success(self, milvus_client, mock_embedding_response):
        """임베딩 생성 성공 테스트."""
        def mock_transport(request):
            return httpx.Response(200, json=mock_embedding_response)

        transport = httpx.MockTransport(mock_transport)

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = httpx.Response(200, json=mock_embedding_response)
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            embedding = await milvus_client.generate_embedding("테스트 텍스트")

            assert len(embedding) == 1024
            assert embedding[0] == 0.1

    @pytest.mark.anyio
    async def test_generate_embedding_no_llm_url(self, mock_settings):
        """LLM URL 미설정 시 에러."""
        mock_settings.llm_base_url = None
        client = MilvusSearchClient()
        client._llm_base_url = None

        with pytest.raises(EmbeddingError) as exc_info:
            await client.generate_embedding("테스트")

        assert "LLM_BASE_URL is not configured" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_generate_embedding_api_error(self, milvus_client):
        """임베딩 API 오류 테스트."""
        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = httpx.Response(500, text="Internal Server Error")
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            with pytest.raises(EmbeddingError) as exc_info:
                await milvus_client.generate_embedding("테스트")

            assert "status 500" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_generate_embedding_timeout(self, milvus_client):
        """임베딩 타임아웃 테스트."""
        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.TimeoutException("Timeout")
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            with pytest.raises(EmbeddingError) as exc_info:
                await milvus_client.generate_embedding("테스트")

            assert "timeout" in str(exc_info.value).lower()

    @pytest.mark.anyio
    async def test_generate_embedding_empty_data(self, milvus_client):
        """임베딩 데이터 없음 테스트."""
        empty_response = {"data": [], "model": "test"}

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = httpx.Response(200, json=empty_response)
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            with pytest.raises(EmbeddingError) as exc_info:
                await milvus_client.generate_embedding("테스트")

            assert "no data" in str(exc_info.value)


# =============================================================================
# 벡터 검색 테스트
# =============================================================================


class TestVectorSearch:
    """벡터 검색 테스트."""

    @pytest.mark.anyio
    async def test_search_success(self, milvus_client, mock_embedding_response):
        """검색 성공 테스트."""
        # Mock embedding generation
        milvus_client.generate_embedding = AsyncMock(return_value=[0.1] * 1024)

        # Mock collection (ragflow_chunks 스키마)
        mock_collection = MagicMock()
        mock_hit = MagicMock()
        mock_hit.id = "doc_1"
        mock_hit.score = 0.95
        mock_hit.entity = MagicMock()
        mock_hit.entity.get = MagicMock(side_effect=lambda k, d="": {
            "text": "테스트 내용",  # ragflow_chunks는 text 필드 사용
            "doc_id": "doc_1",
            "dataset_id": "kb_policy_001",
            "chunk_id": 1,
        }.get(k, d))

        mock_collection.search.return_value = [[mock_hit]]
        milvus_client._get_collection = MagicMock(return_value=mock_collection)

        results = await milvus_client.search("테스트 쿼리", domain="POLICY", top_k=5)

        assert len(results) == 1
        assert results[0]["id"] == "doc_1"
        assert results[0]["content"] == "테스트 내용"  # text → content로 변환됨
        assert results[0]["score"] == 0.95
        assert results[0]["domain"] == "POLICY"  # dataset_id에서 추출

    @pytest.mark.anyio
    async def test_search_with_domain_filter(self, milvus_client):
        """도메인 필터 적용 검색 테스트 (ragflow_chunks 스키마는 domain 필드 없음)."""
        milvus_client.generate_embedding = AsyncMock(return_value=[0.1] * 1024)

        mock_collection = MagicMock()
        mock_collection.search.return_value = [[]]
        milvus_client._get_collection = MagicMock(return_value=mock_collection)

        await milvus_client.search("테스트", domain="EDU")

        # 검색 호출 확인 - ragflow_chunks는 domain 필드가 없어서 expr=None
        # (도메인 필터링은 결과에서 후처리로 수행)
        mock_collection.search.assert_called_once()
        call_args = mock_collection.search.call_args
        # expr은 None이어야 함 (domain 필드 없으므로 필터 적용 안함)
        assert call_args.kwargs.get("expr") is None

    @pytest.mark.anyio
    async def test_search_embedding_failure(self, milvus_client):
        """임베딩 실패 시 MilvusSearchError."""
        milvus_client.generate_embedding = AsyncMock(
            side_effect=EmbeddingError("Embedding failed")
        )

        with pytest.raises(MilvusSearchError) as exc_info:
            await milvus_client.search("테스트")

        assert "embedding" in str(exc_info.value).lower()


# =============================================================================
# search_as_sources 테스트
# =============================================================================


class TestSearchAsSources:
    """search_as_sources 메서드 테스트."""

    @pytest.mark.anyio
    async def test_search_as_sources_success(self, milvus_client):
        """ChatSource 형식 변환 테스트."""
        mock_results = [
            {
                "id": "doc_1",
                "content": "내용1" * 100,  # 긴 내용
                "title": "문서1",
                "domain": "POLICY",
                "doc_id": "doc_001",
                "score": 0.9,
                "metadata": {"page": 5, "article_label": "제10조"},
            },
            {
                "id": "doc_2",
                "content": "내용2",
                "title": "문서2",
                "domain": "POLICY",
                "doc_id": "doc_002",
                "score": 0.8,
                "metadata": {},
            },
        ]
        milvus_client.search = AsyncMock(return_value=mock_results)

        sources = await milvus_client.search_as_sources(
            query="연차 규정",
            domain="POLICY",
            top_k=5,
        )

        assert len(sources) == 2
        assert isinstance(sources[0], ChatSource)
        assert sources[0].doc_id == "doc_001"
        assert sources[0].title == "문서1"
        assert sources[0].score == 0.9
        assert len(sources[0].snippet) <= 500  # 500자 제한 확인

    @pytest.mark.anyio
    async def test_search_as_sources_empty_results(self, milvus_client):
        """검색 결과 없을 때."""
        milvus_client.search = AsyncMock(return_value=[])

        sources = await milvus_client.search_as_sources("존재하지 않는 쿼리")

        assert sources == []

    @pytest.mark.anyio
    async def test_search_as_sources_error_returns_empty(self, milvus_client):
        """검색 실패 시 빈 리스트 반환."""
        milvus_client.search = AsyncMock(side_effect=MilvusSearchError("Search failed"))

        sources = await milvus_client.search_as_sources("테스트")

        assert sources == []


# =============================================================================
# 헬스체크 테스트
# =============================================================================


class TestHealthCheck:
    """헬스체크 테스트."""

    @pytest.mark.anyio
    async def test_health_check_success(self, milvus_client):
        """헬스체크 성공."""
        with patch("app.clients.milvus_client.connections") as mock_connections, \
             patch("app.clients.milvus_client.utility") as mock_utility:

            mock_connections.connect = MagicMock()
            mock_connections.disconnect = MagicMock()
            mock_utility.has_collection.return_value = True

            result = await milvus_client.health_check()

            assert result is True

    @pytest.mark.anyio
    async def test_health_check_no_collection(self, milvus_client):
        """컬렉션 없을 때 헬스체크 실패."""
        with patch("app.clients.milvus_client.connections") as mock_connections, \
             patch("app.clients.milvus_client.utility") as mock_utility:

            mock_connections.connect = MagicMock()
            mock_connections.disconnect = MagicMock()
            mock_utility.has_collection.return_value = False

            result = await milvus_client.health_check()

            assert result is False

    @pytest.mark.anyio
    async def test_health_check_connection_error(self, milvus_client):
        """연결 실패 시 헬스체크 실패."""
        with patch("app.clients.milvus_client.connections") as mock_connections:
            mock_connections.connect.side_effect = Exception("Connection failed")
            mock_connections.disconnect = MagicMock()

            result = await milvus_client.health_check()

            assert result is False


# =============================================================================
# 싱글턴 패턴 테스트
# =============================================================================


class TestSingleton:
    """싱글턴 패턴 테스트."""

    def test_get_milvus_client_singleton(self, mock_settings):
        """싱글턴 인스턴스 반환 확인."""
        clear_milvus_client()

        client1 = get_milvus_client()
        client2 = get_milvus_client()

        assert client1 is client2

        clear_milvus_client()

    def test_clear_milvus_client(self, mock_settings):
        """싱글턴 인스턴스 제거 확인."""
        clear_milvus_client()

        client1 = get_milvus_client()
        clear_milvus_client()
        client2 = get_milvus_client()

        assert client1 is not client2

        clear_milvus_client()


# =============================================================================
# 예외 클래스 테스트
# =============================================================================


class TestExceptions:
    """예외 클래스 테스트."""

    def test_milvus_error(self):
        """MilvusError 기본 테스트."""
        error = MilvusError("Test error")
        assert error.message == "Test error"
        assert error.original_error is None

    def test_milvus_error_with_original(self):
        """MilvusError with original error."""
        original = ValueError("Original")
        error = MilvusError("Wrapped error", original_error=original)
        assert error.original_error == original

    def test_milvus_connection_error(self):
        """MilvusConnectionError 테스트."""
        error = MilvusConnectionError("Connection failed")
        assert isinstance(error, MilvusError)

    def test_milvus_search_error(self):
        """MilvusSearchError 테스트."""
        error = MilvusSearchError("Search failed")
        assert isinstance(error, MilvusError)

    def test_embedding_error(self):
        """EmbeddingError 테스트."""
        error = EmbeddingError("Embedding failed")
        assert isinstance(error, MilvusError)
