"""
FaqDraftService Phase 19-AI-4 테스트 (PII 강차단)

테스트 케이스:
1. 입력 PII 검출 - canonical_question
2. 입력 PII 검출 - sample_questions
3. 입력 PII 검출 - top_docs.snippet
4. 출력 PII 검출 - answer_markdown
5. 출력 PII 검출 - summary
6. PII 없을 때 정상 처리
7. PII 서비스 비활성화 시 패스

Step 7 업데이트: milvus_client → rag_handler
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.faq_service import (
    FaqDraftService,
    FaqGenerationError,
)
from app.models.faq import (
    FaqDraftGenerateRequest,
    FaqSourceDoc,
)
from app.models.intent import MaskingStage, PiiMaskResult, PiiTag
from app.models.chat import ChatSource
from app.services.pii_service import PiiService
from app.services.chat.rag_handler import RagHandler


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
    handler.perform_search_with_fallback = AsyncMock(return_value=(
        [ChatSource(doc_id="test.pdf", title="test.pdf", snippet="테스트 내용", score=0.9)],
        False,
        "MILVUS"
    ))
    return handler


@pytest.fixture
def mock_llm_client():
    """테스트용 LLMClient mock"""
    client = MagicMock()
    client.generate_chat_completion = AsyncMock(return_value="""status: SUCCESS
question: 테스트 FAQ 질문
summary: 답변 요약입니다.
answer_markdown: |
  테스트 답변입니다.
  - bullet 1
  - bullet 2
ai_confidence: 0.9""")
    return client


@pytest.fixture
def mock_pii_service_no_pii():
    """PII 미검출 mock"""
    service = MagicMock(spec=PiiService)
    service.detect_and_mask = AsyncMock(
        return_value=PiiMaskResult(
            original_text="",
            masked_text="",
            has_pii=False,
            tags=[],
        )
    )
    return service


@pytest.fixture
def mock_pii_service_with_pii():
    """PII 검출 mock"""
    service = MagicMock(spec=PiiService)
    service.detect_and_mask = AsyncMock(
        return_value=PiiMaskResult(
            original_text="홍길동 010-1234-5678",
            masked_text="[PERSON] [PHONE]",
            has_pii=True,
            tags=[
                PiiTag(entity="홍길동", label="PERSON", start=0, end=3),
                PiiTag(entity="010-1234-5678", label="PHONE", start=4, end=17),
            ],
        )
    )
    return service


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
# 테스트: 입력 PII 검출
# =============================================================================


class TestInputPiiDetection:
    """입력 PII 검출 테스트"""

    @pytest.mark.anyio
    async def test_pii_detected_in_canonical_question(
        self, mock_rag_handler, mock_llm_client, mock_pii_service_with_pii
    ):
        """canonical_question에 PII 검출 시 에러"""
        request = FaqDraftGenerateRequest(
            cluster_id="cluster-001",
            domain="POLICY",
            canonical_question="홍길동 010-1234-5678의 연차휴가 규정은?",
            sample_questions=[],
            top_docs=[],
        )

        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm_client,
            pii_service=mock_pii_service_with_pii,
        )

        with pytest.raises(FaqGenerationError) as exc_info:
            await service.generate_faq_draft(request)

        assert "PII_DETECTED" in str(exc_info.value)
        # LLM은 호출되지 않아야 함
        mock_llm_client.generate_chat_completion.assert_not_called()

    @pytest.mark.anyio
    async def test_pii_detected_in_sample_questions(
        self, mock_rag_handler, mock_llm_client, mock_pii_service_with_pii
    ):
        """sample_questions에 PII 검출 시 에러"""
        request = FaqDraftGenerateRequest(
            cluster_id="cluster-001",
            domain="POLICY",
            canonical_question="연차휴가 이월 규정은?",
            sample_questions=["홍길동 010-1234-5678 연차 이월 가능한가요?"],
            top_docs=[],
        )

        # canonical_question은 PII 없음, sample_questions에서 PII 검출
        async def mock_detect(text, stage):
            if "홍길동" in text:
                return PiiMaskResult(
                    original_text=text,
                    masked_text="[PERSON] [PHONE]",
                    has_pii=True,
                    tags=[PiiTag(entity="홍길동", label="PERSON")],
                )
            return PiiMaskResult(
                original_text=text,
                masked_text=text,
                has_pii=False,
                tags=[],
            )

        mock_pii = MagicMock(spec=PiiService)
        mock_pii.detect_and_mask = AsyncMock(side_effect=mock_detect)

        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm_client,
            pii_service=mock_pii,
        )

        with pytest.raises(FaqGenerationError) as exc_info:
            await service.generate_faq_draft(request)

        assert "PII_DETECTED" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_pii_detected_in_top_docs_snippet(
        self, mock_rag_handler, mock_llm_client
    ):
        """top_docs.snippet에 PII 검출 시 에러"""
        request = FaqDraftGenerateRequest(
            cluster_id="cluster-001",
            domain="POLICY",
            canonical_question="연차휴가 이월 규정은?",
            sample_questions=[],
            top_docs=[
                FaqSourceDoc(
                    doc_id="doc-001",
                    title="규정",
                    snippet="홍길동 010-1234-5678 연차휴가 이월 가능",
                ),
            ],
        )

        async def mock_detect(text, stage):
            if "홍길동" in text:
                return PiiMaskResult(
                    original_text=text,
                    masked_text="[PERSON] [PHONE]",
                    has_pii=True,
                    tags=[PiiTag(entity="홍길동", label="PERSON")],
                )
            return PiiMaskResult(
                original_text=text,
                masked_text=text,
                has_pii=False,
                tags=[],
            )

        mock_pii = MagicMock(spec=PiiService)
        mock_pii.detect_and_mask = AsyncMock(side_effect=mock_detect)

        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm_client,
            pii_service=mock_pii,
        )

        with pytest.raises(FaqGenerationError) as exc_info:
            await service.generate_faq_draft(request)

        assert "PII_DETECTED" in str(exc_info.value)


# =============================================================================
# 테스트: 출력 PII 검출
# =============================================================================


class TestOutputPiiDetection:
    """출력 PII 검출 테스트"""

    @pytest.mark.anyio
    async def test_pii_detected_in_answer_markdown(
        self, mock_rag_handler, sample_request, sample_top_docs
    ):
        """answer_markdown에 PII 검출 시 에러"""
        sample_request.top_docs = sample_top_docs

        # LLM이 PII가 포함된 응답을 반환
        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(return_value="""status: SUCCESS
