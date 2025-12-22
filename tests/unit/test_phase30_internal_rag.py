"""
Phase 30: Internal RAG API Tests

백엔드-AI 연동 테스트:
1. upsert 성공 시 delete_old_versions 호출 확인
2. upsert 실패 시 delete_old_versions 미호출 확인
3. job 상태 전이 확인 (queued → running → completed/failed)

요구사항 (prompt.txt):
- POST /internal/rag/index: 문서 인덱싱, 202 반환
- POST /internal/rag/delete: 문서 삭제
- GET /internal/jobs/{jobId}: 상태 폴링
- 새 버전 upsert 성공 후에만 이전 버전 삭제
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timezone

from app.models.internal_rag import (
    InternalRagIndexRequest,
    InternalRagIndexResponse,
    InternalRagDeleteRequest,
    InternalRagDeleteResponse,
    JobStatus,
    JobStatusResponse,
    DocumentChunk,
)
from app.services.indexing_service import IndexingService, clear_indexing_service
from app.services.job_service import JobService, JobEntry, clear_job_service


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_milvus_client():
    """Mock Milvus 클라이언트."""
    client = AsyncMock()
    client.generate_embedding = AsyncMock(return_value=[0.1] * 1536)
    client.upsert_chunks = AsyncMock(return_value=3)
    client.delete_by_document = AsyncMock(return_value=0)
    client.delete_old_versions = AsyncMock(return_value=2)
    client.get_document_chunk_count = AsyncMock(return_value=3)
    return client


@pytest.fixture
def mock_document_processor():
    """Mock DocumentProcessor."""
    processor = AsyncMock()
    processor.process = AsyncMock(return_value=[
        DocumentChunk(
            document_id="DOC-001",
            version_no=2,
            domain="POLICY",
            title="Test Document",
            chunk_id=0,  # 정수형 chunk_id
            chunk_text="This is test content for chunk 1.",
        ),
        DocumentChunk(
            document_id="DOC-001",
            version_no=2,
            domain="POLICY",
            title="Test Document",
            chunk_id=1,  # 정수형 chunk_id
            chunk_text="This is test content for chunk 2.",
        ),
        DocumentChunk(
            document_id="DOC-001",
            version_no=2,
            domain="POLICY",
            title="Test Document",
            chunk_id=2,  # 정수형 chunk_id
            chunk_text="This is test content for chunk 3.",
        ),
    ])
    return processor


@pytest.fixture
def job_service():
    """실제 JobService 인스턴스."""
    clear_job_service()
    return JobService()


@pytest.fixture
def indexing_service(mock_document_processor, mock_milvus_client, job_service):
    """테스트용 IndexingService."""
    clear_indexing_service()
    return IndexingService(
        document_processor=mock_document_processor,
        milvus_client=mock_milvus_client,
        job_service=job_service,
    )


@pytest.fixture
def index_request():
    """인덱싱 요청 fixture."""
    return InternalRagIndexRequest(
        document_id="DOC-001",
        version_no=2,
        title="Test Document",
        domain="POLICY",
        file_url="http://storage.example.com/documents/DOC-001.pdf",
        requested_by="user-001",
        job_id="job-001",
    )


# =============================================================================
# TestDeleteOldVersions: upsert 성공/실패 시 delete_old_versions 동작
# =============================================================================


class TestDeleteOldVersions:
    """이전 버전 삭제 로직 테스트."""

    @pytest.mark.asyncio
    async def test_delete_old_versions_called_on_upsert_success(
        self,
        indexing_service,
        mock_milvus_client,
        index_request,
        job_service,
    ):
        """upsert 성공 시 delete_old_versions가 호출되어야 함."""
        # Given: 정상적인 upsert
        mock_milvus_client.upsert_chunks = AsyncMock(return_value=3)

        # When: 인덱싱 요청
        response = await indexing_service.index_document(index_request)

        # Then: 202 Accepted 응답
        assert response.status == JobStatus.QUEUED

        # 파이프라인 완료 대기
        await asyncio.sleep(0.5)

        # Then: delete_old_versions가 호출됨
        mock_milvus_client.delete_old_versions.assert_called_once_with(
            "DOC-001", 2
        )

        # Then: job 상태가 COMPLETED
        status = await job_service.get_job_status("job-001")
        assert status is not None
        assert status.status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_delete_old_versions_not_called_on_upsert_failure(
        self,
        indexing_service,
        mock_milvus_client,
        mock_document_processor,
        index_request,
        job_service,
    ):
        """upsert 실패 시 delete_old_versions가 호출되지 않아야 함."""
        # Given: upsert 실패 설정
        mock_milvus_client.upsert_chunks = AsyncMock(
            side_effect=Exception("Milvus connection failed")
        )

        # When: 인덱싱 요청
        response = await indexing_service.index_document(index_request)

        # Then: 202 Accepted 응답 (비동기 처리)
        assert response.status == JobStatus.QUEUED

        # 파이프라인 완료 대기 (재시도 3회: 1+2+4초 = 7초 + 여유)
        await asyncio.sleep(10)

        # Then: delete_old_versions가 호출되지 않음
        mock_milvus_client.delete_old_versions.assert_not_called()

        # Then: job 상태가 FAILED
        status = await job_service.get_job_status("job-001")
        assert status is not None
        assert status.status == JobStatus.FAILED
        assert "Milvus connection failed" in status.error_message

    @pytest.mark.asyncio
    async def test_delete_old_versions_not_called_on_embedding_failure(
        self,
        indexing_service,
        mock_milvus_client,
        index_request,
        job_service,
    ):
        """임베딩 생성 실패 시 delete_old_versions가 호출되지 않아야 함."""
        # Given: 임베딩 생성 실패
        mock_milvus_client.generate_embedding = AsyncMock(
            side_effect=Exception("Embedding API failed")
        )

        # When
        await indexing_service.index_document(index_request)

        # 파이프라인 완료 대기 (재시도 포함)
        await asyncio.sleep(10)

        # Then: delete_old_versions 미호출
        mock_milvus_client.delete_old_versions.assert_not_called()

        # Then: job FAILED
        status = await job_service.get_job_status("job-001")
        assert status.status == JobStatus.FAILED

    @pytest.mark.asyncio
    async def test_delete_old_versions_not_called_on_download_failure(
        self,
        indexing_service,
        mock_milvus_client,
        mock_document_processor,
        index_request,
        job_service,
    ):
        """다운로드/처리 실패 시 delete_old_versions가 호출되지 않아야 함."""
        # Given: 다운로드 실패
        mock_document_processor.process = AsyncMock(
            side_effect=Exception("File download failed")
        )

        # When
        await indexing_service.index_document(index_request)

        # 파이프라인 완료 대기 (재시도 포함)
        await asyncio.sleep(10)

        # Then: delete_old_versions 미호출
        mock_milvus_client.delete_old_versions.assert_not_called()

        # Then: job FAILED
        status = await job_service.get_job_status("job-001")
        assert status.status == JobStatus.FAILED


# =============================================================================
# TestJobStatusTransition: job 상태 전이 테스트
# =============================================================================


class TestJobStatusTransition:
    """Job 상태 전이 테스트."""

    @pytest.mark.asyncio
    async def test_job_status_transitions_queued_to_running_to_completed(
        self,
        indexing_service,
        index_request,
        job_service,
    ):
        """job 상태가 queued → running → completed로 전이되어야 함."""
        # When: 인덱싱 요청
        response = await indexing_service.index_document(index_request)

        # Then: 초기 응답은 QUEUED
        assert response.status == JobStatus.QUEUED

        # 파이프라인 완료 대기
        await asyncio.sleep(1)

        # Then: 최종 상태는 COMPLETED
        final_status = await job_service.get_job_status("job-001")
        assert final_status is not None
        assert final_status.status == JobStatus.COMPLETED

        # Then: progress가 "completed"로 설정됨
        assert final_status.progress == "completed"

    @pytest.mark.asyncio
    async def test_job_status_transitions_to_failed(
        self,
        indexing_service,
        mock_milvus_client,
        index_request,
        job_service,
    ):
        """실패 시 job 상태가 queued → running → failed로 전이되어야 함."""
        # Given: upsert 실패
        mock_milvus_client.upsert_chunks = AsyncMock(
            side_effect=Exception("Upsert failed")
        )

        observed_statuses = []

        async def record_status():
            while True:
                status = await job_service.get_job_status("job-001")
                if status:
                    if not observed_statuses or observed_statuses[-1] != status.status:
                        observed_statuses.append(status.status)
                    if status.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                        break
                await asyncio.sleep(0.05)

        # When
        await indexing_service.index_document(index_request)

        monitor_task = asyncio.create_task(record_status())
        await asyncio.wait_for(monitor_task, timeout=10.0)

        # Then: FAILED로 끝남
        assert JobStatus.FAILED in observed_statuses
        assert observed_statuses[-1] == JobStatus.FAILED


# =============================================================================
# TestJobService: JobService 단위 테스트
# =============================================================================


class TestJobService:
    """JobService 단위 테스트."""

    @pytest.mark.asyncio
    async def test_create_job_status_queued(self, job_service):
        """새 job 생성 시 상태가 QUEUED여야 함."""
        # When
        job = await job_service.create_job(
            job_id="job-test-001",
            document_id="DOC-001",
            version_no=1,
            job_type="index",
        )

        # Then
        assert job.status == JobStatus.QUEUED
        assert job.document_id == "DOC-001"
        assert job.version_no == 1

    @pytest.mark.asyncio
    async def test_mark_running(self, job_service):
        """mark_running 호출 시 상태가 RUNNING으로 변경."""
        # Given
        await job_service.create_job("job-002", "DOC-001", 1)

        # When
        await job_service.mark_running("job-002", "downloading")

        # Then
        status = await job_service.get_job_status("job-002")
        assert status.status == JobStatus.RUNNING
        assert status.progress == "downloading"

    @pytest.mark.asyncio
    async def test_mark_completed(self, job_service):
        """mark_completed 호출 시 상태가 COMPLETED로 변경."""
        # Given
        await job_service.create_job("job-003", "DOC-001", 1)
        await job_service.mark_running("job-003", "upserting")

        # When
        await job_service.mark_completed("job-003", chunks_processed=10)

        # Then
        status = await job_service.get_job_status("job-003")
        assert status.status == JobStatus.COMPLETED
        assert status.chunks_processed == 10

    @pytest.mark.asyncio
    async def test_mark_failed(self, job_service):
        """mark_failed 호출 시 상태가 FAILED로 변경."""
        # Given
        await job_service.create_job("job-004", "DOC-001", 1)
        await job_service.mark_running("job-004", "embedding")

        # When
        await job_service.mark_failed("job-004", "Embedding generation failed")

        # Then
        status = await job_service.get_job_status("job-004")
        assert status.status == JobStatus.FAILED
        assert "Embedding generation failed" in status.error_message

    @pytest.mark.asyncio
    async def test_get_job_status_not_found(self, job_service):
        """존재하지 않는 job 조회 시 None 반환."""
        # When
        status = await job_service.get_job_status("non-existent-job")

        # Then
        assert status is None


# =============================================================================
# TestInternalRagAPI: API 엔드포인트 테스트
# =============================================================================


class TestInternalRagAPI:
    """Internal RAG API 엔드포인트 테스트."""

    @pytest.mark.asyncio
    async def test_index_endpoint_returns_202(self):
        """POST /internal/rag/index가 202 반환."""
        from fastapi.testclient import TestClient
        from app.main import app

        with patch("app.api.v1.internal_rag.get_indexing_service") as mock_get_svc:
            mock_service = AsyncMock()
            mock_service.index_document = AsyncMock(
                return_value=InternalRagIndexResponse(
                    job_id="job-api-001",
                    status=JobStatus.QUEUED,
                    message="Indexing job queued",
                )
            )
            mock_get_svc.return_value = mock_service

            client = TestClient(app)
            response = client.post(
                "/internal/rag/index",
                json={
                    "documentId": "DOC-001",
                    "versionNo": 2,
                    "title": "Test",
                    "domain": "POLICY",
                    "fileUrl": "http://example.com/test.pdf",
                    "requestedBy": "user-001",
                    "jobId": "job-api-001",
                },
            )

            assert response.status_code == 202
            data = response.json()
            assert data["jobId"] == "job-api-001"
            assert data["status"] == "queued"

    @pytest.mark.asyncio
    async def test_job_status_endpoint_returns_status(self):
        """GET /internal/jobs/{job_id}가 job 상태 반환."""
        from fastapi.testclient import TestClient
        from app.main import app

        with patch("app.api.v1.internal_rag.get_job_service") as mock_get_svc:
            mock_service = AsyncMock()
            mock_service.get_job_status = AsyncMock(
                return_value=JobStatusResponse(
                    job_id="job-status-001",
                    status=JobStatus.RUNNING,
                    document_id="DOC-001",
                    version_no=2,
                    progress="embedding",
                    created_at=datetime.now(timezone.utc).isoformat(),
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            mock_get_svc.return_value = mock_service

            client = TestClient(app)
            response = client.get("/internal/jobs/job-status-001")

            assert response.status_code == 200
            data = response.json()
            assert data["jobId"] == "job-status-001"
            assert data["status"] == "running"
            assert data["progress"] == "embedding"

    @pytest.mark.asyncio
    async def test_job_status_endpoint_returns_404(self):
        """존재하지 않는 job 조회 시 404 반환."""
        from fastapi.testclient import TestClient
        from app.main import app

        with patch("app.api.v1.internal_rag.get_job_service") as mock_get_svc:
            mock_service = AsyncMock()
            mock_service.get_job_status = AsyncMock(return_value=None)
            mock_get_svc.return_value = mock_service

            client = TestClient(app)
            response = client.get("/internal/jobs/non-existent")

            assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_endpoint(self):
        """POST /internal/rag/delete가 삭제 결과 반환."""
        from fastapi.testclient import TestClient
        from app.main import app

        with patch("app.api.v1.internal_rag.get_indexing_service") as mock_get_svc:
            mock_service = AsyncMock()
            mock_service.delete_document = AsyncMock(
                return_value=InternalRagDeleteResponse(
                    job_id="job-del-001",
                    status=JobStatus.COMPLETED,
                    deleted_count=5,
                    message="Deleted 5 chunks",
                )
            )
            mock_get_svc.return_value = mock_service

            client = TestClient(app)
            response = client.post(
                "/internal/rag/delete",
                json={
                    "documentId": "DOC-001",
                    "jobId": "job-del-001",
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["deletedCount"] == 5


# =============================================================================
# TestVersionDeletionOrder: 버전 삭제 순서 테스트
# =============================================================================


class TestVersionDeletionOrder:
    """버전 삭제 순서 테스트: upsert 성공 후에만 이전 버전 삭제."""

    @pytest.mark.asyncio
    async def test_delete_old_versions_after_upsert_in_correct_order(
        self,
        indexing_service,
        mock_milvus_client,
        index_request,
    ):
        """delete_old_versions는 upsert_chunks 이후에 호출되어야 함."""
        # Given: 호출 순서 기록
        call_order = []

        original_upsert = mock_milvus_client.upsert_chunks
        original_delete_old = mock_milvus_client.delete_old_versions

        async def track_upsert(*args, **kwargs):
            call_order.append("upsert_chunks")
            return await original_upsert(*args, **kwargs)

        async def track_delete_old(*args, **kwargs):
            call_order.append("delete_old_versions")
            return await original_delete_old(*args, **kwargs)

        mock_milvus_client.upsert_chunks = track_upsert
        mock_milvus_client.delete_old_versions = track_delete_old

        # When
        await indexing_service.index_document(index_request)

        # 파이프라인 완료 대기
        await asyncio.sleep(0.5)

        # Then: upsert가 delete_old_versions보다 먼저 호출됨
        assert "upsert_chunks" in call_order
        assert "delete_old_versions" in call_order
        assert call_order.index("upsert_chunks") < call_order.index("delete_old_versions")

    @pytest.mark.asyncio
    async def test_delete_old_versions_uses_correct_version_no(
        self,
        indexing_service,
        mock_milvus_client,
        job_service,
    ):
        """delete_old_versions가 올바른 version_no로 호출되어야 함."""
        # Given: version_no = 5인 요청
        request = InternalRagIndexRequest(
            document_id="DOC-VERSION-TEST",
            version_no=5,
            title="Version Test",
            domain="POLICY",
            file_url="http://example.com/test.pdf",
            requested_by="user-001",
            job_id="job-version-test",
        )

        # When
        await indexing_service.index_document(request)
        await asyncio.sleep(0.5)

        # Then: delete_old_versions(document_id, 5) 호출 확인
        mock_milvus_client.delete_old_versions.assert_called_once_with(
            "DOC-VERSION-TEST", 5
        )
