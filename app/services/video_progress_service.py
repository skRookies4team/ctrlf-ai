"""
Phase 22: 교육영상 진행률 서비스 (Video Progress Service)

교육영상 시청 진행률 추적 및 서버 검증을 담당하는 서비스입니다.

서버 검증 룰:
1. 역행 금지: progress가 감소하면 거부
2. 급증 제한: 단위 시간당 progress 증가폭 상한 (10초에 30% 이상 증가 시 거부)
3. 완료 판정: 누적 시청률 >= 95% AND 마지막 구간(5%) 시청 조건 만족
4. 시크 제한: 완료 전 seek_allowed=False, 완료 후 True

API:
- POST /api/video/play/start: 영상 시작
- POST /api/video/progress: 진행률 업데이트
- POST /api/video/complete: 완료 요청
- GET /api/video/status: 상태 조회
"""

import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from app.core.logging import get_logger
from app.models.video_progress import (
    VideoCompleteRequest,
    VideoCompleteResponse,
    VideoPlayStartRequest,
    VideoPlayStartResponse,
    VideoProgressRecord,
    VideoProgressState,
    VideoProgressUpdateRequest,
    VideoProgressUpdateResponse,
    VideoRejectionReason,
    VideoStatusResponse,
)

logger = get_logger(__name__)


# =============================================================================
# 검증 상수
# =============================================================================

# 완료 판정 임계값 (%)
COMPLETION_THRESHOLD_PERCENT = 95.0

# 마지막 구간 비율 (총 시간의 5%)
FINAL_SEGMENT_RATIO = 0.05

# 급증 감지: 시간 간격(초) 동안 증가 가능한 최대 퍼센트
SURGE_TIME_WINDOW_SECONDS = 10.0
SURGE_MAX_INCREASE_PERCENT = 30.0

# 영상 완료 후 시크 허용
SEEK_ALLOWED_ON_COMPLETE = True


# =============================================================================
# In-Memory Storage (추후 DB/Redis로 교체)
# =============================================================================


class VideoProgressStore:
    """영상 진행률 저장소 (인메모리).

    실제 운영에서는 Redis 또는 DB로 교체 권장.

    Usage:
        store = VideoProgressStore()
        store.set("user-123", "training-456", record)
        record = store.get("user-123", "training-456")
    """

    def __init__(self) -> None:
        """VideoProgressStore 초기화."""
        self._store: Dict[str, VideoProgressRecord] = {}

    def _make_key(self, user_id: str, training_id: str) -> str:
        """저장소 키 생성."""
        return f"{user_id}:{training_id}"

    def set(self, user_id: str, training_id: str, record: VideoProgressRecord) -> None:
        """레코드 저장."""
        key = self._make_key(user_id, training_id)
        self._store[key] = record

    def get(self, user_id: str, training_id: str) -> Optional[VideoProgressRecord]:
        """레코드 조회."""
        key = self._make_key(user_id, training_id)
        return self._store.get(key)

    def delete(self, user_id: str, training_id: str) -> None:
        """레코드 삭제."""
        key = self._make_key(user_id, training_id)
        self._store.pop(key, None)

    def clear(self) -> None:
        """모든 레코드 삭제."""
        self._store.clear()


# 전역 저장소 인스턴스
_video_progress_store: Optional[VideoProgressStore] = None


def get_video_progress_store() -> VideoProgressStore:
    """VideoProgressStore 인스턴스 반환."""
    global _video_progress_store
    if _video_progress_store is None:
        _video_progress_store = VideoProgressStore()
    return _video_progress_store


def clear_video_progress_store() -> None:
    """저장소 초기화 (테스트용)."""
    global _video_progress_store
    if _video_progress_store is not None:
        _video_progress_store.clear()
        _video_progress_store = None


# =============================================================================
# VideoProgressService 클래스
# =============================================================================


