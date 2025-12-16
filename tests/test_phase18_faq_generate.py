"""
Phase 18: FAQ 초안 생성 API 테스트

테스트 항목:
1. FAQ 모델 테스트
2. FaqDraftService 단위 테스트
3. FastAPI 엔드포인트 통합 테스트
4. 에러 케이스 테스트

Phase 19-AI-2 업데이트:
- rag_client → search_client (RagflowSearchClient)
- RAG 검색 결과 없음 → NO_DOCS_FOUND 에러 발생
"""

import json
from datetime import datetime, timezone
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.faq import (
    FaqDraft,
    FaqDraftGenerateRequest,
    FaqDraftGenerateResponse,
    FaqSourceDoc,
)
from app.services.faq_service import FaqDraftService, FaqGenerationError


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


@pytest.fixture
def test_client() -> TestClient:
    """FastAPI 테스트 클라이언트."""
    return TestClient(app)


# =============================================================================
# Model Tests
# =============================================================================


class TestFaqModels:
    """FAQ 모델 테스트"""

    def test_faq_source_doc_creation(self):
        """FaqSourceDoc 생성 테스트"""
        doc = FaqSourceDoc(
            doc_id="DOC-001",
            doc_version="v1",
            title="정보보호규정",
            snippet="USB 메모리를 사외로 반출할 때에는...",
            article_label="제3장 제2조",
            article_path="제3장 > 제2조 > 제1항",
        )
        assert doc.doc_id == "DOC-001"
        assert doc.doc_version == "v1"
        assert doc.title == "정보보호규정"

    def test_faq_draft_generate_request(self):
        """FaqDraftGenerateRequest 생성 테스트"""
        request = FaqDraftGenerateRequest(
            domain="SEC_POLICY",
            cluster_id="cluster-001",
            canonical_question="USB 반출 시 어떤 절차가 필요한가요?",
            sample_questions=[
                "USB 반출 승인은 어떻게 받나요?",
                "외부 저장장치 반출 규정이 뭔가요?",
            ],
            top_docs=[
                FaqSourceDoc(doc_id="DOC-001", title="정보보호규정")
            ],
        )
        assert request.domain == "SEC_POLICY"
        assert request.cluster_id == "cluster-001"
        assert len(request.sample_questions) == 2
        assert len(request.top_docs) == 1

    def test_faq_draft_generate_request_defaults(self):
        """FaqDraftGenerateRequest 기본값 테스트"""
        request = FaqDraftGenerateRequest(
            domain="SEC_POLICY",
            cluster_id="cluster-001",
            canonical_question="테스트 질문",
        )
        assert request.sample_questions == []
        assert request.top_docs == []
        assert request.answer_source_hint is None
        assert request.meta is None

    def test_faq_draft_creation(self):
        """FaqDraft 생성 테스트"""
        draft = FaqDraft(
            faq_draft_id="FAQ-001",
            domain="SEC_POLICY",
            cluster_id="cluster-001",
            question="USB 반출 시 어떤 절차가 필요한가요?",
            answer_markdown="**정보보호팀의 사전 승인이 필요합니다.**\n\n- 신청서 작성\n- 승인 요청",
            summary="정보보호팀의 사전 승인이 필요합니다.",
            source_doc_id="DOC-001",
            source_doc_version="v1",
            source_article_label="제3장 제2조",
            source_article_path="제3장 > 제2조 > 제1항",
            answer_source="AI_RAG",
            ai_confidence=0.85,
            created_at=datetime.now(timezone.utc),
        )
        assert draft.faq_draft_id == "FAQ-001"
        assert draft.answer_source == "AI_RAG"
        assert draft.ai_confidence == 0.85

    def test_faq_draft_generate_response_success(self):
        """FaqDraftGenerateResponse 성공 응답 테스트"""
        draft = FaqDraft(
            faq_draft_id="FAQ-001",
            domain="SEC_POLICY",
            cluster_id="cluster-001",
            question="테스트 질문",
            answer_markdown="테스트 답변",
            answer_source="AI_RAG",
            created_at=datetime.now(timezone.utc),
        )
        response = FaqDraftGenerateResponse(
            status="SUCCESS",
            faq_draft=draft,
        )
        assert response.status == "SUCCESS"
        assert response.faq_draft is not None
        assert response.error_message is None

    def test_faq_draft_generate_response_failed(self):
        """FaqDraftGenerateResponse 실패 응답 테스트"""
        response = FaqDraftGenerateResponse(
            status="FAILED",
            faq_draft=None,
            error_message="LLM 호출 실패",
        )
        assert response.status == "FAILED"
        assert response.faq_draft is None
        assert response.error_message == "LLM 호출 실패"


