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
from typing import Any, Dict, List, Optional, Tuple

from app.clients.backend_client import (
    BackendClient,
    ChunkBulkUpsertError,
    SourceSetCompleteCallbackError,
    SourceSetDocumentsFetchError,
    get_backend_client,
)
from app.clients.milvus_client import (
    MilvusSearchClient,
    MilvusError,
    get_milvus_client,
)
from app.clients.ragflow_client import (
    RagflowClient,
    RagflowError,
    RagflowConnectionError,
    get_ragflow_client,
)
from app.core.config import get_settings
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
    ScriptPatch,
    SourceRef,
    SourceSetCompleteRequest,
    SourceSetDocument,
    SourceSetStartRequest,
    SourceSetStartResponse,
    SourceSetStatus,
)

logger = get_logger(__name__)


# =============================================================================
# Internal State (파일 기반 저장소로 이동)
# =============================================================================

# ProcessingJob과 ProcessingStatus는 source_set_job_store.py로 이동
from app.services.source_set_job_store import (
    ProcessingJob,
    ProcessingStatus,
    get_source_set_job_store,
)


@dataclass
class DocumentProcessingResult:
    """문서 처리 결과."""
    document_id: str
    success: bool
    chunks_count: int = 0
    fail_chunks_count: int = 0
    fail_reason: Optional[str] = None
    chunks: List[Dict[str, Any]] = field(default_factory=list)  # Step 3: 스크립트 생성용


# =============================================================================
# SourceSet Orchestrator
# =============================================================================


