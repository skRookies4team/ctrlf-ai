"""
Indexing Service (Phase 25)

문서 인덱싱 전체 파이프라인을 오케스트레이션하는 서비스입니다.

파이프라인:
1. 파일 다운로드
2. 텍스트 추출/청킹
3. 임베딩 생성
4. Milvus upsert
5. 이전 버전 삭제 (성공 시에만)

재시도 정책:
- 각 단계별 최대 3회 재시도
- 백오프: 1초, 2초, 4초
- 실패 시 작업을 FAILED로 표시
"""

import asyncio
from typing import Any, Dict, List, Optional

from app.clients.milvus_client import MilvusSearchClient, get_milvus_client, MilvusError
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.internal_rag import (
    DocumentChunk,
    InternalRagIndexRequest,
    InternalRagIndexResponse,
    InternalRagDeleteRequest,
    InternalRagDeleteResponse,
    JobStatus,
)
from app.services.document_processor import (
    DocumentProcessor,
    DocumentProcessingError,
    get_document_processor,
)
from app.services.job_service import JobService, get_job_service

logger = get_logger(__name__)


# =============================================================================
# Indexing Service
# =============================================================================


class IndexingService:
    """
    문서 인덱싱 서비스.

    문서 다운로드 → 텍스트 추출 → 청킹 → 임베딩 → Milvus upsert 파이프라인을 수행합니다.

    Example:
        service = IndexingService()
        response = await service.index_document(request)
    """

    def __init__(
        self,
        document_processor: Optional[DocumentProcessor] = None,
        milvus_client: Optional[MilvusSearchClient] = None,
        job_service: Optional[JobService] = None,
    ) -> None:
        """
        IndexingService 초기화.

        Args:
            document_processor: DocumentProcessor 인스턴스
            milvus_client: MilvusSearchClient 인스턴스
            job_service: JobService 인스턴스
        """
        self._processor = document_processor or get_document_processor()
        self._milvus = milvus_client or get_milvus_client()
        self._job_service = job_service or get_job_service()

        # 재시도 설정 로드
        settings = get_settings()
        self._max_retries = settings.INDEX_RETRY_MAX_ATTEMPTS
        self._backoff_seconds = [
            float(s.strip())
            for s in settings.INDEX_RETRY_BACKOFF_SECONDS.split(",")
        ]

        logger.info(
            f"IndexingService initialized: max_retries={self._max_retries}, "
            f"backoff={self._backoff_seconds}"
        )

    async def index_document(
        self, request: InternalRagIndexRequest
    ) -> InternalRagIndexResponse:
        """
        문서를 인덱싱합니다.

        Args:
            request: 인덱싱 요청

        Returns:
            InternalRagIndexResponse: 인덱싱 응답
        """
        job_id = request.job_id
        document_id = request.document_id
        version_no = request.version_no

        logger.info(
            f"Starting indexing: job_id={job_id}, document_id={document_id}, "
            f"version_no={version_no}"
        )

        # 작업 생성
        await self._job_service.create_job(
            job_id=job_id,
            document_id=document_id,
            version_no=version_no,
            job_type="index",
        )

        # 비동기로 인덱싱 실행 (fire-and-forget)
        asyncio.create_task(
            self._run_indexing_pipeline(request)
        )

        return InternalRagIndexResponse(
            job_id=job_id,
            status=JobStatus.QUEUED,
            message=f"Indexing job {job_id} has been queued",
        )

    async def _run_indexing_pipeline(
        self, request: InternalRagIndexRequest
    ) -> None:
        """
        인덱싱 파이프라인을 실행합니다.

        단계:
        1. downloading: 파일 다운로드 및 텍스트 추출/청킹
        2. embedding: 임베딩 생성
        3. upserting: Milvus에 upsert
        4. cleaning: 이전 버전 삭제

        Args:
            request: 인덱싱 요청
        """
        job_id = request.job_id
        document_id = request.document_id
        version_no = request.version_no

        try:
            # Step 1: 실행 중 표시
            await self._job_service.mark_running(job_id, "downloading")

            # Step 2: 파일 다운로드 및 텍스트 추출/청킹 (재시도 포함)
            chunks = await self._retry_with_backoff(
                self._download_and_process,
                request,
                stage="downloading",
                job_id=job_id,
            )

            if not chunks:
                await self._job_service.mark_failed(
                    job_id, "No content extracted from document", "extracting"
                )
                return

            logger.info(f"Extracted {len(chunks)} chunks from document")
            await self._job_service.update_job(
                job_id, progress="embedding", chunks_processed=len(chunks)
            )

            # Step 3: 임베딩 생성 (재시도 포함)
            chunks_with_embeddings = await self._retry_with_backoff(
                self._generate_embeddings,
                chunks,
                stage="embedding",
                job_id=job_id,
            )

            await self._job_service.update_job(job_id, progress="upserting")

            # Step 4: 기존 동일 버전 청크 삭제 (idempotency)
            await self._milvus.delete_by_document(document_id, version_no)

            # Step 5: Milvus에 upsert (재시도 포함)
            chunk_dicts = [self._chunk_to_dict(c) for c in chunks_with_embeddings]
            upserted_count = await self._retry_with_backoff(
                self._milvus.upsert_chunks,
                chunk_dicts,
                stage="upserting",
                job_id=job_id,
            )

            logger.info(f"Upserted {upserted_count} chunks to Milvus")

            # Step 6: 이전 버전 삭제 (새 버전 성공 후에만)
            await self._job_service.update_job(job_id, progress="cleaning")
            await self._milvus.delete_old_versions(document_id, version_no)

            # Step 7: 완료
            await self._job_service.mark_completed(job_id, len(chunks_with_embeddings))

            logger.info(
                f"Indexing completed: job_id={job_id}, document_id={document_id}, "
                f"version_no={version_no}, chunks={len(chunks_with_embeddings)}"
            )

        except Exception as e:
            error_msg = f"Indexing failed: {type(e).__name__}: {str(e)}"
            logger.exception(error_msg)
            await self._job_service.mark_failed(job_id, error_msg)

    async def _download_and_process(
        self, request: InternalRagIndexRequest
    ) -> List[DocumentChunk]:
        """파일 다운로드 및 처리."""
        return await self._processor.process(
            file_url=request.file_url,
            document_id=request.document_id,
            version_no=request.version_no,
            domain=request.domain,
            title=request.title,
        )

    async def _generate_embeddings(
        self, chunks: List[DocumentChunk]
    ) -> List[DocumentChunk]:
        """청크들의 임베딩을 생성합니다."""
        chunks_with_embeddings = []

        for chunk in chunks:
            embedding = await self._milvus.generate_embedding(chunk.chunk_text)
            chunk.embedding = embedding
            chunks_with_embeddings.append(chunk)

        return chunks_with_embeddings

    def _chunk_to_dict(self, chunk: DocumentChunk) -> Dict[str, Any]:
        """DocumentChunk를 dict로 변환합니다."""
        return {
            "document_id": chunk.document_id,
            "version_no": chunk.version_no,
            "domain": chunk.domain,
            "title": chunk.title,
            "chunk_id": chunk.chunk_id,
            "chunk_text": chunk.chunk_text,
            "embedding": chunk.embedding,
            "page": chunk.page,
            "section_path": chunk.section_path,
        }

    async def _retry_with_backoff(
        self,
        func,
        *args,
        stage: str,
        job_id: str,
        **kwargs,
    ):
        """
        재시도 로직을 적용하여 함수를 실행합니다.

        Args:
            func: 실행할 함수 (async)
            *args: 함수 인자
            stage: 현재 단계 (로깅용)
            job_id: 작업 ID (로깅용)
            **kwargs: 함수 키워드 인자

        Returns:
            함수 반환값

        Raises:
            Exception: 모든 재시도 실패 시
        """
        last_error = None

        for attempt in range(self._max_retries):
            try:
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                else:
                    return func(*args, **kwargs)

            except Exception as e:
                last_error = e
                backoff = self._backoff_seconds[min(attempt, len(self._backoff_seconds) - 1)]

                logger.warning(
                    f"Retry {attempt + 1}/{self._max_retries} for {stage}: {e}. "
                    f"Waiting {backoff}s..."
                )

                await self._job_service.update_job(
                    job_id,
                    log_message=f"Retry {attempt + 1}/{self._max_retries}: {str(e)[:100]}",
                )

                if attempt < self._max_retries - 1:
                    await asyncio.sleep(backoff)

        # 모든 재시도 실패
        raise last_error

    async def delete_document(
        self, request: InternalRagDeleteRequest
    ) -> InternalRagDeleteResponse:
        """
        문서를 Milvus에서 삭제합니다.

        Args:
            request: 삭제 요청

        Returns:
            InternalRagDeleteResponse: 삭제 응답
        """
        document_id = request.document_id
        version_no = request.version_no
        job_id = request.job_id

        logger.info(
            f"Deleting document: document_id={document_id}, version_no={version_no}"
        )

        try:
            # 삭제 전 청크 수 조회
            count_before = await self._milvus.get_document_chunk_count(
                document_id, version_no
            )

            # Milvus에서 삭제
            deleted_count = await self._milvus.delete_by_document(
                document_id, version_no
            )

            # 실제 삭제 수는 count_before 사용 (Milvus가 정확한 수를 반환하지 않을 수 있음)
            actual_deleted = count_before if deleted_count == 0 else deleted_count

            logger.info(
                f"Document deleted: document_id={document_id}, "
                f"version_no={version_no}, deleted_count={actual_deleted}"
            )

            return InternalRagDeleteResponse(
                job_id=job_id,
                status=JobStatus.COMPLETED,
                deleted_count=actual_deleted,
                message=f"Deleted {actual_deleted} chunks for document {document_id}",
            )

        except MilvusError as e:
            logger.error(f"Delete failed: {e}")
            return InternalRagDeleteResponse(
                job_id=job_id,
                status=JobStatus.FAILED,
                deleted_count=0,
                message=f"Delete failed: {str(e)}",
            )

        except Exception as e:
            logger.exception("Unexpected error during delete")
            return InternalRagDeleteResponse(
                job_id=job_id,
                status=JobStatus.FAILED,
                deleted_count=0,
                message=f"Unexpected error: {str(e)}",
            )


# =============================================================================
# 싱글턴 인스턴스
# =============================================================================

_indexing_service: Optional[IndexingService] = None


def get_indexing_service() -> IndexingService:
    """IndexingService 싱글턴 인스턴스를 반환합니다."""
    global _indexing_service
    if _indexing_service is None:
        _indexing_service = IndexingService()
    return _indexing_service


def clear_indexing_service() -> None:
    """IndexingService 싱글턴 인스턴스를 제거합니다 (테스트용)."""
    global _indexing_service
    _indexing_service = None