# =============================================================================
# Service Tests (Phase 19-AI-2 Updated)
# =============================================================================


class TestFaqDraftService:
    """FaqDraftService 테스트 (Phase 19-AI-2 업데이트)"""

    @pytest.mark.anyio
    async def test_generate_faq_draft_success_with_top_docs(self):
        """top_docs 제공 시 RAGFlow 호출 없이 성공 테스트"""
        # Mock LLM client
        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(
            return_value=json.dumps({
                "question": "USB 반출 시 어떤 절차가 필요한가요?",
                "answer_markdown": "**정보보호팀의 사전 승인이 필요합니다.**",
                "summary": "정보보호팀의 사전 승인이 필요합니다.",
                "source_doc_id": "DOC-001",
                "source_doc_version": "v1",
                "source_article_label": "제3장 제2조",
                "source_article_path": "제3장 > 제2조 > 제1항",
                "answer_source": "AI_RAG",
                "ai_confidence": 0.85,
            })
        )

        # Mock Search client (should not be called)
        mock_search = MagicMock()
        mock_search.search_chunks = AsyncMock()

        service = FaqDraftService(search_client=mock_search, llm_client=mock_llm)

        request = FaqDraftGenerateRequest(
            domain="SEC_POLICY",
            cluster_id="cluster-001",
            canonical_question="USB 반출 시 어떤 절차가 필요한가요?",
            top_docs=[
                FaqSourceDoc(
                    doc_id="DOC-001",
                    title="정보보호규정",
                    snippet="USB 메모리를 사외로 반출할 때에는...",
                )
            ],
        )

        draft = await service.generate_faq_draft(request)

        # RAGFlow should not be called since top_docs provided
        mock_search.search_chunks.assert_not_called()

        # LLM should be called
        mock_llm.generate_chat_completion.assert_called_once()

        # Verify draft
        assert draft.domain == "SEC_POLICY"
        assert draft.cluster_id == "cluster-001"
        assert draft.question == "USB 반출 시 어떤 절차가 필요한가요?"
        # Phase 19-AI-3: top_docs 사용 시 answer_source=TOP_DOCS
        assert draft.answer_source == "TOP_DOCS"
        assert draft.ai_confidence == 0.85
        assert draft.source_doc_id == "DOC-001"

    @pytest.mark.anyio
    async def test_generate_faq_draft_with_rag_search(self):
        """top_docs 없을 때 RAGFlow 검색 테스트"""
        # Mock LLM client
        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(
            return_value=json.dumps({
                "question": "테스트 질문",
                "answer_markdown": "테스트 답변",
                "summary": "요약",
                "answer_source": "AI_RAG",
                "ai_confidence": 0.8,
            })
        )

        # Mock Search client - Phase 19-AI-2 format
        mock_search = MagicMock()
        mock_search.search_chunks = AsyncMock(
            return_value=[
                {
                    "document_name": "검색된 문서.pdf",
                    "page_num": 10,
                    "similarity": 0.9,
                    "content": "검색 결과 내용...",
                }
            ]
        )

        service = FaqDraftService(search_client=mock_search, llm_client=mock_llm)

        request = FaqDraftGenerateRequest(
            domain="SEC_POLICY",
            cluster_id="cluster-002",
            canonical_question="테스트 질문",
            top_docs=[],  # Empty - should trigger RAGFlow search
        )

        draft = await service.generate_faq_draft(request)

        # RAGFlow should be called
        mock_search.search_chunks.assert_called_once()

        # Verify draft
        assert draft.domain == "SEC_POLICY"
        assert draft.question == "테스트 질문"
        # RAGFlow 검색 시 source 필드는 null
        assert draft.source_doc_id is None
        # Phase 19-AI-3: RAGFlow 검색 시 answer_source=RAGFLOW
        assert draft.answer_source == "RAGFLOW"

    @pytest.mark.anyio
    async def test_generate_faq_draft_rag_empty_raises_error(self):
        """RAG 0건 시 NO_DOCS_FOUND 에러 발생 (Phase 19-AI-2)"""
        # Mock LLM client (should not be called)
        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock()

        # Mock Search client - returns empty
        mock_search = MagicMock()
        mock_search.search_chunks = AsyncMock(return_value=[])

        service = FaqDraftService(search_client=mock_search, llm_client=mock_llm)

        request = FaqDraftGenerateRequest(
            domain="SEC_POLICY",
            cluster_id="cluster-003",
            canonical_question="관련 문서가 없는 질문",
        )

        # Phase 19-AI-2: 검색 결과 없으면 에러 발생
        with pytest.raises(FaqGenerationError) as exc_info:
            await service.generate_faq_draft(request)

        assert "NO_DOCS_FOUND" in str(exc_info.value)
        # LLM은 호출되지 않아야 함
        mock_llm.generate_chat_completion.assert_not_called()

    @pytest.mark.anyio
    async def test_generate_faq_draft_llm_failure(self):
        """LLM 호출 실패 테스트"""
        # Mock LLM client - raises exception
        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(
            side_effect=Exception("LLM connection error")
        )

        # Mock Search client - provide results to pass doc check
        mock_search = MagicMock()
        mock_search.search_chunks = AsyncMock(
            return_value=[
                {"document_name": "test.pdf", "similarity": 0.9, "content": "test"}
            ]
        )

        service = FaqDraftService(search_client=mock_search, llm_client=mock_llm)

        request = FaqDraftGenerateRequest(
            domain="SEC_POLICY",
            cluster_id="cluster-004",
            canonical_question="테스트 질문",
        )

        with pytest.raises(FaqGenerationError) as exc_info:
            await service.generate_faq_draft(request)

        assert "LLM 호출 실패" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_generate_faq_draft_json_parse_failure(self):
        """LLM JSON 파싱 실패 테스트"""
        # Mock LLM client - returns invalid JSON
        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(
            return_value="이것은 유효한 JSON이 아닙니다."
        )

        # Mock Search client
        mock_search = MagicMock()
        mock_search.search_chunks = AsyncMock(
            return_value=[
                {"document_name": "test.pdf", "similarity": 0.9, "content": "test"}
            ]
        )

        service = FaqDraftService(search_client=mock_search, llm_client=mock_llm)

        request = FaqDraftGenerateRequest(
            domain="SEC_POLICY",
            cluster_id="cluster-005",
            canonical_question="테스트 질문",
        )

        with pytest.raises(FaqGenerationError) as exc_info:
            await service.generate_faq_draft(request)

        assert "파싱" in str(exc_info.value) or "JSON" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_generate_faq_draft_missing_required_field(self):
        """LLM 응답에 필수 필드 누락 테스트"""
        # Mock LLM client - returns JSON without required field
        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(
            return_value=json.dumps({
                "question": "질문은 있지만",
                # answer_markdown 누락
                "summary": "요약",
            })
        )

        # Mock Search client
        mock_search = MagicMock()
        mock_search.search_chunks = AsyncMock(
            return_value=[
                {"document_name": "test.pdf", "similarity": 0.9, "content": "test"}
            ]
        )

        service = FaqDraftService(search_client=mock_search, llm_client=mock_llm)

        request = FaqDraftGenerateRequest(
            domain="SEC_POLICY",
            cluster_id="cluster-006",
            canonical_question="테스트 질문",
        )

        with pytest.raises(FaqGenerationError) as exc_info:
            await service.generate_faq_draft(request)

        # Phase 19-AI-3: 파싱 실패 메시지
        assert "파싱" in str(exc_info.value)


