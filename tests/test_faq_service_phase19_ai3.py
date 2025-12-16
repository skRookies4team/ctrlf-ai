"""
FaqDraftService Phase 19-AI-3 테스트

테스트 케이스:
1. 필드별 텍스트 형식 파싱
2. JSON 형식 하위 호환
3. LOW_RELEVANCE_CONTEXT 에러 처리
4. answer_source: TOP_DOCS / RAGFLOW 구분
5. summary 120자 제한
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.faq_service import (
    FaqDraftService,
    FaqGenerationError,
    RagSearchResult,
)
from app.models.faq import (
    FaqDraftGenerateRequest,
    FaqSourceDoc,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


@pytest.fixture
def mock_search_client():
    """테스트용 RagflowSearchClient mock"""
    return AsyncMock()


@pytest.fixture
def sample_request() -> FaqDraftGenerateRequest:
    """테스트용 FAQ 생성 요청"""
    return FaqDraftGenerateRequest(
        cluster_id="cluster-001",
        domain="POLICY",
        canonical_question="연차휴가 이월 규정이 어떻게 되나요?",
        sample_questions=["연차 이월 가능한가요?"],
        top_docs=[],
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


# =============================================================================
# 테스트: 필드별 텍스트 형식 파싱
# =============================================================================


class TestFieldTextParsing:
    """필드별 텍스트 형식 파싱 테스트"""

    def test_parse_field_text_format_success(self, mock_search_client):
        """정상적인 필드별 텍스트 파싱"""
        service = FaqDraftService(
            search_client=mock_search_client,
            llm_client=MagicMock(),
        )

        response = """status: SUCCESS
question: 연차휴가는 다음 해로 이월할 수 있나요?
summary: 연차휴가는 익년도로 최대 10일까지 이월 가능합니다.
answer_markdown: |
  연차휴가는 다음 해로 이월이 가능합니다.

  - 최대 10일까지 이월 가능
  - 12월 말까지 이월 신청 필요
  - 미사용 연차는 소멸됩니다

  **참고**
  - 인사규정 (p.15)
ai_confidence: 0.92"""

        result = service._parse_field_text_format(response)

        assert result["status"] == "SUCCESS"
        assert result["question"] == "연차휴가는 다음 해로 이월할 수 있나요?"
        assert "120" not in result["summary"] or len(result["summary"]) <= 120
        assert "이월" in result["answer_markdown"]
        assert result["ai_confidence"] == 0.92

    def test_parse_field_text_format_low_relevance(self, mock_search_client):
        """LOW_RELEVANCE status 파싱"""
        service = FaqDraftService(
            search_client=mock_search_client,
            llm_client=MagicMock(),
        )

        response = """status: LOW_RELEVANCE
question: 연차휴가는 다음 해로 이월할 수 있나요?
summary: 컨텍스트가 질문과 관련이 없습니다.
answer_markdown: |
  관련 정보를 찾을 수 없습니다.
ai_confidence: 0.15"""

        result = service._parse_field_text_format(response)

        assert result["status"] == "LOW_RELEVANCE"
        assert result["ai_confidence"] == 0.15

    def test_parse_llm_response_prefers_field_text(self, mock_search_client):
        """필드별 텍스트가 JSON보다 우선"""
        service = FaqDraftService(
            search_client=mock_search_client,
            llm_client=MagicMock(),
        )

        response = """status: SUCCESS
question: 테스트 질문입니다
summary: 테스트 요약
answer_markdown: |
  테스트 답변입니다.
  - bullet 1
  - bullet 2
ai_confidence: 0.85"""

        result = service._parse_llm_response(response)

        assert result["question"] == "테스트 질문입니다"
        assert result["ai_confidence"] == 0.85


# =============================================================================
# 테스트: JSON 하위 호환
# =============================================================================


class TestJsonBackwardsCompatibility:
    """JSON 형식 하위 호환 테스트"""

    def test_parse_json_format(self, mock_search_client):
        """JSON 형식 파싱"""
        service = FaqDraftService(
            search_client=mock_search_client,
            llm_client=MagicMock(),
        )

        response = """{
            "question": "연차휴가 이월 규정은?",
            "answer_markdown": "**요약**\\n\\n- bullet 1\\n- bullet 2",
            "summary": "연차는 이월 가능합니다.",
            "ai_confidence": 0.9
        }"""

        result = service._parse_llm_response(response)

        assert result["question"] == "연차휴가 이월 규정은?"
        assert result["ai_confidence"] == 0.9

    def test_parse_json_in_code_block(self, mock_search_client):
        """코드 블록 내 JSON 파싱"""
        service = FaqDraftService(
            search_client=mock_search_client,
            llm_client=MagicMock(),
        )

        response = """```json
{
    "question": "테스트 질문",
    "answer_markdown": "테스트 답변",
    "summary": "요약",
    "ai_confidence": 0.8
}
```"""

        result = service._parse_llm_response(response)

        assert result["question"] == "테스트 질문"


# =============================================================================
# 테스트: LOW_RELEVANCE_CONTEXT 에러 처리
# =============================================================================


class TestLowRelevanceContext:
    """LOW_RELEVANCE_CONTEXT 에러 처리 테스트"""

    @pytest.mark.anyio
    async def test_low_relevance_raises_error(
        self, mock_search_client, sample_request, sample_top_docs
    ):
        """LOW_RELEVANCE status일 때 에러 발생"""
        sample_request.top_docs = sample_top_docs

        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(return_value="""status: LOW_RELEVANCE
