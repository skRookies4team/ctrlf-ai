"""
사내규정(POLICY) 문서 Ingest API

Backend → AI → RAGFlow 문서 ingest 파이프라인을 위한 내부 API입니다.

엔드포인트:
1. POST /internal/ai/rag-documents/ingest : Backend → AI ingest 요청
2. POST /v1/internal_ragflow/internal/ai/callbacks/ragflow/ingest : RAGFlow → AI ingest 완료/실패 콜백

흐름:
1. Backend → AI: POST /internal/ai/rag-documents/ingest
2. AI → RAGFlow: POST {RAGFLOW_BASE_URL}/v1/internal_ragflow/internal/ragflow/ingest (비동기)
3. RAGFlow → AI: POST /v1/internal_ragflow/internal/ai/callbacks/ragflow/ingest (콜백)
4. AI → Backend: PATCH /internal/rag/documents/{ragDocumentPk}/status

인증:
- X-Internal-Token 헤더 필수
"""

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, unquote

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.clients.backend_client import (
    RAGDocumentStatusUpdateError,
    get_backend_client,
)
from app.clients.milvus_client import get_milvus_client
from app.clients.ragflow_ingest_client import (
    RAGFlowIngestError,
    RAGFlowUnavailableError,
    get_ragflow_ingest_client,
)
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/internal_ragflow/internal/ai", tags=["RAG Documents Ingest"])


# =============================================================================
# Constants
# =============================================================================

# 허용 도메인 (백엔드에서 전달하는 도메인명 = RAGFlow dataset_id)
ALLOWED_DOMAINS = {
    "사내규정",
    "직무교육",
    "장애인인식개선교육",
    "직장내괴롭힘교육",
    "직장내성희롱교육",
    "정보보안교육",
}

# 도메인 → RAGFlow dataset_id 매핑 (동일한 값 사용)
DOMAIN_DATASET_MAPPING = {
    "사내규정": "사내규정",
    "직무교육": "직무교육",
    "장애인인식개선교육": "장애인인식개선교육",
    "직장내괴롭힘교육": "직장내괴롭힘교육",
    "직장내성희롱교육": "직장내성희롱교육",
    "정보보안교육": "정보보안교육",
}


# =============================================================================
# URL Utility Functions
# =============================================================================


def format_uuid(uuid_str: str) -> str:
    """UUID 문자열을 하이픈이 있는 표준 형식으로 변환합니다.
    
    하이픈 없는 형식 (32자): a7aedf70ec8411f08c69f6c265103247
    하이픈 있는 형식 (36자): a7aedf70-ec84-11f0-8c69-f6c265103247
    
    Args:
        uuid_str: UUID 문자열 (하이픈 있거나 없거나)
        
    Returns:
        str: 하이픈이 있는 표준 UUID 형식
    """
    if not uuid_str:
        return uuid_str
    
    # 이미 하이픈이 있으면 그대로 반환
    if '-' in uuid_str:
        return uuid_str
    
    # 하이픈이 없는 32자리 문자열인 경우만 변환
    if len(uuid_str) == 32:
        # xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx 형식으로 변환
        return f"{uuid_str[:8]}-{uuid_str[8:12]}-{uuid_str[12:16]}-{uuid_str[16:20]}-{uuid_str[20:]}"
    
    # 그 외의 경우는 그대로 반환
    return uuid_str


def extract_filename_from_url(url: str) -> str:
    """Presigned URL에서 파일명을 추출합니다.

    S3 Presigned URL 형식:
    https://bucket.s3.region.amazonaws.com/path/to/file.pdf?X-Amz-Algorithm=...

    Args:
        url: S3 Presigned URL

    Returns:
        str: 파일명 (예: "hr_safety_v3.pdf")

    Raises:
        ValueError: URL에서 파일명을 추출할 수 없는 경우
    """
    try:
        parsed = urlparse(url)
        # URL 디코딩 (한글 파일명 등 처리)
        path = unquote(parsed.path)
        # 마지막 path segment가 파일명
        filename = path.rstrip("/").split("/")[-1]

        if not filename:
            raise ValueError(f"URL에서 파일명을 추출할 수 없습니다: {url[:100]}")

        return filename
    except Exception as e:
        logger.error(f"Failed to extract filename from URL: {url[:100]}, error={e}")
        raise ValueError(f"URL에서 파일명을 추출할 수 없습니다: {str(e)}")


