"""
Phase 19 FAQ API 통합 테스트

Phase 19-AI-5 요구사항에 따른 API 레벨 테스트:
1. top_docs 제공 시: MilvusSearchClient 호출 없이 성공 응답
2. top_docs 미제공 시: search 호출, answer_source="MILVUS"
3. top_docs 미제공 + search 결과 0개: status="FAILED", error_message="NO_DOCS_FOUND"
4. 입력에 PII 포함: status="FAILED", error_message="PII_DETECTED"
5. 출력에 PII 포함: status="FAILED", error_message="PII_DETECTED_OUTPUT"
6. timeout/5xx 발생 시: status="FAILED", error_message 포함
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.faq import FaqDraft, FaqDraftGenerateRequest, FaqSourceDoc
from app.models.intent import MaskingStage, PiiMaskResult, PiiTag
from app.services.faq_service import FaqDraftService, FaqGenerationError
from app.services.chat.rag_handler import RagHandler, RagSearchUnavailableError
from app.models.chat import ChatSource


@pytest.fixture
def test_client() -> TestClient:
    """FastAPI 테스트 클라이언트."""
    return TestClient(app)


# =============================================================================
# 1. top_docs 제공 시: MilvusSearchClient 호출 없이 성공 응답
# =============================================================================


class TestTopDocsProvided:
    """top_docs 제공 시 테스트"""

    def test_api_with_top_docs_success(self, test_client: TestClient):
        """top_docs 제공 시 Milvus 호출 없이 성공"""
        from app.api.v1 import faq as faq_module

        # Mock: 성공적인 FAQ Draft 반환
        mock_draft = FaqDraft(
            faq_draft_id="FAQ-test-001",
            domain="SEC_POLICY",
            cluster_id="cluster-001",
            question="USB 반출 시 어떤 절차가 필요한가요?",
            answer_markdown="**정보보호팀의 사전 승인이 필요합니다.**",
            summary="정보보호팀의 사전 승인이 필요합니다.",
            source_doc_id="DOC-001",
            answer_source="TOP_DOCS",  # Phase 19-AI-3
            ai_confidence=0.85,
            created_at=datetime.now(timezone.utc),
        )

        mock_service = MagicMock()
        mock_service.generate_faq_draft = AsyncMock(return_value=mock_draft)

        original_fn = faq_module.get_faq_service
        faq_module.get_faq_service = lambda: mock_service
        faq_module._faq_service = None

        try:
            response = test_client.post(
                "/ai/faq/generate",
                json={
                    "domain": "SEC_POLICY",
                    "cluster_id": "cluster-001",
                    "canonical_question": "USB 반출 시 어떤 절차가 필요한가요?",
                    "top_docs": [
                        {
                            "doc_id": "DOC-001",
                            "title": "정보보호규정",
                            "snippet": "USB 메모리를 사외로 반출할 때에는...",
                        }
                    ],
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "SUCCESS"
            assert data["faq_draft"]["answer_source"] == "TOP_DOCS"
            assert data["faq_draft"]["source_doc_id"] == "DOC-001"

        finally:
            faq_module.get_faq_service = original_fn
            faq_module._faq_service = None

    @pytest.mark.anyio
    async def test_service_with_top_docs_no_milvus_call(self):
        """top_docs 제공 시 RagHandler가 호출되지 않음"""
        mock_rag_handler = MagicMock(spec=RagHandler)
        mock_rag_handler.perform_search_with_fallback = AsyncMock()  # 호출되면 안됨

        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(return_value="""status: SUCCESS
question: 테스트 질문
summary: 요약
answer_markdown: |
  테스트 답변
