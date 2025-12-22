"""
Phase 20-AI-2: 배치 FAQ 생성 테스트

배치 엔드포인트의 동작을 검증합니다:
- 3개 요청 중 1개가 실패해도 나머지는 정상 처리되는지
- 반환 순서가 입력 순서와 동일한지
"""

from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"

from app.models.faq import (
    FaqDraft,
    FaqDraftGenerateRequest,
    FaqDraftGenerateBatchRequest,
    FaqDraftGenerateBatchResponse,
    FaqDraftGenerateResponse,
)
from app.services.faq_service import FaqDraftService, FaqGenerationError


class TestFaqDraftBatchModels:
    """배치 모델 테스트."""

    def test_batch_request_validation(self):
        """배치 요청 모델 유효성 검사."""
        request = FaqDraftGenerateBatchRequest(
            items=[
                FaqDraftGenerateRequest(
                    domain="POLICY",
                    cluster_id="cluster-001",
                    canonical_question="연차휴가는 어떻게 신청하나요?",
                ),
                FaqDraftGenerateRequest(
                    domain="POLICY",
                    cluster_id="cluster-002",
                    canonical_question="출장비 정산은 어떻게 하나요?",
                ),
            ],
            concurrency=2,
        )

        assert len(request.items) == 2
        assert request.concurrency == 2

    def test_batch_request_min_items(self):
        """배치 요청에 최소 1개 항목 필요."""
        with pytest.raises(ValueError):
            FaqDraftGenerateBatchRequest(items=[])

    def test_batch_response_model(self):
        """배치 응답 모델 테스트."""
        response = FaqDraftGenerateBatchResponse(
            items=[
                FaqDraftGenerateResponse(status="SUCCESS", faq_draft=None, error_message=None),
                FaqDraftGenerateResponse(status="FAILED", faq_draft=None, error_message="PII_DETECTED"),
            ],
            total_count=2,
            success_count=1,
            failed_count=1,
        )

        assert response.total_count == 2
        assert response.success_count == 1
        assert response.failed_count == 1