# =============================================================================
# Idempotency 캐시 (in-memory, 2단계 TTL + LRU)
# =============================================================================
# key: (documentId, version), value: {"timestamp": float, "status": str}
_ingest_cache: Dict[tuple, Dict[str, Any]] = {}

# 2단계 TTL 설정:
# - PROCESSING: 5분 (처리 중 요청은 짧게 유지)
# - COMPLETED/FAILED: 24시간 (완료된 요청은 오래 유지하여 중복 방지)
_CACHE_TTL_PROCESSING_SECONDS = 300  # 5분
_CACHE_TTL_COMPLETED_SECONDS = 86400  # 24시간

# LRU 최대 캐시 크기 (메모리 보호)
_CACHE_MAX_SIZE = 10000


# =============================================================================
# Request/Response Models
# =============================================================================


class IngestRequest(BaseModel):
    """Backend → AI ingest 요청."""
    ragDocumentPk: str = Field(..., description="RAG 문서 PK (UUID)")
    documentId: str = Field(..., description="문서 ID (예: 사내규정.pdf)")
    version: int = Field(..., description="문서 버전")
    sourceUrl: str = Field(..., description="문서 파일 URL (S3 등)")
    domain: str = Field(..., description="도메인 (POLICY만 허용)")
    requestId: str = Field(..., description="요청 ID (UUID)")
    traceId: str = Field(..., description="추적 ID")
    title: Optional[str] = Field(
        None,
        description="문서 제목 (파일명.확장자, 예: 보안관리규정.pdf) - RAGFlow doc_id로 사용"
    )
    department: Optional[str] = Field(
        None,
        description="부서 범위 (전체 부서, 총무팀, 기획팀, 마케팅팀, 인사팀, 재무팀, 개발팀, 영업팀, 법무팀)"
    )


class IngestResponse(BaseModel):
    """Backend → AI ingest 응답."""
    received: bool = True
    ragDocumentPk: str
    documentId: str
    version: int
    status: str = "PROCESSING"
    requestId: str
    traceId: str


class IngestCallbackMeta(BaseModel):
    """RAGFlow 콜백 메타데이터."""
    ragDocumentPk: str
    traceId: str
    requestId: str
    domain: Optional[str] = None  # 문서 도메인 (POLICY, EDUCATION 등)
    source_set_id: Optional[str] = Field(None, alias="source_set_id")  # 소스셋 ID (SourceSet 처리 시 사용)
    spring_document_id: Optional[str] = Field(None, alias="spring_document_id")  # Spring 문서 ID


class IngestCallbackStats(BaseModel):
    """RAGFlow 콜백 통계."""
    chunks: int = 0


class IngestCallbackRequest(BaseModel):
    """RAGFlow → AI ingest 콜백 요청."""
    ingestId: str = Field(..., description="RAGFlow ingest ID")
    docId: str = Field(..., description="문서 ID")
    version: int = Field(..., description="문서 버전")
    status: str = Field(..., description="상태 (COMPLETED|FAILED)")
    processedAt: str = Field(..., description="처리 완료 시간 (ISO-8601)")
    failReason: Optional[str] = Field(None, description="실패 사유")
    meta: IngestCallbackMeta
    stats: Optional[IngestCallbackStats] = None


class IngestCallbackResponse(BaseModel):
    """RAGFlow → AI ingest 콜백 응답."""
    received: bool = True


class ErrorResponse(BaseModel):
    """에러 응답."""
    error: str
    message: str
    traceId: Optional[str] = None


# =============================================================================
# Error Response Helper
# =============================================================================


def _error_response(
    status_code: int,
    error: str,
    message: str,
    trace_id: Optional[str] = None,
) -> JSONResponse:
    """에러 응답을 생성합니다.

    프롬프트 요구사항:
    { "error": "ERROR_CODE", "message": "human readable", "traceId": "..." }
    """
    content = {
        "error": error,
        "message": message,
    }
    if trace_id:
        content["traceId"] = trace_id
    return JSONResponse(status_code=status_code, content=content)