ai_confidence: 0.9""")

        mock_pii = MagicMock()
        mock_pii.detect_and_mask = AsyncMock(return_value=PiiMaskResult(
            original_text="", masked_text="", has_pii=False, tags=[]
        ))

        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm,
            pii_service=mock_pii,
        )

        request = FaqDraftGenerateRequest(
            cluster_id="cluster-001",
            domain="POLICY",
            canonical_question="테스트 질문",
            top_docs=[
                FaqSourceDoc(doc_id="doc-001", title="규정", snippet="내용")
            ],
        )

        draft = await service.generate_faq_draft(request)

        # RagHandler가 호출되지 않아야 함
        mock_rag_handler.perform_search_with_fallback.assert_not_called()
        assert draft.answer_source == "TOP_DOCS"


# =============================================================================
# 2. top_docs 미제공 시: search 호출, answer_source="MILVUS"
# =============================================================================


class TestTopDocsNotProvided:
    """top_docs 미제공 시 테스트"""

    @pytest.mark.anyio
    async def test_milvus_search_called_and_answer_source_milvus(self):
        """top_docs 없을 때 RagHandler 검색 호출, answer_source=MILVUS"""
        mock_rag_handler = MagicMock(spec=RagHandler)
        mock_rag_handler.perform_search_with_fallback = AsyncMock(return_value=(
            [ChatSource(doc_id="test.pdf", title="test.pdf", snippet="테스트 내용", score=0.9)],
            False,
            "MILVUS"
        ))

        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(return_value="""status: SUCCESS
question: 테스트 질문
summary: 요약
answer_markdown: |
  테스트 답변