class TestFaqBatchEndpoint:
    """배치 엔드포인트 통합 테스트."""

    @pytest.mark.anyio
    async def test_partial_failure_does_not_affect_others(self):
        """한 항목 실패가 다른 항목에 영향을 미치지 않음."""
        # Mock FaqDraftService
        mock_draft = FaqDraft(
            faq_draft_id="test-draft-id",
            domain="POLICY",
            cluster_id="cluster-001",
            question="연차휴가는 어떻게 신청하나요?",
            answer_markdown="**답변입니다.**\n\n- 항목1\n- 항목2",
            summary="연차휴가 신청 방법",
            source_doc_id=None,
            source_doc_version=None,
            source_article_label=None,
            source_article_path=None,
            answer_source="RAGFLOW",
            ai_confidence=0.85,
            created_at=datetime.now(timezone.utc),
        )

        async def mock_generate_draft(req):
            if req.cluster_id == "cluster-002":
                raise FaqGenerationError("PII_DETECTED")
            return FaqDraft(
                faq_draft_id=f"test-draft-{req.cluster_id}",
                domain=req.domain,
                cluster_id=req.cluster_id,
                question=req.canonical_question,
                answer_markdown="**답변입니다.**",
                summary="요약",
                source_doc_id=None,
                source_doc_version=None,
                source_article_label=None,
                source_article_path=None,
                answer_source="RAGFLOW",
                ai_confidence=0.85,
                created_at=datetime.now(timezone.utc),
            )

        with patch("app.api.v1.faq.get_faq_service") as mock_get_service:
            mock_service = AsyncMock(spec=FaqDraftService)
            mock_service.generate_faq_draft = mock_generate_draft
            mock_get_service.return_value = mock_service

            # Import after patching
            from app.api.v1.faq import generate_faq_draft_batch

            request = FaqDraftGenerateBatchRequest(
                items=[
                    FaqDraftGenerateRequest(
                        domain="POLICY",
                        cluster_id="cluster-001",
                        canonical_question="연차휴가는 어떻게 신청하나요?",
                    ),
                    FaqDraftGenerateRequest(
                        domain="POLICY",
                        cluster_id="cluster-002",  # 이 항목은 실패할 것
                        canonical_question="개인정보가 포함된 질문",
                    ),
                    FaqDraftGenerateRequest(
                        domain="POLICY",
                        cluster_id="cluster-003",
                        canonical_question="출장비 정산은 어떻게 하나요?",
                    ),
                ],
            )

            response = await generate_faq_draft_batch(request)

            # 검증
            assert response.total_count == 3
            assert response.success_count == 2
            assert response.failed_count == 1

            # 첫 번째 항목: 성공
            assert response.items[0].status == "SUCCESS"
            assert response.items[0].faq_draft is not None

            # 두 번째 항목: 실패
            assert response.items[1].status == "FAILED"
            assert response.items[1].error_message == "PII_DETECTED"

            # 세 번째 항목: 성공
            assert response.items[2].status == "SUCCESS"
            assert response.items[2].faq_draft is not None

    @pytest.mark.anyio
    async def test_response_order_matches_request_order(self):
        """응답 순서가 요청 순서와 동일."""
        async def mock_generate_draft(req):
            return FaqDraft(
                faq_draft_id=f"draft-{req.cluster_id}",
                domain=req.domain,
                cluster_id=req.cluster_id,
                question=req.canonical_question,
                answer_markdown="**답변입니다.**",
                summary="요약",
                source_doc_id=None,
                source_doc_version=None,
                source_article_label=None,
                source_article_path=None,
                answer_source="RAGFLOW",
                ai_confidence=0.85,
                created_at=datetime.now(timezone.utc),
            )

        with patch("app.api.v1.faq.get_faq_service") as mock_get_service:
            mock_service = AsyncMock(spec=FaqDraftService)
            mock_service.generate_faq_draft = mock_generate_draft
            mock_get_service.return_value = mock_service

            from app.api.v1.faq import generate_faq_draft_batch

            cluster_ids = ["cluster-A", "cluster-B", "cluster-C", "cluster-D"]

            request = FaqDraftGenerateBatchRequest(
                items=[
                    FaqDraftGenerateRequest(
                        domain="POLICY",
                        cluster_id=cid,
                        canonical_question=f"질문 {cid}",
                    )
                    for cid in cluster_ids
                ],
            )

            response = await generate_faq_draft_batch(request)

            # 응답 순서 검증
            for i, cid in enumerate(cluster_ids):
                assert response.items[i].faq_draft.cluster_id == cid

    @pytest.mark.anyio
    async def test_concurrency_limit(self):
        """동시성 제한 설정 테스트."""
        call_count = 0
        max_concurrent = 0
        current_concurrent = 0

        async def mock_generate_draft(req):
            nonlocal call_count, max_concurrent, current_concurrent
            import asyncio

            current_concurrent += 1
            max_concurrent = max(max_concurrent, current_concurrent)
            call_count += 1

            await asyncio.sleep(0.01)  # 약간의 지연

            current_concurrent -= 1

            return FaqDraft(
                faq_draft_id=f"draft-{req.cluster_id}",
                domain=req.domain,
                cluster_id=req.cluster_id,
                question=req.canonical_question,
                answer_markdown="**답변입니다.**",
                summary="요약",
                source_doc_id=None,
                source_doc_version=None,
                source_article_label=None,
                source_article_path=None,
                answer_source="RAGFLOW",
                ai_confidence=0.85,
                created_at=datetime.now(timezone.utc),
            )

        with patch("app.api.v1.faq.get_faq_service") as mock_get_service:
            mock_service = AsyncMock(spec=FaqDraftService)
            mock_service.generate_faq_draft = mock_generate_draft
            mock_get_service.return_value = mock_service

            with patch("app.api.v1.faq.get_settings") as mock_settings:
                mock_settings.return_value = MagicMock(FAQ_BATCH_CONCURRENCY=2)

                from app.api.v1.faq import generate_faq_draft_batch

                request = FaqDraftGenerateBatchRequest(
                    items=[
                        FaqDraftGenerateRequest(
                            domain="POLICY",
                            cluster_id=f"cluster-{i}",
                            canonical_question=f"질문 {i}",
                        )
                        for i in range(6)
                    ],
                    concurrency=2,  # 동시성 2로 제한
                )

                response = await generate_faq_draft_batch(request)

                assert response.total_count == 6
                assert call_count == 6
                # 동시성이 2를 넘지 않아야 함
                assert max_concurrent <= 2
