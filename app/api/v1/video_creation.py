"""
영상 생성 API

프론트엔드에서 파일 업로드부터 영상 생성까지 전체 워크플로우를 처리하는 API입니다.

엔드포인트:
- POST /api/v1/videos/create-from-source-set: 소스셋으로부터 영상 생성
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.v1.source_sets import verify_internal_token
from app.core.logging import get_logger
from app.services.video_creation_service import (
    VideoCreationService,
    get_video_creation_service,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/videos", tags=["Video Creation"])


class VideoCreationRequest(BaseModel):
    """영상 생성 요청."""

    source_set_id: str = Field(..., alias="sourceSetId", description="소스셋 ID")
    video_id: str = Field(..., alias="videoId", description="영상 ID")
    education_id: Optional[str] = Field(
        None, alias="educationId", description="교육 ID (선택)"
    )
    request_id: Optional[str] = Field(
        None, alias="requestId", description="요청 ID (멱등성, 선택)"
    )
    trace_id: Optional[str] = Field(
        None, alias="traceId", description="추적 ID (선택)"
    )

    class Config:
        populate_by_name = True


@router.post(
    "/create-from-source-set",
    summary="소스셋으로부터 영상 생성",
    description="""
소스셋의 문서들을 RAG 전처리하고 스크립트를 생성한 후 영상을 제작합니다.

**전체 워크플로우:**
1. 소스셋 처리 시작 (RAG 전처리 + 스크립트 생성)
2. 백엔드에 스크립트 저장
3. HeyGen으로 영상 생성
4. S3에 영상 저장
5. 백엔드에 영상 URL 전달

**인증**: X-Internal-Token 헤더 필수 (내부 API)

**Request Body:**
- sourceSetId: 소스셋 ID
- videoId: 영상 ID
- educationId: 교육 ID (선택)
- requestId: 요청 ID (멱등성, 선택)
- traceId: 추적 ID (선택)

**Response:**
- source_set_id: 소스셋 ID
- video_id: 영상 ID
- script_id: 생성된 스크립트 ID
- video_url: 영상 URL
- s3_uri: S3 URI
- duration_sec: 영상 길이 (초)
- status: "COMPLETED" | "FAILED"
""",
    responses={
        200: {"description": "영상 생성 성공"},
        400: {"description": "잘못된 요청"},
        401: {"description": "인증 토큰 누락"},
        403: {"description": "유효하지 않은 토큰"},
        500: {"description": "서버 오류"},
    },
    dependencies=[Depends(verify_internal_token)],
)
async def create_video_from_source_set(
    request: VideoCreationRequest,
    service: VideoCreationService = Depends(get_video_creation_service),
):
    """소스셋으로부터 영상을 생성합니다.

    Args:
        request: 영상 생성 요청
        service: 영상 생성 서비스

    Returns:
        생성 결과
    """
    try:
        result = await service.create_video_from_source_set(
            source_set_id=request.source_set_id,
            video_id=request.video_id,
            education_id=request.education_id,
            request_id=request.request_id,
            trace_id=request.trace_id,
        )

        if result.get("status") == "FAILED":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": "VIDEO_CREATION_FAILED",
                    "message": result.get("error", "Unknown error"),
                    "source_set_id": request.source_set_id,
                    "video_id": request.video_id,
                },
            )

        return result

    except Exception as e:
        logger.error(
            f"Video creation API error: source_set_id={request.source_set_id}, "
            f"video_id={request.video_id}, error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "VIDEO_CREATION_ERROR",
                "message": str(e),
                "source_set_id": request.source_set_id,
                "video_id": request.video_id,
            },
        )

