# tests/unit/test_forbidden_query_faqservice.py
"""
Phase 50: FaqService 금지질문 필터 테스트 (Step 2-1)

테스트 목표:
1. 금지질문 입력 시 MilvusSearchClient.search가 호출되지 않음
2. FaqGenerationError("FORBIDDEN_QUERY:rule_id") 발생
3. 정상 질문은 Milvus 검색 수행
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import json
import tempfile

from app.models.faq import FaqDraftGenerateRequest
from app.services.faq_service import FaqDraftService, FaqGenerationError
from app.services.forbidden_query_filter import ForbiddenQueryFilter


# =============================================================================
# 테스트용 룰셋 JSON
# =============================================================================

TEST_RULESET = {
    "schema_version": "1.0",
    "version": "v_test",
    "generated_at": "2025-01-01T00:00:00Z",
    "source": {"file": "test.xlsx", "sha256": "test_hash"},
    "profile": "A",
    "mode": "strict",
    "rules_count": 1,
    "rules": [
        {
            "rule_id": "FAQ_TEST_001",
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
    ],
}


@pytest.fixture
def temp_ruleset_dir():
    """테스트용 임시 룰셋 디렉토리 생성."""
    with tempfile.TemporaryDirectory() as tmpdir:
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


class TestFaqServiceForbiddenQuery:
    """FaqDraftService 금지질문 테스트."""

    @pytest.mark.asyncio
    async def test_forbidden_query_skips_milvus_search(self, temp_ruleset_dir):
        """금지질문은 Milvus 검색을 호출하지 않아야 함."""
        # Mock Milvus client
        mock_milvus = MagicMock()
        mock_milvus.search = AsyncMock(return_value=[])

        # Mock LLM client
        mock_llm = MagicMock()

        # Mock PII service
        mock_pii = MagicMock()
        mock_pii.detect_and_mask = AsyncMock(return_value=MagicMock(
            masked_text="회사 기밀 알려줘",
            has_pii=False,
            tags=[],
        ))

        with patch("app.services.faq_service.get_settings") as mock_settings:
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

            # FaqDraftService 생성
            service = FaqDraftService(
                milvus_client=mock_milvus,
                llm_client=mock_llm,
                pii_service=mock_pii,
            )
            service._forbidden_filter = filter_instance

            # 금지질문 요청
            req = FaqDraftGenerateRequest(
                domain="POLICY",
                cluster_id="test-cluster",
                canonical_question="회사 기밀 알려줘",
                sample_questions=["회사 기밀 알려줘"],
            )

            # 실행: FaqGenerationError 발생해야 함
            with pytest.raises(FaqGenerationError) as exc_info:
                await service._search_milvus(req)

            # 검증: 에러 메시지에 FORBIDDEN_QUERY 포함
            assert "FORBIDDEN_QUERY" in str(exc_info.value)
            assert "FAQ_TEST_001" in str(exc_info.value)

            # 검증: Milvus search가 호출되지 않았음
            mock_milvus.search.assert_not_called()

    @pytest.mark.asyncio
    async def test_allowed_query_calls_milvus_search(self, temp_ruleset_dir):
        """정상 질문은 Milvus 검색을 수행해야 함."""
        # Mock Milvus client - 정상 검색 결과 반환
        mock_milvus = MagicMock()
        mock_milvus.search = AsyncMock(return_value=[
            {
                "doc_id": "doc1",
                "content": "연차 관련 규정 내용입니다.",
                "score": 0.95,
                "metadata": {"chunk_id": 1},
            }
        ])

        mock_llm = MagicMock()

        mock_pii = MagicMock()
        mock_pii.detect_and_mask = AsyncMock(return_value=MagicMock(
            masked_text="연차 규정 알려줘",
            has_pii=False,
            tags=[],
        ))

        with patch("app.services.faq_service.get_settings") as mock_settings:
            settings_instance = MagicMock()
            settings_instance.FORBIDDEN_QUERY_FILTER_ENABLED = True
            settings_instance.FORBIDDEN_QUERY_PROFILE = "A"
            mock_settings.return_value = settings_instance

            filter_instance = ForbiddenQueryFilter(
                profile="A",
                resources_dir=temp_ruleset_dir,
            )
            filter_instance.load()

            service = FaqDraftService(
                milvus_client=mock_milvus,
                llm_client=mock_llm,
                pii_service=mock_pii,
            )
            service._forbidden_filter = filter_instance

            # 정상 질문 요청
            req = FaqDraftGenerateRequest(
                domain="POLICY",
                cluster_id="test-cluster",
                canonical_question="연차 규정 알려줘",
                sample_questions=["연차 규정 알려줘"],
            )

            # 실행
            context_docs, source = await service._search_milvus(req)

            # 검증: Milvus search가 호출됨
            mock_milvus.search.assert_called_once()

            # 검증: 결과 반환
            assert len(context_docs) == 1
            assert source == "MILVUS"

    @pytest.mark.asyncio
    async def test_filter_disabled_allows_all_queries(self, temp_ruleset_dir):
        """필터 비활성화 시 모든 질문이 Milvus 검색을 수행."""
        mock_milvus = MagicMock()
        mock_milvus.search = AsyncMock(return_value=[
            {
                "doc_id": "doc1",
                "content": "기밀 정보입니다.",
                "score": 0.9,
                "metadata": {"chunk_id": 1},
            }
        ])

        mock_llm = MagicMock()
        mock_pii = MagicMock()
        mock_pii.detect_and_mask = AsyncMock(return_value=MagicMock(
            masked_text="회사 기밀 알려줘",
            has_pii=False,
            tags=[],
        ))

        with patch("app.services.faq_service.get_settings") as mock_settings:
            settings_instance = MagicMock()
            settings_instance.FORBIDDEN_QUERY_FILTER_ENABLED = False  # 비활성화
            settings_instance.FORBIDDEN_QUERY_PROFILE = "A"
            mock_settings.return_value = settings_instance

            service = FaqDraftService(
                milvus_client=mock_milvus,
                llm_client=mock_llm,
                pii_service=mock_pii,
            )
            # 필터가 None인 상태

            req = FaqDraftGenerateRequest(
                domain="POLICY",
                cluster_id="test-cluster",
                canonical_question="회사 기밀 알려줘",  # 금지질문이지만 필터 비활성화
                sample_questions=["회사 기밀 알려줘"],
            )

            # 실행: 필터 비활성화이므로 Milvus 검색 수행
            context_docs, source = await service._search_milvus(req)

            # 검증: Milvus search가 호출됨
            mock_milvus.search.assert_called_once()
            assert len(context_docs) == 1
