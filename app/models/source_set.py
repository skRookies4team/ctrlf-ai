"""
SourceSet 오케스트레이션 모델 (Phase 1)

멀티 문서 소스셋을 처리하고 스크립트를 생성하는 오케스트레이션 API를 위한 모델입니다.

흐름:
1. Spring → FastAPI: POST /internal/ai/source-sets/{sourceSetId}/start
2. FastAPI → Spring: GET /internal/source-sets/{sourceSetId}/documents
3. FastAPI → RAGFlow: 문서별 ingest
4. FastAPI → Milvus: 벡터 저장
5. FastAPI → Spring: POST /internal/rag/documents/{docId}/chunks:bulk
6. FastAPI → Spring: POST /internal/callbacks/source-sets/{sourceSetId}/complete

상태 머신 (DB: education.source_set.status):
- CREATED → LOCKED → SCRIPT_READY | FAILED
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Enums
# =============================================================================


class SourceSetStatus(str, Enum):
    """소스셋 상태 (DB: education.source_set.status)."""
    CREATED = "CREATED"
    LOCKED = "LOCKED"
    SCRIPT_READY = "SCRIPT_READY"
    FAILED = "FAILED"


class DocumentStatus(str, Enum):
    """문서 상태."""
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# =============================================================================
# Request Models (Spring → FastAPI)
# =============================================================================


class SourceSetStartRequest(BaseModel):
    """소스셋 작업 시작 요청.

    POST /internal/ai/source-sets/{sourceSetId}/start
    """
    education_id: Optional[str] = Field(
        None,
        alias="educationId",
        description="연결 교육 ID (선택)",
    )
    video_id: str = Field(
        ...,
        alias="videoId",
        description="영상 ID (백엔드 발급)",
    )
    request_id: Optional[str] = Field(
        None,
        alias="requestId",
        description="멱등 키 (권장)",
    )
    trace_id: Optional[str] = Field(
        None,
        alias="traceId",
        description="추적용 (권장)",
    )
    script_policy_id: Optional[str] = Field(
        None,
        alias="scriptPolicyId",
        description="스크립트 생성 정책 프리셋 (선택)",
    )
    llm_model_hint: Optional[str] = Field(
        None,
        alias="llmModelHint",
        description="사용 모델 힌트 (선택)",
    )

    class Config:
        populate_by_name = True


class SourceSetStartResponse(BaseModel):
    """소스셋 작업 시작 응답.

    202 Accepted
    """
    received: bool = Field(
        ...,
        description="접수 여부",
    )
    source_set_id: str = Field(
        ...,
        alias="sourceSetId",
        description="소스셋 ID",
    )
    status: SourceSetStatus = Field(
        ...,
        description="현재 상태 (LOCKED)",
    )

    class Config:
        populate_by_name = True


# =============================================================================
# Document Models (FastAPI ↔ Spring)
# =============================================================================


class SourceSetDocument(BaseModel):
    """소스셋 내 문서.

    GET /internal/source-sets/{sourceSetId}/documents 응답의 documents[] 항목
    """
    document_id: str = Field(
        ...,
        alias="documentId",
        description="문서 ID",
    )
    title: str = Field(
        ...,
        description="문서 제목",
    )
    domain: str = Field(
        ...,
        description="문서 도메인 (FOUR_MANDATORY 등)",
    )
    source_url: str = Field(
        ...,
        alias="sourceUrl",
        description="문서 원본 URL (S3 presigned URL 등)",
    )
    status: DocumentStatus = Field(
        default=DocumentStatus.QUEUED,
        description="문서 상태",
    )

    class Config:
        populate_by_name = True


class SourceSetDocumentsResponse(BaseModel):
    """소스셋 문서 목록 응답.

    GET /internal/source-sets/{sourceSetId}/documents
    """
    source_set_id: str = Field(
        ...,
        alias="sourceSetId",
        description="소스셋 ID",
    )
    documents: List[SourceSetDocument] = Field(
        default_factory=list,
        description="문서 목록",
    )

    class Config:
        populate_by_name = True


# =============================================================================
# Callback Models (FastAPI → Spring)
# =============================================================================


class DocumentResult(BaseModel):
    """문서별 처리 결과.

    콜백 body의 documents[] 항목
    """
    document_id: str = Field(
        ...,
        alias="documentId",
        description="문서 ID",
    )
    status: str = Field(
        ...,
        description="처리 결과 (COMPLETED | FAILED)",
    )
    fail_reason: Optional[str] = Field(
        None,
        alias="failReason",
        description="실패 사유 (실패 시)",
    )

    class Config:
        populate_by_name = True


class SourceRef(BaseModel):
    """씬의 출처 참조.

    멀티문서 환경에서 씬이 어느 문서/청크에서 생성되었는지 추적
    """
    document_id: str = Field(
        ...,
        alias="documentId",
        description="문서 ID",
    )
    chunk_index: int = Field(
        ...,
        alias="chunkIndex",
        description="청크 인덱스",
    )

    class Config:
        populate_by_name = True


class GeneratedScene(BaseModel):
    """생성된 씬.

    Note: sceneId는 백엔드 JPA @GeneratedValue로 자동 생성되므로 AI에서 전송하지 않음.
    sceneIndex는 0-based (0, 1, 2, ...)
    """
    scene_index: int = Field(
        ...,
        alias="sceneIndex",
        description="씬 순서 (0-based)",
    )
    purpose: str = Field(
        ...,
        description="씬 목적",
    )
    narration: str = Field(
        ...,
        description="나레이션 텍스트",
    )
    caption: Optional[str] = Field(
        None,
        description="자막/캡션",
    )
    visual: Optional[str] = Field(
        None,
        description="시각 자료 설명",
    )
    duration_sec: float = Field(
        ...,
        alias="durationSec",
        description="씬 길이 (초)",
    )
    confidence_score: Optional[float] = Field(
        None,
        alias="confidenceScore",
        description="신뢰도 점수",
    )
    source_refs: List[SourceRef] = Field(
        default_factory=list,
        alias="sourceRefs",
        description="출처 참조 목록",
    )

    class Config:
        populate_by_name = True


class GeneratedChapter(BaseModel):
    """생성된 챕터.

    Note: chapterId는 백엔드 JPA @GeneratedValue로 자동 생성되므로 AI에서 전송하지 않음.
    chapterIndex는 0-based (0, 1, 2, ...)
    """
    chapter_index: int = Field(
        ...,
        alias="chapterIndex",
        description="챕터 순서 (0-based)",
    )
    title: str = Field(
        ...,
        description="챕터 제목",
    )
    duration_sec: float = Field(
        ...,
        alias="durationSec",
        description="챕터 길이 (초)",
    )
    scenes: List[GeneratedScene] = Field(
        default_factory=list,
        description="씬 목록",
    )

    class Config:
        populate_by_name = True


class GeneratedScript(BaseModel):
    """자동 생성된 스크립트.

    콜백 body의 script 객체
    Spring DB에 바로 저장 가능한 정본 JSON 구조
    """
    script_id: str = Field(
        ...,
        alias="scriptId",
        description="스크립트 ID",
    )
    education_id: Optional[str] = Field(
        None,
        alias="educationId",
        description="교육 ID",
    )
    source_set_id: str = Field(
        ...,
        alias="sourceSetId",
        description="소스셋 ID",
    )
    title: str = Field(
        ...,
        description="스크립트 제목",
    )
    total_duration_sec: float = Field(
        ...,
        alias="totalDurationSec",
        description="전체 길이 (초)",
    )
    version: int = Field(
        default=1,
        description="버전",
    )
    llm_model: Optional[str] = Field(
        None,
        alias="llmModel",
        description="사용된 LLM 모델",
    )
    chapters: List[GeneratedChapter] = Field(
        default_factory=list,
        description="챕터 목록",
    )

    class Config:
        populate_by_name = True


class SourceSetCompleteRequest(BaseModel):
    """소스셋 완료 콜백 요청.

    POST /internal/callbacks/source-sets/{sourceSetId}/complete
    """
    video_id: str = Field(
        ...,
        alias="videoId",
        description="영상 ID",
    )
    status: str = Field(
        ...,
        description="결과 상태 (COMPLETED | FAILED)",
    )
    source_set_status: str = Field(
        ...,
        alias="sourceSetStatus",
        description="소스셋 DB 상태 (SCRIPT_READY | FAILED)",
    )
    documents: List[DocumentResult] = Field(
        default_factory=list,
        description="문서별 결과",
    )
    script: Optional[GeneratedScript] = Field(
        None,
        description="생성된 스크립트 (성공 시)",
    )
    error_code: Optional[str] = Field(
        None,
        alias="errorCode",
        description="실패 코드",
    )
    error_message: Optional[str] = Field(
        None,
        alias="errorMessage",
        description="실패 메시지",
    )
    request_id: Optional[str] = Field(
        None,
        alias="requestId",
        description="멱등 키",
    )
    trace_id: Optional[str] = Field(
        None,
        alias="traceId",
        description="추적용",
    )

    class Config:
        populate_by_name = True


class SourceSetCompleteResponse(BaseModel):
    """소스셋 완료 콜백 응답."""
    saved: bool = Field(
        ...,
        description="저장 여부",
    )


# =============================================================================
# Chunk Models (FastAPI → Spring DB 저장)
# =============================================================================


class ChunkItem(BaseModel):
    """청크 항목.

    POST /internal/rag/documents/{documentId}/chunks:bulk
    """
    chunk_index: int = Field(
        ...,
        alias="chunkIndex",
        description="청크 번호",
    )
    chunk_text: str = Field(
        ...,
        alias="chunkText",
        description="청크 텍스트",
    )
    chunk_meta: Optional[Dict[str, Any]] = Field(
        None,
        alias="chunkMeta",
        description="메타데이터 (권장)",
    )

    class Config:
        populate_by_name = True


class ChunkBulkUpsertRequest(BaseModel):
    """청크 벌크 업서트 요청.

    POST /internal/rag/documents/{documentId}/chunks:bulk
    """
    chunks: List[ChunkItem] = Field(
        ...,
        description="청크 리스트",
    )
    request_id: Optional[str] = Field(
        None,
        alias="requestId",
        description="멱등 키",
    )

    class Config:
        populate_by_name = True


class ChunkBulkUpsertResponse(BaseModel):
    """청크 벌크 업서트 응답."""
    saved: bool = Field(
        ...,
        description="저장 여부",
    )
    count: int = Field(
        ...,
        description="저장된 청크 수",
    )


class FailChunkItem(BaseModel):
    """임베딩 실패 청크 항목.

    POST /internal/rag/documents/{documentId}/fail-chunks:bulk
    """
    chunk_index: int = Field(
        ...,
        alias="chunkIndex",
        description="청크 번호",
    )
    fail_reason: str = Field(
        ...,
        alias="failReason",
        description="실패 사유 (OCR_EMPTY, EMBEDDING_TIMEOUT 등)",
    )

    class Config:
        populate_by_name = True


class FailChunkBulkUpsertRequest(BaseModel):
    """실패 청크 벌크 업서트 요청.

    POST /internal/rag/documents/{documentId}/fail-chunks:bulk
    """
    fails: List[FailChunkItem] = Field(
        ...,
        description="실패 청크 리스트",
    )
    request_id: Optional[str] = Field(
        None,
        alias="requestId",
        description="멱등 키",
    )

    class Config:
        populate_by_name = True


class FailChunkBulkUpsertResponse(BaseModel):
    """실패 청크 벌크 업서트 응답."""
    saved: bool = Field(
        ...,
        description="저장 여부",
    )
    count: int = Field(
        ...,
        description="저장된 실패 로그 수",
    )