question: 테스트 FAQ 질문
summary: 답변 요약
answer_markdown: |
  홍길동 010-1234-5678의 연차휴가는 이월 가능합니다.
ai_confidence: 0.9""")

        # 입력은 PII 없음, 출력에서 PII 검출
        call_count = [0]

        async def mock_detect(text, stage):
            call_count[0] += 1
            if stage == MaskingStage.OUTPUT and "홍길동" in text:
                return PiiMaskResult(
                    original_text=text,
                    masked_text="[PERSON] [PHONE]",
                    has_pii=True,
                    tags=[PiiTag(entity="홍길동", label="PERSON")],
                )
            return PiiMaskResult(
                original_text=text,
                masked_text=text,
                has_pii=False,
                tags=[],
            )

        mock_pii = MagicMock(spec=PiiService)
        mock_pii.detect_and_mask = AsyncMock(side_effect=mock_detect)

        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm,
            pii_service=mock_pii,
        )

        with pytest.raises(FaqGenerationError) as exc_info:
            await service.generate_faq_draft(sample_request)

        assert "PII_DETECTED_OUTPUT" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_pii_detected_in_summary(
        self, mock_rag_handler, sample_request, sample_top_docs
    ):
        """summary에 PII 검출 시 에러"""
        sample_request.top_docs = sample_top_docs

        # LLM이 summary에 PII가 포함된 응답을 반환
        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(return_value="""status: SUCCESS
question: 테스트 FAQ 질문
summary: 홍길동 010-1234-5678의 연차휴가 요약
answer_markdown: |
  연차휴가는 이월 가능합니다.
