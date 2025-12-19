"""
Phase 22: 교육영상 진행률 모델 (Video Progress Models)

교육영상 시청 진행률 추적 및 서버 검증을 위한 데이터 모델입니다.

주요 기능:
1. 영상 시작 시 세션/상태 생성: VIDEO_PLAY_START
2. 일정 주기로 시청 진도 저장: VIDEO_PROGRESS_UPDATE
3. 완료 확정 (서버 검증): VIDEO_COMPLETE
4. 퀴즈 버튼 활성화 조건: QUIZ_UNLOCK
5. 시크 제한: SEEK_LOCK_ENFORCEMENT
6. 배속 제어: PLAYBACK_RATE_CONTROL

서버 검증 룰:
- 역행 금지: progress가 감소하면 거부
- 급증 제한: 단위 시간당 progress 증가폭 상한 (10초에 30% 증가 같은 비정상 차단)
- 완료 판정: 누적 시청률 >= 100% (100%면 완료)
- 시크 제한: 완료 전 seek_allowed=False, 완료 후 True
- 배속 제한: 4대교육 최초 시청 시 1.0배속만 허용, 재시청 또는 일반교육은 배속 허용
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# =============================================================================
# Video Progress State Enum
# =============================================================================


class VideoProgressState(str, Enum):
    """영상 시청 상태.

    설계 명칭: NOT_STARTED → IN_PROGRESS → COMPLETED
    """

    NOT_STARTED = "NOT_STARTED"  # 시작 전
    IN_PROGRESS = "IN_PROGRESS"  # 재생 중 (설계 명칭 통일)
    COMPLETED = "COMPLETED"  # 완료됨

    # 하위 호환성을 위한 alias (응답 매핑용)
    @classmethod
    def _missing_(cls, value: str) -> "VideoProgressState":
        """PLAYING → IN_PROGRESS 하위 호환."""
        if value == "PLAYING":
            return cls.IN_PROGRESS
        return None


# =============================================================================
# Video Play Start Request/Response
# =============================================================================


class VideoPlayStartRequest(BaseModel):
    """영상 재생 시작 요청.

    Attributes:
        user_id: 사용자 ID
        training_id: 교육/영상 ID
        total_duration: 영상 총 길이 (초)
        is_mandatory_edu: 4대교육 여부 (True면 완료 후 퀴즈 필수)
        playback_rate: 재생 속도 (기본 1.0, 4대교육 최초 시청 시 1.0만 허용)
    """

    user_id: str = Field(..., description="사용자 ID")
    training_id: str = Field(..., description="교육/영상 ID")
    total_duration: int = Field(..., gt=0, description="영상 총 길이 (초)")
    is_mandatory_edu: bool = Field(False, description="4대교육 여부")
    playback_rate: float = Field(1.0, ge=0.5, le=2.0, description="재생 속도 (0.5~2.0)")


class VideoPlayStartResponse(BaseModel):
    """영상 재생 시작 응답.

    Attributes:
        session_id: 시청 세션 ID
        user_id: 사용자 ID
        training_id: 교육/영상 ID
        state: 현재 상태
        seek_allowed: 시크 허용 여부
        created_at: 생성 시간
        first_watch: 최초 시청 여부
        max_playback_rate: 허용 최대 배속 (1.0 또는 2.0)
        playback_rate_reason: 배속 제한 사유 (있으면)
    """

    session_id: str = Field(..., description="시청 세션 ID")
    user_id: str = Field(..., description="사용자 ID")
    training_id: str = Field(..., description="교육/영상 ID")
    state: VideoProgressState = Field(
        default=VideoProgressState.IN_PROGRESS,
        description="현재 상태",
    )
    seek_allowed: bool = Field(False, description="시크 허용 여부")
    created_at: str = Field(..., description="생성 시간")
    first_watch: bool = Field(True, description="최초 시청 여부")
    max_playback_rate: float = Field(2.0, description="허용 최대 배속 (1.0 또는 2.0)")
    playback_rate_reason: Optional[str] = Field(None, description="배속 제한 사유")


# =============================================================================
# Video Progress Update Request/Response
# =============================================================================


class VideoProgressUpdateRequest(BaseModel):
    """영상 진행률 업데이트 요청.

    Attributes:
        user_id: 사용자 ID
        training_id: 교육/영상 ID
        current_position: 현재 재생 위치 (초)
        watched_seconds: 실제 시청한 누적 초 (스킵 구간 제외)
        playback_rate: 현재 재생 속도 (기본 1.0)
    """

    user_id: str = Field(..., description="사용자 ID")
    training_id: str = Field(..., description="교육/영상 ID")
    current_position: int = Field(..., ge=0, description="현재 재생 위치 (초)")
    watched_seconds: int = Field(..., ge=0, description="실제 시청한 누적 초")
    playback_rate: float = Field(1.0, ge=0.5, le=2.0, description="현재 재생 속도 (0.5~2.0)")


class VideoProgressUpdateResponse(BaseModel):
    """영상 진행률 업데이트 응답.

    Attributes:
        user_id: 사용자 ID
        training_id: 교육/영상 ID
        progress_percent: 진행률 (%)
        watched_seconds: 실제 시청한 누적 초
        last_position: 마지막 재생 위치
        seek_allowed: 시크 허용 여부
        state: 현재 상태
        updated_at: 업데이트 시간
        accepted: 업데이트 수락 여부
        rejection_reason: 거부 사유 (있으면)
        max_playback_rate: 허용 최대 배속
        playback_rate_enforced: 배속이 강제 조정되었는지 여부
    """

    user_id: str = Field(..., description="사용자 ID")
    training_id: str = Field(..., description="교육/영상 ID")
    progress_percent: float = Field(..., ge=0.0, le=100.0, description="진행률 (%)")
    watched_seconds: int = Field(..., ge=0, description="실제 시청한 누적 초")
    last_position: int = Field(..., ge=0, description="마지막 재생 위치 (초)")
    seek_allowed: bool = Field(False, description="시크 허용 여부")
    state: VideoProgressState = Field(
        default=VideoProgressState.IN_PROGRESS,
        description="현재 상태",
    )
    updated_at: str = Field(..., description="업데이트 시간")
    accepted: bool = Field(True, description="업데이트 수락 여부")
    rejection_reason: Optional[str] = Field(None, description="거부 사유")
    message: Optional[str] = Field(None, description="응답 메시지")
    max_playback_rate: float = Field(2.0, description="허용 최대 배속")
    playback_rate_enforced: bool = Field(False, description="배속 강제 조정 여부")


# =============================================================================
# Video Complete Request/Response
# =============================================================================


class VideoCompleteRequest(BaseModel):
    """영상 완료 요청.

    클라이언트가 보내는 완료 요청입니다.
    서버에서 조건을 검증하여 실제 완료 여부를 결정합니다.

    Attributes:
        user_id: 사용자 ID
        training_id: 교육/영상 ID
        final_position: 최종 재생 위치 (초)
        total_watched_seconds: 총 시청 시간 (초)
    """

    user_id: str = Field(..., description="사용자 ID")
    training_id: str = Field(..., description="교육/영상 ID")
    final_position: int = Field(..., ge=0, description="최종 재생 위치 (초)")
    total_watched_seconds: int = Field(..., ge=0, description="총 시청 시간 (초)")


class VideoCompleteResponse(BaseModel):
    """영상 완료 응답.

    Attributes:
        user_id: 사용자 ID
        training_id: 교육/영상 ID
        completed: 완료 처리됨 여부
        progress_percent: 최종 진행률 (%)
        quiz_unlocked: 퀴즈 잠금 해제 여부 (4대교육만)
        seek_allowed: 시크 허용 여부 (완료 후 True)
        completed_at: 완료 시간 (완료된 경우)
        rejection_reason: 거부 사유 (완료 처리 안 된 경우)
    """

    user_id: str = Field(..., description="사용자 ID")
    training_id: str = Field(..., description="교육/영상 ID")
    completed: bool = Field(False, description="완료 처리됨 여부")
    progress_percent: float = Field(..., ge=0.0, le=100.0, description="최종 진행률 (%)")
    quiz_unlocked: bool = Field(False, description="퀴즈 잠금 해제 여부")
    seek_allowed: bool = Field(False, description="시크 허용 여부")
    completed_at: Optional[str] = Field(None, description="완료 시간")
    rejection_reason: Optional[str] = Field(None, description="거부 사유")


# =============================================================================
# Video Status Request/Response
# =============================================================================


class VideoStatusRequest(BaseModel):
    """영상 상태 조회 요청.

    Attributes:
        user_id: 사용자 ID
        training_id: 교육/영상 ID
    """

    user_id: str = Field(..., description="사용자 ID")
    training_id: str = Field(..., description="교육/영상 ID")


class VideoStatusResponse(BaseModel):
    """영상 상태 조회 응답.

    Attributes:
        user_id: 사용자 ID
        training_id: 교육/영상 ID
        total_duration: 영상 총 길이 (초)
        watched_seconds: 실제 시청한 누적 초
        progress_percent: 진행률 (%)
        last_position: 마지막 재생 위치
        state: 현재 상태
        seek_allowed: 시크 허용 여부
        quiz_unlocked: 퀴즈 잠금 해제 여부
        is_mandatory_edu: 4대교육 여부
        completed_at: 완료 시간 (완료된 경우)
        updated_at: 마지막 업데이트 시간
    """

    user_id: str = Field(..., description="사용자 ID")
    training_id: str = Field(..., description="교육/영상 ID")
    total_duration: int = Field(..., gt=0, description="영상 총 길이 (초)")
    watched_seconds: int = Field(..., ge=0, description="실제 시청한 누적 초")
    progress_percent: float = Field(..., ge=0.0, le=100.0, description="진행률 (%)")
    last_position: int = Field(..., ge=0, description="마지막 재생 위치 (초)")
    state: VideoProgressState = Field(
        default=VideoProgressState.NOT_STARTED,
        description="현재 상태",
    )
    seek_allowed: bool = Field(False, description="시크 허용 여부")
    quiz_unlocked: bool = Field(False, description="퀴즈 잠금 해제 여부")
    is_mandatory_edu: bool = Field(False, description="4대교육 여부")
    completed_at: Optional[str] = Field(None, description="완료 시간")
    updated_at: str = Field(..., description="마지막 업데이트 시간")


# =============================================================================
# Internal Video Progress Record (저장소용)
# =============================================================================


class VideoProgressRecord(BaseModel):
    """영상 진행률 내부 레코드.

    저장소에 저장되는 전체 상태입니다.

    Attributes:
        user_id: 사용자 ID
        training_id: 교육/영상 ID
        video_id: 영상 ID
        total_duration: 영상 총 길이 (초)
        watched_seconds: 실제 시청한 누적 초
        progress_percent: 진행률 (%)
        last_position: 마지막 재생 위치 (초)
        state: 현재 상태
        seek_allowed: 시크 허용 여부
        quiz_unlocked: 퀴즈 잠금 해제 여부
        is_mandatory_edu: 4대교육 여부
        first_watch: 최초 시청 여부 (첫 완료 전까지 True)
        last_final_segment_time: 마지막 구간 시청 시간 (초) - 완료 검증용
        created_at: 생성 시간
        updated_at: 마지막 업데이트 시간
        completed_at: 완료 시간
        last_update_timestamp: 마지막 업데이트 unix timestamp (급증 감지용)
    """

    user_id: str
    training_id: str
    video_id: Optional[str] = None
    total_duration: int
    watched_seconds: int = 0
    progress_percent: float = 0.0
    last_position: int = 0
    state: VideoProgressState = VideoProgressState.NOT_STARTED
    seek_allowed: bool = False
    quiz_unlocked: bool = False
    is_mandatory_edu: bool = False
    first_watch: bool = True  # 최초 시청 여부 (첫 완료 전까지 True)
    last_final_segment_time: int = 0  # (deprecated) 마지막 구간 시청 시간 - 더 이상 사용 안함
    created_at: str = ""
    updated_at: str = ""
    completed_at: Optional[str] = None
    last_update_timestamp: float = 0.0  # unix timestamp


# =============================================================================
# Rejection Reasons (거부 사유)
# =============================================================================


class VideoRejectionReason(str, Enum):
    """영상 진행률 업데이트 거부 사유."""

    PROGRESS_REGRESSION = "PROGRESS_REGRESSION"  # 진행률 역행
    PROGRESS_SURGE = "PROGRESS_SURGE"  # 비정상 급증
    COMPLETION_THRESHOLD_NOT_MET = "COMPLETION_THRESHOLD_NOT_MET"  # 완료 기준 미달 (100% 미만)
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"  # 세션 없음
    ALREADY_COMPLETED = "ALREADY_COMPLETED"  # 이미 완료됨
    PLAYBACK_RATE_NOT_ALLOWED = "PLAYBACK_RATE_NOT_ALLOWED"  # 배속 허용 안됨 (4대교육 최초시청)