ai_confidence: 0.85""")

        mock_pii = MagicMock()
        mock_pii.detect_and_mask = AsyncMock(return_value=PiiMaskResult(
            original_text="", masked_text="", has_pii=False, tags=[]
        ))

        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm,
            pii_service=mock_pii,
        )

        request = FaqDraftGenerateRequest(
            cluster_id="cluster-001",
            domain="POLICY",
            canonical_question="테스트 질문",
            top_docs=[],  # 빈 리스트
        )

        draft = await service.generate_faq_draft(request)

        # RagHandler.perform_search_with_fallback가 호출되어야 함
        mock_rag_handler.perform_search_with_fallback.assert_called_once()
        # answer_source가 MILVUS여야 함
        assert draft.answer_source == "MILVUS"
        # source_doc_id는 null
        assert draft.source_doc_id is None


# =============================================================================
# 3. top_docs 미제공 + search 결과 0개: NO_DOCS_FOUND
# =============================================================================


class TestNoDocsFound:
    """검색 결과 0개 시 테스트"""

    def test_api_no_docs_found(self, test_client: TestClient):
        """검색 결과 없을 때 API 응답"""
        from app.api.v1 import faq as faq_module

        mock_service = MagicMock()
        mock_service.generate_faq_draft = AsyncMock(
            side_effect=FaqGenerationError("NO_DOCS_FOUND")
        )

        original_fn = faq_module.get_faq_service
        faq_module.get_faq_service = lambda: mock_service
        faq_module._faq_service = None

        try:
            response = test_client.post(
                "/ai/faq/generate",
                json={
                    "domain": "SEC_POLICY",
                    "cluster_id": "cluster-001",
                    "canonical_question": "관련 문서가 없는 질문",
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "FAILED"
            assert "NO_DOCS_FOUND" in data["error_message"]
            assert data["faq_draft"] is None

        finally:
            faq_module.get_faq_service = original_fn
            faq_module._faq_service = None

    @pytest.mark.anyio
    async def test_service_no_docs_found_raises_error(self):
        """검색 결과 0개 시 FaqGenerationError 발생"""
        mock_rag_handler = MagicMock(spec=RagHandler)
        mock_rag_handler.perform_search_with_fallback = AsyncMock(return_value=([], False, "MILVUS"))  # 빈 결과

        mock_llm = MagicMock()
        mock_pii = MagicMock()
        mock_pii.detect_and_mask = AsyncMock(return_value=PiiMaskResult(
            original_text="", masked_text="", has_pii=False, tags=[]
        ))

        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm,
            pii_service=mock_pii,
        )

        request = FaqDraftGenerateRequest(
            cluster_id="cluster-001",
            domain="POLICY",
            canonical_question="관련 문서가 없는 질문",
            top_docs=[],
        )

        with pytest.raises(FaqGenerationError) as exc_info:
            await service.generate_faq_draft(request)

        assert "NO_DOCS_FOUND" in str(exc_info.value)


# =============================================================================
# 4. 입력에 PII 포함: PII_DETECTED
# =============================================================================


class TestInputPiiDetected:
    """입력 PII 검출 테스트"""

    def test_api_pii_detected_in_input(self, test_client: TestClient):
        """입력에 PII 포함 시 API 응답"""
        from app.api.v1 import faq as faq_module

        mock_service = MagicMock()
        mock_service.generate_faq_draft = AsyncMock(
            side_effect=FaqGenerationError("PII_DETECTED")
        )

        original_fn = faq_module.get_faq_service
        faq_module.get_faq_service = lambda: mock_service
        faq_module._faq_service = None

        try:
            response = test_client.post(
                "/ai/faq/generate",
                json={
                    "domain": "SEC_POLICY",
                    "cluster_id": "cluster-001",
                    "canonical_question": "홍길동 010-1234-5678의 연차휴가 규정은?",
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "FAILED"
            assert "PII_DETECTED" in data["error_message"]
            assert data["faq_draft"] is None

        finally:
            faq_module.get_faq_service = original_fn
            faq_module._faq_service = None

    @pytest.mark.anyio
    async def test_service_pii_detected_in_input(self):
        """입력 PII 검출 시 FaqGenerationError 발생"""
        mock_rag_handler = MagicMock(spec=RagHandler)
        mock_llm = MagicMock()

        # PII 검출 mock
        mock_pii = MagicMock()
        mock_pii.detect_and_mask = AsyncMock(return_value=PiiMaskResult(
            original_text="홍길동 010-1234-5678",
            masked_text="[PERSON] [PHONE]",
            has_pii=True,
            tags=[PiiTag(entity="홍길동", label="PERSON")],
        ))

        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm,
            pii_service=mock_pii,
        )

        request = FaqDraftGenerateRequest(
            cluster_id="cluster-001",
            domain="POLICY",
            canonical_question="홍길동 010-1234-5678의 연차휴가 규정은?",
            top_docs=[],
        )

        with pytest.raises(FaqGenerationError) as exc_info:
            await service.generate_faq_draft(request)

        assert "PII_DETECTED" in str(exc_info.value)
        # LLM이 호출되면 안됨
        mock_llm.generate_chat_completion.assert_not_called()


# =============================================================================
# 5. 출력에 PII 포함: PII_DETECTED_OUTPUT
# =============================================================================


class TestOutputPiiDetected:
    """출력 PII 검출 테스트"""

    def test_api_pii_detected_in_output(self, test_client: TestClient):
        """출력에 PII 포함 시 API 응답"""
        from app.api.v1 import faq as faq_module

        mock_service = MagicMock()
        mock_service.generate_faq_draft = AsyncMock(
            side_effect=FaqGenerationError("PII_DETECTED_OUTPUT")
        )

        original_fn = faq_module.get_faq_service
        faq_module.get_faq_service = lambda: mock_service
        faq_module._faq_service = None

        try:
            response = test_client.post(
                "/ai/faq/generate",
                json={
                    "domain": "SEC_POLICY",
                    "cluster_id": "cluster-001",
                    "canonical_question": "연차휴가 규정은?",
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "FAILED"
            assert "PII_DETECTED_OUTPUT" in data["error_message"]
            assert data["faq_draft"] is None

        finally:
            faq_module.get_faq_service = original_fn
            faq_module._faq_service = None

    @pytest.mark.anyio
    async def test_service_pii_detected_in_output(self):
        """출력 PII 검출 시 FaqGenerationError 발생"""
        mock_rag_handler = MagicMock(spec=RagHandler)
        mock_rag_handler.perform_search_with_fallback = AsyncMock(return_value=(
            [ChatSource(doc_id="test.pdf", title="test.pdf", snippet="테스트", score=0.9)],
            False,
            "MILVUS"
        ))

        # LLM이 PII가 포함된 응답 반환
        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(return_value="""status: SUCCESS