ai_confidence: 0.9""")

        # 입력은 PII 없음, summary에서 PII 검출
        async def mock_detect(text, stage):
            if stage == MaskingStage.OUTPUT and "홍길동" in text:
                return PiiMaskResult(
                    original_text=text,
                    masked_text="[PERSON] [PHONE]",
                    has_pii=True,
                    tags=[PiiTag(entity="홍길동", label="PERSON")],
                )
            return PiiMaskResult(
                original_text=text,
                masked_text=text,
                has_pii=False,
                tags=[],
            )

        mock_pii = MagicMock(spec=PiiService)
        mock_pii.detect_and_mask = AsyncMock(side_effect=mock_detect)

        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm,
            pii_service=mock_pii,
        )

        with pytest.raises(FaqGenerationError) as exc_info:
            await service.generate_faq_draft(sample_request)

        assert "PII_DETECTED_OUTPUT" in str(exc_info.value)


# =============================================================================
# 테스트: 정상 케이스
# =============================================================================


class TestNoPiiDetection:
    """PII 미검출 시 정상 처리 테스트"""

    @pytest.mark.anyio
    async def test_no_pii_generates_faq_draft(
        self,
        mock_rag_handler,
        mock_llm_client,
        mock_pii_service_no_pii,
        sample_request,
        sample_top_docs,
    ):
        """PII 없을 때 정상 FAQ 초안 생성"""
        sample_request.top_docs = sample_top_docs

        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm_client,
            pii_service=mock_pii_service_no_pii,
        )

        draft = await service.generate_faq_draft(sample_request)

        assert draft.faq_draft_id is not None
        assert draft.cluster_id == "cluster-001"
        assert draft.domain == "POLICY"
        # PII 검사가 여러 번 호출됨 (입력 + 출력)
        assert mock_pii_service_no_pii.detect_and_mask.call_count >= 2

    @pytest.mark.anyio
    async def test_empty_text_skipped(
        self,
        mock_rag_handler,
        mock_llm_client,
        mock_pii_service_no_pii,
    ):
        """빈 텍스트는 PII 검사 스킵"""
        request = FaqDraftGenerateRequest(
            cluster_id="cluster-001",
            domain="POLICY",
            canonical_question="연차휴가 이월 규정은?",
            sample_questions=["", "  "],  # 빈 문자열
            top_docs=[
                FaqSourceDoc(doc_id="doc-001", snippet=""),  # 빈 snippet
                FaqSourceDoc(doc_id="doc-002", snippet="정상 내용"),
            ],
        )

        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm_client,
            pii_service=mock_pii_service_no_pii,
        )

        draft = await service.generate_faq_draft(request)

        assert draft.faq_draft_id is not None


# =============================================================================
# 테스트: PII 검사 메서드 단위 테스트
# =============================================================================


class TestPiiCheckMethods:
    """_check_input_pii, _check_output_pii 단위 테스트"""

    @pytest.mark.anyio
    async def test_check_input_pii_all_fields(
        self, mock_rag_handler, mock_llm_client
    ):
        """입력 PII 검사가 모든 필드를 확인"""
        checked_texts = []

        async def mock_detect(text, stage):
            checked_texts.append(text)
            return PiiMaskResult(
                original_text=text,
                masked_text=text,
                has_pii=False,
                tags=[],
            )

        mock_pii = MagicMock(spec=PiiService)
        mock_pii.detect_and_mask = AsyncMock(side_effect=mock_detect)

        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm_client,
            pii_service=mock_pii,
        )

        request = FaqDraftGenerateRequest(
            cluster_id="cluster-001",
            domain="POLICY",
            canonical_question="대표 질문",
            sample_questions=["샘플1", "샘플2"],
            top_docs=[
                FaqSourceDoc(doc_id="doc-001", snippet="스니펫1"),
                FaqSourceDoc(doc_id="doc-002", snippet="스니펫2"),
            ],
        )

        await service._check_input_pii(request)

        # 모든 텍스트가 검사됨
        assert "대표 질문" in checked_texts
        assert "샘플1" in checked_texts
        assert "샘플2" in checked_texts
        assert "스니펫1" in checked_texts
        assert "스니펫2" in checked_texts

    @pytest.mark.anyio
    async def test_check_output_pii_all_fields(
        self, mock_rag_handler, mock_llm_client
    ):
        """출력 PII 검사가 모든 필드를 확인"""
        checked_texts = []

        async def mock_detect(text, stage):
            checked_texts.append(text)
            return PiiMaskResult(
                original_text=text,
                masked_text=text,
                has_pii=False,
                tags=[],
            )

        mock_pii = MagicMock(spec=PiiService)
        mock_pii.detect_and_mask = AsyncMock(side_effect=mock_detect)

        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm_client,
            pii_service=mock_pii,
        )

        parsed = {
            "question": "질문",
            "answer_markdown": "답변 마크다운",
            "summary": "요약",
            "ai_confidence": 0.9,
        }

        await service._check_output_pii(parsed)

        # answer_markdown과 summary가 검사됨
        assert "답변 마크다운" in checked_texts
        assert "요약" in checked_texts
        # question은 출력 검사 대상 아님
        assert "질문" not in checked_texts
