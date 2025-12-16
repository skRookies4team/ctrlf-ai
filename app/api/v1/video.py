"""
Phase 22: Video Progress API Router Module

교육영상 시청 진행률 추적 및 서버 검증 API를 제공합니다.

Endpoints:
    - POST /api/video/play/start: 영상 재생 시작
    - POST /api/video/progress: 진행률 업데이트
    - POST /api/video/complete: 완료 요청
    - GET /api/video/status: 상태 조회
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.models.video_progress import (
    VideoCompleteRequest,
    VideoCompleteResponse,
    VideoPlayStartRequest,
    VideoPlayStartResponse,
    VideoProgressUpdateRequest,
    VideoProgressUpdateResponse,
    VideoStatusResponse,
)
from app.services.video_progress_service import VideoProgressService

router = APIRouter(prefix="/api/video", tags=["Video Progress"])


def get_video_progress_service() -> VideoProgressService:
    """Dependency injection for VideoProgressService."""
    return VideoProgressService()


# =============================================================================
# POST /api/video/play/start - 영상 재생 시작
# =============================================================================


@router.post(
    "/play/start",
    response_model=VideoPlayStartResponse,
    summary="Start Video Playback",
    description=(
        "영상 재생을 시작하고 시청 세션을 생성합니다. "
        "완료 전까지 시크(seek) 조작이 제한됩니다."
    ),
    responses={
        200: {"description": "Successfully started video playback"},
        422: {"description": "Validation error in request body"},
    },
)
async def start_video_playback(
    request: VideoPlayStartRequest,
    service: VideoProgressService = Depends(get_video_progress_service),
) -> VideoPlayStartResponse:
    """
    영상 재생을 시작합니다.

    **Request Body:**
    - `user_id`: 사용자 ID
    - `training_id`: 교육/영상 ID
    - `video_id`: 영상 ID (선택)
    - `total_duration`: 영상 총 길이 (초)
    - `is_mandatory_edu`: 4대교육 여부

    **Response:**
    - `session_id`: 시청 세션 ID
    - `state`: 현재 상태 (PLAYING)
    - `seek_allowed`: 시크 허용 여부 (완료 전 False)

    Args:
        request: 영상 시작 요청
        service: VideoProgressService 인스턴스

    Returns:
        VideoPlayStartResponse: 시작 응답
    """
    return service.start_video(request)


# =============================================================================
# POST /api/video/progress - 진행률 업데이트
# =============================================================================


@router.post(
    "/progress",
    response_model=VideoProgressUpdateResponse,
    summary="Update Video Progress",
    description=(
        "영상 시청 진행률을 업데이트합니다. "
        "서버에서 역행/급증을 검증하여 비정상 요청을 거부합니다."
    ),
    responses={
        200: {"description": "Progress update response (accepted or rejected)"},
        422: {"description": "Validation error in request body"},
    },
)
async def update_video_progress(
    request: VideoProgressUpdateRequest,
    service: VideoProgressService = Depends(get_video_progress_service),
) -> VideoProgressUpdateResponse:
    """
    영상 진행률을 업데이트합니다.

    **Server Validation Rules:**
    - 역행 금지: 진행률이 감소하면 거부
    - 급증 제한: 10초에 30% 이상 증가 시 거부

    **Request Body:**
    - `user_id`: 사용자 ID
    - `training_id`: 교육/영상 ID
    - `current_position`: 현재 재생 위치 (초)
    - `watched_seconds`: 실제 시청한 누적 초

    **Response:**
    - `accepted`: 업데이트 수락 여부
    - `rejection_reason`: 거부 사유 (거부 시)
    - `progress_percent`: 현재 진행률

    Args:
        request: 진행률 업데이트 요청
        service: VideoProgressService 인스턴스

    Returns:
        VideoProgressUpdateResponse: 업데이트 응답
    """
    return service.update_progress(request)


# =============================================================================
# POST /api/video/complete - 완료 요청
# =============================================================================


@router.post(
    "/complete",
    response_model=VideoCompleteResponse,
    summary="Complete Video",
    description=(
        "영상 완료를 요청합니다. 서버에서 완료 조건을 검증합니다. "
        "조건: 누적 95% 이상 + 마지막 구간(5%) 시청 필요."
    ),
    responses={
        200: {"description": "Completion response (completed or rejected)"},
        422: {"description": "Validation error in request body"},
    },
)
async def complete_video(
    request: VideoCompleteRequest,
    service: VideoProgressService = Depends(get_video_progress_service),
) -> VideoCompleteResponse:
    """
    영상 완료를 요청합니다.

    **Server Validation Rules:**
    - 완료 조건: 누적 시청률 >= 95%
    - 마지막 구간(5%) 시청 기록 필요

    **Request Body:**
    - `user_id`: 사용자 ID
    - `training_id`: 교육/영상 ID
    - `final_position`: 최종 재생 위치 (초)
    - `total_watched_seconds`: 총 시청 시간 (초)

    **Response:**
    - `completed`: 완료 처리됨 여부
    - `quiz_unlocked`: 퀴즈 잠금 해제 여부 (4대교육)
    - `seek_allowed`: 시크 허용 여부 (완료 후 True)
    - `rejection_reason`: 거부 사유 (미완료 시)

    Args:
        request: 완료 요청
        service: VideoProgressService 인스턴스

    Returns:
        VideoCompleteResponse: 완료 응답
    """
    return service.complete_video(request)


# =============================================================================
# GET /api/video/status - 상태 조회
# =============================================================================


@router.get(
    "/status",
    response_model=VideoStatusResponse,
    summary="Get Video Status",
    description="영상 시청 상태를 조회합니다.",
    responses={
        200: {"description": "Successfully retrieved video status"},
        404: {"description": "Video progress record not found"},
    },
)
async def get_video_status(
    user_id: str = Query(..., description="사용자 ID"),
    training_id: str = Query(..., description="교육/영상 ID"),
    service: VideoProgressService = Depends(get_video_progress_service),
) -> VideoStatusResponse:
    """
    영상 시청 상태를 조회합니다.

    **Query Parameters:**
    - `user_id`: 사용자 ID
    - `training_id`: 교육/영상 ID

    **Response:**
    - `state`: 현재 상태
    - `progress_percent`: 진행률
    - `seek_allowed`: 시크 허용 여부
    - `quiz_unlocked`: 퀴즈 잠금 해제 여부

    Args:
        user_id: 사용자 ID
        training_id: 교육/영상 ID
        service: VideoProgressService 인스턴스

    Returns:
        VideoStatusResponse: 상태 응답

    Raises:
        HTTPException: 레코드가 없으면 404
    """
    status_response = service.get_status(user_id, training_id)
    if not status_response:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No video progress record found for user_id={user_id}, training_id={training_id}",
        )
    return status_response


# =============================================================================
# GET /api/video/quiz/check - 퀴즈 시작 가능 여부 확인
# =============================================================================


@router.get(
    "/quiz/check",
    summary="Check Quiz Availability",
    description=(
        "퀴즈 시작 가능 여부를 확인합니다. "
        "4대교육의 경우 영상 완료 후에만 퀴즈 시작 가능합니다."
    ),
    responses={
        200: {"description": "Quiz availability check result"},
        403: {"description": "Quiz not available (video not completed)"},
    },
)
async def check_quiz_availability(
    user_id: str = Query(..., description="사용자 ID"),
    training_id: str = Query(..., description="교육/영상 ID"),
    service: VideoProgressService = Depends(get_video_progress_service),
) -> dict:
    """
    퀴즈 시작 가능 여부를 확인합니다.

    4대교육(필수교육)의 경우 영상을 완료해야 퀴즈를 시작할 수 있습니다.

    **Query Parameters:**
    - `user_id`: 사용자 ID
    - `training_id`: 교육/영상 ID

    **Response:**
    - `can_start`: 시작 가능 여부
    - `reason`: 사유

    Args:
        user_id: 사용자 ID
        training_id: 교육/영상 ID
        service: VideoProgressService 인스턴스

    Returns:
        dict: {"can_start": bool, "reason": str}

    Raises:
        HTTPException: 퀴즈 시작 불가 시 403
    """
    can_start, reason = service.can_start_quiz(user_id, training_id)

    if not can_start:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "can_start": False,
                "reason": reason,
                "message": "영상을 먼저 완료해 주세요.",
            },
        )

    return {
        "can_start": True,
        "reason": reason,
    }
