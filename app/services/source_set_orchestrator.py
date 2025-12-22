"""
SourceSet 오케스트레이터 서비스 (Phase 3)

멀티 문서 소스셋을 처리하고 스크립트를 자동 생성하는 오케스트레이션 서비스입니다.

흐름:
1. Spring → FastAPI: POST /internal/ai/source-sets/{sourceSetId}/start
2. FastAPI → Spring: GET /internal/source-sets/{sourceSetId}/documents
3. FastAPI → RAGFlow: 문서별 ingest 요청 (프록시)
4. RAGFlow → FastAPI: 처리 완료 (청크 + 임베딩)
5. FastAPI → Milvus: 벡터 저장
6. FastAPI → Spring: POST /internal/rag/documents/{docId}/chunks:bulk
7. FastAPI → LLM: 스크립트 생성
8. FastAPI → Spring: POST /internal/callbacks/source-sets/{sourceSetId}/complete

상태 머신 (DB: education.source_set.status):
- CREATED → LOCKED → SCRIPT_READY | FAILED

핵심 원칙:
- /start는 멱등: 같은 sourceSetId에 이미 LOCKED 이상이면 202/409
- FastAPI = RAGFlow 오케스트레이터 (직접 처리 X)
- 임베딩 벡터는 Milvus, DB는 chunk_text만 저장
- 콜백은 멱등 (upsert)
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from app.clients.backend_client import (
    BackendClient,
    ChunkBulkUpsertError,
    SourceSetCompleteCallbackError,
    SourceSetDocumentsFetchError,
    get_backend_client,
)
from app.clients.ragflow_client import RagflowClient
from app.core.logging import get_logger
from app.models.source_set import (
    ChunkBulkUpsertRequest,
    ChunkItem,
    DocumentResult,
    DocumentStatus,
    FailChunkBulkUpsertRequest,
    FailChunkItem,
    GeneratedChapter,
    GeneratedScene,
    GeneratedScript,
    SourceRef,
    SourceSetCompleteRequest,
    SourceSetDocument,
    SourceSetStartRequest,
    SourceSetStartResponse,
    SourceSetStatus,
)

logger = get_logger(__name__)


# =============================================================================
# Internal State
# =============================================================================


class ProcessingStatus(str, Enum):
    """내부 처리 상태."""
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class ProcessingJob:
    """소스셋 처리 작업 상태."""
    source_set_id: str
    video_id: str
    education_id: Optional[str]
    request_id: Optional[str]
    trace_id: Optional[str]
    script_policy_id: Optional[str]
    llm_model_hint: Optional[str]
    status: ProcessingStatus = ProcessingStatus.PENDING
    documents: List[SourceSetDocument] = field(default_factory=list)
    document_results: List[DocumentResult] = field(default_factory=list)
    generated_script: Optional[GeneratedScript] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class DocumentProcessingResult:
    """문서 처리 결과."""
    document_id: str
    success: bool
    chunks_count: int = 0
    fail_chunks_count: int = 0
    fail_reason: Optional[str] = None


# =============================================================================
# SourceSet Orchestrator
# =============================================================================


class SourceSetOrchestrator:
    """소스셋 오케스트레이터 서비스.

    멀티 문서 소스셋을 RAGFlow를 통해 처리하고
    스크립트를 자동 생성하는 오케스트레이션 로직을 담당합니다.

    Attributes:
        _backend_client: 백엔드 API 클라이언트
        _ragflow_client: RAGFlow API 클라이언트
        _processing_jobs: 진행 중인 작업 상태 (in-memory)
        _running_tasks: 비동기 태스크 관리
    """

    def __init__(
        self,
        backend_client: Optional[BackendClient] = None,
        ragflow_client: Optional[RagflowClient] = None,
    ):
        """초기화.

        Args:
            backend_client: 백엔드 클라이언트 (None이면 싱글톤 사용)
            ragflow_client: RAGFlow 클라이언트 (None이면 새로 생성)
        """
        self._backend_client = backend_client or get_backend_client()
        self._ragflow_client = ragflow_client or RagflowClient()
        self._processing_jobs: Dict[str, ProcessingJob] = {}
        self._running_tasks: Dict[str, asyncio.Task] = {}

    # =========================================================================
    # Public API
    # =========================================================================

    async def start(
        self,
        source_set_id: str,
        request: SourceSetStartRequest,
    ) -> SourceSetStartResponse:
        """소스셋 처리를 시작합니다.

        POST /internal/ai/source-sets/{sourceSetId}/start

        Args:
            source_set_id: 소스셋 ID
            request: 시작 요청

        Returns:
            SourceSetStartResponse: 접수 응답 (202)

        Note:
            - 멱등성: 이미 처리 중이면 기존 상태 반환
            - 비동기: 즉시 202 반환 후 백그라운드에서 처리
        """
        # 1. 멱등성 체크: 이미 처리 중인 경우
        if source_set_id in self._processing_jobs:
            job = self._processing_jobs[source_set_id]
            logger.info(
                f"SourceSet already processing: source_set_id={source_set_id}, "
                f"status={job.status}"
            )
            return SourceSetStartResponse(
                received=True,
                source_set_id=source_set_id,
                status=SourceSetStatus.LOCKED,
            )

        # 2. 작업 생성
        job = ProcessingJob(
            source_set_id=source_set_id,
            video_id=request.video_id,
            education_id=request.education_id,
            request_id=request.request_id,
            trace_id=request.trace_id,
            script_policy_id=request.script_policy_id,
            llm_model_hint=request.llm_model_hint,
            status=ProcessingStatus.PROCESSING,
        )
        self._processing_jobs[source_set_id] = job

        logger.info(
            f"Starting source set processing: source_set_id={source_set_id}, "
            f"video_id={request.video_id}"
        )

        # 3. 백그라운드에서 처리 시작 (fire-and-forget)
        task = asyncio.create_task(self._process_source_set(source_set_id))
        self._running_tasks[source_set_id] = task

        # 4. 즉시 202 반환
        return SourceSetStartResponse(
            received=True,
            source_set_id=source_set_id,
            status=SourceSetStatus.LOCKED,
        )

    def get_job_status(self, source_set_id: str) -> Optional[ProcessingJob]:
        """작업 상태를 조회합니다.

        Args:
            source_set_id: 소스셋 ID

        Returns:
            ProcessingJob 또는 None
        """
        return self._processing_jobs.get(source_set_id)

    # =========================================================================
    # Background Processing
    # =========================================================================

    async def _process_source_set(self, source_set_id: str) -> None:
        """소스셋 처리 파이프라인 (백그라운드).

        1. 문서 목록 조회
        2. 각 문서 RAGFlow로 처리
        3. 스크립트 생성
        4. 완료 콜백 전송
        """
        job = self._processing_jobs.get(source_set_id)
        if not job:
            logger.error(f"Job not found: source_set_id={source_set_id}")
            return

        try:
            # 1. 문서 목록 조회
            logger.info(f"Fetching documents: source_set_id={source_set_id}")
            documents_response = await self._backend_client.get_source_set_documents(
                source_set_id
            )
            job.documents = documents_response.documents

            if not job.documents:
                logger.warning(f"No documents in source set: {source_set_id}")
                await self._send_failure_callback(
                    job,
                    error_code="NO_DOCUMENTS",
                    error_message="소스셋에 문서가 없습니다.",
                )
                return

            logger.info(
                f"Found {len(job.documents)} documents: source_set_id={source_set_id}"
            )

            # 2. 각 문서 처리
            all_chunks: List[Dict[str, Any]] = []
            document_results: List[DocumentResult] = []
            has_failure = False

            for doc in job.documents:
                try:
                    result = await self._process_document(source_set_id, doc, job)
                    document_results.append(
                        DocumentResult(
                            document_id=doc.document_id,
                            status="COMPLETED" if result.success else "FAILED",
                            fail_reason=result.fail_reason,
                        )
                    )
                    if not result.success:
                        has_failure = True
                except Exception as e:
                    logger.error(
                        f"Document processing failed: doc_id={doc.document_id}, error={e}"
                    )
                    document_results.append(
                        DocumentResult(
                            document_id=doc.document_id,
                            status="FAILED",
                            fail_reason=str(e)[:200],
                        )
                    )
                    has_failure = True

            job.document_results = document_results

            # 3. 하나라도 실패했으면 전체 실패 처리
            if has_failure:
                await self._send_failure_callback(
                    job,
                    error_code="DOCUMENT_PROCESSING_FAILED",
                    error_message="하나 이상의 문서 처리에 실패했습니다.",
                )
                return

            # 4. 스크립트 생성
            logger.info(f"Generating script: source_set_id={source_set_id}")
            script = await self._generate_script(job)
            job.generated_script = script

            # 5. 성공 콜백 전송
            await self._send_success_callback(job)

            # 6. 상태 업데이트
            job.status = ProcessingStatus.COMPLETED
            job.updated_at = datetime.utcnow()

            logger.info(
                f"Source set processing completed: source_set_id={source_set_id}"
            )

        except SourceSetDocumentsFetchError as e:
            logger.error(f"Failed to fetch documents: {e}")
            job.status = ProcessingStatus.FAILED
            job.error_code = e.error_code
            job.error_message = e.message
            await self._send_failure_callback(
                job,
                error_code=e.error_code,
                error_message=e.message,
            )

        except Exception as e:
            logger.exception(f"Source set processing failed: source_set_id={source_set_id}")
            job.status = ProcessingStatus.FAILED
            job.error_code = "PROCESSING_ERROR"
            job.error_message = str(e)[:200]
            await self._send_failure_callback(
                job,
                error_code="PROCESSING_ERROR",
                error_message=str(e)[:200],
            )

        finally:
            # 태스크 정리
            if source_set_id in self._running_tasks:
                del self._running_tasks[source_set_id]

    async def _process_document(
        self,
        source_set_id: str,
        doc: SourceSetDocument,
        job: ProcessingJob,
    ) -> DocumentProcessingResult:
        """개별 문서를 RAGFlow로 처리합니다.

        1. RAGFlow에 ingest 요청
        2. 처리 결과 (청크) 수신
        3. Milvus에 벡터 저장 (RAGFlow가 처리)
        4. Spring DB에 chunk_text 저장

        Args:
            source_set_id: 소스셋 ID
            doc: 처리할 문서
            job: 처리 작업 상태

        Returns:
            DocumentProcessingResult: 처리 결과
        """
        logger.info(
            f"Processing document: source_set_id={source_set_id}, "
            f"doc_id={doc.document_id}"
        )

        try:
            # 1. RAGFlow에 문서 처리 요청
            from app.models.rag import RagProcessRequest

            rag_request = RagProcessRequest(
                doc_id=doc.document_id,
                file_url=doc.source_url,
                domain=doc.domain,
            )

            response = await self._ragflow_client.process_document_request(rag_request)

            if not response.success:
                logger.warning(
                    f"RAGFlow processing failed: doc_id={doc.document_id}, "
                    f"message={response.message}"
                )
                return DocumentProcessingResult(
                    document_id=doc.document_id,
                    success=False,
                    fail_reason=response.message,
                )

            # 2. 청크 텍스트를 Spring DB에 저장
            # NOTE: RAGFlow가 처리 완료 시 청크 정보를 반환하거나,
            # 별도의 API로 청크를 조회해야 함.
            # 현재는 RAGFlow 응답에 청크 정보가 없으므로 스킵.
            # 실제 구현 시 RAGFlow의 청크 반환 API 연동 필요.

            # 임시: 청크 저장 시뮬레이션
            # TODO: RAGFlow에서 청크 정보 반환 시 아래 로직 활성화
            # chunks = await self._get_chunks_from_ragflow(doc.document_id)
            # await self._save_chunks_to_backend(doc.document_id, chunks, job)

            logger.info(
                f"Document processed via RAGFlow: doc_id={doc.document_id}"
            )

            return DocumentProcessingResult(
                document_id=doc.document_id,
                success=True,
                chunks_count=0,  # TODO: 실제 청크 수
            )

        except Exception as e:
            logger.error(
                f"Document processing error: doc_id={doc.document_id}, error={e}"
            )
            return DocumentProcessingResult(
                document_id=doc.document_id,
                success=False,
                fail_reason=str(e)[:200],
            )

    async def _save_chunks_to_backend(
        self,
        document_id: str,
        chunks: List[Dict[str, Any]],
        job: ProcessingJob,
    ) -> None:
        """청크 텍스트를 백엔드 DB에 저장합니다.

        Args:
            document_id: 문서 ID
            chunks: 청크 리스트
            job: 처리 작업 상태
        """
        if not chunks:
            return

        chunk_items = [
            ChunkItem(
                chunk_index=chunk.get("chunk_index", idx),
                chunk_text=chunk.get("chunk_text", ""),
                chunk_meta=chunk.get("chunk_meta"),
            )
            for idx, chunk in enumerate(chunks)
        ]

        request = ChunkBulkUpsertRequest(
            chunks=chunk_items,
            request_id=job.request_id,
        )

        try:
            await self._backend_client.bulk_upsert_chunks(document_id, request)
            logger.info(
                f"Chunks saved to backend: doc_id={document_id}, count={len(chunks)}"
            )
        except ChunkBulkUpsertError as e:
            logger.error(f"Failed to save chunks: doc_id={document_id}, error={e}")
            raise

    async def _save_fail_chunks_to_backend(
        self,
        document_id: str,
        fail_chunks: List[Dict[str, Any]],
        job: ProcessingJob,
    ) -> None:
        """임베딩 실패 로그를 백엔드 DB에 저장합니다.

        Args:
            document_id: 문서 ID
            fail_chunks: 실패 청크 리스트
            job: 처리 작업 상태
        """
        if not fail_chunks:
            return

        fail_items = [
            FailChunkItem(
                chunk_index=fc.get("chunk_index", idx),
                fail_reason=fc.get("fail_reason", "UNKNOWN"),
            )
            for idx, fc in enumerate(fail_chunks)
        ]

        request = FailChunkBulkUpsertRequest(
            fails=fail_items,
            request_id=job.request_id,
        )

        try:
            await self._backend_client.bulk_upsert_fail_chunks(document_id, request)
            logger.info(
                f"Fail chunks saved: doc_id={document_id}, count={len(fail_chunks)}"
            )
        except Exception as e:
            logger.error(f"Failed to save fail chunks: doc_id={document_id}, error={e}")
            # 실패 로그 저장 실패는 전체 처리를 중단하지 않음

    # =========================================================================
    # Script Generation
    # =========================================================================

    async def _generate_script(self, job: ProcessingJob) -> GeneratedScript:
        """스크립트를 생성합니다.

        TODO: 실제 LLM 연동 구현 필요
        현재는 더미 스크립트 반환

        Args:
            job: 처리 작업 상태

        Returns:
            GeneratedScript: 생성된 스크립트
        """
        # TODO: 실제 구현 시 기존 video_script_generation_service 활용 또는
        # LLM 직접 호출하여 스크립트 생성

        script_id = f"script-{uuid.uuid4().hex[:12]}"

        # 더미 스크립트 생성
        scenes = [
            GeneratedScene(
                scene_id=f"scene-{uuid.uuid4().hex[:8]}",
                scene_index=1,
                purpose="도입",
                narration="안녕하세요. 오늘은 법정의무교육에 대해 알아보겠습니다.",
                caption="법정의무교육 소개",
                visual="타이틀 슬라이드",
                duration_sec=15.0,
                confidence_score=0.85,
                source_refs=[
                    SourceRef(
                        document_id=job.documents[0].document_id if job.documents else "unknown",
                        chunk_index=0,
                    )
                ],
            ),
        ]

        chapters = [
            GeneratedChapter(
                chapter_id=f"chapter-{uuid.uuid4().hex[:8]}",
                chapter_index=1,
                title="도입",
                duration_sec=15.0,
                scenes=scenes,
            ),
        ]

        script = GeneratedScript(
            script_id=script_id,
            education_id=job.education_id,
            source_set_id=job.source_set_id,
            title="법정의무교육 스크립트",
            total_duration_sec=15.0,
            version=1,
            llm_model=job.llm_model_hint or "default",
            chapters=chapters,
        )

        logger.info(f"Script generated: script_id={script_id}")

        return script

    # =========================================================================
    # Callbacks
    # =========================================================================

    async def _send_success_callback(self, job: ProcessingJob) -> None:
        """성공 콜백을 백엔드에 전송합니다.

        Args:
            job: 처리 작업 상태
        """
        request = SourceSetCompleteRequest(
            video_id=job.video_id,
            status="COMPLETED",
            source_set_status="SCRIPT_READY",
            documents=job.document_results,
            script=job.generated_script,
            request_id=job.request_id,
            trace_id=job.trace_id,
        )

        try:
            await self._backend_client.notify_source_set_complete(
                job.source_set_id, request
            )
            logger.info(
                f"Success callback sent: source_set_id={job.source_set_id}"
            )
        except SourceSetCompleteCallbackError as e:
            logger.error(
                f"Failed to send success callback: source_set_id={job.source_set_id}, "
                f"error={e}"
            )

    async def _send_failure_callback(
        self,
        job: ProcessingJob,
        error_code: str,
        error_message: str,
    ) -> None:
        """실패 콜백을 백엔드에 전송합니다.

        Args:
            job: 처리 작업 상태
            error_code: 에러 코드
            error_message: 에러 메시지
        """
        job.status = ProcessingStatus.FAILED
        job.error_code = error_code
        job.error_message = error_message
        job.updated_at = datetime.utcnow()

        request = SourceSetCompleteRequest(
            video_id=job.video_id,
            status="FAILED",
            source_set_status="FAILED",
            documents=job.document_results,
            script=None,
            error_code=error_code,
            error_message=error_message,
            request_id=job.request_id,
            trace_id=job.trace_id,
        )

        try:
            await self._backend_client.notify_source_set_complete(
                job.source_set_id, request
            )
            logger.info(
                f"Failure callback sent: source_set_id={job.source_set_id}, "
                f"error_code={error_code}"
            )
        except SourceSetCompleteCallbackError as e:
            logger.error(
                f"Failed to send failure callback: source_set_id={job.source_set_id}, "
                f"error={e}"
            )


# =============================================================================
# Singleton Instance
# =============================================================================


_orchestrator: Optional[SourceSetOrchestrator] = None


def get_source_set_orchestrator() -> SourceSetOrchestrator:
    """SourceSetOrchestrator 싱글톤 인스턴스 반환."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = SourceSetOrchestrator()
    return _orchestrator


def clear_source_set_orchestrator() -> None:
    """SourceSetOrchestrator 싱글톤 초기화 (테스트용)."""
    global _orchestrator
    _orchestrator = None