# =============================================================================
# Dependencies
# =============================================================================


async def verify_internal_token(
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
) -> None:
    """내부 API 인증 토큰 검증.

    Args:
        x_internal_token: X-Internal-Token 헤더 값

    Raises:
        JSONResponse: 인증 실패 시 401
    """
    settings = get_settings()
    expected_token = settings.BACKEND_INTERNAL_TOKEN

    # 토큰이 설정되지 않은 경우 (개발 환경)
    if not expected_token:
        logger.warning("BACKEND_INTERNAL_TOKEN not configured, skipping auth")
        return

    if not x_internal_token:
        raise UnauthorizedError("X-Internal-Token 헤더가 필요합니다.")

    if x_internal_token != expected_token:
        raise UnauthorizedError("유효하지 않은 인증 토큰입니다.")


class UnauthorizedError(Exception):
    """인증 실패 예외."""
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


# =============================================================================
# Helper Functions
# =============================================================================


def _get_ttl_for_status(status: str) -> int:
    """상태별 TTL을 반환합니다.

    Args:
        status: 캐시 상태 (PROCESSING|COMPLETED|FAILED)

    Returns:
        int: TTL (초)
    """
    if status == "PROCESSING":
        return _CACHE_TTL_PROCESSING_SECONDS
    else:
        # COMPLETED, FAILED는 24시간 유지
        return _CACHE_TTL_COMPLETED_SECONDS


def _cleanup_expired_cache() -> None:
    """만료된 캐시 항목 정리 (2단계 TTL 적용)."""
    now = time.time()
    expired_keys = []

    for key, value in _ingest_cache.items():
        status = value.get("status", "PROCESSING")
        ttl = _get_ttl_for_status(status)
        if now - value["timestamp"] > ttl:
            expired_keys.append(key)

    for key in expired_keys:
        del _ingest_cache[key]


def _enforce_cache_size_limit() -> None:
    """캐시 크기 제한 적용 (LRU 방식).

    가장 오래된 항목부터 삭제하여 최대 크기 유지.
    """
    if len(_ingest_cache) <= _CACHE_MAX_SIZE:
        return

    # timestamp 기준 정렬하여 오래된 항목 삭제
    sorted_keys = sorted(
        _ingest_cache.keys(),
        key=lambda k: _ingest_cache[k]["timestamp"]
    )

    # 초과분 삭제
    excess_count = len(_ingest_cache) - _CACHE_MAX_SIZE
    for key in sorted_keys[:excess_count]:
        del _ingest_cache[key]

    logger.info(f"Cache size limit enforced: removed {excess_count} oldest entries")


def _get_cached_status(document_id: str, version: int) -> Optional[Dict[str, Any]]:
    """캐시된 상태를 반환합니다.

    Args:
        document_id: 문서 ID
        version: 문서 버전

    Returns:
        Optional[Dict]: 캐시 항목 또는 None
    """
    _cleanup_expired_cache()
    cache_key = (document_id, version)
    return _ingest_cache.get(cache_key)


def _is_duplicate_request(document_id: str, version: int) -> bool:
    """중복 요청인지 확인.

    Args:
        document_id: 문서 ID
        version: 문서 버전

    Returns:
        bool: 중복 요청 여부
    """
    return _get_cached_status(document_id, version) is not None


def _mark_request_processing(document_id: str, version: int) -> None:
    """요청을 처리 중으로 표시.

    Args:
        document_id: 문서 ID
        version: 문서 버전
    """
    cache_key = (document_id, version)
    _ingest_cache[cache_key] = {
        "timestamp": time.time(),
        "status": "PROCESSING",
    }
    # 항목 추가 후 크기 제한 적용 (LRU)
    _enforce_cache_size_limit()


def _mark_request_completed(document_id: str, version: int, ingest_status: str) -> None:
    """요청 완료 상태로 표시.

    완료 상태(COMPLETED/FAILED)는 24시간 TTL로 캐시됩니다.

    Args:
        document_id: 문서 ID
        version: 문서 버전
        ingest_status: 완료 상태 (COMPLETED|FAILED)
    """
    cache_key = (document_id, version)
    _ingest_cache[cache_key] = {
        "timestamp": time.time(),
        "status": ingest_status,  # COMPLETED or FAILED
    }


