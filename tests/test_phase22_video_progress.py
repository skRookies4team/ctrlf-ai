"""
Phase 22: 교육영상 상태전이 서버 검증 테스트

테스트 케이스:
1. 정상 시나리오: start → progress 누적 → complete 성공 → quiz_unlock true
2. 비정상 시나리오:
   - progress 역행 시도 → 거부
   - progress 급증 시도 → 거부
   - 완료 조건 미충족 → 거부
   - 완료 전 QUIZ_START → 거부
   - 완료 후 QUIZ_START → 허용
3. seek_allowed 상태 검증
"""

import time
import pytest
from datetime import datetime

from app.models.video_progress import (
    VideoCompleteRequest,
    VideoPlayStartRequest,
    VideoProgressState,
    VideoProgressUpdateRequest,
    VideoRejectionReason,
)
from app.services.video_progress_service import (
    VideoProgressService,
    VideoProgressStore,
    clear_video_progress_store,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


@pytest.fixture(autouse=True)
def reset_store():
    """테스트 격리를 위해 저장소 초기화."""
    clear_video_progress_store()
    yield
    clear_video_progress_store()


@pytest.fixture
def video_service() -> VideoProgressService:
    """테스트용 VideoProgressService 인스턴스."""
    return VideoProgressService(
        store=VideoProgressStore(),
        completion_threshold=95.0,
        final_segment_ratio=0.05,
        surge_time_window=10.0,
        surge_max_increase=30.0,
    )


# =============================================================================
# 테스트 1: 정상 시나리오 - 영상 시작
# =============================================================================


def test_start_video_success(video_service: VideoProgressService):
    """영상 시작 - 정상 케이스."""
    # Arrange
    request = VideoPlayStartRequest(
        user_id="user-001",
        training_id="training-001",
        total_duration=600,  # 10분
        is_mandatory_edu=True,  # 4대교육
    )

    # Act
    response = video_service.start_video(request)

    # Assert
    assert response.user_id == "user-001"
    assert response.training_id == "training-001"
    assert response.state == VideoProgressState.PLAYING
    assert response.seek_allowed is False  # 완료 전에는 시크 불가
    assert response.session_id is not None


def test_start_video_non_mandatory(video_service: VideoProgressService):
    """영상 시작 - 일반 교육 (4대교육 아님)."""
    request = VideoPlayStartRequest(
        user_id="user-002",
        training_id="training-002",
        total_duration=300,
        is_mandatory_edu=False,
    )

    response = video_service.start_video(request)

    assert response.state == VideoProgressState.PLAYING
    assert response.seek_allowed is False


# =============================================================================
# 테스트 2: 진행률 업데이트 - 정상
# =============================================================================


def test_update_progress_success(video_service: VideoProgressService):
    """진행률 업데이트 - 정상 케이스."""
    # Arrange: 영상 시작
    start_request = VideoPlayStartRequest(
        user_id="user-001",
        training_id="training-001",
        total_duration=600,
        is_mandatory_edu=True,
    )
    video_service.start_video(start_request)

    # Act: 진행률 30% 업데이트
    update_request = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="training-001",
        current_position=180,
        watched_seconds=180,
    )
    response = video_service.update_progress(update_request)

    # Assert
    assert response.accepted is True
    assert response.rejection_reason is None
    assert response.progress_percent == 30.0
    assert response.watched_seconds == 180
    assert response.seek_allowed is False


# =============================================================================
# 테스트 3: 진행률 역행 거부
# =============================================================================


def test_update_progress_regression_rejected(video_service: VideoProgressService):
    """진행률 역행 - 거부됨."""
    # Arrange: 영상 시작 및 50% 진행
    start_request = VideoPlayStartRequest(
        user_id="user-001",
        training_id="training-001",
        total_duration=600,
        is_mandatory_edu=True,
    )
    video_service.start_video(start_request)

    update_request_1 = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="training-001",
        current_position=300,
        watched_seconds=300,  # 50%
    )
    video_service.update_progress(update_request_1)

    # Act: 30%로 역행 시도
    update_request_2 = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="training-001",
        current_position=180,
        watched_seconds=180,  # 30% (역행)
    )
    response = video_service.update_progress(update_request_2)

    # Assert
    assert response.accepted is False
    assert response.rejection_reason == VideoRejectionReason.PROGRESS_REGRESSION.value
    assert response.progress_percent == 50.0  # 이전 값 유지


# =============================================================================
# 테스트 4: 진행률 급증 거부
# =============================================================================