class SourceSetOrchestrator:
    """소스셋 오케스트레이터 서비스.

    멀티 문서 소스셋을 RAGFlow를 통해 처리하고
    스크립트를 자동 생성하는 오케스트레이션 로직을 담당합니다.

    Attributes:
        _backend_client: 백엔드 API 클라이언트
        _job_store: Job 상태 저장소 (파일 기반)
        _running_tasks: 비동기 태스크 관리
        _ragflow_client: RAGFlow API 클라이언트 (Phase 51 복구)
    """

    def __init__(
        self,
        backend_client: Optional[BackendClient] = None,
        milvus_client: Optional[MilvusSearchClient] = None,
        ragflow_client: Optional[RagflowClient] = None,
        job_store=None,  # SourceSetJobStore (None이면 싱글톤 사용)
    ):
        """초기화.

        Args:
            backend_client: 백엔드 클라이언트 (None이면 싱글톤 사용)
            milvus_client: Milvus 클라이언트 (None이면 싱글톤 사용, Option 3)
            ragflow_client: RAGFlow 클라이언트 (None이면 싱글톤 사용, Phase 51)
            job_store: Job 상태 저장소 (None이면 싱글톤 사용)
        """
        self._backend_client = backend_client or get_backend_client()
        self._job_store = job_store or get_source_set_job_store()
        self._running_tasks: Dict[str, asyncio.Task] = {}
        
        # 콜백 대기 메커니즘 (ingest_id -> Event, Result 매핑)
        self._callback_events: Dict[str, asyncio.Event] = {}
        self._callback_results: Dict[str, Dict[str, Any]] = {}

        # Option 3: Milvus 클라이언트 (SCRIPT_RETRIEVER_BACKEND=milvus 시 사용)
        self._settings = get_settings()
        self._use_milvus = (
            self._settings.script_retriever_backend == "milvus"
            and self._settings.MILVUS_ENABLED
        )
        if self._use_milvus:
            self._milvus_client = milvus_client or get_milvus_client()
            logger.info(
                f"SourceSetOrchestrator initialized with Milvus "
                f"(SCRIPT_RETRIEVER_BACKEND={self._settings.script_retriever_backend})"
            )
        else:
            self._milvus_client = None

        # Phase 51: RAGFlow 클라이언트 (Milvus fallback 또는 단독 사용)
        self._ragflow_client = ragflow_client or get_ragflow_client()
        if self._ragflow_client.is_configured:
            logger.info(
                f"SourceSetOrchestrator initialized with RAGFlow "
                f"(RAGFLOW_BASE_URL configured)"
            )
        else:
            logger.warning(
                f"SourceSetOrchestrator: RAGFlow not configured "
                f"(SCRIPT_RETRIEVER_BACKEND={self._settings.script_retriever_backend})"
            )

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
        existing_job = self._job_store.get(source_set_id)
        if existing_job:
            logger.info(
                f"SourceSet already processing: source_set_id={source_set_id}, "
                f"status={existing_job.status}"
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
            status=ProcessingStatus.PENDING,  # 초기 상태는 PENDING, 문서 조회 후 PROCESSING으로 변경
        )
        self._job_store.save(job)  # 초기 상태 저장

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
        return self._job_store.get(source_set_id)

    async def notify_document_completed(
        self,
        source_set_id: str,
        document_id: str,
        ragflow_doc_id: str,
        status: str,
    ) -> None:
        """RAGFlow 콜백으로 문서 완료를 알립니다.

        모든 문서가 완료되면 스크립트 생성 및 백엔드 콜백을 처리합니다.

        Args:
            source_set_id: 소스셋 ID
            document_id: Spring 문서 ID
            ragflow_doc_id: RAGFlow 문서 ID
            status: 완료 상태 (COMPLETED|FAILED)
        """
        job = self._processing_jobs.get(source_set_id)
        if not job:
            logger.debug(
                f"Job not found for source_set_id={source_set_id}, "
                f"document_id={document_id} (may have already completed)"
            )
            return

        # 문서 완료 정보 저장
        job.document_completion_info[document_id] = {
            "status": status,
            "ragflow_doc_id": ragflow_doc_id,
            "completed_at": datetime.utcnow(),
        }

        logger.info(
            f"Document completion notified: source_set_id={source_set_id}, "
            f"document_id={document_id}, ragflow_doc_id={ragflow_doc_id}, status={status}"
        )

        # 모든 문서 완료 체크 및 처리
        await self._check_and_process_completion(source_set_id)

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
        job = self._job_store.get(source_set_id)
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
            self._job_store.save(job)  # 상태 저장

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

            # 2. 모든 문서를 RAGFlow에 ingest 요청만 보냄 (콜백 기반 처리)
            # 각 문서의 완료는 RAGFlow 콜백으로 처리됨
            ingest_tasks = []
            for doc in job.documents:
                task = asyncio.create_task(
                    self._ingest_document_only(source_set_id, doc, job)
                )
                ingest_tasks.append(task)
            
            # 모든 ingest 요청 완료 대기 (실패해도 계속 진행)
            ingest_results = await asyncio.gather(*ingest_tasks, return_exceptions=True)
            
            # ingest 실패한 문서 체크
            ingest_failures = []
            for i, result in enumerate(ingest_results):
                if isinstance(result, Exception):
                    doc = job.documents[i]
                    logger.error(
                        f"Document ingest failed: doc_id={doc.document_id}, error={result}"
                    )
                    ingest_failures.append(doc.document_id)
                elif not result:
                    doc = job.documents[i]
                    ingest_failures.append(doc.document_id)
            
            if ingest_failures:
                await self._send_failure_callback(
                    job,
                    error_code="DOCUMENT_INGEST_FAILED",
                    error_message=f"문서 ingest 실패: {', '.join(ingest_failures)}",
                )
                return
            
            # 3. 이제 모든 처리는 RAGFlow 콜백에서 처리됨
            # notify_document_completed에서 모든 문서 완료 시 스크립트 생성 및 백엔드 콜백
            logger.info(
                f"All documents ingest requested: source_set_id={source_set_id}, "
                f"document_count={len(job.documents)}, "
                f"waiting for RAGFlow callbacks..."
            )
            
            # 작업 상태를 PROCESSING으로 설정 (콜백에서 완료 처리)
            job.status = ProcessingStatus.PROCESSING
            job.updated_at = datetime.utcnow()

        except SourceSetDocumentsFetchError as e:
            logger.error(f"Failed to fetch documents: {e}")
            job.status = ProcessingStatus.FAILED
            job.error_code = e.error_code
            job.error_message = e.message
            job.updated_at = datetime.utcnow()
            self._job_store.save(job)  # 상태 저장
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
            job.updated_at = datetime.utcnow()
            self._job_store.save(job)  # 상태 저장
            await self._send_failure_callback(
                job,
                error_code="PROCESSING_ERROR",
                error_message=str(e)[:200],
            )

        finally:
            # 태스크 정리
            if source_set_id in self._running_tasks:
                del self._running_tasks[source_set_id]

    async def _ingest_document_only(
        self,
        source_set_id: str,
        doc: SourceSetDocument,
        job: ProcessingJob,
    ) -> bool:
        """문서를 RAGFlow에 ingest 요청만 보냅니다 (콜백 기반 처리).

        Args:
            source_set_id: 소스셋 ID
            doc: 처리할 문서
            job: 처리 작업 상태

        Returns:
            bool: ingest 요청 성공 여부
        """
        try:
            # dataset_id 결정 (domain → dataset_id 매핑)
            dataset_id = self._ragflow_client._dataset_to_kb_id(doc.domain)

            # domain 매핑 검증
            if not dataset_id:
                logger.error(
                    f"Invalid domain - no dataset mapping: "
                    f"doc_id={doc.document_id}, domain={doc.domain}"
                )
                return False

            # ragflow_doc_id 결정
            ragflow_doc_id = doc.title.strip() if doc.title else None
            if not ragflow_doc_id:
                ragflow_doc_id = self._extract_milvus_doc_id(doc.source_url, doc.document_id)

            logger.info(
                f"Ingesting document to RAGFlow: ragflow_doc_id={ragflow_doc_id}, "
                f"title={doc.title}, spring_doc_id={doc.document_id}"
            )

            # RAGFlow에 ingest 요청
            ingest_result = await self._ragflow_client.ingest_document_with_retry(
                dataset_id=dataset_id,
                doc_id=ragflow_doc_id,
                file_url=doc.source_url,
                version=1,
                meta={
                    "ragDocumentPk": doc.document_id,
                    "traceId": job.trace_id if job else None,
                    "requestId": job.request_id if job else None,
                    "domain": doc.domain,
                    "source_set_id": job.source_set_id if job else None,
                    "spring_document_id": doc.document_id,
                },
            )

            ingest_id = ingest_result.get("ingestId")
            if not ingest_id:
                logger.error(f"RAGFlow ingest failed: no ingest ID returned: doc_id={doc.document_id}")
                return False

            logger.info(f"Document ingest accepted: doc_id={doc.document_id}, ingest_id={ingest_id}")
            return True

        except Exception as e:
            logger.error(f"Document ingest error: doc_id={doc.document_id}, error={e}")
            return False

    async def _check_and_process_completion(self, source_set_id: str) -> None:
        """모든 문서가 완료되었는지 체크하고, 완료되면 스크립트 생성 및 백엔드 콜백을 처리합니다.

        Args:
            source_set_id: 소스셋 ID
        """
        job = self._processing_jobs.get(source_set_id)
        if not job:
            return

        # 이미 완료된 작업은 처리하지 않음
        if job.status == ProcessingStatus.COMPLETED:
            return

        # 모든 문서의 완료 상태 체크
        completed_count = 0
        failed_count = 0
        total_count = len(job.documents)

        for doc in job.documents:
            completion_info = job.document_completion_info.get(doc.document_id)
            if not completion_info:
                # 아직 완료되지 않은 문서가 있음
                return
            
            status = completion_info.get("status")
            if status == "COMPLETED":
                completed_count += 1
            elif status == "FAILED":
                failed_count += 1

        # 모든 문서가 완료되지 않았으면 대기
        if completed_count + failed_count < total_count:
            logger.debug(
                f"Waiting for more documents: source_set_id={source_set_id}, "
                f"completed={completed_count}, failed={failed_count}, total={total_count}"
            )
            return

        # 하나라도 실패한 경우 전체 실패 처리
        if failed_count > 0:
            logger.error(
                f"Source set processing failed: source_set_id={source_set_id}, "
                f"failed_documents={failed_count}/{total_count}"
            )
            await self._send_failure_callback(
                job,
                error_code="DOCUMENT_PROCESSING_FAILED",
                error_message=f"{failed_count}개 문서 처리 실패",
            )
            job.status = ProcessingStatus.FAILED
            return

        # 모든 문서가 완료되었으므로 청크 조회 및 스크립트 생성
        logger.info(
            f"All documents completed: source_set_id={source_set_id}, "
            f"total={total_count}, processing chunks and generating script..."
        )

        # 청크 조회 및 스크립트 생성
        await self._process_chunks_and_generate_script(source_set_id)

    async def _process_chunks_and_generate_script(self, source_set_id: str) -> None:
        """완료된 문서들의 청크를 조회하고 스크립트를 생성합니다.

        Args:
            source_set_id: 소스셋 ID
        """
        job = self._processing_jobs.get(source_set_id)
        if not job:
            return

        try:
            # 모든 문서의 청크 조회
            all_document_chunks: Dict[str, List[Dict[str, Any]]] = {}
            document_results: List[DocumentResult] = []

            for doc in job.documents:
                completion_info = job.document_completion_info.get(doc.document_id)
                if not completion_info or completion_info.get("status") != "COMPLETED":
                    continue

                ragflow_doc_id = completion_info.get("ragflow_doc_id")
                if not ragflow_doc_id:
                    logger.warning(f"No ragflow_doc_id for completed document: doc_id={doc.document_id}")
                    continue

                # Milvus에서 청크 조회
                dataset_id = self._ragflow_client._dataset_to_kb_id(doc.domain)
                chunks = []
                try:
                    if self._use_milvus and self._milvus_client:
                        milvus_chunks = await self._milvus_client.get_document_chunks(
                            doc_id=ragflow_doc_id,
                            dataset_id=dataset_id,
                        )
                        chunks = [
                            {
                                "chunk_id": chunk.get("chunk_id", ""),
                                "text": chunk.get("text", ""),
                                "metadata": {
                                    "page_num": chunk.get("page_num", 0),
                                    "section": chunk.get("section", ""),
                                    "section_path": chunk.get("section_path", ""),
                                },
                            }
                            for chunk in milvus_chunks
                        ]
                        
                        # Spring DB에 청크 저장
                        await self._save_chunks_to_backend(doc.document_id, chunks, job)
                        
                        logger.info(f"Retrieved {len(chunks)} chunks from Milvus: doc_id={ragflow_doc_id}")
                    else:
                        logger.warning(f"Milvus not available: doc_id={doc.document_id}")
                except Exception as e:
                    logger.error(f"Failed to fetch chunks from Milvus: doc_id={doc.document_id}, error={e}")
                    document_results.append(
                        DocumentResult(
                            document_id=doc.document_id,
                            status="FAILED",
                            fail_reason=f"Failed to fetch chunks: {str(e)[:200]}",
                        )
                    )
                    continue

                if chunks:
                    all_document_chunks[doc.document_id] = chunks
                    document_results.append(
                        DocumentResult(
                            document_id=doc.document_id,
                            status="COMPLETED",
                            fail_reason="",
                        )
                    )
                else:
                    document_results.append(
                        DocumentResult(
                            document_id=doc.document_id,
                            status="FAILED",
                            fail_reason="No chunks generated",
                        )
                    )

            job.document_results = document_results

            # 청크가 없으면 실패 처리
            total_chunks = sum(len(chunks) for chunks in all_document_chunks.values())
            if total_chunks == 0:
                await self._send_failure_callback(
                    job,
                    error_code="NO_CHUNKS_GENERATED",
                    error_message="문서 처리는 성공했으나 청크가 생성되지 않았습니다.",
                )
                job.status = ProcessingStatus.FAILED
                return

            # 스크립트 생성
            logger.info(f"Generating script: source_set_id={source_set_id}, total_chunks={total_chunks}")
            script = await self._generate_script(job, all_document_chunks)
            job.generated_script = script

            # 성공 콜백 전송
            await self._send_success_callback(job)

            # 상태 업데이트
            job.status = ProcessingStatus.COMPLETED
            job.updated_at = datetime.utcnow()

            logger.info(
                f"Source set processing completed: source_set_id={source_set_id}"
            )

        except Exception as e:
            logger.exception(f"Failed to process chunks and generate script: source_set_id={source_set_id}")
            job.status = ProcessingStatus.FAILED
            job.error_code = "SCRIPT_GENERATION_ERROR"
            job.error_message = str(e)[:200]
            await self._send_failure_callback(
                job,
                error_code="SCRIPT_GENERATION_ERROR",
                error_message=str(e)[:200],
            )

    async def _process_document(
        self,
        source_set_id: str,
        doc: SourceSetDocument,
        job: ProcessingJob,
    ) -> DocumentProcessingResult:
        """개별 문서를 RAGFlow로 처리합니다 (Step 3 구현).

        1. RAGFlow에 문서 업로드
        2. 파싱 트리거
        3. Polling으로 완료 대기 (DONE/FAIL/CANCEL)
        4. 완료 시 청크 조회
        5. Spring DB에 chunk_text + chunk_meta 저장

        Args:
            source_set_id: 소스셋 ID
            doc: 처리할 문서
            job: 처리 작업 상태

        Returns:
            DocumentProcessingResult: 처리 결과
        """
        from app.core.config import get_settings

        settings = get_settings()

        # Phase 51: RAGFlow 클라이언트 사용 가능 여부 확인
        if not self._ragflow_client.is_configured:
            logger.error(
                f"RAGFlow not configured - document processing unavailable: "
                f"source_set_id={source_set_id}, doc_id={doc.document_id}"
            )
            return DocumentProcessingResult(
                document_id=doc.document_id,
                success=False,
                fail_reason="RAGFLOW_NOT_CONFIGURED: RAGFLOW_BASE_URL을 설정하세요.",
            )

        # source_url null 체크
        if not doc.source_url or not doc.source_url.strip():
            logger.error(
                f"Document source_url is empty: source_set_id={source_set_id}, "
                f"doc_id={doc.document_id}"
            )
            return DocumentProcessingResult(
                document_id=doc.document_id,
                success=False,
                fail_reason="source_url is empty or null",
            )

        logger.info(
            f"Processing document: source_set_id={source_set_id}, "
            f"doc_id={doc.document_id}, url={doc.source_url[:50]}..."
        )

        try:
            # dataset_id 결정 (domain → dataset_id 매핑)
            dataset_id = self._ragflow_client._dataset_to_kb_id(doc.domain)

            # domain 매핑 검증 (매핑 없으면 FAILED 처리, fallback 금지)
            if not dataset_id:
                logger.error(
                    f"Invalid domain - no dataset mapping: "
                    f"doc_id={doc.document_id}, domain={doc.domain}"
                )
                return DocumentProcessingResult(
                    document_id=doc.document_id,
                    success=False,
                    fail_reason=f"INVALID_DOMAIN: {doc.domain}",
                )

            # 1. RAGFlow에 문서 ingest (업로드 + 파싱 통합) - FAIL 시 재시도 지원
            import asyncio
            max_retry_count = settings.RAGFLOW_MAX_RETRY_COUNT
            initial_delay = settings.RAGFLOW_POLL_INITIAL_DELAY_SEC
            max_wait_time = 720.0  # 최대 12분 (720초) 대기
            poll_interval = settings.RAGFLOW_POLL_INTERVAL_SEC

            ragflow_doc_id = None  # 파일명.확장자 (retry 루프에서 설정)
            ragflow_internal_id = None  # RAGFlow 내부 UUID (polling에서 발견 시 설정)
            final_status = None
            chunk_count = 0
            last_fail_reason = None

            for retry_attempt in range(max_retry_count + 1):  # 최초 1회 + 재시도 N회
                if retry_attempt > 0:
                    logger.warning(
                        f"Retrying document ingest ({retry_attempt}/{max_retry_count}): "
                        f"doc_id={doc.document_id}"
                    )

                # 백엔드에서 title 필드로 원본 파일명을 전달받음 (S3 URL은 UUID로 저장됨)
                ragflow_doc_id = doc.title.strip() if doc.title else None
                if not ragflow_doc_id:
                    # fallback: source_url에서 추출 (UUID일 가능성 있음)
                    ragflow_doc_id = self._extract_milvus_doc_id(doc.source_url, doc.document_id)
                logger.info(
                    f"Ingesting document to RAGFlow: ragflow_doc_id={ragflow_doc_id}, "
                    f"title={doc.title}, spring_doc_id={doc.document_id}"
                )

                # 재시도 로직이 내장된 래퍼 사용 (네트워크 타임아웃/오류에도 1회 재시도)
                ingest_result = await self._ragflow_client.ingest_document_with_retry(
                    dataset_id=dataset_id,
                    doc_id=ragflow_doc_id,  # 파일명.확장자 형태
                    file_url=doc.source_url,
                    version=1,  # RAGFlow API 필수 파라미터
                    meta={
                        "ragDocumentPk": doc.document_id,  # RAGFlow API 필수
                        "traceId": job.trace_id if job else None,  # RAGFlow API 필수
                        "requestId": job.request_id if job else None,  # RAGFlow API 필수
                        "domain": doc.domain,
                        "source_set_id": job.source_set_id if job else None,
                        "spring_document_id": doc.document_id,  # 원본 Spring UUID 보존
                    },
                )

                ingest_id = ingest_result.get("ingestId")
                if not ingest_id:
                    last_fail_reason = "RAGFlow ingest failed: no ingest ID returned"
                    logger.error(f"{last_fail_reason}: doc_id={doc.document_id}")
                    continue  # 재시도

                logger.info(f"Document ingest accepted: doc_id={doc.document_id}, ingest_id={ingest_id}")

                # 2. Ingest 완료 대기 - 콜백만 사용 (polling 불가: RAGFLOW_API_KEY는 UI 토큰, 문서 리스트 API 접근 권한 없음)
                logger.info(
                    f"Waiting for RAGFlow callback: doc_id={doc.document_id}, "
                    f"ingest_id={ingest_id}, timeout={max_wait_time}s. "
                    f"Note: Polling is not available because RAGFLOW_API_KEY is UI token without document list API access."
                )
                
                # 콜백 대기 (전체 타임아웃 사용)
                callback_result = await self._wait_for_callback(
                    ingest_id=ingest_id,
                    timeout=max_wait_time,
                    doc_id=doc.document_id,
                )
                
                if callback_result is None:
                    # 콜백 타임아웃
                    last_fail_reason = f"RAGFlow callback timeout after {max_wait_time}s. RAGFlow server did not send callback."
                    logger.error(f"{last_fail_reason}: doc_id={doc.document_id}, ingest_id={ingest_id}")
                    continue  # 재시도
                
                callback_status = callback_result.get("status")
                if callback_status == "FAILED":
                    last_fail_reason = callback_result.get("failReason") or "RAGFlow ingest failed"
                    logger.error(f"RAGFlow ingest failed: doc_id={doc.document_id}, reason={last_fail_reason}")
                    continue  # 재시도
                
                if callback_status != "COMPLETED":
                    last_fail_reason = f"Unexpected callback status: {callback_status}"
                    logger.error(f"{last_fail_reason}: doc_id={doc.document_id}")
                    continue  # 재시도
                
                # 성공: 콜백에서 받은 정보 사용
                callback_doc_id = callback_result.get("documentId")  # 콜백에서 받은 docId (파일명)
                final_status = "DONE"
                chunk_count = callback_result.get("chunkCount", 0)
                
                logger.info(
                    f"RAGFlow callback received: doc_id={doc.document_id}, "
                    f"status={callback_status}, callback_doc_id={callback_doc_id}, "
                    f"chunks={chunk_count}"
                )
                
                # RAGFlow 내부 UUID 찾기 (문서 리스트에서)
                ragflow_internal_id = None
                if callback_doc_id:
                    try:
                        found_doc = await self._ragflow_client.find_document_by_doc_id(
                            dataset_id=dataset_id,
                            doc_id=callback_doc_id,
                        )
                        if found_doc:
                            ragflow_internal_id = found_doc.get("id")
                            logger.info(
                                f"Found RAGFlow internal ID: doc_id={callback_doc_id}, "
                                f"ragflow_internal_id={ragflow_internal_id}"
                            )
                        else:
                            logger.warning(
                                f"Document not found in RAGFlow list: doc_id={callback_doc_id}, "
                                f"will try to use callback_doc_id directly"
                            )
                            # 문서를 찾지 못한 경우, callback_doc_id를 직접 사용 시도
                            ragflow_internal_id = callback_doc_id
                    except Exception as e:
                        logger.warning(
                            f"Error finding document in RAGFlow list: doc_id={callback_doc_id}, "
                            f"error={e}, will try to use callback_doc_id directly"
                        )
                        # 에러 발생 시 callback_doc_id를 직접 사용 시도
                        ragflow_internal_id = callback_doc_id
                
                # 성공, retry 루프 탈출
                break

            # 모든 재시도 후에도 실패한 경우
            if final_status != "DONE":
                fail_reason = last_fail_reason or f"RAGFlow callback failed after {max_retry_count} retries"
                logger.error(f"Document processing failed after retries: doc_id={doc.document_id}, reason={fail_reason}")
                return DocumentProcessingResult(
                    document_id=doc.document_id,
                    success=False,
                    fail_reason=fail_reason,
                )

            # 3. 청크 조회 (RAGFlow 내부 UUID 사용)
            if not ragflow_internal_id:
                logger.error(f"RAGFlow internal ID not found: doc_id={doc.document_id}")
                return DocumentProcessingResult(
                    document_id=doc.document_id,
                    success=False,
                    fail_reason="RAGFlow internal ID not found after callback",
                )
            
            logger.info(f"Fetching chunks: doc_id={ragflow_doc_id}, ragflow_internal_id={ragflow_internal_id}, count={chunk_count}")
            chunks = await self._fetch_all_chunks(
                dataset_id=dataset_id,
                document_id=ragflow_internal_id,  # RAGFlow 내부 UUID
                page_size=settings.RAGFLOW_CHUNK_PAGE_SIZE,
            )

            if not chunks:
                logger.warning(f"No chunks found: doc_id={doc.document_id}")
                return DocumentProcessingResult(
                    document_id=doc.document_id,
                    success=False,
                    fail_reason="RAGFlow parsing completed but no chunks generated",
                )

            # 5. Spring DB에 청크 저장
            await self._save_chunks_to_backend(doc.document_id, chunks, job)

            logger.info(
                f"Document processed: doc_id={doc.document_id}, chunks={len(chunks)}"
            )

            return DocumentProcessingResult(
                document_id=doc.document_id,
                success=True,
                chunks_count=len(chunks),
                chunks=chunks,  # 스크립트 생성용으로 청크 포함
            )

        except RagflowConnectionError as e:
            logger.error(f"RAGFlow connection error: doc_id={doc.document_id}, error={e}")
            return DocumentProcessingResult(
                document_id=doc.document_id,
                success=False,
                fail_reason=f"RAGFlow connection failed: {str(e)[:100]}",
            )

        except RagflowError as e:
            logger.error(f"RAGFlow error: doc_id={doc.document_id}, error={e}")
            return DocumentProcessingResult(
                document_id=doc.document_id,
                success=False,
                fail_reason=f"RAGFlow error: {str(e)[:100]}",
            )

        except Exception as e:
            logger.exception(f"Document processing error: doc_id={doc.document_id}")
            return DocumentProcessingResult(
                document_id=doc.document_id,
                success=False,
                fail_reason=str(e)[:200],
            )

    async def _process_document_with_routing(
        self,
        source_set_id: str,
        doc: SourceSetDocument,
        job: ProcessingJob,
    ) -> DocumentProcessingResult:
        """문서를 처리합니다 (Option 3: Milvus → RAGFlow 라우팅).

        SCRIPT_RETRIEVER_BACKEND 설정에 따라:
        - milvus: Milvus에서 청크 조회 시도 → 실패 시 RAGFlow 처리
        - ragflow: RAGFlow로만 처리

        Args:
            source_set_id: 소스셋 ID
            doc: 처리할 문서
            job: 처리 작업 상태

        Returns:
            DocumentProcessingResult: 처리 결과
        """
        # Milvus 사용 시: Milvus 먼저 시도 → RAGFlow fallback
        if self._use_milvus and self._milvus_client:
            try:
                result = await self._process_document_milvus(source_set_id, doc, job)
                if result.success and result.chunks:
                    logger.info(
                        f"Document processed via Milvus: doc_id={doc.document_id}, "
                        f"chunks={len(result.chunks)}"
                    )
                    return result

                # Milvus에서 청크가 없으면 RAGFlow fallback
                logger.warning(
                    f"Milvus returned no chunks for doc_id={doc.document_id}, "
                    f"falling back to RAGFlow"
                )

            except MilvusError as e:
                logger.warning(
                    f"Milvus processing failed for doc_id={doc.document_id}, "
                    f"falling back to RAGFlow: {e}"
                )
            except Exception as e:
                logger.warning(
                    f"Milvus unexpected error for doc_id={doc.document_id}, "
                    f"falling back to RAGFlow: {e}"
                )

        # RAGFlow 처리 (기본 또는 fallback)
        return await self._process_document(source_set_id, doc, job)

    async def _process_document_milvus(
        self,
        source_set_id: str,
        doc: SourceSetDocument,
        job: ProcessingJob,
    ) -> DocumentProcessingResult:
        """Milvus에서 문서 청크를 조회합니다 (Option 3).

        RAGFlow를 거치지 않고 Milvus에서 직접 청크를 조회합니다.
        이미 인덱싱된 문서에 대해 사용합니다.

        Args:
            source_set_id: 소스셋 ID
            doc: 처리할 문서
            job: 처리 작업 상태

        Returns:
            DocumentProcessingResult: 처리 결과

        Raises:
            MilvusError: Milvus 조회 실패 시
        """
        # source_url null 체크
        if not doc.source_url or not doc.source_url.strip():
            logger.error(
                f"Document source_url is empty: source_set_id={source_set_id}, "
                f"doc_id={doc.document_id}"
            )
            return DocumentProcessingResult(
                document_id=doc.document_id,
                success=False,
                fail_reason="source_url is empty or null",
            )

        logger.info(
            f"Processing document via Milvus: source_set_id={source_set_id}, "
            f"doc_id={doc.document_id}"
        )

        # Milvus에서 청크 조회
        chunks = await self._fetch_document_chunks_milvus(doc)

        if not chunks:
            logger.warning(
                f"No chunks found in Milvus for doc_id={doc.document_id}"
            )
            return DocumentProcessingResult(
                document_id=doc.document_id,
                success=False,
                fail_reason="No chunks found in Milvus",
            )

        # Spring DB에 청크 저장 (선택적 - Milvus에서 가져온 청크도 저장)
        # 주의: 이미 저장된 청크일 수 있으므로 upsert 사용
        await self._save_chunks_to_backend(doc.document_id, chunks, job)

        logger.info(
            f"Document processed via Milvus: doc_id={doc.document_id}, "
            f"chunks={len(chunks)}"
        )

        return DocumentProcessingResult(
            document_id=doc.document_id,
            success=True,
            chunks_count=len(chunks),
            chunks=chunks,
        )

    async def _poll_document_status(
        self,
        dataset_id: str,
        document_id: str,
        poll_interval: float = 3.0,
        timeout: float = 900.0,
    ) -> Tuple[str, int]:
        """RAGFlow 문서 파싱 완료를 폴링합니다.

        Args:
            dataset_id: RAGFlow 데이터셋 ID
            document_id: RAGFlow 문서 ID
            poll_interval: 폴링 간격 (초)
            timeout: 최대 대기 시간 (초)

        Returns:
            Tuple[str, int]: (최종 상태, 청크 수)
                - 상태: DONE, FAIL, CANCEL, TIMEOUT
        """
        import time
        start_time = time.time()
        terminal_states = {"DONE", "FAIL", "CANCEL"}

        logger.info(
            f"Starting polling: dataset={dataset_id}, doc={document_id}, "
            f"interval={poll_interval}s, timeout={timeout}s"
        )

        while True:
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                logger.warning(f"Polling timeout: doc={document_id}, elapsed={elapsed:.1f}s")
                return ("TIMEOUT", 0)

            try:
                status_info = await self._ragflow_client.get_document_status(
                    dataset_id=dataset_id,
                    document_id=document_id,
                )

                run_status = status_info.get("run", "UNSTART")
                progress = status_info.get("progress", 0.0)
                chunk_count = status_info.get("chunk_count", 0)

                logger.debug(
                    f"Polling status: doc={document_id}, run={run_status}, "
                    f"progress={progress:.1%}, chunks={chunk_count}"
                )

                if run_status in terminal_states:
                    logger.info(
                        f"Polling complete: doc={document_id}, status={run_status}, "
                        f"chunks={chunk_count}, elapsed={elapsed:.1f}s"
                    )
                    return (run_status, chunk_count)

            except Exception as e:
                logger.warning(f"Polling error (will retry): doc={document_id}, error={e}")

            await asyncio.sleep(poll_interval)

    async def _fetch_all_chunks(
        self,
        dataset_id: str,
        document_id: str,
        page_size: int = 1000,
    ) -> List[Dict[str, Any]]:
        """RAGFlow에서 모든 청크를 조회합니다 (페이지네이션 지원).

        Args:
            dataset_id: RAGFlow 데이터셋 ID
            document_id: RAGFlow 문서 ID
            page_size: 페이지당 청크 수

        Returns:
            List[Dict[str, Any]]: 청크 리스트 (chunkIndex 포함)
        """
        all_chunks: List[Dict[str, Any]] = []
        page = 1

        while True:
            result = await self._ragflow_client.get_document_chunks(
                dataset_id=dataset_id,
                document_id=document_id,
                page=page,
                page_size=page_size,
            )

            chunks = result.get("chunks", [])
            total = result.get("total", 0)

            if not chunks:
                break

            # 청크 인덱스 부여 (0부터 시작, 응답 순서 기준)
            for chunk in chunks:
                chunk_index = len(all_chunks)
                all_chunks.append({
                    "chunk_index": chunk_index,
                    "chunk_text": chunk.get("content", ""),
                    "chunk_meta": {
                        "ragflow_chunk_id": chunk.get("id"),
                        "positions": chunk.get("positions", []),
                        "important_keywords": chunk.get("important_keywords", []),
                        "questions": chunk.get("questions", []),
                        "image_id": chunk.get("image_id", ""),
                        "docnm_kwd": chunk.get("docnm_kwd", ""),
                    },
                })

            # 모든 페이지 조회 완료 확인
            if len(all_chunks) >= total:
                break

            page += 1

        logger.info(f"Fetched {len(all_chunks)} chunks from RAGFlow")
        return all_chunks

    # =========================================================================
    # Option 3: Milvus 기반 문서 텍스트 조회
    # =========================================================================

    def _extract_milvus_doc_id(self, source_url: str, fallback_id: str) -> str:
        """source_url에서 Milvus doc_id (파일명)를 추출합니다.

        Milvus에서 doc_id는 파일명으로 저장됩니다.
        source_url 예: https://bucket.s3.amazonaws.com/path/to/장애인식관련법령.docx?signature=...

        Args:
            source_url: 문서 원본 URL
            fallback_id: 추출 실패 시 사용할 ID

        Returns:
            str: Milvus doc_id (파일명)
        """
        try:
            # URL에서 파일명 추출
            # 1. 쿼리 파라미터 제거
            url_without_query = source_url.split("?")[0]
            # 2. 마지막 경로 요소 추출
            filename = url_without_query.split("/")[-1]
            # 3. URL 디코딩 (한글 파일명 처리)
            from urllib.parse import unquote
            filename = unquote(filename)

            if filename and len(filename) > 0:
                logger.debug(f"Extracted Milvus doc_id: {filename} from {source_url[:50]}...")
                return filename
        except Exception as e:
            logger.warning(f"Failed to extract filename from URL: {e}")

        return fallback_id

    async def _fetch_document_chunks_milvus(
        self,
        doc: "SourceSetDocument",
    ) -> List[Dict[str, Any]]:
        """Milvus에서 문서 전체 청크를 조회합니다 (Option 3).

        Args:
            doc: 소스셋 문서 정보

        Returns:
            List[Dict[str, Any]]: 청크 리스트 (chunk_index, chunk_text, chunk_meta 포함)

        Raises:
            MilvusError: Milvus 조회 실패 시
        """
        if not self._milvus_client:
            raise MilvusError("Milvus client not initialized")

        # 백엔드에서 title 필드로 원본 파일명을 전달받음 (S3 URL은 UUID로 저장됨)
        milvus_doc_id = doc.title.strip() if doc.title else None
        if not milvus_doc_id:
            # fallback: source_url에서 추출 (UUID일 가능성 있음)
            milvus_doc_id = self._extract_milvus_doc_id(doc.source_url, doc.document_id)

        logger.info(
            f"Fetching document from Milvus: spring_doc_id={doc.document_id}, "
            f"title={doc.title}, milvus_doc_id={milvus_doc_id}"
        )

        try:
            # Milvus에서 청크 조회
            milvus_chunks = await self._milvus_client.get_document_chunks(
                doc_id=milvus_doc_id,
                dataset_id=None,  # dataset_id 필터 없이 전체 조회
            )

            if not milvus_chunks:
                logger.warning(
                    f"No chunks found in Milvus for doc_id={milvus_doc_id}"
                )
                return []

            # 청크 포맷 변환 (RAGFlow 포맷과 동일하게)
            all_chunks = []
            for idx, chunk in enumerate(milvus_chunks):
                all_chunks.append({
                    "chunk_index": idx,
                    "chunk_text": chunk.get("text", ""),
                    "chunk_meta": {
                        "milvus_chunk_id": chunk.get("chunk_id"),
                        "milvus_doc_id": milvus_doc_id,
                        "source": "milvus",
                    },
                })

            logger.info(
                f"Fetched {len(all_chunks)} chunks from Milvus for doc_id={milvus_doc_id}"
            )
            return all_chunks

        except MilvusError:
            raise
        except Exception as e:
            logger.error(f"Milvus chunk fetch failed: {e}")
            raise MilvusError(f"Failed to fetch chunks from Milvus: {e}")

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
                chunk_text=chunk.get("text", chunk.get("chunk_text", "")),  # Milvus에서는 "text"로 저장됨
                chunk_meta=chunk.get("metadata", chunk.get("chunk_meta")),  # Milvus에서는 "metadata"로 저장됨
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
    # Script Generation (씬 단위 RAG 방식)
    # =========================================================================

    async def _generate_script(
        self,
        job: ProcessingJob,
        document_chunks: Dict[str, List[Dict[str, Any]]],
    ) -> GeneratedScript:
        """스크립트를 생성합니다 (씬 단위 RAG 방식).

        문서를 통째로 프롬프트에 싣지 않고, 씬 단위로 필요한 청크만 검색하여
        컨텍스트 제한을 우회합니다.

        흐름:
        1. 아웃라인 생성: 문서 메타데이터로 씬 목차 설계 (1회 LLM)
        2. 씬별 RAG 검색 + 생성: 각 씬 키워드로 Top-K 검색 후 생성 (N회 LLM)
           - 각 씬 생성 후 백엔드에 패치 콜백 전송 (부분 저장)
        3. 결과 병합: 씬들을 합쳐서 최종 스크립트

        Args:
            job: 처리 작업 상태
            document_chunks: 문서별 청크 (doc_id → chunks)

        Returns:
            GeneratedScript: 생성된 스크립트
        """
        from app.services.scene_based_script_generator import (
            SceneBasedScriptGenerator,
            SceneCallback,
        )

        # 문서 정보 준비
        documents = [
            {
                "document_id": doc.document_id,
                "title": doc.title,
                "domain": doc.domain,
            }
            for doc in job.documents
        ]

        # LLM 모델 설정
        model = job.llm_model_hint or "LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct"

        # 씬 생성 콜백 정의 (씬 생성 시마다 백엔드에 패치 전송)
        async def on_scene_generated(
            script_id: str,
            chapter_index: int,
            chapter_title: str,
            scene_index: int,
            scene: GeneratedScene,
            current_scene: int,
            total_scenes: int,
        ) -> None:
            await self._send_scene_patch_callback(
                job=job,
                chapter_index=chapter_index,
                chapter_title=chapter_title,
                scene_index=scene_index,
                scene=scene,
                script_id=script_id,
                current_scene=current_scene,
                total_scenes=total_scenes,
            )

        # 씬 단위 RAG 스크립트 생성기
        generator = SceneBasedScriptGenerator(
            milvus_client=self._milvus_client,
            model=model,
            top_k=3,  # 씬당 검색할 청크 수
        )

        logger.info(
            f"Using scene-based RAG script generation with patch callbacks: "
            f"source_set_id={job.source_set_id}, model={model}"
        )

        # 스크립트 생성 (씬별 콜백 전달)
        script = await generator.generate_script(
            source_set_id=job.source_set_id,
            video_id=job.video_id,
            education_id=job.education_id,
            documents=documents,
            document_chunks=document_chunks,
            on_scene_generated=on_scene_generated,
        )

        return script

    # =========================================================================
    # Legacy Script Generation (기존 방식 - 백업용)
    # =========================================================================

    async def _generate_script_legacy(
        self,
        job: ProcessingJob,
        document_chunks: Dict[str, List[Dict[str, Any]]],
    ) -> GeneratedScript:
        """스크립트를 생성합니다 (기존 방식 - 전체 문서를 프롬프트에 포함).

        Note: 컨텍스트 제한으로 긴 문서 처리 불가. 씬 단위 RAG 방식 권장.

        Args:
            job: 처리 작업 상태
            document_chunks: 문서별 청크 (doc_id → chunks)

        Returns:
            GeneratedScript: 생성된 스크립트
        """
        from app.clients.llm_client import LLMClient
        from app.core.config import get_settings
        import json

        settings = get_settings()
        script_id = f"script-{uuid.uuid4().hex[:12]}"

        # 1. 청크 텍스트를 하나의 컨텍스트로 합치기
        context_parts = []
        chunk_mapping: List[Tuple[str, int]] = []  # (doc_id, chunk_index) for sourceRefs

        for doc_id, chunks in document_chunks.items():
            doc_title = next(
                (d.title for d in job.documents if d.document_id == doc_id),
                "문서"
            )
            context_parts.append(f"\n### 문서: {doc_title}\n")
            for chunk in chunks:
                chunk_index = chunk.get("chunk_index", 0)
                chunk_text = chunk.get("chunk_text", "")
                if chunk_text.strip():
                    context_parts.append(f"[청크 {len(chunk_mapping)}] {chunk_text}\n")
                    chunk_mapping.append((doc_id, chunk_index))

        full_context = "".join(context_parts)

        # 2. LLM 프롬프트 구성
        system_prompt = """당신은 법정의무교육 영상 스크립트 전문 작성자입니다.
주어진 교육 자료를 바탕으로 교육 영상 스크립트를 JSON 형식으로 생성해주세요.

출력 JSON 스키마:
{
  "title": "교육 제목",
  "chapters": [
    {
      "chapter_index": 1,
      "title": "챕터 제목",
      "scenes": [
        {
          "scene_index": 1,
          "purpose": "씬 목적 (도입/설명/사례/정리 등)",
          "narration": "나레이션 텍스트",
          "caption": "화면 자막",
          "visual": "시각 자료 설명",
          "duration_sec": 15,
          "source_chunk_indexes": [0, 1]
        }
      ]
    }
  ]
}

중요 규칙:
1. 나레이션은 자연스러운 구어체로 작성
2. 각 씬은 10-30초 분량으로 구성
3. source_chunk_indexes에는 해당 씬의 내용과 관련된 청크 번호([청크 N])를 기재
4. 전체 영상 길이는 3-10분 목표
5. 반드시 유효한 JSON만 출력 (설명 없이)"""

        # 컨텍스트 길이 제한 (LLM 8192 토큰 제한 고려)
        max_context_chars = 8000
        truncated_context = full_context[:max_context_chars]
        if len(full_context) > max_context_chars:
            logger.info(
                f"Context truncated: {len(full_context)} -> {max_context_chars} chars"
            )

        user_prompt = f"""다음 교육 자료를 바탕으로 교육 영상 스크립트를 생성해주세요:

{truncated_context}

JSON 스크립트:"""

        # 3. LLM 호출
        llm_client = LLMClient()
        model = job.llm_model_hint or "LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct"

        try:
            response = await llm_client.generate_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=model,
                temperature=0.3,
                max_tokens=2500,  # 8192 - 입력토큰(~5000) = ~3000 여유
            )

            # 4. JSON 파싱
            script_json = self._parse_script_json(response)

            if not script_json:
                logger.warning("Failed to parse LLM response, using fallback")
                return self._generate_fallback_script(job, document_chunks)

            # 5. GeneratedScript 모델로 변환 (sourceRefs 후처리)
            chapters = []
            total_duration = 0.0

            for ch_idx, chapter_data in enumerate(script_json.get("chapters", [])):
                scenes = []
                chapter_duration = 0.0

                for sc_idx, scene_data in enumerate(chapter_data.get("scenes", [])):
                    duration = scene_data.get("duration_sec", 15.0)
                    chapter_duration += duration

                    # sourceRefs 후처리: source_chunk_indexes → SourceRef 변환
                    source_refs = []
                    for chunk_idx in scene_data.get("source_chunk_indexes", []):
                        if 0 <= chunk_idx < len(chunk_mapping):
                            doc_id, original_idx = chunk_mapping[chunk_idx]
                            source_refs.append(SourceRef(
                                document_id=doc_id,
                                chunk_index=original_idx,
                            ))

                    scenes.append(GeneratedScene(
                        scene_id=f"scene-{uuid.uuid4().hex[:8]}",
                        scene_index=sc_idx + 1,
                        purpose=scene_data.get("purpose", ""),
                        narration=scene_data.get("narration", ""),
                        caption=scene_data.get("caption"),
                        visual=scene_data.get("visual"),
                        duration_sec=duration,
                        confidence_score=0.8,
                        source_refs=source_refs,
                    ))

                total_duration += chapter_duration
                chapters.append(GeneratedChapter(
                    chapter_id=f"chapter-{uuid.uuid4().hex[:8]}",
                    chapter_index=ch_idx + 1,
                    title=chapter_data.get("title", f"챕터 {ch_idx + 1}"),
                    duration_sec=chapter_duration,
                    scenes=scenes,
                ))

            script = GeneratedScript(
                script_id=script_id,
                education_id=job.education_id,
                source_set_id=job.source_set_id,
                title=script_json.get("title", "교육 스크립트"),
                total_duration_sec=total_duration,
                version=1,
                llm_model=model,
                chapters=chapters,
            )

            logger.info(
                f"Script generated: script_id={script_id}, "
                f"chapters={len(chapters)}, duration={total_duration:.1f}s"
            )

            return script

        except Exception as e:
            logger.exception(f"LLM script generation failed: {e}")
            return self._generate_fallback_script(job, document_chunks)

    def _parse_script_json(self, response: str) -> Optional[Dict[str, Any]]:
        """LLM 응답에서 JSON을 파싱합니다."""
        import json
        import re

        # JSON 블록 추출 시도
        json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
        if json_match:
            response = json_match.group(1)

        # { } 블록 추출
        brace_match = re.search(r'\{.*\}', response, re.DOTALL)
        if brace_match:
            response = brace_match.group(0)

        try:
            return json.loads(response)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error: {e}")
            return None

    def _generate_fallback_script(
        self,
        job: ProcessingJob,
        document_chunks: Dict[str, List[Dict[str, Any]]],
    ) -> GeneratedScript:
        """LLM 실패 시 폴백 스크립트를 생성합니다."""
        script_id = f"script-{uuid.uuid4().hex[:12]}"

        # 첫 번째 문서의 첫 번째 청크로 기본 씬 생성
        first_doc_id = list(document_chunks.keys())[0] if document_chunks else None
        first_chunk = document_chunks.get(first_doc_id, [{}])[0] if first_doc_id else {}

        scenes = [
            GeneratedScene(
                scene_id=f"scene-{uuid.uuid4().hex[:8]}",
                scene_index=1,
                purpose="도입",
                narration=first_chunk.get("chunk_text", "교육 내용을 시작합니다.")[:200],
                caption="교육 시작",
                visual="타이틀 슬라이드",
                duration_sec=15.0,
                confidence_score=0.5,
                source_refs=[
                    SourceRef(
                        document_id=first_doc_id or "unknown",
                        chunk_index=0,
                    )
                ] if first_doc_id else [],
            ),
        ]

        chapters = [
            GeneratedChapter(
                chapter_id=f"chapter-{uuid.uuid4().hex[:8]}",
                chapter_index=1,
                title="교육 내용",
                duration_sec=15.0,
                scenes=scenes,
            ),
        ]

        return GeneratedScript(
            script_id=script_id,
            education_id=job.education_id,
            source_set_id=job.source_set_id,
            title="교육 스크립트 (자동 생성 실패 - 폴백)",
            total_duration_sec=15.0,
            version=1,
            llm_model="fallback",
            chapters=chapters,
        )

    # =========================================================================
    # RAGFlow Callback Handling
    # =========================================================================
    
    async def _wait_for_callback(
        self,
        ingest_id: str,
        timeout: float,
        doc_id: str,
    ) -> Optional[Dict[str, Any]]:
        """RAGFlow 콜백을 기다립니다.
        
        Args:
            ingest_id: RAGFlow ingest ID
            timeout: 타임아웃 (초)
            doc_id: 문서 ID (로깅용)
            
        Returns:
            Optional[Dict]: 콜백 결과 또는 None (타임아웃)
        """
        # Event 생성 및 등록
        event = asyncio.Event()
        self._callback_events[ingest_id] = event
        
        try:
            logger.info(
                f"Waiting for callback: ingest_id={ingest_id}, "
                f"doc_id={doc_id}, timeout={timeout}s. "
                f"Note: RAGFlow server must be configured with callback URL: "
                f"POST /v1/internal_ragflow/internal/ai/callbacks/ragflow/ingest"
            )
            
            # 주기적으로 대기 상태 로깅 (5분마다)
            check_interval = 300.0  # 5분
            start_time = asyncio.get_event_loop().time()
            last_log_time = start_time
            
            # 타임아웃과 함께 대기
            try:
                while True:
                    elapsed = asyncio.get_event_loop().time() - start_time
                    remaining = timeout - elapsed
                    
                    if remaining <= 0:
                        raise asyncio.TimeoutError()
                    
                    # 5분마다 상태 로깅
                    if elapsed - last_log_time >= check_interval:
                        logger.info(
                            f"Still waiting for callback: ingest_id={ingest_id}, "
                            f"doc_id={doc_id}, elapsed={elapsed:.1f}s, "
                            f"remaining={remaining:.1f}s. "
                            f"Please verify RAGFlow server callback configuration."
                        )
                        last_log_time = elapsed
                    
                    # 짧은 간격으로 체크 (타임아웃 정확도 향상)
                    wait_time = min(remaining, check_interval)
                    try:
                        await asyncio.wait_for(event.wait(), timeout=wait_time)
                        break  # 이벤트가 설정됨
                    except asyncio.TimeoutError:
                        continue  # 계속 대기
                        
            except asyncio.TimeoutError:
                logger.error(
                    f"Callback timeout: ingest_id={ingest_id}, "
                    f"doc_id={doc_id}, timeout={timeout}s. "
                    f"RAGFlow server did not send callback. "
                    f"Please verify: "
                    f"1. RAGFlow server callback URL is configured correctly, "
                    f"2. RAGFlow server can reach AI server at callback endpoint, "
                    f"3. RAGFlow server has correct AI_CALLBACK_TOKEN."
                )
                return None
            
            # 콜백 결과 반환
            result = self._callback_results.get(ingest_id)
            if result:
                logger.info(
                    f"Callback received: ingest_id={ingest_id}, "
                    f"doc_id={doc_id}, status={result.get('status')}"
                )
                return result
            else:
                logger.warning(
                    f"Callback event set but no result: ingest_id={ingest_id}, "
                    f"doc_id={doc_id}"
                )
                return None
                
        finally:
            # 정리
            if ingest_id in self._callback_events:
                del self._callback_events[ingest_id]
            if ingest_id in self._callback_results:
                del self._callback_results[ingest_id]
    
    def notify_callback(
        self,
        ingest_id: str,
        status: str,
        document_id: Optional[str] = None,
        chunk_count: Optional[int] = None,
        fail_reason: Optional[str] = None,
    ) -> None:
        """RAGFlow 콜백을 처리합니다.
        
        Args:
            ingest_id: RAGFlow ingest ID
            status: 상태 (COMPLETED, FAILED)
            document_id: RAGFlow 내부 문서 ID (선택)
            chunk_count: 청크 수 (선택)
            fail_reason: 실패 사유 (선택)
        """
        logger.info(
            f"RAGFlow callback notification: ingest_id={ingest_id}, "
            f"status={status}, document_id={document_id}, "
            f"chunk_count={chunk_count}"
        )
        
        # 결과 저장
        result = {
            "status": status,
            "documentId": document_id,
            "chunkCount": chunk_count,
            "failReason": fail_reason,
        }
        self._callback_results[ingest_id] = result
        
        # Event 설정 (대기 중인 태스크 깨우기)
        if ingest_id in self._callback_events:
            self._callback_events[ingest_id].set()
            logger.debug(f"Callback event set: ingest_id={ingest_id}")
        else:
            logger.warning(
                f"Callback received but no waiting task: ingest_id={ingest_id}"
            )

    # =========================================================================
    # Callbacks
    # =========================================================================

    async def _send_scene_patch_callback(
        self,
        job: ProcessingJob,
        chapter_index: int,
        chapter_title: str,
        scene_index: int,
        scene: GeneratedScene,
        script_id: str,
        current_scene: int,
        total_scenes: int,
    ) -> None:
        """씬 패치 콜백을 백엔드에 전송합니다.

        씬이 생성될 때마다 호출되어 부분 저장을 수행합니다.

        Args:
            job: 처리 작업 상태
            chapter_index: 챕터 인덱스 (0-based)
            chapter_title: 챕터 제목
            scene_index: 씬 인덱스 (0-based)
            scene: 생성된 씬
            script_id: 스크립트 ID
            current_scene: 현재 씬 번호 (1-based)
            total_scenes: 전체 씬 수
        """
        # 결정적 request_id 생성 (재시도 시 중복 방지)
        # 백엔드가 UUID 형식을 기대하므로 UUID5로 변환 (같은 입력에 대해 항상 같은 UUID 생성)
        patch_request_id_str = f"{job.source_set_id}:{chapter_index}:{scene_index}"
        # UUID5 namespace (고정된 namespace 사용)
        namespace = uuid.UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')  # DNS namespace 재사용
        patch_request_id = str(uuid.uuid5(namespace, patch_request_id_str))

        patch = ScriptPatch(
            type="SCENE_UPSERT",
            script_id=script_id,
            chapter_index=chapter_index,
            chapter_title=chapter_title,
            scene_index=scene_index,
            scene=scene,
            total_scenes=total_scenes,
            current_scene=current_scene,
        )

        request = SourceSetCompleteRequest(
            video_id=job.video_id,
            status="COMPLETED",
            source_set_status="SCRIPT_GENERATING",
            documents=job.document_results,
            script=None,
            script_patch=patch,
            request_id=patch_request_id,
            trace_id=job.trace_id,
        )

        try:
            await self._backend_client.notify_source_set_complete(
                job.source_set_id, request
            )
            logger.info(
                f"Scene patch callback sent: source_set_id={job.source_set_id}, "
                f"chapter={chapter_index}, scene={scene_index}, "
                f"progress={current_scene}/{total_scenes}"
            )
        except SourceSetCompleteCallbackError as e:
            # 패치 전송 실패는 경고로 처리 (다음 씬 계속 진행)
            logger.warning(
                f"Failed to send scene patch callback: source_set_id={job.source_set_id}, "
                f"chapter={chapter_index}, scene={scene_index}, error={e}"
            )

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
        self._job_store.save(job)  # 상태 저장

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

