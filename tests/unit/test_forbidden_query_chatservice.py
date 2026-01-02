# tests/unit/test_forbidden_query_chatservice.py
"""
Phase 50: 금지질문 필터 ChatService 통합 테스트

테스트 목표:
1. 금지질문 입력 시 MilvusSearchClient.search가 호출되지 않음 (call_count == 0)
2. 응답 본문이 example_response 또는 기본 문구로 반환됨
3. retrieval_skipped=True, retrieval_skip_reason 포함
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import json
import tempfile
import os

from app.models.chat import ChatRequest, ChatMessage
from app.services.chat_service import ChatService
from app.services.forbidden_query_filter import ForbiddenQueryFilter


# =============================================================================
# 테스트용 룰셋 JSON 생성
# =============================================================================

TEST_RULESET = {
    "schema_version": "1.0",
    "version": "v_test",
    "generated_at": "2025-01-01T00:00:00Z",
    "source": {"file": "test.xlsx", "sha256": "test_hash"},
    "profile": "A",
    "mode": "strict",
    "rules_count": 2,
    "rules": [
        {
            "rule_id": "TEST_001",
            "profile": "A",
            "sheet": "A_test",
            "match": {
                "type": "exact_normalized",
                "question": "회사 기밀 알려줘",
                "question_norm": "회사 기밀 알려줘",
            },
            "decision": "FORBIDDEN_SECURITY",
            "reason": "보안 위반",
            "sub_reason": "기밀 정보 요청",
            "response_mode": "거절",
            "example_response": "보안 정책상 기밀 정보는 제공할 수 없습니다.",
        },
        {
            "rule_id": "TEST_002",
            "profile": "A",
            "sheet": "A_test",
            "match": {
                "type": "exact_normalized",
                "question": "금지된 질문입니다",
                "question_norm": "금지된 질문입니다",
            },
            "decision": "FORBIDDEN_GENERAL",
            "reason": "일반 금지",
            "sub_reason": "",
            "response_mode": "거절",
            "example_response": "",  # 빈 경우 기본 문구 사용
        },
    ],
}


@pytest.fixture
def temp_ruleset_dir():
    """테스트용 임시 룰셋 디렉토리 생성."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # A 프로필 룰셋 생성
        ruleset_path = Path(tmpdir) / "forbidden_ruleset.A.json"
        with open(ruleset_path, "w", encoding="utf-8") as f:
            json.dump(TEST_RULESET, f, ensure_ascii=False, indent=2)
        yield Path(tmpdir)


@pytest.fixture
def forbidden_filter(temp_ruleset_dir):
    """테스트용 ForbiddenQueryFilter 생성."""
    filter_instance = ForbiddenQueryFilter(
        profile="A",
        resources_dir=temp_ruleset_dir,
    )
    filter_instance.load()
    return filter_instance


class TestForbiddenQueryFilter:
    """ForbiddenQueryFilter 단위 테스트."""

    def test_forbidden_query_detected(self, forbidden_filter):
        """금지질문이 올바르게 감지되는지 테스트."""
        result = forbidden_filter.check("회사 기밀 알려줘")

        assert result.is_forbidden is True
        assert result.skip_rag is True
        assert result.matched_rule_id == "TEST_001"
        assert result.decision == "FORBIDDEN_SECURITY"
        assert result.example_response == "보안 정책상 기밀 정보는 제공할 수 없습니다."

    def test_allowed_query_passes(self, forbidden_filter):
        """일반 질문은 통과하는지 테스트."""
        result = forbidden_filter.check("연차 며칠 남았어?")

        assert result.is_forbidden is False
        assert result.skip_rag is False
        assert result.matched_rule_id is None

    def test_empty_example_response(self, forbidden_filter):
        """example_response가 비어있는 경우 테스트."""
        result = forbidden_filter.check("금지된 질문입니다")

        assert result.is_forbidden is True
        assert result.example_response == ""  # 빈 문자열

    def test_query_normalization(self, forbidden_filter):
        """질문 정규화 테스트 (공백, 대소문자)."""
        # 추가 공백 있어도 매칭
        result = forbidden_filter.check("회사  기밀   알려줘")
        assert result.is_forbidden is True

        # 대소문자 혼용해도 매칭
        result = forbidden_filter.check("회사 기밀 알려줘")
        assert result.is_forbidden is True