class TestFaqDraftServiceHelpers:
    """FaqDraftService 헬퍼 메서드 테스트"""

    def test_normalize_answer_source(self):
        """answer_source 정규화 테스트"""
        service = FaqDraftService.__new__(FaqDraftService)

        # Phase 19-AI-3: TOP_DOCS, RAGFLOW 추가
        assert service._normalize_answer_source("TOP_DOCS") == "TOP_DOCS"
        assert service._normalize_answer_source("RAGFLOW") == "RAGFLOW"
        assert service._normalize_answer_source("AI_RAG") == "AI_RAG"
        assert service._normalize_answer_source("ai_rag") == "AI_RAG"
        assert service._normalize_answer_source("LOG_REUSE") == "LOG_REUSE"
        assert service._normalize_answer_source("MIXED") == "MIXED"
        # Phase 19-AI-3: 기본값이 RAGFLOW로 변경
        assert service._normalize_answer_source("invalid") == "RAGFLOW"
        assert service._normalize_answer_source(None) == "RAGFLOW"

    def test_normalize_confidence(self):
        """ai_confidence 정규화 테스트"""
        service = FaqDraftService.__new__(FaqDraftService)

        assert service._normalize_confidence(0.85) == 0.85
        assert service._normalize_confidence(1.5) == 1.0  # Clamped to max
        assert service._normalize_confidence(-0.5) == 0.0  # Clamped to min
        assert service._normalize_confidence(None) is None
        assert service._normalize_confidence("invalid") is None

    def test_extract_json_from_response(self):
        """JSON 추출 테스트"""
        service = FaqDraftService.__new__(FaqDraftService)

        # Code block format
        response1 = '```json\n{"key": "value"}\n```'
        assert service._extract_json_from_response(response1) == '{"key": "value"}'

        # Raw JSON
        response2 = '{"key": "value"}'
        assert service._extract_json_from_response(response2) == '{"key": "value"}'

        # JSON with surrounding text
        response3 = 'Here is the result: {"key": "value"} end'
        assert service._extract_json_from_response(response3) == '{"key": "value"}'

        # No JSON
        response4 = 'No JSON here'
        assert service._extract_json_from_response(response4) is None


