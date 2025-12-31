"""
Internal RAG Models (Phase 25)

백엔드에서 AI 서버로 직접 RAG 인덱싱/삭제 요청을 보내기 위한 모델입니다.
RAGFlow를 우회하고 AI 서버가 Milvus에 직접 upsert/delete/search를 수행합니다.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl


# =============================================================================
# Job Status
# =============================================================================


class JobStatus(str, Enum):
    """인덱싱/삭제 작업 상태."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# =============================================================================
# Index Request/Response
# =============================================================================


class InternalRagIndexRequest(BaseModel):
    """
    POST /internal/rag/index 요청 모델.

    백엔드에서 문서 인덱싱을 요청할 때 사용합니다.
    """

    document_id: str = Field(
        ...,
        alias="documentId",
        description="문서 고유 ID",
        examples=["DOC-001"],
    )
    version_no: int = Field(
        ...,
        alias="versionNo",
        description="문서 버전 번호",
        ge=1,
        examples=[1],
    )
    title: Optional[str] = Field(
        None,
        description="문서 제목",
        examples=["인사규정 v2.0"],
    )
    domain: str = Field(
        ...,
        description="문서 도메인 (POLICY, EDU, INCIDENT 등)",
        examples=["POLICY"],
    )
    file_url: str = Field(
        ...,
        alias="fileUrl",
        description="문서 파일 URL (http(s) 또는 presigned URL)",
        examples=["https://storage.example.com/docs/hr-policy.pdf"],
    )
    requested_by: Optional[str] = Field(
        None,
        alias="requestedBy",
        description="요청자 ID",
        examples=["admin-001"],
    )
    job_id: str = Field(
        ...,
        alias="jobId",
        description="작업 추적용 고유 Job ID",
        examples=["job-uuid-1234"],
    )

    class Config:
        populate_by_name = True


class InternalRagIndexResponse(BaseModel):
    """
    POST /internal/rag/index 응답 모델.
    """

    job_id: str = Field(..., alias="jobId", description="작업 ID")
    status: JobStatus = Field(..., description="작업 상태")
    message: str = Field(..., description="상태 메시지")

    class Config:
        populate_by_name = True


# =============================================================================
# Delete Request/Response
# =============================================================================


class InternalRagDeleteRequest(BaseModel):
    """
    POST /internal/rag/delete 요청 모델.

    백엔드에서 문서 삭제를 요청할 때 사용합니다.
    """

    document_id: str = Field(
        ...,
        alias="documentId",
        description="문서 고유 ID",
        examples=["DOC-001"],
    )
    version_no: Optional[int] = Field(
        None,
        alias="versionNo",
        description="삭제할 버전 번호 (없으면 전체 버전 삭제)",
        ge=1,
        examples=[1],
    )
    job_id: Optional[str] = Field(
        None,
        alias="jobId",
        description="작업 추적용 고유 Job ID",
        examples=["job-uuid-5678"],
    )

    class Config:
        populate_by_name = True


class InternalRagDeleteResponse(BaseModel):
    """
    POST /internal/rag/delete 응답 모델.
    """

    job_id: Optional[str] = Field(None, alias="jobId", description="작업 ID")
    status: JobStatus = Field(..., description="작업 상태")
    deleted_count: int = Field(
        ..., alias="deletedCount", description="삭제된 청크 수"
    )
    message: str = Field(..., description="상태 메시지")

    class Config:
        populate_by_name = True


# =============================================================================
# Job Status Response
# =============================================================================


class JobStatusResponse(BaseModel):
    """
    GET /internal/jobs/{jobId} 응답 모델.

    백엔드에서 작업 상태를 폴링할 때 사용합니다.
    """

    job_id: str = Field(..., alias="jobId", description="작업 ID")
    status: JobStatus = Field(..., description="현재 작업 상태")
    document_id: Optional[str] = Field(
        None, alias="documentId", description="문서 ID"
    )
    version_no: Optional[int] = Field(
        None, alias="versionNo", description="버전 번호"
    )
    progress: Optional[str] = Field(
        None, description="진행 단계 (downloading, extracting, chunking, embedding, upserting)"
    )
    chunks_processed: Optional[int] = Field(
        None, alias="chunksProcessed", description="처리된 청크 수"
    )
    error_message: Optional[str] = Field(
        None, alias="errorMessage", description="실패 시 에러 메시지"
    )
    created_at: Optional[str] = Field(
        None, alias="createdAt", description="작업 생성 시간 (ISO 8601)"
    )
    updated_at: Optional[str] = Field(
        None, alias="updatedAt", description="마지막 업데이트 시간 (ISO 8601)"
    )

    class Config:
        populate_by_name = True


# =============================================================================
# Chunk Models (for internal processing)
# =============================================================================


class DocumentChunk(BaseModel):
    """
    문서 청크 모델 (내부 처리용).

    텍스트 추출/청킹 후 Milvus에 저장하기 전 단계의 청크입니다.
    """

    document_id: str = Field(..., description="문서 ID")
    version_no: int = Field(..., description="버전 번호")
    domain: str = Field(..., description="도메인")
    title: str = Field(..., description="문서 제목")
    chunk_id: int = Field(..., description="청크 순번 (0부터 시작)")
    chunk_text: str = Field(..., description="청크 텍스트")
    page: Optional[int] = Field(None, description="페이지 번호 (PDF인 경우)")
    section_path: Optional[str] = Field(
        None, description="섹션 경로 (예: 제1장 > 제2조)"
    )
    embedding: Optional[List[float]] = Field(
        None, description="임베딩 벡터"
    )


class MilvusChunkRecord(BaseModel):
    """
    Milvus에 저장되는 청크 레코드 스키마.

    필수 필드: document_id, version_no, domain, title, chunk_id, chunk_text, embedding
    선택 필드: page, section_path
    """

    document_id: str
    version_no: int
    domain: str
    title: str
    chunk_id: int
    chunk_text: str
    embedding: List[float]
    page: Optional[int] = None
    section_path: Optional[str] = None


# =============================================================================
# Search Models
# =============================================================================


class ChunkSearchRequest(BaseModel):
    """
    검색 요청 모델.
    """

    query: str = Field(..., description="검색 쿼리")
    domain: str = Field(..., description="도메인 필터")
    top_k: int = Field(5, description="반환할 최대 결과 수", ge=1, le=100)
    version_no: Optional[int] = Field(
        None,
        alias="versionNo",
        description="특정 버전만 검색 (없으면 최신 버전)",
    )
    filters: Optional[Dict[str, Any]] = Field(
        None, description="추가 필터 조건"
    )

    class Config:
        populate_by_name = True


class ChunkSearchResult(BaseModel):
    """
    검색 결과 청크.
    """

    document_id: str = Field(..., alias="documentId")
    version_no: int = Field(..., alias="versionNo")
    domain: str
    title: str
    chunk_id: int = Field(..., alias="chunkId")
    chunk_text: str = Field(..., alias="chunkText")
    score: float
    page: Optional[int] = None
    section_path: Optional[str] = Field(None, alias="sectionPath")

    class Config:
        populate_by_name = True
