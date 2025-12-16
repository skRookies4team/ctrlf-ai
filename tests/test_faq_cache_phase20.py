"""
Phase 20-AI-1: RAGFlow 검색 결과 캐싱 테스트

TTL LRU 캐시의 동작을 검증합니다:
- 동일 입력 2회 호출 시 HTTP 클라이언트 호출이 1번만 발생하는지
- TTL 만료 시 다시 호출되는지
"""

import time
from unittest.mock import AsyncMock, patch, MagicMock

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"

from app.utils.cache import TTLCache, make_cache_key
from app.clients.ragflow_search_client import (
    RagflowSearchClient,
    clear_rag_cache,
    get_rag_cache,
)
from app.core.config import clear_settings_cache


@pytest.fixture(autouse=True)
def reset_cache():
    """테스트 전후 캐시 초기화."""
    clear_rag_cache()
    clear_settings_cache()
    yield
    clear_rag_cache()
    clear_settings_cache()


class TestTTLCache:
    """TTLCache 단위 테스트."""

    def test_set_and_get(self):
        """기본 set/get 동작 테스트."""
        cache = TTLCache[str](maxsize=10, ttl_seconds=60, name="test")

        cache.set("key1", "value1")
        result = cache.get("key1")

        assert result == "value1"

    def test_cache_miss(self):
        """캐시 미스 시 None 반환."""
        cache = TTLCache[str](maxsize=10, ttl_seconds=60, name="test")

        result = cache.get("nonexistent")

        assert result is None

    def test_ttl_expiration(self):
        """TTL 만료 시 캐시 제거."""
        cache = TTLCache[str](maxsize=10, ttl_seconds=0.1, name="test")

        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

        # TTL 만료 대기
        time.sleep(0.15)

        result = cache.get("key1")
        assert result is None

    def test_lru_eviction(self):
        """maxsize 초과 시 LRU 정책으로 제거."""
        cache = TTLCache[str](maxsize=3, ttl_seconds=60, name="test")

        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")

        # maxsize 초과 시 가장 오래된 항목 제거
        cache.set("key4", "value4")

        assert cache.get("key1") is None  # 제거됨
        assert cache.get("key2") == "value2"
        assert cache.get("key3") == "value3"
        assert cache.get("key4") == "value4"

    def test_lru_access_order(self):
        """접근 시 LRU 순서 갱신."""
        cache = TTLCache[str](maxsize=3, ttl_seconds=60, name="test")

        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")

        # key1 접근하여 최신으로 갱신
        cache.get("key1")

        # key4 추가 시 key2가 제거됨 (가장 오래된)
        cache.set("key4", "value4")

        assert cache.get("key1") == "value1"  # 접근했으므로 유지
        assert cache.get("key2") is None  # 제거됨
        assert cache.get("key3") == "value3"
        assert cache.get("key4") == "value4"

    def test_stats(self):
        """캐시 통계 확인."""
        cache = TTLCache[str](maxsize=10, ttl_seconds=60, name="test_stats")

        cache.set("key1", "value1")
        cache.get("key1")  # hit
        cache.get("key1")  # hit
        cache.get("nonexistent")  # miss

        stats = cache.stats()

        assert stats["name"] == "test_stats"
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["hit_rate"] == "66.7%"


class TestMakeCacheKey:
    """캐시 키 생성 테스트."""

    def test_consistent_key(self):
        """동일 입력에 대해 동일 키 생성."""
        data = {"dataset": "POLICY", "query": "연차휴가", "top_k": 5}

        key1 = make_cache_key(data)
        key2 = make_cache_key(data)

        assert key1 == key2

    def test_different_data_different_key(self):
        """다른 입력에 대해 다른 키 생성."""
        data1 = {"dataset": "POLICY", "query": "연차휴가", "top_k": 5}
        data2 = {"dataset": "POLICY", "query": "연차휴가", "top_k": 10}

        key1 = make_cache_key(data1)
        key2 = make_cache_key(data2)

        assert key1 != key2

    def test_key_order_independent(self):
        """딕셔너리 키 순서와 무관하게 동일 키 생성."""
        data1 = {"dataset": "POLICY", "query": "연차휴가", "top_k": 5}
        data2 = {"top_k": 5, "dataset": "POLICY", "query": "연차휴가"}

        key1 = make_cache_key(data1)
        key2 = make_cache_key(data2)

        assert key1 == key2