def _clear_request_cache(document_id: str, version: int) -> None:
    """요청 캐시 삭제 (테스트용).

    Args:
        document_id: 문서 ID
        version: 문서 버전
    """
    cache_key = (document_id, version)
    if cache_key in _ingest_cache:
        del _ingest_cache[cache_key]


def _get_cache_stats() -> Dict[str, Any]:
    """캐시 통계를 반환합니다 (디버깅/모니터링용).

    Returns:
        Dict: 캐시 통계 (total, processing, completed, failed)
    """
    stats = {"total": len(_ingest_cache), "processing": 0, "completed": 0, "failed": 0}
    for value in _ingest_cache.values():
        status = value.get("status", "PROCESSING").lower()
        if status in stats:
            stats[status] += 1
    return stats


# =============================================================================
# Routes
# =============================================================================


@router.post(
    "/rag-documents/ingest",
    summary="사내규정 문서 Ingest 요청 (Backend → AI)",
    description="""
Backend에서 호출하여 사내규정(POLICY) 문서를 RAGFlow에 ingest합니다.

**URL**: POST /internal/ai/rag-documents/ingest

**호출 주체**: Spring 백엔드

**인증**: X-Internal-Token 헤더 필수

**처리 흐름**:
1. 즉시 202 Accepted 반환 (비동기 처리)
2. AI → RAGFlow ingest 요청
3. RAGFlow → AI 콜백으로 완료/실패 수신
4. AI → Backend 상태 업데이트

**멱등성**:
- 이미 처리 중: 202 + PROCESSING
- 이미 완료: 200 + COMPLETED

**제약사항**:
- domain은 POLICY만 허용 (다른 값: 400 INVALID_DOMAIN)
""",
    responses={
        200: {"description": "이미 완료됨", "model": IngestResponse},
        202: {"description": "접수됨 (비동기 처리 시작)", "model": IngestResponse},
        400: {"description": "잘못된 요청 (INVALID_REQUEST, INVALID_DOMAIN)"},
        401: {"description": "인증 실패"},
        502: {"description": "RAGFlow 서비스 불가"},
    },
)
async def ingest_rag_document(
    request: IngestRequest,
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
):
    """사내규정 문서 ingest 요청을 처리합니다."""
    # 인증 검증
    settings = get_settings()
    expected_token = settings.BACKEND_INTERNAL_TOKEN

    if expected_token:  # 토큰이 설정된 경우만 검증
        if not x_internal_token:
            return _error_response(
                status_code=401,
                error="UNAUTHORIZED",
                message="X-Internal-Token 헤더가 필요합니다.",
                trace_id=request.traceId if hasattr(request, 'traceId') else None,
            )
        if x_internal_token != expected_token:
            return _error_response(
                status_code=401,
                error="UNAUTHORIZED",
                message="유효하지 않은 인증 토큰입니다.",
                trace_id=request.traceId,
            )
    else:
        logger.warning("BACKEND_INTERNAL_TOKEN not configured, skipping auth")

    logger.info(
        f"Received ingest request: document_id={request.documentId}, "
        f"version={request.version}, domain={request.domain}, "
        f"trace_id={request.traceId}"
    )

    # 도메인 검증
    if request.domain not in ALLOWED_DOMAINS:
        logger.warning(
            f"Invalid domain: {request.domain}, trace_id={request.traceId}"
        )
        return _error_response(
            status_code=400,
            error="INVALID_DOMAIN",
            message=f"도메인 '{request.domain}'은(는) 허용되지 않습니다. 허용: {list(ALLOWED_DOMAINS)}",
            trace_id=request.traceId,
        )

    # 멱등성 확인 (동일 documentId + version 중복 방지)
    cached = _get_cached_status(request.documentId, request.version)
    if cached:
        cached_status = cached.get("status", "PROCESSING")
        logger.info(
            f"Duplicate request: document_id={request.documentId}, "
            f"version={request.version}, cached_status={cached_status}, "
            f"trace_id={request.traceId}"
        )

        response_data = IngestResponse(
            ragDocumentPk=request.ragDocumentPk,
            documentId=request.documentId,
            version=request.version,
            status=cached_status,
            requestId=request.requestId,
            traceId=request.traceId,
        )

        # 이미 완료된 경우 200, 처리 중인 경우 202
        if cached_status in ("COMPLETED", "FAILED"):
            return JSONResponse(
                status_code=200,
                content=response_data.model_dump(),
            )
        else:
            return JSONResponse(
                status_code=202,
                content=response_data.model_dump(),
            )

    # 처리 중으로 표시
    _mark_request_processing(request.documentId, request.version)

    # RAGFlow dataset_id 매핑
    dataset_id = DOMAIN_DATASET_MAPPING.get(request.domain)
    if not dataset_id:
        return _error_response(
            status_code=400,
            error="INVALID_DOMAIN",
            message=f"도메인 '{request.domain}'에 대한 dataset 매핑이 없습니다.",
            trace_id=request.traceId,
        )

    # title 필드를 doc_id로 사용 (title이 없으면 sourceUrl에서 추출 - fallback)
    if request.title and request.title.strip():
        doc_id_for_ragflow = request.title.strip()
        logger.info(
            f"Using title as doc_id: {doc_id_for_ragflow}, "
            f"original_document_id={request.documentId}, trace_id={request.traceId}"
        )
    else:
        # fallback: sourceUrl에서 파일명 추출 (S3 UUID 파일명이 될 수 있음)
        try:
            doc_id_for_ragflow = extract_filename_from_url(request.sourceUrl)
            logger.warning(
                f"title not provided, extracted doc_id from URL: {doc_id_for_ragflow}, "
                f"original_document_id={request.documentId}, trace_id={request.traceId}"
            )
        except ValueError as e:
            _clear_request_cache(request.documentId, request.version)
            return _error_response(
                status_code=400,
                error="INVALID_SOURCE_URL",
                message=str(e),
                trace_id=request.traceId,
            )

    # RAGFlow ingest 호출 (비동기로 백그라운드에서 처리)
    async def call_ragflow():
        try:
            client = get_ragflow_ingest_client()
            await client.ingest(
                dataset_id=dataset_id,
                doc_id=doc_id_for_ragflow,
                version=request.version,
                file_url=request.sourceUrl,
                rag_document_pk=request.ragDocumentPk,
                domain=request.domain,
                trace_id=request.traceId,
                request_id=request.requestId,
                department=request.department,
            )
            logger.info(
                f"RAGFlow ingest request sent: doc_id={doc_id_for_ragflow}, "
                f"version={request.version}, department={request.department}, "
                f"trace_id={request.traceId}"
            )
        except RAGFlowUnavailableError as e:
            logger.error(
                f"RAGFlow unavailable: document_id={request.documentId}, "
                f"error={e}, trace_id={request.traceId}"
            )
            # 캐시 정리 (재시도 허용)
            _clear_request_cache(request.documentId, request.version)
        except RAGFlowIngestError as e:
            logger.error(
                f"RAGFlow ingest failed: document_id={request.documentId}, "
                f"error={e}, trace_id={request.traceId}"
            )
            _clear_request_cache(request.documentId, request.version)
        except Exception as e:
            logger.error(
                f"RAGFlow ingest unexpected error: document_id={request.documentId}, "
                f"error={e}, trace_id={request.traceId}"
            )
            _clear_request_cache(request.documentId, request.version)

    # 백그라운드 태스크로 RAGFlow 호출
    asyncio.create_task(call_ragflow())

    return JSONResponse(
        status_code=202,
        content=IngestResponse(
            ragDocumentPk=request.ragDocumentPk,
            documentId=request.documentId,
            version=request.version,
            status="PROCESSING",
            requestId=request.requestId,
            traceId=request.traceId,
        ).model_dump(),
    )