# =============================================================================
# API Tests
# =============================================================================


class TestFaqAPI:
    """FAQ API 엔드포인트 테스트"""

    def test_faq_generate_success(self, test_client: TestClient):
        """FAQ 생성 성공 테스트"""
        from app.api.v1 import faq as faq_module

        # Mock service
        mock_draft = FaqDraft(
            faq_draft_id="FAQ-test-001",
            domain="SEC_POLICY",
            cluster_id="cluster-001",
            question="USB 반출 시 어떤 절차가 필요한가요?",
            answer_markdown="**정보보호팀의 사전 승인이 필요합니다.**",
            summary="정보보호팀의 사전 승인이 필요합니다.",
            source_doc_id="DOC-001",
            answer_source="AI_RAG",
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
                    "sample_questions": ["USB 반출 승인은?"],
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "SUCCESS"
            assert data["faq_draft"]["faq_draft_id"] == "FAQ-test-001"
            assert data["faq_draft"]["domain"] == "SEC_POLICY"
            assert data["faq_draft"]["answer_source"] == "AI_RAG"
            assert data["error_message"] is None

        finally:
            faq_module.get_faq_service = original_fn
            faq_module._faq_service = None

    def test_faq_generate_service_failure(self, test_client: TestClient):
        """FAQ 생성 서비스 실패 테스트"""
        from app.api.v1 import faq as faq_module

        mock_service = MagicMock()
        mock_service.generate_faq_draft = AsyncMock(
            side_effect=FaqGenerationError("LLM 응답 파싱 실패")
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
            assert data["faq_draft"] is None
            assert "파싱" in data["error_message"]

        finally:
            faq_module.get_faq_service = original_fn
            faq_module._faq_service = None

    def test_faq_generate_validation_error_missing_domain(self, test_client: TestClient):
        """필수 필드 누락 - domain"""
        response = test_client.post(
            "/ai/faq/generate",
            json={
                # domain 누락
                "cluster_id": "cluster-001",
                "canonical_question": "테스트 질문",
            },
        )
        assert response.status_code == 422

    def test_faq_generate_validation_error_missing_cluster_id(self, test_client: TestClient):
        """필수 필드 누락 - cluster_id"""
        response = test_client.post(
            "/ai/faq/generate",
            json={
                "domain": "SEC_POLICY",
                # cluster_id 누락
                "canonical_question": "테스트 질문",
            },
        )
        assert response.status_code == 422

    def test_faq_generate_validation_error_missing_question(self, test_client: TestClient):
        """필수 필드 누락 - canonical_question"""
        response = test_client.post(
            "/ai/faq/generate",
            json={
                "domain": "SEC_POLICY",
                "cluster_id": "cluster-001",
                # canonical_question 누락
            },
        )
        assert response.status_code == 422

    def test_faq_generate_with_top_docs(self, test_client: TestClient):
        """top_docs 포함 요청 테스트"""
        from app.api.v1 import faq as faq_module

        mock_draft = FaqDraft(
            faq_draft_id="FAQ-test-002",
            domain="SEC_POLICY",
            cluster_id="cluster-001",
            question="테스트 질문",
            answer_markdown="테스트 답변",
            answer_source="AI_RAG",
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
                    "canonical_question": "테스트 질문",
                    "top_docs": [
                        {
                            "doc_id": "DOC-001",
                            "title": "정보보호규정",
                            "snippet": "관련 내용...",
                            "article_label": "제3장 제2조",
                        }
                    ],
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "SUCCESS"

        finally:
            faq_module.get_faq_service = original_fn
            faq_module._faq_service = None


# =============================================================================
# Integration Tests (Phase 19-AI-2 Updated)
# =============================================================================


class TestFaqIntegration:
    """FAQ 통합 테스트 (Phase 19-AI-2 업데이트)"""

    @pytest.mark.anyio
    async def test_full_faq_generation_flow(self):
        """전체 FAQ 생성 플로우 테스트"""
        # Mock LLM
        mock_llm = MagicMock()
        mock_llm.generate_chat_completion = AsyncMock(
            return_value=json.dumps({
                "question": "4대 필수교육 미이수 시 어떤 패널티가 있나요?",
                "answer_markdown": "**인사고과 반영 및 교육 재이수가 필요합니다.**\n\n- 인사고과 감점\n- 해당 교육 재이수 필수\n- 3회 이상 미이수 시 징계 대상",
                "summary": "인사고과 반영 및 교육 재이수가 필요합니다.",
                "source_doc_id": "EDU-DOC-001",
                "source_doc_version": "v2",
                "source_article_label": "제4조",
                "source_article_path": "제2장 > 제4조",
                "answer_source": "AI_RAG",
                "ai_confidence": 0.92,
            })
        )

        # Mock Search client - Phase 19-AI-2 format
        mock_search = MagicMock()
        mock_search.search_chunks = AsyncMock(
            return_value=[
                {
                    "document_name": "필수교육 운영규정.pdf",
                    "page_num": 15,
                    "similarity": 0.95,
                    "content": "4대 필수교육을 미이수한 경우 인사고과에 반영됩니다...",
                }
            ]
        )

        service = FaqDraftService(search_client=mock_search, llm_client=mock_llm)

        request = FaqDraftGenerateRequest(
            domain="TRAINING_QUIZ",
            cluster_id="edu-cluster-001",
            canonical_question="4대 필수교육 미이수 시 어떤 패널티가 있나요?",
            sample_questions=[
                "필수교육 안 들으면 어떻게 되나요?",
                "보안교육 미이수 패널티가 뭔가요?",
            ],
        )

        draft = await service.generate_faq_draft(request)

        # Verify complete flow
        assert draft.domain == "TRAINING_QUIZ"
        assert draft.cluster_id == "edu-cluster-001"
        assert "4대 필수교육" in draft.question
        assert "인사고과" in draft.answer_markdown
        # Phase 19-AI-2: RAGFlow 검색 시 source 필드는 null
        assert draft.source_doc_id is None
        # Phase 19-AI-3: RAGFlow 검색 시 answer_source=RAGFLOW
        assert draft.answer_source == "RAGFLOW"
        assert draft.ai_confidence == 0.92
        assert draft.created_at is not None

        # Verify RAGFlow was called
        mock_search.search_chunks.assert_called_once()

        # Verify LLM was called
        mock_llm.generate_chat_completion.assert_called_once()
