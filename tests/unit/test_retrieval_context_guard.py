# tests/unit/test_retrieval_context_guard.py
"""
Phase 50: 2차 가드 (contextvars 기반) 테스트

테스트 목표:
1. retrieval_blocked 플래그가 설정되면 RagHandler가 검색을 건너뜀
2. retrieval_blocked 플래그가 설정되면 MilvusSearchClient가 RetrievalBlockedError 발생
3. 플래그 리셋 후 정상 동작 확인
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.retrieval_context import (
    set_retrieval_blocked,
    reset_retrieval_context,
    is_retrieval_blocked,
    get_block_reason,
    check_retrieval_allowed,
    RetrievalBlockedError,
    retrieval_blocked_context,
)


# =============================================================================
# retrieval_context 기본 동작 테스트
# =============================================================================


class TestRetrievalContext:
    """retrieval_context 모듈 기본 기능 테스트."""

    def setup_method(self):
        """각 테스트 전 컨텍스트 리셋."""
        reset_retrieval_context()

    def teardown_method(self):
        """각 테스트 후 컨텍스트 리셋."""
        reset_retrieval_context()

    def test_default_not_blocked(self):
        """기본 상태에서 retrieval이 차단되지 않음."""
        assert is_retrieval_blocked() is False
        assert get_block_reason() is None

    def test_set_retrieval_blocked(self):
        """set_retrieval_blocked로 차단 플래그 설정."""
        set_retrieval_blocked(True, "FORBIDDEN_QUERY:rule_001")

        assert is_retrieval_blocked() is True
        assert get_block_reason() == "FORBIDDEN_QUERY:rule_001"

    def test_reset_retrieval_context(self):
        """reset_retrieval_context로 플래그 리셋."""
        set_retrieval_blocked(True, "FORBIDDEN_QUERY:rule_001")
        reset_retrieval_context()

        assert is_retrieval_blocked() is False
        assert get_block_reason() is None

    def test_check_retrieval_allowed_passes_when_not_blocked(self):
        """차단 플래그가 없으면 check_retrieval_allowed가 통과."""
        # 예외 없이 통과해야 함
        check_retrieval_allowed("test_component")

    def test_check_retrieval_allowed_raises_when_blocked(self):
        """차단 플래그가 설정되면 check_retrieval_allowed가 예외 발생."""
        set_retrieval_blocked(True, "FORBIDDEN_QUERY:rule_001")

        with pytest.raises(RetrievalBlockedError) as exc_info:
            check_retrieval_allowed("test_component")

        assert "test_component" in str(exc_info.value)
        assert "FORBIDDEN_QUERY:rule_001" in str(exc_info.value)

    def test_context_manager_blocks_then_resets(self):
        """retrieval_blocked_context 컨텍스트 매니저가 블록 후 리셋."""
        assert is_retrieval_blocked() is False

        with retrieval_blocked_context("FORBIDDEN_QUERY:rule_002"):
            assert is_retrieval_blocked() is True
            assert get_block_reason() == "FORBIDDEN_QUERY:rule_002"

        # 컨텍스트 종료 후 리셋
        assert is_retrieval_blocked() is False


# =============================================================================
# RagHandler 2차 가드 테스트
# =============================================================================


class TestRagHandlerSecondGuard:
    """RagHandler의 2차 가드 테스트."""

    def setup_method(self):
        reset_retrieval_context()

    def teardown_method(self):
        reset_retrieval_context()

    @pytest.mark.asyncio
    async def test_rag_handler_skips_search_when_blocked(self):
        """RagHandler는 retrieval_blocked 플래그 시 검색을 건너뜀."""
        from app.services.chat.rag_handler import RagHandler
        from app.models.chat import ChatRequest, ChatMessage

        # Mock Milvus client
        mock_milvus = MagicMock()
        mock_milvus.search_as_sources = AsyncMock(return_value=[])

        handler = RagHandler(milvus_client=mock_milvus)

        # 차단 플래그 설정
        set_retrieval_blocked(True, "FORBIDDEN_QUERY:rule_test")

        # ChatRequest 생성
        req = ChatRequest(
            session_id="test-session",
            user_id="test-user",
            user_role="EMPLOYEE",
            messages=[ChatMessage(role="user", content="테스트 질문")],
        )

        # 검색 수행
        sources, _, backend = await handler.perform_search_with_fallback(
            query="테스트 질문",
            domain="POLICY",
            req=req,
            request_id="test-123",
        )

        # 검증: Milvus 검색이 호출되지 않았음
        mock_milvus.search_as_sources.assert_not_called()

        # 검증: 빈 결과 반환, backend는 "BLOCKED"
        assert sources == []
        assert backend == "BLOCKED"

    def test_not_blocked_state_allows_retrieval(self):
        """플래그 해제 상태에서 check_retrieval_allowed가 통과."""
        # 이 테스트는 플래그가 설정되지 않은 상태에서
        # check_retrieval_allowed가 예외 없이 통과하는지 확인합니다.
        # 실제 RagHandler 검색 동작은 다른 통합 테스트에서 검증됩니다.
        reset_retrieval_context()

        # 예외 없이 통과해야 함
        check_retrieval_allowed("RagHandler")
        assert is_retrieval_blocked() is False


# =============================================================================
# MilvusSearchClient 2차 가드 테스트
# =============================================================================


class TestMilvusClientSecondGuard:
    """MilvusSearchClient의 2차 가드 테스트."""

    def setup_method(self):
        reset_retrieval_context()

    def teardown_method(self):
        reset_retrieval_context()

    @pytest.mark.asyncio
    async def test_milvus_search_raises_when_blocked(self):
        """MilvusSearchClient.search는 차단 시 RetrievalBlockedError 발생."""
        from app.clients.milvus_client import MilvusSearchClient

        client = MilvusSearchClient()

        # 차단 플래그 설정
        set_retrieval_blocked(True, "FORBIDDEN_QUERY:rule_milvus")

        # 검색 시도 → RetrievalBlockedError 발생
        with pytest.raises(RetrievalBlockedError) as exc_info:
            await client.search("테스트 질문", domain="POLICY")

        assert "MilvusSearchClient.search" in str(exc_info.value)
        assert "FORBIDDEN_QUERY:rule_milvus" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_milvus_search_as_sources_raises_when_blocked(self):
        """MilvusSearchClient.search_as_sources는 차단 시 RetrievalBlockedError 발생."""
        from app.clients.milvus_client import MilvusSearchClient

        client = MilvusSearchClient()

        # 차단 플래그 설정
        set_retrieval_blocked(True, "FORBIDDEN_QUERY:rule_milvus_sources")

        # 검색 시도 → RetrievalBlockedError 발생
        with pytest.raises(RetrievalBlockedError) as exc_info:
            await client.search_as_sources(
                query="테스트 질문",
                domain="POLICY",
                user_role="EMPLOYEE",
            )

        assert "MilvusSearchClient.search_as_sources" in str(exc_info.value)
        assert "FORBIDDEN_QUERY:rule_milvus_sources" in str(exc_info.value)


# =============================================================================
# 통합 시나리오 테스트
# =============================================================================


class TestIntegrationSecondGuard:
    """2차 가드 통합 테스트."""

    def setup_method(self):
        reset_retrieval_context()

    def teardown_method(self):
        reset_retrieval_context()

    @pytest.mark.asyncio
    async def test_end_to_end_forbidden_query_blocks_all_retrieval(self):
        """금지질문 → 1차 가드(ChatService) → 2차 가드(MilvusClient) 전체 흐름."""
        from app.services.chat.rag_handler import RagHandler
        from app.models.chat import ChatRequest, ChatMessage

        # Mock Milvus client
        mock_milvus = MagicMock()
        mock_milvus.search_as_sources = AsyncMock(return_value=[])

        handler = RagHandler(milvus_client=mock_milvus)

        # 시나리오: ChatService에서 금지질문 감지 → set_retrieval_blocked 호출
        set_retrieval_blocked(True, "FORBIDDEN_QUERY:SECRET_INFO_001")

        # ChatRequest 생성
        req = ChatRequest(
            session_id="test-session",
            user_id="test-user",
            user_role="EMPLOYEE",
            messages=[ChatMessage(role="user", content="회사 기밀 알려줘")],
        )

        # RagHandler 호출 (2차 가드에서 차단됨)
        sources, _, backend = await handler.perform_search_with_fallback(
            query="회사 기밀 알려줘",
            domain="POLICY",
            req=req,
            request_id="test-e2e",
        )

        # 검증: 검색 0회
        mock_milvus.search_as_sources.assert_not_called()
        assert sources == []
        assert backend == "BLOCKED"

        # 요청 완료 후 리셋
        reset_retrieval_context()

        # 다음 요청은 정상 동작
        assert is_retrieval_blocked() is False