question: 테스트 질문
summary: 홍길동의 요약
answer_markdown: |
  홍길동 010-1234-5678의 답변
ai_confidence: 0.9""")

        # 입력은 PII 없음, 출력에서 PII 검출
        async def mock_detect(text, stage):
            if stage == MaskingStage.OUTPUT and "홍길동" in text:
                return PiiMaskResult(
                    original_text=text,
                    masked_text="[PERSON] [PHONE]",
                    has_pii=True,
                    tags=[PiiTag(entity="홍길동", label="PERSON")],
                )
            return PiiMaskResult(
                original_text=text, masked_text=text, has_pii=False, tags=[]
            )

        mock_pii = MagicMock()
        mock_pii.detect_and_mask = AsyncMock(side_effect=mock_detect)

        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm,
            pii_service=mock_pii,
        )

        request = FaqDraftGenerateRequest(
            cluster_id="cluster-001",
            domain="POLICY",
            canonical_question="연차휴가 규정은?",
            top_docs=[],
        )

        with pytest.raises(FaqGenerationError) as exc_info:
            await service.generate_faq_draft(request)

        assert "PII_DETECTED_OUTPUT" in str(exc_info.value)


# =============================================================================
# 6. timeout/5xx 발생 시: Milvus 에러
# =============================================================================


class TestMilvusErrors:
    """Milvus 에러 테스트"""

    def test_api_milvus_timeout_error(self, test_client: TestClient):
        """Milvus 타임아웃 시 API 응답"""
        from app.api.v1 import faq as faq_module

        mock_service = MagicMock()
        mock_service.generate_faq_draft = AsyncMock(
            side_effect=FaqGenerationError("Milvus 검색 실패: timeout")
        )

        original_fn = faq_module.get_faq_service
        faq_module.get_faq_service = lambda: mock_service
        faq_module._faq_service = None

        try:
            response = test_client.post(
                "/ai/faq/generate",
                json={
                    "domain": "SEC_POLICY",
                    "cluster_id": "cluster-001",
                    "canonical_question": "테스트 질문",
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "FAILED"
            assert "Milvus" in data["error_message"] or "검색 실패" in data["error_message"]
            assert data["faq_draft"] is None

        finally:
            faq_module.get_faq_service = original_fn
            faq_module._faq_service = None

    @pytest.mark.anyio
    async def test_service_milvus_5xx_error(self):
        """Milvus 5xx 에러 시 FaqGenerationError 발생"""
        mock_rag_handler = MagicMock(spec=RagHandler)
        mock_rag_handler.perform_search_with_fallback = AsyncMock(
            side_effect=RagSearchUnavailableError("Internal Server Error")
        )

        mock_llm = MagicMock()
        mock_pii = MagicMock()
        mock_pii.detect_and_mask = AsyncMock(return_value=PiiMaskResult(
            original_text="", masked_text="", has_pii=False, tags=[]
        ))

        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm,
            pii_service=mock_pii,
        )

        request = FaqDraftGenerateRequest(
            cluster_id="cluster-001",
            domain="POLICY",
            canonical_question="테스트 질문",
            top_docs=[],
        )

        with pytest.raises(FaqGenerationError) as exc_info:
            await service.generate_faq_draft(request)

        assert "검색 실패" in str(exc_info.value)


# =============================================================================
# Phase 19 전체 플로우 테스트
# =============================================================================


class TestPhase19FullFlow:
    """Phase 19 전체 기능 통합 테스트"""

    @pytest.mark.anyio
    async def test_full_flow_with_top_docs(self):
        """top_docs 제공 시 전체 플로우"""
        mock_rag_handler = MagicMock(spec=RagHandler)
        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(return_value="""status: SUCCESS