def test_update_progress_surge_rejected(video_service: VideoProgressService):
    """진행률 급증 - 10초에 30% 초과 증가 시 거부."""
    # Arrange: 영상 시작
    start_request = VideoPlayStartRequest(
        user_id="user-001",
        training_id="training-001",
        total_duration=600,
        is_mandatory_edu=True,
    )
    video_service.start_video(start_request)

    # 10% 진행
    update_request_1 = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="training-001",
        current_position=60,
        watched_seconds=60,  # 10%
    )
    video_service.update_progress(update_request_1)

    # Act: 짧은 시간 내 50%로 급증 시도 (40% 증가)
    # Note: 같은 timestamp면 급증 체크가 스킵됨. 실제로는 시간 경과 필요.
    # 테스트에서는 서비스의 타임스탬프를 직접 조작할 수 없으므로
    # 급증 감지 로직을 개별적으로 테스트

    # 저장소에서 레코드를 직접 수정하여 타임스탬프 조작
    record = video_service._store.get("user-001", "training-001")
    record.last_update_timestamp = time.time() - 5  # 5초 전
    video_service._store.set("user-001", "training-001", record)

    update_request_2 = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="training-001",
        current_position=300,
        watched_seconds=300,  # 50% (40% 증가)
    )
    response = video_service.update_progress(update_request_2)

    # Assert
    assert response.accepted is False
    assert response.rejection_reason == VideoRejectionReason.PROGRESS_SURGE.value


# =============================================================================
# 테스트 5: 완료 조건 미충족 - 95% 미만
# =============================================================================


def test_complete_video_threshold_not_met(video_service: VideoProgressService):
    """완료 - 95% 미만으로 거부."""
    # Arrange: 영상 시작 및 80% 진행
    start_request = VideoPlayStartRequest(
        user_id="user-001",
        training_id="training-001",
        total_duration=600,
        is_mandatory_edu=True,
    )
    video_service.start_video(start_request)

    update_request = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="training-001",
        current_position=480,
        watched_seconds=480,  # 80%
    )
    video_service.update_progress(update_request)

    # Act: 완료 요청 (80%만 시청)
    complete_request = VideoCompleteRequest(
        user_id="user-001",
        training_id="training-001",
        final_position=480,
        total_watched_seconds=480,
    )
    response = video_service.complete_video(complete_request)

    # Assert
    assert response.completed is False
    assert response.rejection_reason == VideoRejectionReason.COMPLETION_THRESHOLD_NOT_MET.value
    assert response.quiz_unlocked is False


# =============================================================================
# 테스트 6: 완료 성공 + 퀴즈 잠금 해제
# =============================================================================


def test_complete_video_success_with_quiz_unlock(video_service: VideoProgressService):
    """완료 성공 - 4대교육 퀴즈 잠금 해제."""
    # Arrange: 영상 시작
    start_request = VideoPlayStartRequest(
        user_id="user-001",
        training_id="training-001",
        total_duration=600,  # 10분
        is_mandatory_edu=True,  # 4대교육
    )
    video_service.start_video(start_request)

    # 마지막 구간 시청 (마지막 5% = 30초, 570초 이후)
    update_request = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="training-001",
        current_position=590,  # 마지막 구간
        watched_seconds=580,  # 96.7%
    )
    video_service.update_progress(update_request)

    # Act: 완료 요청
    complete_request = VideoCompleteRequest(
        user_id="user-001",
        training_id="training-001",
        final_position=600,
        total_watched_seconds=580,  # 96.7%
    )
    response = video_service.complete_video(complete_request)

    # Assert
    assert response.completed is True
    assert response.quiz_unlocked is True  # 4대교육 퀴즈 해제
    assert response.seek_allowed is True  # 완료 후 시크 허용
    assert response.completed_at is not None


# =============================================================================
# 테스트 7: 완료 후 seek_allowed 상태 변경
# =============================================================================


def test_seek_allowed_changes_on_complete(video_service: VideoProgressService):
    """완료 전/후 seek_allowed 상태 변화."""
    # Arrange: 영상 시작
    start_request = VideoPlayStartRequest(
        user_id="user-001",
        training_id="training-001",
        total_duration=600,
        is_mandatory_edu=False,  # 일반 교육
    )
    start_response = video_service.start_video(start_request)
    assert start_response.seek_allowed is False  # 완료 전

    # 마지막 구간 시청
    update_request = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="training-001",
        current_position=590,
        watched_seconds=580,
    )
    update_response = video_service.update_progress(update_request)
    assert update_response.seek_allowed is False  # 아직 완료 안됨

    # Act: 완료
    complete_request = VideoCompleteRequest(
        user_id="user-001",
        training_id="training-001",
        final_position=600,
        total_watched_seconds=580,
    )
    complete_response = video_service.complete_video(complete_request)

    # Assert
    assert complete_response.seek_allowed is True  # 완료 후


# =============================================================================
# 테스트 8: 퀴즈 시작 - 완료 전 거부
# =============================================================================


def test_quiz_start_before_complete_rejected(video_service: VideoProgressService):
    """퀴즈 시작 - 완료 전 거부."""
    # Arrange: 영상 시작 및 50% 진행
    start_request = VideoPlayStartRequest(
        user_id="user-001",
        training_id="training-001",
        total_duration=600,
        is_mandatory_edu=True,
    )
    video_service.start_video(start_request)

    update_request = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="training-001",
        current_position=300,
        watched_seconds=300,
    )
    video_service.update_progress(update_request)

    # Act: 퀴즈 시작 가능 여부 확인
    can_start, reason = video_service.can_start_quiz("user-001", "training-001")

    # Assert
    assert can_start is False
    assert "not completed" in reason.lower()


