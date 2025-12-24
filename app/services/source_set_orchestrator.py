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

            # 2. 각 문서 처리 및 청크 수집
            all_document_chunks: Dict[str, List[Dict[str, Any]]] = {}  # doc_id → chunks
            document_results: List[DocumentResult] = []
            processing_results: List[DocumentProcessingResult] = []
            has_failure = False

            for doc in job.documents:
                try:
                    result = await self._process_document(source_set_id, doc, job)
                    processing_results.append(result)
                    document_results.append(
                        DocumentResult(
                            document_id=doc.document_id,
                            status="COMPLETED" if result.success else "FAILED",
                            fail_reason=result.fail_reason,
                        )
                    )
                    if result.success and result.chunks:
                        all_document_chunks[doc.document_id] = result.chunks
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

            # 4. 청크가 없으면 실패 처리
            total_chunks = sum(len(chunks) for chunks in all_document_chunks.values())
            if total_chunks == 0:
                await self._send_failure_callback(
                    job,
                    error_code="NO_CHUNKS_GENERATED",
                    error_message="문서 처리는 성공했으나 청크가 생성되지 않았습니다.",
                )
                return

            # 5. 스크립트 생성
            logger.info(f"Generating script: source_set_id={source_set_id}, total_chunks={total_chunks}")
            script = await self._generate_script(job, all_document_chunks)
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
        from app.clients.ragflow_client import RagflowError, RagflowConnectionError

        settings = get_settings()

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

            # 1. RAGFlow에 문서 업로드
            logger.info(f"Uploading document to RAGFlow: doc_id={doc.document_id}")
            file_name = doc.source_url.split("/")[-1].split("?")[0] or f"{doc.document_id}.pdf"

            upload_result = await self._ragflow_client.upload_document(
                dataset_id=dataset_id,
                file_url=doc.source_url,
                file_name=file_name,
            )
            ragflow_doc_id = upload_result.get("id")

            if not ragflow_doc_id:
                return DocumentProcessingResult(
                    document_id=doc.document_id,
                    success=False,
                    fail_reason="RAGFlow document upload failed: no document ID returned",
                )

            logger.info(f"Document uploaded: doc_id={doc.document_id}, ragflow_id={ragflow_doc_id}")

            # 2. 파싱 트리거
            await self._ragflow_client.trigger_parsing(
                dataset_id=dataset_id,
                document_ids=[ragflow_doc_id],
            )

            # 3. Polling으로 완료 대기
            final_status, chunk_count = await self._poll_document_status(
                dataset_id=dataset_id,
                document_id=ragflow_doc_id,
                poll_interval=settings.RAGFLOW_POLL_INTERVAL_SEC,
                timeout=settings.RAGFLOW_POLL_TIMEOUT_SEC,
            )

            if final_status != "DONE":
                fail_reason = f"RAGFlow parsing {final_status}"
                logger.warning(f"Document parsing failed: doc_id={doc.document_id}, status={final_status}")
                return DocumentProcessingResult(
                    document_id=doc.document_id,
                    success=False,
                    fail_reason=fail_reason,
                )

            # 4. 청크 조회
            logger.info(f"Fetching chunks: doc_id={doc.document_id}, count={chunk_count}")
            chunks = await self._fetch_all_chunks(
                dataset_id=dataset_id,
                document_id=ragflow_doc_id,
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

    async def _generate_script(
        self,
        job: ProcessingJob,
        document_chunks: Dict[str, List[Dict[str, Any]]],
    ) -> GeneratedScript:
        """스크립트를 생성합니다 (Step 3: LLM 연동).

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

        user_prompt = f"""다음 교육 자료를 바탕으로 교육 영상 스크립트를 생성해주세요:

{full_context[:15000]}  # 토큰 제한 고려

JSON 스크립트:"""

        # 3. LLM 호출
        llm_client = LLMClient()
        model = job.llm_model_hint or "qwen2.5-14b-instruct"

        try:
            response = await llm_client.generate_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=model,
                temperature=0.3,
                max_tokens=4096,
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
