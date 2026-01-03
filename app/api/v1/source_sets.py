"""
SourceSet 오케스트레이션 API (Phase 4)

Spring 백엔드에서 호출하여 소스셋 처리를 시작하는 내부 API입니다.

엔드포인트:
- POST /internal/ai/source-sets/{sourceSetId}/start : 소스셋 처리 시작

흐름:
1. Spring → FastAPI: POST /internal/ai/source-sets/{sourceSetId}/start
2. FastAPI: 202 Accepted 즉시 반환
3. FastAPI: 백그라운드에서 RAGFlow 오케스트레이션 수행
4. FastAPI → Spring: 완료 콜백 전송

인증:
- X-Internal-Token 헤더 필수
"""

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.source_set import (
    SourceSetStartRequest,
    SourceSetStartResponse,
    SourceSetStatus,
)
from app.services.source_set_orchestrator import (
    ProcessingStatus,
    get_source_set_orchestrator,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/internal/ai", tags=["SourceSet Orchestration"])


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
        HTTPException: 인증 실패 시 401/403
    """
    settings = get_settings()
    expected_token = settings.BACKEND_INTERNAL_TOKEN

    # 토큰이 설정되지 않은 경우 (개발 환경)
    if not expected_token:
        logger.warning("BACKEND_INTERNAL_TOKEN not configured, skipping auth")
        return

    # 받은 토큰 로그 출력 (디버깅용)
    if x_internal_token:
        masked_received = (
            x_internal_token[:4] + "****" + x_internal_token[-4:]
            if len(x_internal_token) > 8
            else "****"
        )
        logger.info(
            f"=== FastAPI 토큰 검증 ==="
            f"받은 X-Internal-Token: {masked_received} (길이: {len(x_internal_token)})"
        )
    else:
        logger.warning("X-Internal-Token 헤더가 없습니다.")

    if not x_internal_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "reason_code": "MISSING_TOKEN",
                "message": "X-Internal-Token 헤더가 필요합니다.",
            },
        )

    # 예상 토큰 로그 출력 (디버깅용)
    if expected_token:
        masked_expected = (
            expected_token[:4] + "****" + expected_token[-4:]
            if len(expected_token) > 8
            else "****"
        )
        logger.info(
            f"예상 BACKEND_INTERNAL_TOKEN: {masked_expected} (길이: {len(expected_token)})"
        )

    if x_internal_token != expected_token:
        # 토큰 불일치 상세 로그
        masked_received = (
            x_internal_token[:4] + "****" + x_internal_token[-4:]
            if len(x_internal_token) > 8
            else "****"
        )
        masked_expected = (
            expected_token[:4] + "****" + expected_token[-4:]
            if len(expected_token) > 8
            else "****"
        )
        logger.error(
            f"토큰 불일치: 받은 토큰={masked_received} (길이: {len(x_internal_token)}), "
            f"예상 토큰={masked_expected} (길이: {len(expected_token)})"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "reason_code": "INVALID_TOKEN",
                "message": "유효하지 않은 인증 토큰입니다.",
            },
        )

    logger.debug("토큰 검증 성공")


# =============================================================================
# SourceSet Orchestration APIs
# =============================================================================


@router.post(
    "/source-sets/{source_set_id}/start",
    response_model=SourceSetStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="소스셋 처리 시작 (Spring → AI)",
    description="""
소스셋의 문서들을 RAGFlow로 적재하고 스크립트를 자동 생성합니다.

**URL**: POST /internal/ai/source-sets/{sourceSetId}/start

**호출 주체**: Spring 백엔드

**인증**: X-Internal-Token 헤더 필수

**처리 흐름**:
1. 즉시 202 Accepted 반환 (비동기 처리)
2. 백그라운드에서:
   - Spring에서 문서 목록 조회
   - 각 문서를 RAGFlow로 ingest
   - 청크 텍스트를 Spring DB에 저장
   - 스크립트 생성 (LLM)
   - 완료 콜백 전송

**멱등성**:
- 동일 sourceSetId 재요청 시 기존 상태 반환
- requestId로 중복 요청 추적

**에러 처리**:
- 하나라도 문서 처리 실패 시 FAILED 콜백
- 전체 성공 시 SCRIPT_READY 콜백
""",
    responses={
        202: {"description": "접수됨 (비동기 처리 시작)"},
        401: {"description": "인증 토큰 누락"},
        403: {"description": "유효하지 않은 토큰"},
        409: {"description": "상태 충돌 (이미 SCRIPT_READY 등)"},
    },
    dependencies=[Depends(verify_internal_token)],
)
async def start_source_set(
    source_set_id: str,
    request: SourceSetStartRequest,
):
    """소스셋 처리를 시작합니다."""
    orchestrator = get_source_set_orchestrator()

    # 기존 작업 상태 확인 (멱등성)
    existing_job = orchestrator.get_job_status(source_set_id)
    if existing_job:
        # 이미 완료된 경우
        if existing_job.status == ProcessingStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "reason_code": "ALREADY_COMPLETED",
                    "message": "이미 처리가 완료된 소스셋입니다.",
                    "source_set_status": "SCRIPT_READY",
                },
            )
        # 이미 실패한 경우
        if existing_job.status == ProcessingStatus.FAILED:
            # 재시도 허용 - 기존 상태 초기화 후 재처리
            logger.info(
                f"Retrying failed source set: source_set_id={source_set_id}"
            )
            # 기존 작업 삭제하고 새로 시작
            # (실제로는 orchestrator에서 처리)

    logger.info(
        f"Starting source set: source_set_id={source_set_id}, "
        f"video_id={request.video_id}, request_id={request.request_id}"
    )

    try:
        response = await orchestrator.start(source_set_id, request)
        return response

    except Exception as e:
        logger.error(
            f"Failed to start source set: source_set_id={source_set_id}, error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "reason_code": "START_FAILED",
                "message": f"소스셋 처리 시작 실패: {str(e)[:200]}",
            },
        )


@router.get(
    "/source-sets/{source_set_id}/status",
    summary="소스셋 처리 상태 조회 (옵션)",
    description="""
소스셋 처리 상태를 조회합니다.

**참고**: 이 엔드포인트는 디버깅/모니터링 목적입니다.
실제 상태는 완료 콜백으로 Spring에 전달됩니다.
""",
    dependencies=[Depends(verify_internal_token)],
)
async def get_source_set_status(
    source_set_id: str,
):
    """소스셋 처리 상태를 조회합니다."""
    orchestrator = get_source_set_orchestrator()
    job = orchestrator.get_job_status(source_set_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason_code": "NOT_FOUND",
                "message": "해당 소스셋 처리 작업을 찾을 수 없습니다.",
            },
        )

    # 내부 상태를 DB 상태로 변환
    source_set_status = SourceSetStatus.LOCKED
    if job.status == ProcessingStatus.COMPLETED:
        source_set_status = SourceSetStatus.SCRIPT_READY
    elif job.status == ProcessingStatus.FAILED:
        source_set_status = SourceSetStatus.FAILED

    return {
        "sourceSetId": source_set_id,
        "status": job.status.value,
        "sourceSetStatus": source_set_status.value,
        "videoId": job.video_id,
        "documentsCount": len(job.documents),
        "documentResults": [
            {
                "documentId": r.document_id,
                "status": r.status,
                "failReason": r.fail_reason,
            }
            for r in job.document_results
        ],
        "hasScript": job.generated_script is not None,
        "errorCode": job.error_code,
        "errorMessage": job.error_message,
        "createdAt": job.created_at.isoformat() + "Z",
        "updatedAt": job.updated_at.isoformat() + "Z",
    }