question: 연차휴가 이월 규정은?
summary: 컨텍스트가 관련 없습니다.
answer_markdown: |
  관련 정보 없음
ai_confidence: 0.2""")

        service = FaqDraftService(
            search_client=mock_search_client,
            llm_client=mock_llm,
        )

        with pytest.raises(FaqGenerationError) as exc_info:
            await service.generate_faq_draft(sample_request)

        assert "LOW_RELEVANCE_CONTEXT" in str(exc_info.value)


# =============================================================================
# 테스트: answer_source TOP_DOCS / RAGFLOW 구분
# =============================================================================


class TestAnswerSource:
    """answer_source 구분 테스트"""

    @pytest.mark.anyio
    async def test_answer_source_top_docs(
        self, mock_search_client, sample_request, sample_top_docs
    ):
        """top_docs 사용 시 answer_source=TOP_DOCS"""
        sample_request.top_docs = sample_top_docs

        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(return_value="""status: SUCCESS
question: 연차휴가 이월 가능한가요?
summary: 연차휴가는 익년도로 이월 가능합니다.
answer_markdown: |
  연차휴가 이월이 가능합니다.
  - 최대 10일 이월
  - 12월 신청 필요
ai_confidence: 0.9""")

        service = FaqDraftService(
            search_client=mock_search_client,
            llm_client=mock_llm,
        )

        draft = await service.generate_faq_draft(sample_request)

        assert draft.answer_source == "TOP_DOCS"
        assert draft.source_doc_id == "doc-001"

    @pytest.mark.anyio
    async def test_answer_source_ragflow(self, mock_search_client, sample_request):
        """RAGFlow 검색 시 answer_source=RAGFLOW"""
        mock_search_client.search_chunks = AsyncMock(return_value=[
            {"document_name": "test.pdf", "page_num": 5, "similarity": 0.9, "content": "테스트 내용"}
        ])

        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(return_value="""status: SUCCESS
question: 연차휴가 이월 가능한가요?
summary: 연차휴가는 익년도로 이월 가능합니다.
answer_markdown: |
  연차휴가 이월이 가능합니다.
  - 최대 10일 이월
ai_confidence: 0.85""")

        service = FaqDraftService(
            search_client=mock_search_client,
            llm_client=mock_llm,
        )

        draft = await service.generate_faq_draft(sample_request)

        assert draft.answer_source == "RAGFLOW"
        assert draft.source_doc_id is None


# =============================================================================
# 테스트: summary 120자 제한
# =============================================================================


class TestSummaryLimit:
    """summary 120자 제한 테스트"""

    @pytest.mark.anyio
    async def test_summary_truncated_to_120_chars(
        self, mock_search_client, sample_request, sample_top_docs
    ):
        """120자 초과 summary 잘림"""
        sample_request.top_docs = sample_top_docs

        long_summary = "가" * 150  # 150자

        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(return_value=f"""status: SUCCESS
question: 테스트 질문
summary: {long_summary}
answer_markdown: |
  테스트 답변
ai_confidence: 0.9""")

        service = FaqDraftService(
            search_client=mock_search_client,
            llm_client=mock_llm,
        )

        draft = await service.generate_faq_draft(sample_request)

        assert len(draft.summary) <= 120
        assert draft.summary.endswith("...")

    @pytest.mark.anyio
    async def test_summary_under_120_not_truncated(
        self, mock_search_client, sample_request, sample_top_docs
    ):
        """120자 이하 summary는 그대로"""
        sample_request.top_docs = sample_top_docs

        short_summary = "짧은 요약입니다."

        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(return_value=f"""status: SUCCESS
question: 테스트 질문
summary: {short_summary}
answer_markdown: |
  테스트 답변
ai_confidence: 0.9""")

        service = FaqDraftService(
            search_client=mock_search_client,
            llm_client=mock_llm,
        )

        draft = await service.generate_faq_draft(sample_request)

        assert draft.summary == short_summary


# =============================================================================
# 테스트: 프롬프트 템플릿
# =============================================================================


class TestPromptTemplate:
    """프롬프트 템플릿 테스트"""

    def test_system_prompt_contains_key_instructions(self, mock_search_client):
        """SYSTEM 프롬프트에 핵심 지침 포함"""
        from app.services.faq_service import SYSTEM_PROMPT

        assert "기업 내부 FAQ 작성 보조자" in SYSTEM_PROMPT
        assert "컨텍스트" in SYSTEM_PROMPT
        assert "LOW_RELEVANCE" in SYSTEM_PROMPT
        assert "120자" in SYSTEM_PROMPT

    def test_build_llm_messages_structure(
        self, mock_search_client, sample_request, sample_top_docs
    ):
        """LLM 메시지 구조 확인"""
        service = FaqDraftService(
            search_client=mock_search_client,
            llm_client=MagicMock(),
        )

        messages = service._build_llm_messages(
            sample_request, sample_top_docs, used_top_docs=True
        )

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "canonical_question" in messages[1]["content"]
        assert "context_docs" in messages[1]["content"]