class VideoProgressService:
    """교육영상 진행률 서비스.

    서버 측 검증 로직을 포함하여 영상 시청 진행률을 관리합니다.

    Usage:
        service = VideoProgressService()

        # 영상 시작
        response = service.start_video(request)

        # 진행률 업데이트
        response = service.update_progress(request)

        # 완료 요청
        response = service.complete_video(request)

        # 상태 조회
        response = service.get_status(user_id, training_id)
    """

    def __init__(
        self,
        store: Optional[VideoProgressStore] = None,
        completion_threshold: float = COMPLETION_THRESHOLD_PERCENT,
        final_segment_ratio: float = FINAL_SEGMENT_RATIO,
        surge_time_window: float = SURGE_TIME_WINDOW_SECONDS,
        surge_max_increase: float = SURGE_MAX_INCREASE_PERCENT,
    ) -> None:
        """VideoProgressService 초기화.

        Args:
            store: 저장소 인스턴스
            completion_threshold: 완료 판정 임계값 (%)
            final_segment_ratio: 마지막 구간 비율
            surge_time_window: 급증 감지 시간 간격 (초)
            surge_max_increase: 급증 감지 최대 증가율 (%)
        """
        self._store = store or get_video_progress_store()
        self._completion_threshold = completion_threshold
        self._final_segment_ratio = final_segment_ratio
        self._surge_time_window = surge_time_window
        self._surge_max_increase = surge_max_increase

    # =========================================================================
    # 영상 시작 (VIDEO_PLAY_START)
    # =========================================================================

    def start_video(self, request: VideoPlayStartRequest) -> VideoPlayStartResponse:
        """영상 재생을 시작합니다.

        세션을 생성하고 초기 상태를 저장합니다.

        Args:
            request: 영상 시작 요청

        Returns:
            VideoPlayStartResponse: 시작 응답
        """
        now = datetime.now(timezone.utc)
        session_id = str(uuid.uuid4())

        # 기존 레코드가 있으면 완료 상태가 아닌 경우 덮어쓰기
        existing = self._store.get(request.user_id, request.training_id)
        if existing and existing.state == VideoProgressState.COMPLETED:
            logger.info(
                f"Video already completed: user_id={request.user_id}, "
                f"training_id={request.training_id}"
            )
            # 완료된 영상은 재시작 허용 (시크 허용 상태로)
            existing.state = VideoProgressState.PLAYING
            existing.updated_at = now.isoformat()
            self._store.set(request.user_id, request.training_id, existing)

            return VideoPlayStartResponse(
                session_id=session_id,
                user_id=request.user_id,
                training_id=request.training_id,
                state=VideoProgressState.PLAYING,
                seek_allowed=existing.seek_allowed,  # 완료 후에는 True
                created_at=now.isoformat(),
            )

        # 새 레코드 생성
        record = VideoProgressRecord(
            user_id=request.user_id,
            training_id=request.training_id,
            video_id=request.training_id,
            total_duration=request.total_duration,
            watched_seconds=0,
            progress_percent=0.0,
            last_position=0,
            state=VideoProgressState.PLAYING,
            seek_allowed=False,  # 완료 전에는 시크 불가
            quiz_unlocked=False,
            is_mandatory_edu=request.is_mandatory_edu,
            last_final_segment_time=0,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            completed_at=None,
            last_update_timestamp=time.time(),
        )

        self._store.set(request.user_id, request.training_id, record)

        logger.info(
            f"Video started: user_id={request.user_id}, "
            f"training_id={request.training_id}, "
            f"total_duration={request.total_duration}s, "
            f"is_mandatory_edu={request.is_mandatory_edu}"
        )

        return VideoPlayStartResponse(
            session_id=session_id,
            user_id=request.user_id,
            training_id=request.training_id,
            state=VideoProgressState.PLAYING,
            seek_allowed=False,
            created_at=now.isoformat(),
        )

    # =========================================================================
    # 진행률 업데이트 (VIDEO_PROGRESS_UPDATE)
    # =========================================================================

    def update_progress(
        self, request: VideoProgressUpdateRequest
    ) -> VideoProgressUpdateResponse:
        """영상 진행률을 업데이트합니다.

        서버 검증 룰을 적용하여 비정상 요청을 거부합니다.

        Args:
            request: 진행률 업데이트 요청

        Returns:
            VideoProgressUpdateResponse: 업데이트 응답
        """
        now = datetime.now(timezone.utc)
        current_timestamp = time.time()

        # 1. 세션 존재 여부 확인
        record = self._store.get(request.user_id, request.training_id)
        if not record:
            logger.warning(
                f"Session not found: user_id={request.user_id}, "
                f"training_id={request.training_id}"
            )
            return VideoProgressUpdateResponse(
                user_id=request.user_id,
                training_id=request.training_id,
                progress_percent=0.0,
                watched_seconds=0,
                last_position=0,
                seek_allowed=False,
                state=VideoProgressState.NOT_STARTED,
                updated_at=now.isoformat(),
                accepted=False,
                rejection_reason=VideoRejectionReason.SESSION_NOT_FOUND.value,
            )

        # 2. 이미 완료된 경우 - 업데이트 스킵
        if record.state == VideoProgressState.COMPLETED:
            logger.debug(
                f"Already completed: user_id={request.user_id}, "
                f"training_id={request.training_id}"
            )
            return VideoProgressUpdateResponse(
                user_id=request.user_id,
                training_id=request.training_id,
                progress_percent=record.progress_percent,
                watched_seconds=record.watched_seconds,
                last_position=record.last_position,
                seek_allowed=record.seek_allowed,
                state=record.state,
                updated_at=record.updated_at,
                accepted=True,
                rejection_reason=None,
            )

        # 3. 새로운 진행률 계산
        new_progress = self._calculate_progress(
            request.watched_seconds, record.total_duration
        )

        # 4. 역행 검증 (진행률 감소 거부)
        if new_progress < record.progress_percent:
            logger.warning(
                f"Progress regression detected: user_id={request.user_id}, "
                f"old={record.progress_percent:.1f}%, new={new_progress:.1f}%"
            )
            return VideoProgressUpdateResponse(
                user_id=request.user_id,
                training_id=request.training_id,
                progress_percent=record.progress_percent,
                watched_seconds=record.watched_seconds,
                last_position=record.last_position,
                seek_allowed=record.seek_allowed,
                state=record.state,
                updated_at=record.updated_at,
                accepted=False,
                rejection_reason=VideoRejectionReason.PROGRESS_REGRESSION.value,
            )

        # 5. 급증 검증 (비정상 속도 거부)
        is_surge, surge_reason = self._check_progress_surge(
            old_progress=record.progress_percent,
            new_progress=new_progress,
            old_timestamp=record.last_update_timestamp,
            new_timestamp=current_timestamp,
        )
        if is_surge:
            logger.warning(
                f"Progress surge detected: user_id={request.user_id}, "
                f"old={record.progress_percent:.1f}%, new={new_progress:.1f}%, "
                f"reason={surge_reason}"
            )
            return VideoProgressUpdateResponse(
                user_id=request.user_id,
                training_id=request.training_id,
                progress_percent=record.progress_percent,
                watched_seconds=record.watched_seconds,
                last_position=record.last_position,
                seek_allowed=record.seek_allowed,
                state=record.state,
                updated_at=record.updated_at,
                accepted=False,
                rejection_reason=VideoRejectionReason.PROGRESS_SURGE.value,
            )

        # 6. 마지막 구간 시청 여부 업데이트
        final_segment_start = int(record.total_duration * (1 - self._final_segment_ratio))
        if request.current_position >= final_segment_start:
            # 마지막 구간 시청 시간 누적
            time_in_final = min(
                request.current_position - final_segment_start,
                int(record.total_duration * self._final_segment_ratio)
            )
            record.last_final_segment_time = max(
                record.last_final_segment_time,
                time_in_final,
            )

        # 7. 레코드 업데이트
        record.watched_seconds = request.watched_seconds
        record.progress_percent = new_progress
        record.last_position = request.current_position
        record.updated_at = now.isoformat()
        record.last_update_timestamp = current_timestamp

        self._store.set(request.user_id, request.training_id, record)

        logger.debug(
            f"Progress updated: user_id={request.user_id}, "
            f"training_id={request.training_id}, "
            f"progress={new_progress:.1f}%"
        )

        return VideoProgressUpdateResponse(
            user_id=request.user_id,
            training_id=request.training_id,
            progress_percent=record.progress_percent,
            watched_seconds=record.watched_seconds,
            last_position=record.last_position,
            seek_allowed=record.seek_allowed,
            state=record.state,
            updated_at=record.updated_at,
            accepted=True,
            rejection_reason=None,
        )

    # =========================================================================
    # 완료 요청 (VIDEO_COMPLETE)
    # =========================================================================

    def complete_video(self, request: VideoCompleteRequest) -> VideoCompleteResponse:
        """영상 완료를 요청합니다.

        클라이언트가 complete=true를 보내도 서버에서 조건을 검증합니다.

        완료 조건:
        1. 누적 시청률 >= 95%
        2. 마지막 구간(5%) 시청 기록이 있어야 함

        Args:
            request: 완료 요청

        Returns:
            VideoCompleteResponse: 완료 응답
        """
        now = datetime.now(timezone.utc)

        # 1. 세션 존재 여부 확인
        record = self._store.get(request.user_id, request.training_id)
        if not record:
            logger.warning(
                f"Session not found for complete: user_id={request.user_id}, "
                f"training_id={request.training_id}"
            )
            return VideoCompleteResponse(
                user_id=request.user_id,
                training_id=request.training_id,
                completed=False,
                progress_percent=0.0,
                quiz_unlocked=False,
                seek_allowed=False,
                completed_at=None,
                rejection_reason=VideoRejectionReason.SESSION_NOT_FOUND.value,
            )

        # 2. 이미 완료된 경우
        if record.state == VideoProgressState.COMPLETED:
            return VideoCompleteResponse(
                user_id=request.user_id,
                training_id=request.training_id,
                completed=True,
                progress_percent=record.progress_percent,
                quiz_unlocked=record.quiz_unlocked,
                seek_allowed=record.seek_allowed,
                completed_at=record.completed_at,
                rejection_reason=None,
            )

        # 3. 최종 진행률 계산 (요청의 total_watched_seconds 기준)
        final_progress = self._calculate_progress(
            request.total_watched_seconds, record.total_duration
        )

        # 4. 완료 조건 검증: 누적 시청률 >= 95%
        if final_progress < self._completion_threshold:
            logger.info(
                f"Completion threshold not met: user_id={request.user_id}, "
                f"progress={final_progress:.1f}%, threshold={self._completion_threshold}%"
            )
            return VideoCompleteResponse(
                user_id=request.user_id,
                training_id=request.training_id,
                completed=False,
                progress_percent=final_progress,
                quiz_unlocked=False,
                seek_allowed=False,
                completed_at=None,
                rejection_reason=VideoRejectionReason.COMPLETION_THRESHOLD_NOT_MET.value,
            )

        # 5. 완료 조건 검증: 마지막 구간 시청
        min_final_segment_time = int(
            record.total_duration * self._final_segment_ratio * 0.5
        )  # 마지막 구간의 50% 이상
        if record.last_final_segment_time < min_final_segment_time:
            # 마지막 위치가 마지막 구간에 있으면 허용
            final_segment_start = int(
                record.total_duration * (1 - self._final_segment_ratio)
            )
            if request.final_position < final_segment_start:
                logger.info(
                    f"Final segment not watched: user_id={request.user_id}, "
                    f"final_segment_time={record.last_final_segment_time}s, "
                    f"min_required={min_final_segment_time}s"
                )
                return VideoCompleteResponse(
                    user_id=request.user_id,
                    training_id=request.training_id,
                    completed=False,
                    progress_percent=final_progress,
                    quiz_unlocked=False,
                    seek_allowed=False,
                    completed_at=None,
                    rejection_reason=VideoRejectionReason.FINAL_SEGMENT_NOT_WATCHED.value,
                )

        # 6. 완료 처리
        record.state = VideoProgressState.COMPLETED
        record.progress_percent = min(final_progress, 100.0)
        record.watched_seconds = request.total_watched_seconds
        record.last_position = request.final_position
        record.seek_allowed = SEEK_ALLOWED_ON_COMPLETE
        record.completed_at = now.isoformat()
        record.updated_at = now.isoformat()

        # 7. 4대교육이면 퀴즈 잠금 해제
        if record.is_mandatory_edu:
            record.quiz_unlocked = True
            logger.info(
                f"Quiz unlocked for mandatory edu: user_id={request.user_id}, "
                f"training_id={request.training_id}"
            )

        self._store.set(request.user_id, request.training_id, record)

        logger.info(
            f"Video completed: user_id={request.user_id}, "
            f"training_id={request.training_id}, "
            f"progress={record.progress_percent:.1f}%, "
            f"quiz_unlocked={record.quiz_unlocked}"
        )

        return VideoCompleteResponse(
            user_id=request.user_id,
            training_id=request.training_id,
            completed=True,
            progress_percent=record.progress_percent,
            quiz_unlocked=record.quiz_unlocked,
            seek_allowed=record.seek_allowed,
            completed_at=record.completed_at,
            rejection_reason=None,
        )

    # =========================================================================
    # 상태 조회 (VIDEO_STATUS)
    # =========================================================================

    def get_status(self, user_id: str, training_id: str) -> Optional[VideoStatusResponse]:
        """영상 상태를 조회합니다.

        Args:
            user_id: 사용자 ID
            training_id: 교육/영상 ID

        Returns:
            VideoStatusResponse: 상태 응답 또는 None (없으면)
        """
        record = self._store.get(user_id, training_id)
        if not record:
            return None

        return VideoStatusResponse(
            user_id=record.user_id,
            training_id=record.training_id,
            total_duration=record.total_duration,
            watched_seconds=record.watched_seconds,
            progress_percent=record.progress_percent,
            last_position=record.last_position,
            state=record.state,
            seek_allowed=record.seek_allowed,
            quiz_unlocked=record.quiz_unlocked,
            is_mandatory_edu=record.is_mandatory_edu,
            completed_at=record.completed_at,
            updated_at=record.updated_at,
        )

    # =========================================================================
    # 퀴즈 시작 가능 여부 확인
    # =========================================================================

    def can_start_quiz(self, user_id: str, training_id: str) -> Tuple[bool, str]:
        """퀴즈 시작 가능 여부를 확인합니다.

        4대교육의 경우 영상 완료 후에만 퀴즈 시작 가능합니다.

        Args:
            user_id: 사용자 ID
            training_id: 교육/영상 ID

        Returns:
            Tuple[bool, str]: (시작 가능 여부, 사유)
        """
        record = self._store.get(user_id, training_id)
        if not record:
            # 레코드 없으면 4대교육이 아닌 것으로 간주하여 허용
            return True, "No video progress record (non-mandatory edu assumed)"

        if not record.is_mandatory_edu:
            return True, "Not mandatory education"

        if record.quiz_unlocked:
            return True, "Quiz unlocked after video completion"

        if record.state == VideoProgressState.COMPLETED:
            return True, "Video completed"

        return False, f"Video not completed (progress={record.progress_percent:.1f}%)"

    # =========================================================================
    # 내부 헬퍼 메서드
    # =========================================================================

    def _calculate_progress(
        self, watched_seconds: int, total_duration: int
    ) -> float:
        """진행률 계산."""
        if total_duration <= 0:
            return 0.0
        progress = (watched_seconds / total_duration) * 100
        return min(progress, 100.0)

    def _check_progress_surge(
        self,
        old_progress: float,
        new_progress: float,
        old_timestamp: float,
        new_timestamp: float,
    ) -> Tuple[bool, str]:
        """급증 여부 검사.

        단위 시간당 증가폭이 상한을 초과하면 급증으로 판단합니다.

        Args:
            old_progress: 이전 진행률 (%)
            new_progress: 새 진행률 (%)
            old_timestamp: 이전 타임스탬프
            new_timestamp: 새 타임스탬프

        Returns:
            Tuple[bool, str]: (급증 여부, 사유)
        """
        time_diff = new_timestamp - old_timestamp
        progress_diff = new_progress - old_progress

        # 시간 차이가 0이면 체크 스킵
        if time_diff <= 0:
            return False, ""

        # 시간 간격이 설정된 윈도우 이내일 때만 체크
        if time_diff <= self._surge_time_window:
            if progress_diff > self._surge_max_increase:
                return True, (
                    f"Progress increased {progress_diff:.1f}% in {time_diff:.1f}s "
                    f"(max allowed: {self._surge_max_increase}% in {self._surge_time_window}s)"
                )

        return False, ""
