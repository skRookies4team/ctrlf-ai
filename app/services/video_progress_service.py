"""
Phase 22: 교육영상 진행률 서비스 (Video Progress Service)

교육영상 시청 진행률 추적 및 서버 검증을 담당하는 서비스입니다.

서버 검증 룰:
1. 역행 금지: progress가 감소하면 거부
2. 급증 제한: 단위 시간당 progress 증가폭 상한 (10초에 30% 이상 증가 시 거부)
3. 완료 판정: 누적 시청률 >= 100% (100%면 완료)
4. 시크 제한: 완료 전 seek_allowed=False, 완료 후 True
5. 배속 제한: 4대교육 최초 시청 시 1.0배속만 허용, 재시청 또는 일반교육은 배속 허용

배속 정책:
- 배속은 기본적으로 지원 (0.5x ~ 2.0x)
- 4대교육(필수/mandatory) 영상은 최초 시청(first_watch=true) 시 1.0배속만 허용
- 최초 시청이 아닌 경우(재시청, first_watch=false)는 배속 허용
- 일반 교육은 항상 배속 허용

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
from app.services.education_catalog_service import get_education_catalog_service

logger = get_logger(__name__)


# =============================================================================
# 검증 상수
# =============================================================================

# 완료 판정 임계값 (%) - 100%면 완료
COMPLETION_THRESHOLD_PERCENT = 100.0

# 급증 감지: 시간-위치 기반 검증의 grace 허용치 (초)
# delta_position <= elapsed_wall_clock + grace 이면 허용
SURGE_GRACE_SECONDS = 5.0

# 영상 완료 후 시크 허용
SEEK_ALLOWED_ON_COMPLETE = True

# 배속 관련 상수
DEFAULT_PLAYBACK_RATE = 1.0  # 기본 재생 속도
MAX_PLAYBACK_RATE_ALLOWED = 2.0  # 최대 허용 배속
MAX_PLAYBACK_RATE_MANDATORY_FIRST_WATCH = 1.0  # 4대교육 최초 시청 시 최대 배속


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
        surge_grace_seconds: float = SURGE_GRACE_SECONDS,
    ) -> None:
        """VideoProgressService 초기화.

        Args:
            store: 저장소 인스턴스
            completion_threshold: 완료 판정 임계값 (%) - 100%면 완료
            surge_grace_seconds: 급증 감지 grace 허용치 (초)
        """
        self._store = store or get_video_progress_store()
        self._completion_threshold = completion_threshold
        self._surge_grace_seconds = surge_grace_seconds

    # =========================================================================
    # 영상 시작 (VIDEO_PLAY_START)
    # =========================================================================

    def start_video(self, request: VideoPlayStartRequest) -> VideoPlayStartResponse:
        """영상 재생을 시작합니다.

        Phase 22 수정: EducationCatalogService로 4대교육 서버 판정
        배속 검증: 4대교육 최초 시청 시 1.0배속만 허용

        세션을 생성하고 초기 상태를 저장합니다.

        Args:
            request: 영상 시작 요청

        Returns:
            VideoPlayStartResponse: 시작 응답

        Raises:
            HTTPException: 4대교육 최초 시청 시 배속이 1.0 초과인 경우 400 반환
        """
        from fastapi import HTTPException

        now = datetime.now(timezone.utc)
        session_id = str(uuid.uuid4())

        # Phase 22: 서버 측 4대교육 판정 (클라이언트 is_mandatory_edu 불신)
        edu_catalog = get_education_catalog_service()
        is_mandatory_4type = edu_catalog.is_mandatory_4type(request.training_id)

        # 기존 레코드가 있으면 완료 상태 확인 → first_watch 판단
        existing = self._store.get(request.user_id, request.training_id)
        is_first_watch = True  # 기본값: 최초 시청

        if existing and existing.state == VideoProgressState.COMPLETED:
            # 완료된 영상은 재시청 (first_watch=False)
            is_first_watch = False
            logger.info(
                f"Video already completed (rewatch): user_id={request.user_id}, "
                f"training_id={request.training_id}"
            )
            # 완료된 영상은 재시작 허용 (시크 허용 상태로)
            existing.state = VideoProgressState.IN_PROGRESS
            existing.updated_at = now.isoformat()
            existing.first_watch = False  # 재시청
            self._store.set(request.user_id, request.training_id, existing)

            # 재시청은 배속 허용 (max_playback_rate=2.0)
            max_rate = MAX_PLAYBACK_RATE_ALLOWED

            return VideoPlayStartResponse(
                session_id=session_id,
                user_id=request.user_id,
                training_id=request.training_id,
                state=VideoProgressState.IN_PROGRESS,
                seek_allowed=existing.seek_allowed,  # 완료 후에는 True
                created_at=now.isoformat(),
                first_watch=False,
                max_playback_rate=max_rate,
                playback_rate_reason=None,
            )

        # 기존 레코드가 있지만 완료 안된 경우도 first_watch 유지
        if existing:
            is_first_watch = existing.first_watch

        # 배속 제한 계산
        max_rate = self._get_max_playback_rate(is_mandatory_4type, is_first_watch)
        playback_rate_reason = None

        # 배속 검증: 4대교육 최초 시청 시 1.0 초과 배속 거부
        if is_mandatory_4type and is_first_watch and request.playback_rate > 1.0:
            playback_rate_reason = "MANDATORY_FIRST_WATCH"
            logger.warning(
                f"Playback rate not allowed: user_id={request.user_id}, "
                f"training_id={request.training_id}, "
                f"playback_rate={request.playback_rate}, max_allowed={max_rate}"
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "PLAYBACK_RATE_NOT_ALLOWED",
                    "message": "4대교육 최초 시청 시 배속(1.0x 초과)은 허용되지 않습니다.",
                    "max_playback_rate": max_rate,
                    "reason": playback_rate_reason,
                }
            )

        # 새 레코드 생성 (is_mandatory_edu는 서버 판정 값 사용)
        record = VideoProgressRecord(
            user_id=request.user_id,
            training_id=request.training_id,
            video_id=request.training_id,
            total_duration=request.total_duration,
            watched_seconds=0,
            progress_percent=0.0,
            last_position=0,
            state=VideoProgressState.IN_PROGRESS,
            seek_allowed=False,  # 완료 전에는 시크 불가
            quiz_unlocked=False,
            is_mandatory_edu=is_mandatory_4type,  # Phase 22: 서버 판정 값
            first_watch=is_first_watch,  # 최초 시청 여부
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
            f"is_mandatory_edu={is_mandatory_4type}, "
            f"first_watch={is_first_watch}, "
            f"max_playback_rate={max_rate}"
        )

        return VideoPlayStartResponse(
            session_id=session_id,
            user_id=request.user_id,
            training_id=request.training_id,
            state=VideoProgressState.IN_PROGRESS,
            seek_allowed=False,
            created_at=now.isoformat(),
            first_watch=is_first_watch,
            max_playback_rate=max_rate,
            playback_rate_reason=playback_rate_reason,
        )

    # =========================================================================
    # 진행률 업데이트 (VIDEO_PROGRESS_UPDATE)
    # =========================================================================

    def update_progress(
        self, request: VideoProgressUpdateRequest
    ) -> VideoProgressUpdateResponse:
        """영상 진행률을 업데이트합니다.

        서버 검증 룰을 적용하여 비정상 요청을 거부합니다.
        배속 검증: 4대교육 최초 시청 시 1.0 초과 배속 거부

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

        # 배속 제한 계산
        max_rate = self._get_max_playback_rate(record.is_mandatory_edu, record.first_watch)

        # 2. 이미 완료된 경우 - 업데이트 no-op (accepted=True, 상태 불변)
        if record.state == VideoProgressState.COMPLETED:
            logger.debug(
                f"Already completed (no-op): user_id={request.user_id}, "
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
                message="이미 완료된 영상입니다.",
                max_playback_rate=MAX_PLAYBACK_RATE_ALLOWED,  # 완료 후는 배속 허용
                playback_rate_enforced=False,
            )

        # 3. 배속 검증: 4대교육 최초 시청 시 1.0 초과 배속 거부
        if record.is_mandatory_edu and record.first_watch and request.playback_rate > 1.0:
            logger.warning(
                f"Playback rate not allowed during progress: user_id={request.user_id}, "
                f"training_id={request.training_id}, "
                f"playback_rate={request.playback_rate}, max_allowed={max_rate}"
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
                rejection_reason=VideoRejectionReason.PLAYBACK_RATE_NOT_ALLOWED.value,
                message="4대교육 최초 시청 시 배속(1.0x 초과)은 허용되지 않습니다.",
                max_playback_rate=max_rate,
                playback_rate_enforced=False,
            )

        # 4. 새로운 진행률 계산
        new_progress = self._calculate_progress(
            request.watched_seconds, record.total_duration
        )

        # 5. 역행 검증 (진행률 감소 거부)
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
                max_playback_rate=max_rate,
                playback_rate_enforced=False,
            )

        # 6. 급증 검증 (시간-위치 기반: delta_position <= elapsed + grace)
        is_surge, surge_reason = self._check_progress_surge(
            old_position_seconds=record.last_position,
            new_position_seconds=request.current_position,
            old_timestamp=record.last_update_timestamp,
            new_timestamp=current_timestamp,
        )
        if is_surge:
            logger.warning(
                f"Progress surge detected: user_id={request.user_id}, "
                f"old_pos={record.last_position}s, new_pos={request.current_position}s, "
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
                max_playback_rate=max_rate,
                playback_rate_enforced=False,
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
            max_playback_rate=max_rate,
            playback_rate_enforced=False,
        )

    # =========================================================================
    # 완료 요청 (VIDEO_COMPLETE)
    # =========================================================================

    def complete_video(self, request: VideoCompleteRequest) -> VideoCompleteResponse:
        """영상 완료를 요청합니다.

        클라이언트가 complete=true를 보내도 서버에서 조건을 검증합니다.

        완료 조건:
        - 누적 시청률 >= 100% (100%면 완료)

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

        # 4. 완료 조건 검증: 누적 시청률 >= 100%
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

        # 5. 완료 처리
        record.state = VideoProgressState.COMPLETED
        record.progress_percent = min(final_progress, 100.0)
        record.watched_seconds = request.total_watched_seconds
        record.last_position = request.final_position
        record.seek_allowed = SEEK_ALLOWED_ON_COMPLETE
        record.first_watch = False  # 완료 후 재시청은 배속 허용
        record.completed_at = now.isoformat()
        record.updated_at = now.isoformat()

        # 6. 4대교육이면 퀴즈 잠금 해제
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

        Phase 22 수정: EducationCatalogService로 4대교육 서버 판정

        4대교육의 경우 영상 완료 후에만 퀴즈 시작 가능합니다.
        4대교육 여부는 클라이언트 입력이 아닌 서버에서 판정합니다.

        Args:
            user_id: 사용자 ID
            training_id: 교육/영상 ID

        Returns:
            Tuple[bool, str]: (시작 가능 여부, 사유)
        """
        # Phase 22: 서버 측 4대교육 판정 (클라이언트 is_mandatory_edu 불신)
        edu_catalog = get_education_catalog_service()
        is_mandatory_4type = edu_catalog.is_mandatory_4type(training_id)

        # 4대교육이 아니면 퀴즈 제한 없음
        if not is_mandatory_4type:
            return True, "Not mandatory education (server determined)"

        # 4대교육인 경우: 레코드 필수 + COMPLETED 필수
        record = self._store.get(user_id, training_id)
        if not record:
            # 4대교육인데 레코드 없으면 거부 (영상 시청 필수)
            return False, "Video progress record required for mandatory education"

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
        old_position_seconds: int,
        new_position_seconds: int,
        old_timestamp: float,
        new_timestamp: float,
    ) -> Tuple[bool, str]:
        """급증 여부 검사 (시간-위치 기반).

        delta_position <= elapsed_wall_clock + grace 이면 허용.
        seek가 불가한 환경에서 이 검증만으로 충분합니다.

        Args:
            old_position_seconds: 이전 재생 위치 (초)
            new_position_seconds: 새 재생 위치 (초)
            old_timestamp: 이전 wall clock 타임스탬프
            new_timestamp: 새 wall clock 타임스탬프

        Returns:
            Tuple[bool, str]: (급증 여부, 사유)
        """
        elapsed_wall_clock = new_timestamp - old_timestamp
        delta_position = new_position_seconds - old_position_seconds

        # 시간 차이가 0 이하면 체크 스킵
        if elapsed_wall_clock <= 0:
            return False, ""

        # 위치가 감소하면 (역행) 스킵 - 별도 역행 체크에서 처리
        if delta_position <= 0:
            return False, ""

        # 시간-위치 기반 검증: delta_position <= elapsed + grace
        allowed_delta = elapsed_wall_clock + self._surge_grace_seconds

        if delta_position > allowed_delta:
            return True, (
                f"Position advanced {delta_position}s but only {elapsed_wall_clock:.1f}s "
                f"elapsed (allowed: {allowed_delta:.1f}s with {self._surge_grace_seconds}s grace)"
            )

        return False, ""

    def _get_max_playback_rate(
        self, is_mandatory_edu: bool, first_watch: bool
    ) -> float:
        """허용 최대 배속을 계산합니다.

        배속 정책:
        - 4대교육 최초 시청: 1.0배속만 허용
        - 4대교육 재시청: 2.0배속까지 허용
        - 일반 교육: 2.0배속까지 허용

        Args:
            is_mandatory_edu: 4대교육 여부
            first_watch: 최초 시청 여부

        Returns:
            float: 허용 최대 배속 (1.0 또는 2.0)
        """
        if is_mandatory_edu and first_watch:
            return MAX_PLAYBACK_RATE_MANDATORY_FIRST_WATCH  # 1.0
        return MAX_PLAYBACK_RATE_ALLOWED  # 2.0