@router.post(
    "/callbacks/ragflow/ingest",
    response_model=IngestCallbackResponse,
    status_code=status.HTTP_200_OK,
    summary="RAGFlow Ingest 콜백 (RAGFlow → AI)",
    description="""
RAGFlow에서 ingest 완료/실패 시 호출하는 콜백 엔드포인트입니다.

**URL**: POST /v1/internal_ragflow/internal/ai/callbacks/ragflow/ingest

**호출 주체**: RAGFlow 서비스

**인증**: X-Internal-Token 헤더 필수

**처리 흐름**:
1. 콜백 수신
2. 캐시에 완료 상태 저장 (멱등성)
3. Backend에 상태 업데이트 (PATCH /internal/rag/documents/{ragDocumentPk}/status)
4. 200 OK 반환 (Backend 호출 실패해도 200 반환, 에러 로그만)
""",
    responses={
        200: {"description": "콜백 수신 완료"},
        401: {"description": "인증 실패"},
    },
)
async def ingest_callback(
    request: IngestCallbackRequest,
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
):
    """RAGFlow ingest 콜백을 처리합니다."""
    # 인증 검증 (RAGFlow 전용 토큰 사용)
    # 보안: Backend 토큰과 분리하여 토큰 유출 시 피해 범위 제한
    settings = get_settings()
    expected_token = settings.AI_CALLBACK_TOKEN

    if expected_token:  # 토큰이 설정된 경우만 검증
        if not x_internal_token:
            return _error_response(
                status_code=401,
                error="UNAUTHORIZED",
                message="X-Internal-Token 헤더가 필요합니다.",
                trace_id=request.meta.traceId,
            )
        if x_internal_token != expected_token:
            return _error_response(
                status_code=401,
                error="UNAUTHORIZED",
                message="유효하지 않은 인증 토큰입니다.",
                trace_id=request.meta.traceId,
            )
    else:
        logger.warning("AI_CALLBACK_TOKEN not configured, skipping auth")

    logger.info(
        f"Received RAGFlow ingest callback: doc_id={request.docId}, "
        f"version={request.version}, status={request.status}, "
        f"ingest_id={request.ingestId}, trace_id={request.meta.traceId}, "
        f"source_set_id={request.meta.source_set_id}, spring_document_id={request.meta.spring_document_id}"
    )

    # SourceSet 처리 중인 경우: source_set_orchestrator에 문서 완료 알림
    if request.meta.source_set_id:
        logger.info(
            f"SourceSet callback detected: source_set_id={request.meta.source_set_id}, "
            f"status={request.status}, spring_document_id={request.meta.spring_document_id}"
        )
    else:
        logger.warning(
            f"No source_set_id in callback meta: doc_id={request.docId}, "
            f"meta={request.meta.model_dump() if hasattr(request.meta, 'model_dump') else request.meta.dict() if hasattr(request.meta, 'dict') else request.meta}"
        )
    
    if request.meta.source_set_id and request.status == "COMPLETED":
        try:
            from app.services.source_set_orchestrator import get_source_set_orchestrator
            
            orchestrator = get_source_set_orchestrator()
            # 문서 완료를 알려서 스크립트 생성 및 백엔드 콜백 진행
            await orchestrator.notify_document_completed(
                source_set_id=request.meta.source_set_id,
                document_id=request.meta.spring_document_id or request.docId,
                ragflow_doc_id=request.docId,
                status=request.status,
            )
            logger.info(
                f"Notified source_set_orchestrator: source_set_id={request.meta.source_set_id}, "
                f"document_id={request.meta.spring_document_id}, status={request.status}"
            )
        except Exception as e:
            # SourceSet 알림 실패해도 Backend 상태 업데이트는 진행
            logger.warning(
                f"Failed to notify source_set_orchestrator: source_set_id={request.meta.source_set_id}, "
                f"error={e}, trace_id={request.meta.traceId}"
            )
    elif request.meta.source_set_id and request.status == "FAILED":
        try:
            from app.services.source_set_orchestrator import get_source_set_orchestrator
            
            orchestrator = get_source_set_orchestrator()
            await orchestrator.notify_document_completed(
                source_set_id=request.meta.source_set_id,
                document_id=request.meta.spring_document_id or request.docId,
                ragflow_doc_id=request.docId,
                status="FAILED",
            )
            logger.info(
                f"Notified source_set_orchestrator (FAILED): source_set_id={request.meta.source_set_id}, "
                f"document_id={request.meta.spring_document_id}"
            )
        except Exception as e:
            logger.warning(
                f"Failed to notify source_set_orchestrator (FAILED): source_set_id={request.meta.source_set_id}, "
                f"error={e}, trace_id={request.meta.traceId}"
            )

    # 캐시에 완료 상태 저장 (멱등성: 다음 동일 요청 시 200 + COMPLETED 반환)
    _mark_request_completed(request.docId, request.version, request.status)

    # COMPLETED 상태인 경우 Milvus에서 문서 전체 텍스트 조회
    content: Optional[str] = None
    if request.status == "COMPLETED":
        try:
            milvus_client = get_milvus_client()

            # domain에 맞는 dataset_id 결정
            # meta.domain이 있으면 그대로 사용 (RAGFlow dataset_id와 동일)
            # 없으면 기본값 "사내규정" 사용 (하위 호환성)
            dataset_id = request.meta.domain if request.meta.domain else "사내규정"

            logger.debug(
                f"Retrieving document content: doc_id={request.docId}, "
                f"domain={request.meta.domain}, dataset_id={dataset_id}, "
                f"trace_id={request.meta.traceId}"
            )

            content = await milvus_client.get_full_document_text(
                doc_id=request.docId,
                dataset_id=dataset_id,
            )
            if content:
                logger.info(
                    f"Retrieved document content from Milvus: doc_id={request.docId}, "
                    f"dataset_id={dataset_id}, content_length={len(content)}, "
                    f"trace_id={request.meta.traceId}"
                )
            else:
                logger.warning(
                    f"No content found in Milvus for doc_id={request.docId}, "
                    f"dataset_id={dataset_id}, trace_id={request.meta.traceId}"
                )
        except Exception as e:
            # Milvus 조회 실패해도 Backend 상태 업데이트는 진행
            logger.error(
                f"Failed to retrieve content from Milvus: doc_id={request.docId}, "
                f"error={e}, trace_id={request.meta.traceId}"
            )

    # Backend 상태 업데이트
    # SourceSet 플로우에서는 백엔드에 RAG document 엔티티가 생성되지 않으므로
    # status 업데이트를 건너뜀 (source_set_orchestrator가 이미 처리 완료)
    if not request.meta.source_set_id:
        # 일반 RAG document ingest 플로우에서만 상태 업데이트
        try:
            backend_client = get_backend_client()
            # UUID 형식 변환 (하이픈 없는 형식을 하이픈 있는 형식으로)
            formatted_rag_document_pk = format_uuid(request.meta.ragDocumentPk)
            await backend_client.update_rag_document_status(
                rag_document_pk=formatted_rag_document_pk,
                status=request.status,
                document_id=request.docId,
                version=request.version,
                processed_at=request.processedAt,
                fail_reason=request.failReason,
                content=content,
            )
            logger.info(
                f"Backend status updated: rag_document_pk={request.meta.ragDocumentPk}, "
                f"status={request.status}, content_included={content is not None}, "
                f"trace_id={request.meta.traceId}"
            )
        except RAGDocumentStatusUpdateError as e:
            # Backend 호출 실패해도 200 반환 (에러 로그만)
            logger.error(
                f"Failed to update backend status: rag_document_pk={request.meta.ragDocumentPk}, "
                f"error={e}, trace_id={request.meta.traceId}"
            )
        except Exception as e:
            logger.error(
                f"Unexpected error updating backend status: "
                f"rag_document_pk={request.meta.ragDocumentPk}, "
                f"error={e}, trace_id={request.meta.traceId}"
            )
    else:
        # SourceSet 플로우: source_set_orchestrator가 이미 처리했으므로 건너뜀
        logger.debug(
            f"Skipping RAG document status update for SourceSet: "
            f"source_set_id={request.meta.source_set_id}, "
            f"rag_document_pk={request.meta.ragDocumentPk}, "
            f"trace_id={request.meta.traceId}"
        )

    return IngestCallbackResponse(received=True)
