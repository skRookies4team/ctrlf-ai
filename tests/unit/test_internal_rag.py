"""
Internal RAG API Tests (Phase 25)

문서 인덱싱/삭제 API 및 관련 서비스 테스트입니다.

테스트 항목:
1. 모델 검증
2. Job Service 테스트
3. Document Processor 테스트 (mock)
4. Indexing Service 테스트 (mock)
5. API 엔드포인트 테스트
6. Idempotency 검증
7. 이전 버전 삭제 검증
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.internal_rag import (
    DocumentChunk,
    InternalRagDeleteRequest,
    InternalRagIndexRequest,
    JobStatus,
    JobStatusResponse,
)
from app.services.job_service import JobEntry, JobService, clear_job_service
from app.services.indexing_service import IndexingService, clear_indexing_service


client = TestClient(app)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_singletons():
    """각 테스트 전후로 싱글턴 인스턴스를 초기화합니다."""
    clear_job_service()
    clear_indexing_service()
    yield
    clear_job_service()
    clear_indexing_service()


# =============================================================================
# Model Tests
# =============================================================================


class TestInternalRagModels:
    """Internal RAG 모델 테스트."""

    def test_index_request_valid(self):
        """유효한 인덱싱 요청 모델 생성."""
        request = InternalRagIndexRequest(
            documentId="DOC-001",
            versionNo=1,
            title="테스트 문서",
            domain="POLICY",
            fileUrl="https://example.com/doc.pdf",
            requestedBy="admin",
            jobId="job-123",
        )

        assert request.document_id == "DOC-001"
        assert request.version_no == 1
        assert request.title == "테스트 문서"
        assert request.domain == "POLICY"
        assert request.file_url == "https://example.com/doc.pdf"
        assert request.job_id == "job-123"

    def test_index_request_minimal(self):
        """최소 필수 필드만으로 인덱싱 요청 생성."""
        request = InternalRagIndexRequest(
            documentId="DOC-002",
            versionNo=1,
            domain="EDU",
            fileUrl="https://example.com/doc.txt",
            jobId="job-456",
        )

        assert request.document_id == "DOC-002"
        assert request.title is None
        assert request.requested_by is None

    def test_delete_request_with_version(self):
        """버전 지정 삭제 요청."""
        request = InternalRagDeleteRequest(
            documentId="DOC-001",
            versionNo=2,
            jobId="job-del-123",
        )

        assert request.document_id == "DOC-001"
        assert request.version_no == 2
        assert request.job_id == "job-del-123"

    def test_delete_request_all_versions(self):
        """전체 버전 삭제 요청 (versionNo 없음)."""
        request = InternalRagDeleteRequest(
            documentId="DOC-001",
        )

        assert request.document_id == "DOC-001"
        assert request.version_no is None
        assert request.job_id is None

    def test_document_chunk_model(self):
        """DocumentChunk 모델 테스트."""
        chunk = DocumentChunk(
            document_id="DOC-001",
            version_no=1,
            domain="POLICY",
            title="테스트 문서",
            chunk_id=0,
            chunk_text="이것은 테스트 청크입니다.",
            page=1,
            section_path="제1장 > 제1조",
        )

        assert chunk.document_id == "DOC-001"
        assert chunk.chunk_id == 0
        assert chunk.page == 1
        assert chunk.section_path == "제1장 > 제1조"


# =============================================================================
# Job Service Tests
# =============================================================================


class TestJobService:
    """JobService 테스트."""

    @pytest.fixture
    def job_service(self):
        """JobService 인스턴스 생성."""
        return JobService()

    @pytest.mark.asyncio
    async def test_create_job(self, job_service):
        """작업 생성 테스트."""
        job = await job_service.create_job(
            job_id="test-job-1",
            document_id="DOC-001",
            version_no=1,
            job_type="index",
        )

        assert job.job_id == "test-job-1"
        assert job.document_id == "DOC-001"
        assert job.version_no == 1
        assert job.status == JobStatus.QUEUED

    @pytest.mark.asyncio
    async def test_update_job_status(self, job_service):
        """작업 상태 업데이트 테스트."""
        await job_service.create_job("test-job-2", "DOC-001", 1)

        # RUNNING으로 업데이트
        await job_service.mark_running("test-job-2", "downloading")
        job = await job_service.get_job("test-job-2")

        assert job.status == JobStatus.RUNNING
        assert job.progress == "downloading"

        # COMPLETED로 업데이트
        await job_service.mark_completed("test-job-2", chunks_processed=10)
        job = await job_service.get_job("test-job-2")

        assert job.status == JobStatus.COMPLETED
        assert job.chunks_processed == 10

    @pytest.mark.asyncio
    async def test_mark_failed(self, job_service):
        """작업 실패 표시 테스트."""
        await job_service.create_job("test-job-3", "DOC-001", 1)
        await job_service.mark_failed("test-job-3", "Download failed", "downloading")

        job = await job_service.get_job("test-job-3")

        assert job.status == JobStatus.FAILED
        assert job.error_message == "Download failed"
        assert job.progress == "downloading"

    @pytest.mark.asyncio
    async def test_get_job_status_response(self, job_service):
        """JobStatusResponse 반환 테스트."""
        await job_service.create_job("test-job-4", "DOC-001", 1)

        response = await job_service.get_job_status("test-job-4")

        assert isinstance(response, JobStatusResponse)
        assert response.job_id == "test-job-4"
        assert response.status == JobStatus.QUEUED

    @pytest.mark.asyncio
    async def test_get_nonexistent_job(self, job_service):
        """존재하지 않는 작업 조회."""
        response = await job_service.get_job_status("nonexistent-job")
        assert response is None

    @pytest.mark.asyncio
    async def test_job_idempotency_replace(self, job_service):
        """동일 job_id로 재생성 시 교체됨."""
        # 첫 번째 생성
        job1 = await job_service.create_job("same-job-id", "DOC-001", 1)
        await job_service.mark_running("same-job-id", "step1")

        # 같은 ID로 재생성 (교체됨)
        job2 = await job_service.create_job("same-job-id", "DOC-002", 2)

        # 새 작업으로 교체됨
        job = await job_service.get_job("same-job-id")
        assert job.document_id == "DOC-002"
        assert job.version_no == 2
        assert job.status == JobStatus.QUEUED


# =============================================================================
# API Endpoint Tests
# =============================================================================


class TestInternalRagAPI:
    """Internal RAG API 엔드포인트 테스트."""

    def test_index_endpoint_returns_410_deprecated(self):
        """인덱싱 엔드포인트가 410 Gone 반환 (Phase 42 A안 확정 - Deprecated)."""
        response = client.post(
            "/internal/rag/index",
            json={
                "documentId": "DOC-001",
                "versionNo": 1,
                "domain": "POLICY",
                "fileUrl": "https://example.com/doc.pdf",
                "jobId": "test-job",
            },
        )

        assert response.status_code == 410
        data = response.json()
        assert data["detail"]["error"] == "ENDPOINT_DEPRECATED"
        assert "alternative" in data["detail"]

    def test_delete_endpoint_success(self):
        """삭제 엔드포인트 성공 테스트."""
        from app.models.internal_rag import InternalRagDeleteResponse

        with patch("app.api.v1.internal_rag.get_indexing_service") as mock_get:
            mock_service = MagicMock()
            mock_service.delete_document = AsyncMock(return_value=InternalRagDeleteResponse(
                job_id="del-job",
                status=JobStatus.COMPLETED,
                deleted_count=5,
                message="Deleted 5 chunks",
            ))
            mock_get.return_value = mock_service

            response = client.post(
                "/internal/rag/delete",
                json={
                    "documentId": "DOC-001",
                    "versionNo": 1,
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "completed"
            assert data["deletedCount"] == 5

    def test_job_status_endpoint_found(self):
        """작업 상태 조회 - 존재하는 작업."""
        with patch("app.api.v1.internal_rag.get_job_service") as mock_get:
            mock_service = MagicMock()
            mock_service.get_job_status = AsyncMock(return_value=JobStatusResponse(
                job_id="test-job",
                status=JobStatus.RUNNING,
                document_id="DOC-001",
                version_no=1,
                progress="embedding",
            ))
            mock_get.return_value = mock_service

            response = client.get("/internal/jobs/test-job")

            assert response.status_code == 200
            data = response.json()
            assert data["jobId"] == "test-job"
            assert data["status"] == "running"
            assert data["progress"] == "embedding"

    def test_job_status_endpoint_not_found(self):
        """작업 상태 조회 - 존재하지 않는 작업."""
        with patch("app.api.v1.internal_rag.get_job_service") as mock_get:
            mock_service = MagicMock()
            mock_service.get_job_status = AsyncMock(return_value=None)
            mock_get.return_value = mock_service

            response = client.get("/internal/jobs/nonexistent")

            assert response.status_code == 404

    def test_index_request_validation_error(self):
        """인덱싱 요청 유효성 검사 실패."""
        response = client.post(
            "/internal/rag/index",
            json={
                "documentId": "DOC-001",
                # versionNo, domain, fileUrl, jobId 누락
            },
        )

        assert response.status_code == 422  # Validation Error


# =============================================================================
# Indexing Service Tests (with mocks)
# =============================================================================


class TestIndexingService:
    """IndexingService 테스트 (mock 사용)."""

    @pytest.fixture
    def mock_dependencies(self):
        """의존성 mock 생성."""
        mock_processor = MagicMock()
        mock_milvus = MagicMock()
        mock_job_service = JobService()

        return mock_processor, mock_milvus, mock_job_service

    @pytest.mark.asyncio
    async def test_index_document_queues_job(self, mock_dependencies):
        """인덱싱 요청이 작업을 큐에 등록함."""
        mock_processor, mock_milvus, mock_job_service = mock_dependencies

        service = IndexingService(
            document_processor=mock_processor,
            milvus_client=mock_milvus,
            job_service=mock_job_service,
        )

        request = InternalRagIndexRequest(
            documentId="DOC-001",
            versionNo=1,
            domain="POLICY",
            fileUrl="https://example.com/doc.pdf",
            jobId="test-job-idx",
        )

        response = await service.index_document(request)

        assert response.status == JobStatus.QUEUED
        assert response.job_id == "test-job-idx"

        # 작업이 생성되었는지 확인
        job = await mock_job_service.get_job("test-job-idx")
        assert job is not None
        assert job.document_id == "DOC-001"

    @pytest.mark.asyncio
    async def test_delete_document_success(self, mock_dependencies):
        """문서 삭제 성공."""
        mock_processor, mock_milvus, mock_job_service = mock_dependencies

        # Mock 설정
        mock_milvus.get_document_chunk_count = AsyncMock(return_value=5)
        mock_milvus.delete_by_document = AsyncMock(return_value=5)

        service = IndexingService(
            document_processor=mock_processor,
            milvus_client=mock_milvus,
            job_service=mock_job_service,
        )

        request = InternalRagDeleteRequest(
            documentId="DOC-001",
            versionNo=1,
        )

        response = await service.delete_document(request)

        assert response.status == JobStatus.COMPLETED
        assert response.deleted_count == 5
        mock_milvus.delete_by_document.assert_called_once_with("DOC-001", 1)


# =============================================================================
# Milvus Client Extension Tests
# =============================================================================


class TestMilvusClientPhase25:
    """MilvusSearchClient Phase 25 확장 테스트."""

    @pytest.fixture
    def mock_milvus_client(self):
        """Mock MilvusSearchClient."""
        from app.clients.milvus_client import MilvusSearchClient

        with patch.object(MilvusSearchClient, "_ensure_connection"):
            with patch.object(MilvusSearchClient, "_get_collection"):
                client = MilvusSearchClient(
                    host="localhost",
                    port=19530,
                    collection_name="test_collection",
                    llm_base_url="http://localhost:8000/v1",
                )
                return client

    @pytest.mark.asyncio
    async def test_upsert_chunks_empty(self, mock_milvus_client):
        """빈 청크 리스트 upsert."""
        result = await mock_milvus_client.upsert_chunks([])
        assert result == 0

    @pytest.mark.asyncio
    async def test_search_chunks_with_filters(self, mock_milvus_client):
        """필터가 적용된 검색."""
        with patch.object(mock_milvus_client, "generate_embedding", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            mock_collection = MagicMock()
            mock_collection.search.return_value = [[]]  # 빈 결과
            mock_milvus_client._get_collection = MagicMock(return_value=mock_collection)

            results = await mock_milvus_client.search_chunks(
                query="test query",
                domain="POLICY",
                top_k=5,
                version_no=1,
            )

            # search가 호출되었는지 확인
            mock_collection.search.assert_called_once()
            call_args = mock_collection.search.call_args

            # expr에 domain과 version_no 필터가 포함되어야 함
            expr = call_args.kwargs.get("expr") or call_args[1].get("expr")
            assert 'domain == "POLICY"' in expr
            assert 'version_no == 1' in expr


# =============================================================================
# Version Deletion Tests
# =============================================================================


class TestVersionDeletion:
    """이전 버전 삭제 로직 테스트."""

    @pytest.mark.asyncio
    async def test_delete_old_versions_called_after_success(self):
        """새 버전 upsert 성공 후에만 이전 버전 삭제."""
        from app.clients.milvus_client import MilvusSearchClient

        # Mock 설정
        mock_milvus = MagicMock(spec=MilvusSearchClient)
        mock_milvus.delete_by_document = AsyncMock(return_value=0)
        mock_milvus.delete_old_versions = AsyncMock(return_value=5)
        mock_milvus.upsert_chunks = AsyncMock(return_value=10)
        mock_milvus.generate_embedding = AsyncMock(return_value=[0.1] * 1024)

        mock_processor = MagicMock()
        mock_processor.process = AsyncMock(return_value=[
            DocumentChunk(
                document_id="DOC-001",
                version_no=2,
                domain="POLICY",
                title="Test",
                chunk_id=0,
                chunk_text="Test content",
            )
        ])

        mock_job_service = JobService()

        service = IndexingService(
            document_processor=mock_processor,
            milvus_client=mock_milvus,
            job_service=mock_job_service,
        )

        request = InternalRagIndexRequest(
            documentId="DOC-001",
            versionNo=2,
            domain="POLICY",
            fileUrl="https://example.com/doc.pdf",
            jobId="test-version-job",
        )

        # 인덱싱 요청 (비동기 작업 시작)
        await service.index_document(request)

        # 비동기 작업 완료 대기
        await asyncio.sleep(0.5)

        # delete_old_versions가 호출되었는지 확인
        # (upsert 성공 후에만 호출되어야 함)
        mock_milvus.delete_old_versions.assert_called_once_with("DOC-001", 2)

    @pytest.mark.asyncio
    async def test_delete_old_versions_not_called_on_upsert_failure(self):
        """upsert 실패 시 이전 버전 삭제가 호출되지 않아야 함."""
        from app.clients.milvus_client import MilvusSearchClient, MilvusError

        # Mock 설정 - upsert가 실패하도록 구성
        mock_milvus = MagicMock(spec=MilvusSearchClient)
        mock_milvus.delete_by_document = AsyncMock(return_value=0)
        mock_milvus.delete_old_versions = AsyncMock(return_value=0)
        mock_milvus.upsert_chunks = AsyncMock(side_effect=MilvusError("Upsert failed"))
        mock_milvus.generate_embedding = AsyncMock(return_value=[0.1] * 1024)

        mock_processor = MagicMock()
        mock_processor.process = AsyncMock(return_value=[
            DocumentChunk(
                document_id="DOC-001",
                version_no=2,
                domain="POLICY",
                title="Test",
                chunk_id=0,
                chunk_text="Test content",
            )
        ])

        mock_job_service = JobService()

        # 재시도 설정을 최소화 (테스트 속도 향상)
        with patch("app.services.indexing_service.get_settings") as mock_settings:
            settings = MagicMock()
            settings.INDEX_RETRY_MAX_ATTEMPTS = 1
            settings.INDEX_RETRY_BACKOFF_SECONDS = "0.01"
            mock_settings.return_value = settings

            service = IndexingService(
                document_processor=mock_processor,
                milvus_client=mock_milvus,
                job_service=mock_job_service,
            )

        request = InternalRagIndexRequest(
            documentId="DOC-001",
            versionNo=2,
            domain="POLICY",
            fileUrl="https://example.com/doc.pdf",
            jobId="test-fail-job",
        )

        # 인덱싱 요청 (비동기 작업 시작)
        await service.index_document(request)

        # 비동기 작업 완료 대기
        await asyncio.sleep(0.5)

        # upsert가 호출되었는지 확인
        mock_milvus.upsert_chunks.assert_called()

        # 핵심: delete_old_versions는 호출되지 않아야 함
        mock_milvus.delete_old_versions.assert_not_called()

        # Job 상태가 FAILED인지 확인
        job = await mock_job_service.get_job("test-fail-job")
        assert job.status == JobStatus.FAILED
        assert "Upsert failed" in job.error_message

    @pytest.mark.asyncio
    async def test_pipeline_order_upsert_before_delete(self):
        """파이프라인 순서: upsert 성공 → delete_old_versions 호출."""
        from app.clients.milvus_client import MilvusSearchClient

        call_order = []

        async def mock_upsert(*args, **kwargs):
            call_order.append("upsert")
            return 10

        async def mock_delete_old(*args, **kwargs):
            call_order.append("delete_old_versions")
            return 5

        mock_milvus = MagicMock(spec=MilvusSearchClient)
        mock_milvus.delete_by_document = AsyncMock(return_value=0)
        mock_milvus.delete_old_versions = AsyncMock(side_effect=mock_delete_old)
        mock_milvus.upsert_chunks = AsyncMock(side_effect=mock_upsert)
        mock_milvus.generate_embedding = AsyncMock(return_value=[0.1] * 1024)

        mock_processor = MagicMock()
        mock_processor.process = AsyncMock(return_value=[
            DocumentChunk(
                document_id="DOC-001",
                version_no=3,
                domain="POLICY",
                title="Test",
                chunk_id=0,
                chunk_text="Test content",
            )
        ])

        mock_job_service = JobService()

        service = IndexingService(
            document_processor=mock_processor,
            milvus_client=mock_milvus,
            job_service=mock_job_service,
        )

        request = InternalRagIndexRequest(
            documentId="DOC-001",
            versionNo=3,
            domain="POLICY",
            fileUrl="https://example.com/doc.pdf",
            jobId="test-order-job",
        )

        await service.index_document(request)
        await asyncio.sleep(0.5)

        # 순서 검증: upsert가 먼저, delete_old_versions가 나중
        assert call_order == ["upsert", "delete_old_versions"]