class TestRagflowSearchClientCache:
    """RagflowSearchClient 캐시 통합 테스트."""

    @pytest.mark.anyio
    async def test_cache_hit_prevents_http_call(self):
        """동일 요청 2회 호출 시 HTTP 호출 1번만 발생."""
        # Mock 설정
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "results": [
                    {"content": "test content", "similarity": 0.9}
                ]
            }
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        # 설정 mock
        with patch("app.clients.ragflow_search_client.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                ragflow_base_url="http://test-ragflow:8080",
                RAGFLOW_API_KEY=None,
                ragflow_dataset_to_kb_mapping={"POLICY": "kb_policy"},
                RAGFLOW_KB_ID_POLICY="kb_policy",
                RAGFLOW_KB_ID_TRAINING=None,
                RAGFLOW_KB_ID_SECURITY=None,
                RAGFLOW_KB_ID_INCIDENT=None,
                RAGFLOW_KB_ID_EDUCATION=None,
                FAQ_RAG_CACHE_ENABLED=True,
                FAQ_RAG_CACHE_TTL_SECONDS=300,
                FAQ_RAG_CACHE_MAXSIZE=1024,
            )

            with patch("app.clients.ragflow_search_client.get_async_http_client", return_value=mock_client):
                client = RagflowSearchClient()

                # 첫 번째 호출 (캐시 미스 - HTTP 호출 발생)
                result1 = await client.search_chunks("연차휴가 규정", "POLICY", top_k=5)

                # 두 번째 호출 (캐시 히트 - HTTP 호출 없음)
                result2 = await client.search_chunks("연차휴가 규정", "POLICY", top_k=5)

                # HTTP 클라이언트는 1번만 호출되어야 함
                assert mock_client.post.call_count == 1

                # 결과는 동일해야 함
                assert result1 == result2

    @pytest.mark.anyio
    async def test_cache_miss_on_different_query(self):
        """다른 쿼리는 캐시 미스로 HTTP 호출 발생."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "results": [
                    {"content": "test content", "similarity": 0.9}
                ]
            }
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.clients.ragflow_search_client.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                ragflow_base_url="http://test-ragflow:8080",
                RAGFLOW_API_KEY=None,
                ragflow_dataset_to_kb_mapping={"POLICY": "kb_policy"},
                RAGFLOW_KB_ID_POLICY="kb_policy",
                RAGFLOW_KB_ID_TRAINING=None,
                RAGFLOW_KB_ID_SECURITY=None,
                RAGFLOW_KB_ID_INCIDENT=None,
                RAGFLOW_KB_ID_EDUCATION=None,
                FAQ_RAG_CACHE_ENABLED=True,
                FAQ_RAG_CACHE_TTL_SECONDS=300,
                FAQ_RAG_CACHE_MAXSIZE=1024,
            )

            with patch("app.clients.ragflow_search_client.get_async_http_client", return_value=mock_client):
                client = RagflowSearchClient()

                # 다른 쿼리로 호출
                await client.search_chunks("연차휴가 규정", "POLICY", top_k=5)
                await client.search_chunks("출장비 정산", "POLICY", top_k=5)

                # HTTP 클라이언트는 2번 호출되어야 함
                assert mock_client.post.call_count == 2

    @pytest.mark.anyio
    async def test_cache_disabled(self):
        """캐시 비활성화 시 항상 HTTP 호출."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "results": [
                    {"content": "test content", "similarity": 0.9}
                ]
            }
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("app.clients.ragflow_search_client.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                ragflow_base_url="http://test-ragflow:8080",
                RAGFLOW_API_KEY=None,
                ragflow_dataset_to_kb_mapping={"POLICY": "kb_policy"},
                RAGFLOW_KB_ID_POLICY="kb_policy",
                RAGFLOW_KB_ID_TRAINING=None,
                RAGFLOW_KB_ID_SECURITY=None,
                RAGFLOW_KB_ID_INCIDENT=None,
                RAGFLOW_KB_ID_EDUCATION=None,
                FAQ_RAG_CACHE_ENABLED=False,  # 캐시 비활성화
                FAQ_RAG_CACHE_TTL_SECONDS=300,
                FAQ_RAG_CACHE_MAXSIZE=1024,
            )

            with patch("app.clients.ragflow_search_client.get_async_http_client", return_value=mock_client):
                client = RagflowSearchClient()

                # 동일 쿼리 2회 호출
                await client.search_chunks("연차휴가 규정", "POLICY", top_k=5, use_cache=False)
                await client.search_chunks("연차휴가 규정", "POLICY", top_k=5, use_cache=False)

                # 캐시 비활성화 시 항상 HTTP 호출
                assert mock_client.post.call_count == 2