# =============================================================================
# 테스트 9: 퀴즈 시작 - 완료 후 허용
# =============================================================================


def test_quiz_start_after_complete_allowed(video_service: VideoProgressService):
    """퀴즈 시작 - 완료 후 허용."""
    # Arrange: 영상 시작 및 완료
    start_request = VideoPlayStartRequest(
        user_id="user-001",
        training_id="training-001",
        total_duration=600,
        is_mandatory_edu=True,
    )
    video_service.start_video(start_request)

    # 마지막 구간 시청
    update_request = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="training-001",
        current_position=590,
        watched_seconds=580,
    )
    video_service.update_progress(update_request)

    # 완료
    complete_request = VideoCompleteRequest(
        user_id="user-001",
        training_id="training-001",
        final_position=600,
        total_watched_seconds=580,
    )
    video_service.complete_video(complete_request)

    # Act: 퀴즈 시작 가능 여부 확인
    can_start, reason = video_service.can_start_quiz("user-001", "training-001")

    # Assert
    assert can_start is True


# =============================================================================
# 테스트 10: 세션 없는 경우
# =============================================================================


def test_update_progress_session_not_found(video_service: VideoProgressService):
    """세션 없는 경우 - 거부."""
    # Act: 시작하지 않은 영상에 진행률 업데이트
    update_request = VideoProgressUpdateRequest(
        user_id="user-999",
        training_id="training-999",
        current_position=100,
        watched_seconds=100,
    )
    response = video_service.update_progress(update_request)

    # Assert
    assert response.accepted is False
    assert response.rejection_reason == VideoRejectionReason.SESSION_NOT_FOUND.value


# =============================================================================
# 테스트 11: 상태 조회
# =============================================================================


def test_get_status_success(video_service: VideoProgressService):
    """상태 조회 - 정상."""
    # Arrange: 영상 시작 및 진행
    start_request = VideoPlayStartRequest(
        user_id="user-001",
        training_id="training-001",
        total_duration=600,
        is_mandatory_edu=True,
    )
    video_service.start_video(start_request)

    update_request = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="training-001",
        current_position=300,
        watched_seconds=300,
    )
    video_service.update_progress(update_request)

    # Act
    status = video_service.get_status("user-001", "training-001")

    # Assert
    assert status is not None
    assert status.user_id == "user-001"
    assert status.training_id == "training-001"
    assert status.progress_percent == 50.0
    assert status.state == VideoProgressState.PLAYING
    assert status.is_mandatory_edu is True


# =============================================================================
# 테스트 12: 상태 조회 - 존재하지 않는 레코드
# =============================================================================


def test_get_status_not_found(video_service: VideoProgressService):
    """상태 조회 - 레코드 없음."""
    status = video_service.get_status("user-999", "training-999")
    assert status is None


# =============================================================================
# 테스트 13: 이미 완료된 영상 재시작
# =============================================================================


def test_restart_completed_video(video_service: VideoProgressService):
    """이미 완료된 영상 재시작."""
    # Arrange: 영상 시작 및 완료
    start_request = VideoPlayStartRequest(
        user_id="user-001",
        training_id="training-001",
        total_duration=600,
        is_mandatory_edu=True,
    )
    video_service.start_video(start_request)

    # 마지막 구간 시청
    update_request = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="training-001",
        current_position=590,
        watched_seconds=580,
    )
    video_service.update_progress(update_request)

    # 완료
    complete_request = VideoCompleteRequest(
        user_id="user-001",
        training_id="training-001",
        final_position=600,
        total_watched_seconds=580,
    )
    video_service.complete_video(complete_request)

    # Act: 완료된 영상 재시작
    restart_response = video_service.start_video(start_request)

    # Assert
    assert restart_response.state == VideoProgressState.PLAYING
    assert restart_response.seek_allowed is True  # 완료 후에는 시크 허용 유지


# =============================================================================
# 테스트 14: 일반 교육은 퀴즈 체크 스킵
# =============================================================================


def test_quiz_check_non_mandatory_allowed(video_service: VideoProgressService):
    """일반 교육은 퀴즈 체크 스킵."""
    # Arrange: 일반 교육 시작 (완료 안됨)
    start_request = VideoPlayStartRequest(
        user_id="user-001",
        training_id="training-001",
        total_duration=600,
        is_mandatory_edu=False,  # 일반 교육
    )
    video_service.start_video(start_request)

    # Act: 퀴즈 시작 가능 여부 확인
    can_start, reason = video_service.can_start_quiz("user-001", "training-001")

    # Assert: 일반 교육은 퀴즈 체크 없이 허용
    assert can_start is True
    assert "Not mandatory" in reason
