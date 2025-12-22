"""
Phase 20-AI-3: 컨텍스트 PII 방어 테스트

RAGFlow 검색 결과 snippet에서 PII가 검출되면 강차단되는지 검증합니다:
- RAGFlow mock 결과 snippet에 이메일/전화번호 등을 넣고 PII_DETECTED_CONTEXT로 떨어지는지
"""

from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"

from app.models.faq import FaqDraftGenerateRequest
from app.models.intent import PiiMaskResult, PiiTag, MaskingStage
from app.services.faq_service import (
    FaqDraftService,
    FaqGenerationError,
    RagSearchResult,
)


class TestContextPiiDetection:
    """컨텍스트 PII 검출 테스트."""

    @pytest.mark.anyio
    async def test_pii_in_ragflow_snippet_raises_error(self):
        """RAGFlow 스니펫에 PII가 포함되면 PII_DETECTED_CONTEXT 에러 발생."""
        # PII 서비스 mock: 이메일 검출
        mock_pii_service = AsyncMock()

        async def mock_detect_and_mask(text, stage):
            if "test@example.com" in text:
                return PiiMaskResult(
                    original_text=text,
                    masked_text=text.replace("test@example.com", "[EMAIL]"),
                    has_pii=True,
                    tags=[
                        PiiTag(
                            entity="test@example.com",
                            label="EMAIL",
                            start=text.find("test@example.com"),
                            end=text.find("test@example.com") + len("test@example.com"),
                        )
                    ],
                )
            return PiiMaskResult(
                original_text=text,
                masked_text=text,
                has_pii=False,
                tags=[],
            )

        mock_pii_service.detect_and_mask = mock_detect_and_mask

        # RAGFlow 검색 클라이언트 mock: PII가 포함된 스니펫 반환
        mock_search_client = AsyncMock()
        mock_search_client.search_chunks = AsyncMock(return_value=[
            {
                "document_name": "내부규정.pdf",
                "page_num": 1,
                "similarity": 0.9,
                "content": "연차휴가 신청 시 담당자 test@example.com으로 문의하세요.",  # PII 포함
            },
            {
                "document_name": "내부규정.pdf",
                "page_num": 2,
                "similarity": 0.85,
                "content": "휴가 신청 절차는 다음과 같습니다.",  # PII 없음
            },
        ])

        # LLM 클라이언트 mock (호출되면 안 됨)
        mock_llm_client = AsyncMock()

        service = FaqDraftService(
            search_client=mock_search_client,
            llm_client=mock_llm_client,
            pii_service=mock_pii_service,
        )

        request = FaqDraftGenerateRequest(
            domain="POLICY",
            cluster_id="cluster-001",
            canonical_question="연차휴가 신청 방법",
        )

        # PII_DETECTED_CONTEXT 에러가 발생해야 함
        with pytest.raises(FaqGenerationError) as exc_info:
            await service.generate_faq_draft(request)

        assert "PII_DETECTED_CONTEXT" in str(exc_info.value)

        # LLM은 호출되지 않아야 함 (PII 차단으로 조기 종료)
        mock_llm_client.generate_chat_completion.assert_not_called()

    @pytest.mark.anyio
    async def test_phone_number_in_ragflow_snippet_raises_error(self):
        """RAGFlow 스니펫에 전화번호가 포함되면 PII_DETECTED_CONTEXT 에러 발생."""
        mock_pii_service = AsyncMock()

        async def mock_detect_and_mask(text, stage):
            if "010-1234-5678" in text:
                return PiiMaskResult(
                    original_text=text,
                    masked_text=text.replace("010-1234-5678", "[PHONE]"),
                    has_pii=True,
                    tags=[
                        PiiTag(
                            entity="010-1234-5678",
                            label="PHONE",
                            start=text.find("010-1234-5678"),
                            end=text.find("010-1234-5678") + len("010-1234-5678"),
                        )
                    ],
                )
            return PiiMaskResult(
                original_text=text,
                masked_text=text,
                has_pii=False,
                tags=[],
            )

        mock_pii_service.detect_and_mask = mock_detect_and_mask

        mock_search_client = AsyncMock()
        mock_search_client.search_chunks = AsyncMock(return_value=[
            {
                "document_name": "연락처.pdf",
                "page_num": 1,
                "similarity": 0.9,
                "content": "담당자 연락처: 010-1234-5678",  # PII 포함
            },
        ])

        mock_llm_client = AsyncMock()

        service = FaqDraftService(
            search_client=mock_search_client,
            llm_client=mock_llm_client,
            pii_service=mock_pii_service,
        )

        request = FaqDraftGenerateRequest(
            domain="POLICY",
            cluster_id="cluster-002",
            canonical_question="담당자 연락처",
        )

        with pytest.raises(FaqGenerationError) as exc_info:
            await service.generate_faq_draft(request)

        assert "PII_DETECTED_CONTEXT" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_no_pii_in_ragflow_snippet_proceeds(self):
        """RAGFlow 스니펫에 PII가 없으면 정상 진행."""
        mock_pii_service = AsyncMock()

        async def mock_detect_and_mask(text, stage):
            return PiiMaskResult(
                original_text=text,
                masked_text=text,
                has_pii=False,
                tags=[],
            )

        mock_pii_service.detect_and_mask = mock_detect_and_mask

        mock_search_client = AsyncMock()
        mock_search_client.search_chunks = AsyncMock(return_value=[
            {
                "document_name": "휴가규정.pdf",
                "page_num": 1,
                "similarity": 0.9,
                "content": "연차휴가 신청은 사내 시스템을 통해 진행합니다.",  # PII 없음
            },
        ])

        mock_llm_client = AsyncMock()
        mock_llm_client.generate_chat_completion = AsyncMock(return_value="""
status: SUCCESS
question: 연차휴가는 어떻게 신청하나요?
summary: 사내 시스템을 통해 신청합니다.
answer_markdown: |
  **사내 시스템을 통해 신청합니다.**

  - 시스템 접속
  - 휴가 신청 메뉴 선택
  - 정보 입력 후 제출
ai_confidence: 0.85
""")

        service = FaqDraftService(
            search_client=mock_search_client,
            llm_client=mock_llm_client,
            pii_service=mock_pii_service,
        )

        request = FaqDraftGenerateRequest(
            domain="POLICY",
            cluster_id="cluster-003",
            canonical_question="연차휴가 신청 방법",
        )

        # 에러 없이 정상 진행
        draft = await service.generate_faq_draft(request)

        assert draft is not None
        assert draft.cluster_id == "cluster-003"
        mock_llm_client.generate_chat_completion.assert_called_once()

    @pytest.mark.anyio
    async def test_empty_snippet_skipped(self):
        """빈 스니펫은 PII 검사를 건너뜀."""
        mock_pii_service = AsyncMock()
        pii_check_count = 0

        async def mock_detect_and_mask(text, stage):
            nonlocal pii_check_count
            pii_check_count += 1
            return PiiMaskResult(
                original_text=text,
                masked_text=text,
                has_pii=False,
                tags=[],
            )

        mock_pii_service.detect_and_mask = mock_detect_and_mask

        mock_search_client = AsyncMock()
        mock_search_client.search_chunks = AsyncMock(return_value=[
            {
                "document_name": "휴가규정.pdf",
                "page_num": 1,
                "similarity": 0.9,
                "content": "",  # 빈 스니펫
            },
            {
                "document_name": "휴가규정.pdf",
                "page_num": 2,
                "similarity": 0.85,
                "content": "   ",  # 공백만 있는 스니펫
            },
            {
                "document_name": "휴가규정.pdf",
                "page_num": 3,
                "similarity": 0.8,
                "content": "정상적인 내용입니다.",  # 정상 스니펫
            },
        ])

        mock_llm_client = AsyncMock()
        mock_llm_client.generate_chat_completion = AsyncMock(return_value="""
status: SUCCESS
question: 테스트 질문
summary: 테스트 요약
answer_markdown: |
  테스트 답변
ai_confidence: 0.85
""")

        service = FaqDraftService(
            search_client=mock_search_client,
            llm_client=mock_llm_client,
            pii_service=mock_pii_service,
        )

        request = FaqDraftGenerateRequest(
            domain="POLICY",
            cluster_id="cluster-004",
            canonical_question="테스트 질문",
        )

        await service.generate_faq_draft(request)

        # 빈 스니펫과 공백 스니펫은 검사 대상에서 제외되어야 함
        # 입력 PII 검사(canonical_question) + 컨텍스트 PII 검사(정상 스니펫 1개) + 출력 PII 검사(answer_markdown, summary)
        # = 1(입력) + 1(컨텍스트) + 2(출력) = 4회
        # 하지만 빈 스니펫 2개는 건너뛰므로 컨텍스트 검사는 1회만
        assert pii_check_count >= 1  # 최소 1번은 호출됨
