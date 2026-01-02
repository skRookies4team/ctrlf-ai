"""
FaqDraftService 단위 테스트 (Phase 19-AI-2)

테스트 케이스:
1. top_docs가 있으면 그대로 사용
2. top_docs가 없으면 RagHandler 검색
3. RagHandler 검색 결과 없으면 NO_DOCS_FOUND 에러
4. 검색 결과 포맷팅
5. source 필드 처리 (top_docs vs MILVUS)
6. 참고 문서 정보 answer_markdown에 추가

Step 7 업데이트: milvus_client → rag_handler
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from app.services.faq_service import (
    FaqDraftService,
    FaqGenerationError,
    RagSearchResult,
    DocContext,
)
from app.models.faq import (
    FaqDraft,
    FaqDraftGenerateRequest,
    FaqSourceDoc,
)
from app.models.chat import ChatSource
from app.services.chat.rag_handler import RagHandler, RagSearchUnavailableError


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


@pytest.fixture
def mock_rag_handler():
    """테스트용 RagHandler mock"""
    handler = MagicMock(spec=RagHandler)
    handler.perform_search_with_fallback = AsyncMock(return_value=([], False, "MILVUS"))
    return handler


@pytest.fixture
def mock_llm_client():
    """테스트용 LLMClient mock"""
    client = AsyncMock()
    client.generate_chat_completion = AsyncMock(return_value="""{
        "question": "테스트 FAQ 질문",
        "answer_markdown": "**답변 요약**\\n\\n- 상세 내용 1\\n- 상세 내용 2",
        "summary": "답변 요약",
        "answer_source": "AI_RAG",
        "ai_confidence": 0.9
    }""")
    return client


@pytest.fixture
def sample_request() -> FaqDraftGenerateRequest:
    """테스트용 FAQ 생성 요청"""
    return FaqDraftGenerateRequest(
        cluster_id="cluster-001",
        domain="POLICY",
        canonical_question="연차휴가 이월 규정이 어떻게 되나요?",
        sample_questions=[
            "연차 이월 가능한가요?",
            "작년 연차 올해 사용 가능?",
        ],
        top_docs=[],  # 빈 리스트로 설정 (None 대신)
    )


@pytest.fixture
def sample_top_docs() -> list[FaqSourceDoc]:
    """테스트용 top_docs"""
    return [
        FaqSourceDoc(
            doc_id="doc-001",
            doc_version="v1.0",
            title="연차휴가 규정",
            snippet="연차휴가는 다음 해로 이월이 가능합니다.",
            article_label="제5조",
            article_path="/policy/leave/article-5",
        ),
    ]


@pytest.fixture
def sample_chat_sources() -> list[ChatSource]:
    """테스트용 RagHandler 검색 결과 (ChatSource 형태)"""
    return [
        ChatSource(
            doc_id="인사규정.pdf",
            title="인사규정.pdf",
            snippet="연차휴가는 익년도로 이월 가능하며, 최대 10일까지 이월됩니다.",
            score=0.92,
        ),
        ChatSource(
            doc_id="휴가관리지침.pdf",
            title="휴가관리지침.pdf",
            snippet="연차휴가 이월 신청은 매년 12월 말까지 완료해야 합니다.",
            score=0.85,
        ),
    ]


@pytest.fixture
def sample_milvus_chunks() -> list[dict]:
    """테스트용 Milvus 검색 결과 (레거시 형태)"""
    return [
        {
            "document_name": "인사규정.pdf",
            "page_num": 15,
            "similarity": 0.92,
            "content": "연차휴가는 익년도로 이월 가능하며, 최대 10일까지 이월됩니다.",
        },
        {
            "document_name": "휴가관리지침.pdf",
            "page_num": 8,
            "similarity": 0.85,
            "content": "연차휴가 이월 신청은 매년 12월 말까지 완료해야 합니다.",
        },
    ]


# =============================================================================
# 테스트: RagSearchResult.from_chunk
# =============================================================================


class TestRagSearchResult:
    """RagSearchResult 단위 테스트"""

    def test_from_chunk_full_data(self):
        """모든 필드가 있는 chunk 변환"""
        chunk = {
            "document_name": "테스트문서.pdf",
            "page_num": 10,
            "similarity": 0.95,
            "content": "테스트 내용입니다.",
        }
        result = RagSearchResult.from_chunk(chunk)

        assert result.title == "테스트문서.pdf"
        assert result.page == 10
        assert result.score == 0.95
        assert result.snippet == "테스트 내용입니다."

    def test_from_chunk_alternative_fields(self):
        """대체 필드명 처리"""
        chunk = {
            "doc_name": "대체문서.pdf",
            "page": 5,
            "score": 0.88,
            "text": "대체 내용입니다.",
        }
        result = RagSearchResult.from_chunk(chunk)

        assert result.title == "대체문서.pdf"
        assert result.page == 5
        assert result.score == 0.88
        assert result.snippet == "대체 내용입니다."

    def test_from_chunk_missing_fields(self):
        """필드 누락 시 기본값"""
        chunk = {}
        result = RagSearchResult.from_chunk(chunk)

        assert result.title is None
        assert result.page is None
        assert result.score == 0.0
        assert result.snippet == ""

    def test_from_chunk_snippet_truncation(self):
        """500자 초과 snippet 잘림"""
        long_content = "a" * 600
        chunk = {"content": long_content}
        result = RagSearchResult.from_chunk(chunk)

        assert len(result.snippet) == 500


# =============================================================================
# 테스트: _get_context_docs
# =============================================================================


class TestGetContextDocs:
    """_get_context_docs 메서드 테스트"""

    @pytest.mark.anyio
    async def test_use_top_docs_when_provided(
        self, mock_rag_handler, mock_llm_client, sample_request, sample_top_docs
    ):
        """top_docs가 제공되면 그대로 사용"""
        sample_request.top_docs = sample_top_docs
        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm_client,
        )

        context_docs, answer_source = await service._get_context_docs(sample_request)

        assert answer_source == "TOP_DOCS"
        assert context_docs == sample_top_docs
        mock_rag_handler.perform_search_with_fallback.assert_not_called()

    @pytest.mark.anyio
    async def test_search_milvus_when_no_top_docs(
        self, mock_rag_handler, mock_llm_client, sample_request, sample_chat_sources
    ):
        """top_docs가 없으면 RagHandler 검색"""
        mock_rag_handler.perform_search_with_fallback = AsyncMock(
            return_value=(sample_chat_sources, False, "MILVUS")
        )
        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm_client,
        )

        context_docs, answer_source = await service._get_context_docs(sample_request)

        assert answer_source == "MILVUS"
        assert len(context_docs) == 2
        mock_rag_handler.perform_search_with_fallback.assert_called_once()

    @pytest.mark.anyio
    async def test_no_docs_found_error(
        self, mock_rag_handler, mock_llm_client, sample_request
    ):
        """RagHandler 검색 결과가 없으면 NO_DOCS_FOUND 에러"""
        mock_rag_handler.perform_search_with_fallback = AsyncMock(
            return_value=([], False, "MILVUS")
        )
        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm_client,
        )

        with pytest.raises(FaqGenerationError) as exc_info:
            await service._get_context_docs(sample_request)

        assert "NO_DOCS_FOUND" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_milvus_search_error(
        self, mock_rag_handler, mock_llm_client, sample_request
    ):
        """RagHandler 검색 오류 처리"""
        mock_rag_handler.perform_search_with_fallback = AsyncMock(
            side_effect=RagSearchUnavailableError("Search failed")
        )
        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm_client,
        )

        with pytest.raises(FaqGenerationError) as exc_info:
            await service._get_context_docs(sample_request)

        assert "검색 실패" in str(exc_info.value) or "Search" in str(exc_info.value)


# =============================================================================
# 테스트: _format_docs_for_prompt
# =============================================================================


class TestFormatDocsForPrompt:
    """_format_docs_for_prompt 메서드 테스트"""

    def test_format_faq_source_docs(self, mock_rag_handler, mock_llm_client, sample_top_docs):
        """FaqSourceDoc 포맷팅"""
        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm_client,
        )

        result = service._format_docs_for_prompt(sample_top_docs, answer_source="TOP_DOCS")

        assert "### 문서 1:" in result
        assert "연차휴가 규정" in result
        assert "(제5조)" in result
        assert "연차휴가는 다음 해로 이월이 가능합니다." in result

    def test_format_rag_search_results(self, mock_rag_handler, mock_llm_client, sample_milvus_chunks):
        """RagSearchResult 포맷팅"""
        context_docs = [RagSearchResult.from_chunk(c) for c in sample_milvus_chunks]
        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm_client,
        )

        result = service._format_docs_for_prompt(context_docs, answer_source="MILVUS")

        assert "### 문서 1:" in result
        assert "인사규정.pdf" in result
        assert "(chunk #15)" in result
        assert "[유사도: 0.92]" in result

    def test_format_empty_docs(self, mock_rag_handler, mock_llm_client):
        """빈 문서 리스트 포맷팅"""
        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm_client,
        )

        result = service._format_docs_for_prompt([], answer_source="TOP_DOCS")

        assert "컨텍스트 문서 없음" in result


# =============================================================================
# 테스트: _create_faq_draft
# =============================================================================


class TestCreateFaqDraft:
    """_create_faq_draft 메서드 테스트"""

    def test_create_with_top_docs(
        self, mock_rag_handler, mock_llm_client, sample_request, sample_top_docs
    ):
        """top_docs 사용 시 source 필드 채움"""
        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm_client,
        )
        parsed = {
            "question": "연차휴가 이월 규정은?",
            "answer_markdown": "**답변입니다**",
            "summary": "요약",
            "answer_source": "AI_RAG",
            "ai_confidence": 0.9,
        }

        draft = service._create_faq_draft(
            sample_request, parsed, sample_top_docs, answer_source="TOP_DOCS"
        )

        assert draft.source_doc_id == "doc-001"
        assert draft.source_doc_version == "v1.0"
        assert draft.source_article_label == "제5조"
        assert "참고 문서" not in draft.answer_markdown

    def test_create_with_milvus_results(
        self, mock_rag_handler, mock_llm_client, sample_request, sample_milvus_chunks
    ):
        """Milvus 검색 결과 사용 시 source 필드 null, answer_source=MILVUS"""
        context_docs = [RagSearchResult.from_chunk(c) for c in sample_milvus_chunks]
        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm_client,
        )
        parsed = {
            "question": "연차휴가 이월 규정은?",
            "answer_markdown": "**답변입니다**\n\n**참고**\n- 인사규정.pdf (p.15)",
            "summary": "요약",
            "answer_source": "AI_RAG",
            "ai_confidence": 0.9,
        }

        draft = service._create_faq_draft(
            sample_request, parsed, context_docs, answer_source="MILVUS"
        )

        assert draft.source_doc_id is None
        assert draft.source_doc_version is None
        assert draft.source_article_label is None
        assert draft.answer_source == "MILVUS"
        # Phase 19-AI-3: LLM이 직접 참고 섹션을 생성
        assert "참고" in draft.answer_markdown


# =============================================================================
# 테스트: generate_faq_draft 통합 테스트
# =============================================================================


class TestGenerateFaqDraft:
    """generate_faq_draft 통합 테스트"""

    @pytest.mark.anyio
    async def test_generate_with_top_docs(
        self, mock_rag_handler, mock_llm_client, sample_request, sample_top_docs
    ):
        """top_docs 제공 시 전체 플로우"""
        sample_request.top_docs = sample_top_docs
        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm_client,
        )

        draft = await service.generate_faq_draft(sample_request)

        assert draft.faq_draft_id is not None
        assert draft.cluster_id == "cluster-001"
        assert draft.domain == "POLICY"
        assert draft.source_doc_id == "doc-001"
        assert draft.answer_source == "TOP_DOCS"
        mock_rag_handler.perform_search_with_fallback.assert_not_called()

    @pytest.mark.anyio
    async def test_generate_with_milvus_search(
        self, mock_rag_handler, mock_llm_client, sample_request, sample_chat_sources
    ):
        """RagHandler 검색 사용 시 전체 플로우"""
        mock_rag_handler.perform_search_with_fallback = AsyncMock(
            return_value=(sample_chat_sources, False, "MILVUS")
        )
        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm_client,
        )

        draft = await service.generate_faq_draft(sample_request)

        assert draft.faq_draft_id is not None
        assert draft.source_doc_id is None
        assert draft.answer_source == "MILVUS"
        mock_rag_handler.perform_search_with_fallback.assert_called_once()

    @pytest.mark.anyio
    async def test_generate_no_docs_found(
        self, mock_rag_handler, mock_llm_client, sample_request
    ):
        """검색 결과 없음 시 FAILED 응답"""
        mock_rag_handler.perform_search_with_fallback = AsyncMock(
            return_value=([], False, "MILVUS")
        )
        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm_client,
        )

        with pytest.raises(FaqGenerationError) as exc_info:
            await service.generate_faq_draft(sample_request)

        assert "NO_DOCS_FOUND" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_generate_llm_error(
        self, mock_rag_handler, sample_request, sample_top_docs
    ):
        """LLM 호출 실패 시 에러"""
        sample_request.top_docs = sample_top_docs
        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion = AsyncMock(
            side_effect=Exception("LLM connection failed")
        )
        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm,
        )

        with pytest.raises(FaqGenerationError) as exc_info:
            await service.generate_faq_draft(sample_request)

        assert "LLM 호출 실패" in str(exc_info.value)