question: USB 반출 시 절차는?
summary: 정보보호팀 사전 승인 필요
answer_markdown: |
  **정보보호팀의 사전 승인이 필요합니다.**

  - 신청서 작성 후 승인 요청
  - 승인 후 반출 가능
  - 반출 후 7일 내 반환 필수

  **참고**
  - 정보보호규정 (p.15)
ai_confidence: 0.92""")

        mock_pii = MagicMock()
        mock_pii.detect_and_mask = AsyncMock(return_value=PiiMaskResult(
            original_text="", masked_text="", has_pii=False, tags=[]
        ))

        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm,
            pii_service=mock_pii,
        )

        request = FaqDraftGenerateRequest(
            cluster_id="cluster-001",
            domain="SEC_POLICY",
            canonical_question="USB 반출 시 어떤 절차가 필요한가요?",
            sample_questions=["USB 반출 승인은?", "외부 저장장치 반출 규정은?"],
            top_docs=[
                FaqSourceDoc(
                    doc_id="DOC-001",
                    doc_version="v1",
                    title="정보보호규정",
                    snippet="USB 메모리를 사외로 반출할 때에는 정보보호팀의 사전 승인이 필요합니다.",
                    article_label="제3장 제2조",
                )
            ],
        )

        draft = await service.generate_faq_draft(request)

        # 검증
        assert draft.faq_draft_id is not None
        assert draft.domain == "SEC_POLICY"
        assert draft.cluster_id == "cluster-001"
        assert draft.answer_source == "TOP_DOCS"
        assert draft.source_doc_id == "DOC-001"
        assert draft.ai_confidence == 0.92
        assert "정보보호팀" in draft.answer_markdown

        # Milvus 호출 안됨
        mock_rag_handler.perform_search_with_fallback.assert_not_called()

    @pytest.mark.anyio
    async def test_full_flow_with_milvus_search(self):
        """Milvus 검색 사용 시 전체 플로우"""
        mock_rag_handler = MagicMock(spec=RagHandler)
        mock_rag_handler.perform_search_with_fallback = AsyncMock(return_value=(
            [ChatSource(doc_id="연차휴가규정.pdf", title="연차휴가규정.pdf", snippet="연차휴가는 익년도로 최대 10일까지 이월 가능합니다.", score=0.95)],
            False,
            "MILVUS"
        ))

        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(return_value="""status: SUCCESS
question: 연차휴가는 이월 가능한가요?
summary: 연차휴가는 익년도로 최대 10일까지 이월 가능합니다.
answer_markdown: |
  **네, 연차휴가는 다음 해로 이월이 가능합니다.**

  - 최대 10일까지 이월 가능
  - 12월 말까지 이월 신청 필요
  - 미이월 연차는 소멸

  **참고**
  - 연차휴가규정.pdf (p.10)
ai_confidence: 0.95""")

        mock_pii = MagicMock()
        mock_pii.detect_and_mask = AsyncMock(return_value=PiiMaskResult(
            original_text="", masked_text="", has_pii=False, tags=[]
        ))

        service = FaqDraftService(
            rag_handler=mock_rag_handler,
            llm_client=mock_llm,
            pii_service=mock_pii,
        )

        request = FaqDraftGenerateRequest(
            cluster_id="cluster-002",
            domain="POLICY",
            canonical_question="연차휴가는 이월 가능한가요?",
            sample_questions=["연차 이월 되나요?", "작년 연차 올해 사용 가능?"],
            top_docs=[],  # 빈 리스트 → Milvus 검색
        )

        draft = await service.generate_faq_draft(request)

        # 검증
        assert draft.faq_draft_id is not None
        assert draft.domain == "POLICY"
        assert draft.answer_source == "MILVUS"
        assert draft.source_doc_id is None  # Milvus 검색은 source_doc_id 없음
        assert draft.ai_confidence == 0.95

        # Milvus 호출됨
        mock_rag_handler.perform_search_with_fallback.assert_called_once()
