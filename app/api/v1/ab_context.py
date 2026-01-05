"""
A/B 테스트 컨텍스트 API

Backend → AI 모델 선택 API입니다.

엔드포인트:
1. POST /internal/ai/context/model - 모델 설정
2. GET /internal/ai/context/model/{requestId} - 모델 조회
3. DELETE /internal/ai/context/model/{requestId} - 모델 삭제
4. GET /internal/ai/context/stats - 통계 조회

흐름:
1. Backend에서 Frontend가 선택한 모델 정보를 AI에 전달
2. AI는 requestId별로 모델 컨텍스트를 저장
3. 이후 해당 requestId로 요청 시 저장된 모델 설정 사용
4. 모델에 따라 임베딩/컬렉션 분기:
   - openai: OpenAI text-embedding-3-large, ragflow_chunks
   - sroberta: SRoberta 임베딩, ragflow_chunks_sroberta

인증:
- X-Internal-Token 헤더 필수
"""

from typing import Optional

from fastapi import APIRouter, Header, status
from fastapi.responses import JSONResponse

from app.core.ab_context import (
    ALLOWED_MODEL_TYPES,
    clear_ab_model,
    get_ab_context_stats,
    get_ab_model,
    get_model_config,
    set_ab_model,
)
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.ab_test import (
    ABContextStatsResponse,
    ABModelErrorResponse,
    ABModelGetResponse,
    ABModelSetRequest,
    ABModelSetResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/internal/ai/context", tags=["A/B Test Context"])


# =============================================================================
# Error Response Helper
# =============================================================================


def _error_response(
    status_code: int,
    error: str,
    message: str,
    request_id: Optional[str] = None,
) -> JSONResponse:
    """에러 응답을 생성합니다."""
    content = ABModelErrorResponse(
        error=error,
        message=message,
        requestId=request_id,
    ).model_dump()
    return JSONResponse(status_code=status_code, content=content)


# =============================================================================
# Authentication
# =============================================================================


def _verify_token(
    x_internal_token: Optional[str],
    request_id: Optional[str] = None,
) -> Optional[JSONResponse]:
    """
    내부 API 토큰을 검증합니다.

    Args:
        x_internal_token: X-Internal-Token 헤더 값
        request_id: 요청 ID (에러 응답용)

    Returns:
        Optional[JSONResponse]: 인증 실패 시 에러 응답, 성공 시 None
    """
    settings = get_settings()
    expected_token = settings.BACKEND_INTERNAL_TOKEN

    # 토큰이 설정되지 않은 경우 (개발 환경)
    if not expected_token:
        logger.warning("BACKEND_INTERNAL_TOKEN not configured, skipping auth")
        return None

    if not x_internal_token:
        return _error_response(
            status_code=401,
            error="UNAUTHORIZED",
            message="X-Internal-Token 헤더가 필요합니다.",
            request_id=request_id,
        )

    if x_internal_token != expected_token:
        return _error_response(
            status_code=401,
            error="UNAUTHORIZED",
            message="유효하지 않은 인증 토큰입니다.",
            request_id=request_id,
        )

    return None


# =============================================================================
# Routes
# =============================================================================


@router.post(
    "/model",
    response_model=ABModelSetResponse,
    status_code=status.HTTP_200_OK,
    summary="A/B 테스트 모델 설정 (Backend -> AI)",
    description="""
Backend에서 호출하여 A/B 테스트 모델을 설정합니다.

**URL**: POST /internal/ai/context/model

**호출 주체**: Spring 백엔드

**인증**: X-Internal-Token 헤더 필수

**모델 옵션**:
- `openai`: OpenAI text-embedding-3-large (3072 dim), ragflow_chunks 컬렉션
- `sroberta`: SRoberta 임베딩 (384 dim), ragflow_chunks_sroberta 컬렉션

**처리 흐름**:
1. requestId와 model 값 검증
2. 내부 컨텍스트 저장소에 모델 설정 저장
3. 이후 해당 requestId로 요청 시 저장된 모델 사용
""",
    responses={
        200: {"description": "모델 설정 성공", "model": ABModelSetResponse},
        400: {"description": "잘못된 요청 (INVALID_MODEL)"},
        401: {"description": "인증 실패"},
    },
)
async def set_model_context(
    request: ABModelSetRequest,
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
):
    """A/B 테스트 모델을 설정합니다."""
    # 인증 검증
    auth_error = _verify_token(x_internal_token, request.requestId)
    if auth_error:
        return auth_error

    logger.info(
        f"[A/B API] Set model request: request_id={request.requestId}, "
        f"model={request.model}"
    )

    # 모델 검증
    if request.model not in ALLOWED_MODEL_TYPES:
        return _error_response(
            status_code=400,
            error="INVALID_MODEL",
            message=f"허용되지 않는 모델입니다. 허용: {list(ALLOWED_MODEL_TYPES)}",
            request_id=request.requestId,
        )

    # 모델 설정
    try:
        set_ab_model(request.requestId, request.model)
    except ValueError as e:
        return _error_response(
            status_code=400,
            error="INVALID_MODEL",
            message=str(e),
            request_id=request.requestId,
        )

    logger.info(
        f"[A/B API] Model set successfully: request_id={request.requestId}, "
        f"model={request.model}"
    )

    return ABModelSetResponse(
        success=True,
        requestId=request.requestId,
        model=request.model,
        message="Model context set successfully",
    )


@router.get(
    "/model/{request_id}",
    response_model=ABModelGetResponse,
    status_code=status.HTTP_200_OK,
    summary="A/B 테스트 모델 조회",
    description="""
특정 요청 ID에 대한 A/B 테스트 모델 설정을 조회합니다.

**URL**: GET /internal/ai/context/model/{request_id}

**인증**: X-Internal-Token 헤더 필수
""",
    responses={
        200: {"description": "조회 성공", "model": ABModelGetResponse},
        401: {"description": "인증 실패"},
        404: {"description": "컨텍스트 없음"},
    },
)
async def get_model_context(
    request_id: str,
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
):
    """A/B 테스트 모델을 조회합니다."""
    # 인증 검증
    auth_error = _verify_token(x_internal_token, request_id)
    if auth_error:
        return auth_error

    model = get_ab_model(request_id)

    if not model:
        return _error_response(
            status_code=404,
            error="NOT_FOUND",
            message=f"요청 ID '{request_id}'에 대한 모델 컨텍스트가 없습니다.",
            request_id=request_id,
        )

    # 모델 설정 조회
    config = get_model_config(model)
    embedding_model, embedding_dim, collection_name = config if config else (None, None, None)

    return ABModelGetResponse(
        requestId=request_id,
        model=model,
        embeddingModel=embedding_model,
        embeddingDim=embedding_dim,
        collectionName=collection_name,
    )


@router.delete(
    "/model/{request_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="A/B 테스트 모델 삭제",
    description="""
특정 요청 ID에 대한 A/B 테스트 모델 설정을 삭제합니다.

**URL**: DELETE /internal/ai/context/model/{request_id}

**인증**: X-Internal-Token 헤더 필수
""",
    responses={
        204: {"description": "삭제 성공"},
        401: {"description": "인증 실패"},
    },
)
async def delete_model_context(
    request_id: str,
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
):
    """A/B 테스트 모델을 삭제합니다."""
    # 인증 검증
    auth_error = _verify_token(x_internal_token, request_id)
    if auth_error:
        return auth_error

    clear_ab_model(request_id)
    logger.info(f"[A/B API] Model context deleted: request_id={request_id}")

    return None


@router.get(
    "/stats",
    response_model=ABContextStatsResponse,
    status_code=status.HTTP_200_OK,
    summary="A/B 컨텍스트 통계 조회",
    description="""
현재 활성화된 A/B 컨텍스트 통계를 조회합니다.

**URL**: GET /internal/ai/context/stats

**인증**: X-Internal-Token 헤더 필수

**모니터링/디버깅용**
""",
    responses={
        200: {"description": "조회 성공", "model": ABContextStatsResponse},
        401: {"description": "인증 실패"},
    },
)
async def get_context_stats(
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
):
    """A/B 컨텍스트 통계를 조회합니다."""
    # 인증 검증
    auth_error = _verify_token(x_internal_token)
    if auth_error:
        return auth_error

    stats = get_ab_context_stats()

    return ABContextStatsResponse(
        total=stats.get("total", 0),
        byModel=stats.get("by_model", {}),
    )