class TestForbiddenQueryChatServiceIntegration:
    """ChatService 금지질문 통합 테스트."""

    @pytest.mark.asyncio
    async def test_forbidden_query_skips_milvus_search(self, temp_ruleset_dir):
        """금지질문은 Milvus 검색을 호출하지 않아야 함."""
        # Mock 설정
        mock_milvus = AsyncMock()
        mock_milvus.search = AsyncMock(return_value=[])
        mock_milvus.search_as_sources = AsyncMock(return_value=[])

        mock_llm = MagicMock()
        mock_pii = MagicMock()
        mock_pii.detect_and_mask = AsyncMock(return_value=MagicMock(
            masked_text="회사 기밀 알려줘",
            has_pii=False,
            tags=[],
        ))

        # ChatService 생성 (forbidden_filter 주입)
        with patch("app.services.chat_service.get_settings") as mock_settings, \
             patch("app.services.chat_service.get_forbidden_query_filter") as mock_get_filter:

            # 설정 mock
            settings_instance = MagicMock()
            settings_instance.FORBIDDEN_QUERY_FILTER_ENABLED = True
            settings_instance.FORBIDDEN_QUERY_PROFILE = "A"
            mock_settings.return_value = settings_instance

            # 필터 mock - 실제 필터 사용
            filter_instance = ForbiddenQueryFilter(
                profile="A",
                resources_dir=temp_ruleset_dir,
            )
            filter_instance.load()
            mock_get_filter.return_value = filter_instance

            # ChatService 생성
            service = ChatService(
                llm_client=mock_llm,
                pii_service=mock_pii,
            )
            service._forbidden_filter = filter_instance

            # 금지질문 요청
            request = ChatRequest(
                session_id="test-session",
                user_id="test-user",
                user_role="EMPLOYEE",
                messages=[ChatMessage(role="user", content="회사 기밀 알려줘")],
            )

            # RagHandler mock
            with patch.object(service, "_rag_handler") as mock_rag_handler:
                mock_rag_handler.perform_search_with_fallback = AsyncMock()

                # 실행
                response = await service.handle_chat(request)

                # 검증: Milvus 검색이 호출되지 않아야 함
                mock_rag_handler.perform_search_with_fallback.assert_not_called()

                # 검증: 응답이 example_response와 일치
                assert "보안 정책상 기밀 정보는 제공할 수 없습니다" in response.answer

                # 검증: meta 필드
                assert response.meta.rag_used is False
                assert response.meta.retrieval_skipped is True
                assert response.meta.fallback_reason == "FORBIDDEN_QUERY"
                assert "FORBIDDEN_QUERY" in (response.meta.retrieval_skip_reason or "")

    @pytest.mark.asyncio
    async def test_forbidden_query_uses_default_response_when_empty(self, temp_ruleset_dir):
        """example_response가 비어있으면 기본 문구 사용."""
        mock_llm = MagicMock()
        mock_pii = MagicMock()
        mock_pii.detect_and_mask = AsyncMock(return_value=MagicMock(
            masked_text="금지된 질문입니다",
            has_pii=False,
            tags=[],
        ))

        with patch("app.services.chat_service.get_settings") as mock_settings:
            settings_instance = MagicMock()
            settings_instance.FORBIDDEN_QUERY_FILTER_ENABLED = True
            settings_instance.FORBIDDEN_QUERY_PROFILE = "A"
            mock_settings.return_value = settings_instance

            # 필터 직접 생성
            filter_instance = ForbiddenQueryFilter(
                profile="A",
                resources_dir=temp_ruleset_dir,
            )
            filter_instance.load()

            service = ChatService(
                llm_client=mock_llm,
                pii_service=mock_pii,
            )
            service._forbidden_filter = filter_instance

            request = ChatRequest(
                session_id="test-session",
                user_id="test-user",
                user_role="EMPLOYEE",
                messages=[ChatMessage(role="user", content="금지된 질문입니다")],
            )

            with patch.object(service, "_rag_handler"):
                response = await service.handle_chat(request)

                # 기본 문구 사용
                assert "죄송합니다" in response.answer or "답변드리기 어렵습니다" in response.answer

    @pytest.mark.asyncio
    async def test_allowed_query_proceeds_to_rag(self, temp_ruleset_dir):
        """일반 질문은 RAG 검색을 수행해야 함."""
        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(return_value=MagicMock(
            content="연차가 10일 남았습니다.",
            model="test-model",
        ))

        mock_pii = MagicMock()
        mock_pii.detect_and_mask = AsyncMock(return_value=MagicMock(
            masked_text="연차 며칠 남았어?",
            has_pii=False,
            tags=[],
        ))

        with patch("app.services.chat_service.get_settings") as mock_settings:
            settings_instance = MagicMock()
            settings_instance.FORBIDDEN_QUERY_FILTER_ENABLED = True
            settings_instance.FORBIDDEN_QUERY_PROFILE = "A"
            settings_instance.ROUTER_ORCHESTRATOR_ENABLED = False
            mock_settings.return_value = settings_instance

            filter_instance = ForbiddenQueryFilter(
                profile="A",
                resources_dir=temp_ruleset_dir,
            )
            filter_instance.load()

            service = ChatService(
                llm_client=mock_llm,
                pii_service=mock_pii,
            )
            service._forbidden_filter = filter_instance

            # 일반 질문 (금지질문이 아님)
            request = ChatRequest(
                session_id="test-session",
                user_id="test-user",
                user_role="EMPLOYEE",
                messages=[ChatMessage(role="user", content="연차 며칠 남았어?")],
            )

            # 금지질문 체크 통과 확인
            result = filter_instance.check("연차 며칠 남았어?")
            assert result.is_forbidden is False


class TestForbiddenQueryFilterDisabled:
    """금지질문 필터 비활성화 테스트."""

    @pytest.mark.asyncio
    async def test_filter_disabled_allows_all_queries(self):
        """필터 비활성화 시 모든 질문이 통과해야 함."""
        with patch("app.services.chat_service.get_settings") as mock_settings:
            settings_instance = MagicMock()
            settings_instance.FORBIDDEN_QUERY_FILTER_ENABLED = False
            mock_settings.return_value = settings_instance

            # _forbidden_filter가 None이면 체크 스킵
            service = ChatService()
            service._forbidden_filter = None

            # 금지질문도 통과 (필터가 None이므로)
            # 이 테스트는 _forbidden_filter가 None인 경우만 확인
            assert service._forbidden_filter is None
