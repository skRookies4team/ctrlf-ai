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
    """테스트 격리를 위해 저장소 및 카탈로그 초기화."""
    from app.services.education_catalog_service import clear_education_catalog_service

    clear_video_progress_store()
    clear_education_catalog_service()
    yield
    clear_video_progress_store()
    clear_education_catalog_service()


@pytest.fixture
def video_service() -> VideoProgressService:
    """테스트용 VideoProgressService 인스턴스."""
    # Phase 22 수정: surge_time_window, surge_max_increase 제거
    # 새 파라미터: final_segment_min_seconds, surge_grace_seconds
    return VideoProgressService(
        store=VideoProgressStore(),
        completion_threshold=95.0,
        final_segment_ratio=0.05,
        final_segment_min_seconds=30.0,  # max(5%, 30초)
        surge_grace_seconds=5.0,  # 시간 기반 surge 감지
    )


# =============================================================================
# 테스트 1: 정상 시나리오 - 영상 시작
# =============================================================================


def test_start_video_success(video_service: VideoProgressService):
    """영상 시작 - 정상 케이스."""
    # Arrange
    # Phase 22: 4대교육은 서버 판정, EDU-4TYPE- prefix 사용
    request = VideoPlayStartRequest(
        user_id="user-001",
        training_id="EDU-4TYPE-001",  # 4대교육 prefix
        total_duration=600,  # 10분
        is_mandatory_edu=True,  # 서버에서 재판정됨
    )

    # Act
    response = video_service.start_video(request)

    # Assert
    assert response.user_id == "user-001"
    assert response.training_id == "EDU-4TYPE-001"
    assert response.state == VideoProgressState.IN_PROGRESS  # Phase 22 수정: PLAYING → IN_PROGRESS
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

    assert response.state == VideoProgressState.IN_PROGRESS  # Phase 22 수정
    assert response.seek_allowed is False


# =============================================================================
# 테스트 2: 진행률 업데이트 - 정상
# =============================================================================


def test_update_progress_success(video_service: VideoProgressService):
    """진행률 업데이트 - 정상 케이스."""
    # Arrange: 영상 시작
    start_request = VideoPlayStartRequest(
        user_id="user-001",
        training_id="EDU-4TYPE-001",  # Phase 22: 4대교육 prefix
        total_duration=600,
        is_mandatory_edu=True,
    )
    video_service.start_video(start_request)

    # Phase 22 수정: 시간 기반 surge 감지를 위해 타임스탬프 조정
    # 180초 진행하려면 최소 180초의 wall clock time이 필요
    record = video_service._store.get("user-001", "EDU-4TYPE-001")
    record.last_update_timestamp = time.time() - 200  # 200초 전으로 설정
    video_service._store.set("user-001", "EDU-4TYPE-001", record)

    # Act: 진행률 30% 업데이트
    update_request = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="EDU-4TYPE-001",
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
        training_id="EDU-4TYPE-001",  # Phase 22: 4대교육 prefix
        total_duration=600,
        is_mandatory_edu=True,
    )
    video_service.start_video(start_request)

    # Phase 22: 시간 기반 surge 감지를 피하기 위해 타임스탬프 조정
    record = video_service._store.get("user-001", "EDU-4TYPE-001")
    record.last_update_timestamp = time.time() - 400  # 400초 전으로 설정
    video_service._store.set("user-001", "EDU-4TYPE-001", record)

    update_request_1 = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="EDU-4TYPE-001",
        current_position=300,
        watched_seconds=300,  # 50%
    )
    video_service.update_progress(update_request_1)

    # Act: 30%로 역행 시도 (regression)
    # 역행은 시간과 무관하게 항상 거부됨
    update_request_2 = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="EDU-4TYPE-001",
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
        training_id="EDU-4TYPE-001",  # Phase 22: 4대교육 prefix
        total_duration=600,
        is_mandatory_edu=True,
    )
    video_service.start_video(start_request)

    # 10% 진행
    update_request_1 = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="EDU-4TYPE-001",
        current_position=60,
        watched_seconds=60,  # 10%
    )
    video_service.update_progress(update_request_1)

    # Act: 짧은 시간 내 50%로 급증 시도 (40% 증가)
    # Note: 같은 timestamp면 급증 체크가 스킵됨. 실제로는 시간 경과 필요.
    # 테스트에서는 서비스의 타임스탬프를 직접 조작할 수 없으므로
    # 급증 감지 로직을 개별적으로 테스트

    # 저장소에서 레코드를 직접 수정하여 타임스탬프 조작
    record = video_service._store.get("user-001", "EDU-4TYPE-001")
    record.last_update_timestamp = time.time() - 5  # 5초 전
    video_service._store.set("user-001", "EDU-4TYPE-001", record)

    update_request_2 = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="EDU-4TYPE-001",
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
        training_id="EDU-4TYPE-001",  # Phase 22: 4대교육 prefix
        total_duration=600,
        is_mandatory_edu=True,
    )
    video_service.start_video(start_request)

    update_request = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="EDU-4TYPE-001",
        current_position=480,
        watched_seconds=480,  # 80%
    )
    video_service.update_progress(update_request)

    # Act: 완료 요청 (80%만 시청)
    complete_request = VideoCompleteRequest(
        user_id="user-001",
        training_id="EDU-4TYPE-001",
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
        training_id="EDU-4TYPE-001",
        total_duration=600,  # 10분
        is_mandatory_edu=True,  # 4대교육
    )
    video_service.start_video(start_request)

    # 마지막 구간 시청 (마지막 5% = 30초, 570초 이후)
    update_request = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="EDU-4TYPE-001",
        current_position=590,  # 마지막 구간
        watched_seconds=580,  # 96.7%
    )
    video_service.update_progress(update_request)

    # Act: 완료 요청
    complete_request = VideoCompleteRequest(
        user_id="user-001",
        training_id="EDU-4TYPE-001",
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
        training_id="EDU-4TYPE-001",
        total_duration=600,
        is_mandatory_edu=False,  # 일반 교육
    )
    start_response = video_service.start_video(start_request)
    assert start_response.seek_allowed is False  # 완료 전

    # 마지막 구간 시청
    update_request = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="EDU-4TYPE-001",
        current_position=590,
        watched_seconds=580,
    )
    update_response = video_service.update_progress(update_request)
    assert update_response.seek_allowed is False  # 아직 완료 안됨

    # Act: 완료
    complete_request = VideoCompleteRequest(
        user_id="user-001",
        training_id="EDU-4TYPE-001",
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
        training_id="EDU-4TYPE-001",  # Phase 22: 4대교육 prefix
        total_duration=600,
        is_mandatory_edu=True,
    )
    video_service.start_video(start_request)

    update_request = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="EDU-4TYPE-001",
        current_position=300,
        watched_seconds=300,
    )
    video_service.update_progress(update_request)

    # Act: 퀴즈 시작 가능 여부 확인
    can_start, reason = video_service.can_start_quiz("user-001", "EDU-4TYPE-001")

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
        training_id="EDU-4TYPE-001",  # Phase 22: 4대교육 prefix
        total_duration=600,
        is_mandatory_edu=True,
    )
    video_service.start_video(start_request)

    # 마지막 구간 시청
    update_request = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="EDU-4TYPE-001",
        current_position=590,
        watched_seconds=580,
    )
    video_service.update_progress(update_request)

    # 완료
    complete_request = VideoCompleteRequest(
        user_id="user-001",
        training_id="EDU-4TYPE-001",
        final_position=600,
        total_watched_seconds=580,
    )
    video_service.complete_video(complete_request)

    # Act: 퀴즈 시작 가능 여부 확인
    can_start, reason = video_service.can_start_quiz("user-001", "EDU-4TYPE-001")

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
        training_id="EDU-4TYPE-001",  # Phase 22: 4대교육 prefix
        total_duration=600,
        is_mandatory_edu=True,
    )
    video_service.start_video(start_request)

    update_request = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="EDU-4TYPE-001",
        current_position=300,
        watched_seconds=300,
    )
    video_service.update_progress(update_request)

    # Act
    status = video_service.get_status("user-001", "EDU-4TYPE-001")

    # Assert
    assert status is not None
    assert status.user_id == "user-001"
    assert status.training_id == "EDU-4TYPE-001"
    assert status.progress_percent == 50.0
    assert status.state == VideoProgressState.IN_PROGRESS  # Phase 22 수정
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
        training_id="EDU-4TYPE-001",  # Phase 22: 4대교육 prefix
        total_duration=600,
        is_mandatory_edu=True,
    )
    video_service.start_video(start_request)

    # 마지막 구간 시청
    update_request = VideoProgressUpdateRequest(
        user_id="user-001",
        training_id="EDU-4TYPE-001",
        current_position=590,
        watched_seconds=580,
    )
    video_service.update_progress(update_request)

    # 완료
    complete_request = VideoCompleteRequest(
        user_id="user-001",
        training_id="EDU-4TYPE-001",
        final_position=600,
        total_watched_seconds=580,
    )
    video_service.complete_video(complete_request)

    # Act: 완료된 영상 재시작
    restart_response = video_service.start_video(start_request)

    # Assert
    assert restart_response.state == VideoProgressState.IN_PROGRESS  # Phase 22 수정
    assert restart_response.seek_allowed is True  # 완료 후에는 시크 허용 유지


# =============================================================================
# 테스트 14: 일반 교육은 퀴즈 체크 스킵
# =============================================================================


def test_quiz_check_non_mandatory_allowed(video_service: VideoProgressService):
    """일반 교육은 퀴즈 체크 스킵."""
    # Arrange: 일반 교육 시작 (완료 안됨)
    # Phase 22: 서버 판정이므로 4대교육 prefix가 없는 ID 사용
    start_request = VideoPlayStartRequest(
        user_id="user-001",
        training_id="GENERAL-EDU-001",  # 일반 교육 (4대교육 prefix 없음)
        total_duration=600,
        is_mandatory_edu=False,  # 서버가 재판정
    )
    video_service.start_video(start_request)

    # Act: 퀴즈 시작 가능 여부 확인
    can_start, reason = video_service.can_start_quiz("user-001", "GENERAL-EDU-001")

    # Assert: 일반 교육은 퀴즈 체크 없이 허용
    assert can_start is True
    assert "Not mandatory" in reason


# =============================================================================
# Phase 22 추가 테스트: Last Segment 30초 > 5% (짧은 영상)
# =============================================================================


def test_last_segment_30sec_floor_for_short_video():
    """짧은 영상(200초)에서 last segment는 max(5%, 30초) = 30초."""
    # Arrange: 30초 floor가 적용되는 서비스
    service = VideoProgressService(
        store=VideoProgressStore(),
        completion_threshold=95.0,
        final_segment_ratio=0.05,  # 5%
        final_segment_min_seconds=30.0,  # 최소 30초
    )

    # 200초 영상: 5% = 10초이지만, max(10, 30) = 30초가 last segment
    start_request = VideoPlayStartRequest(
        user_id="user-short",
        training_id="EDU-4TYPE-SHORT",
        total_duration=200,  # 200초 (5% = 10초 < 30초)
        is_mandatory_edu=True,
    )
    service.start_video(start_request)

    # Act: 170초 위치 (200 - 30 = 170, last segment 시작점)
    # 175초에서 시작해서 마지막 구간 시청
    update_request_1 = VideoProgressUpdateRequest(
        user_id="user-short",
        training_id="EDU-4TYPE-SHORT",
        current_position=175,  # 마지막 구간 (200 - 30 = 170 이후)
        watched_seconds=175,  # 87.5%
    )
    response_1 = service.update_progress(update_request_1)
    assert response_1.accepted is True

    # 완료 요청 (95% 이상 + 마지막 구간 시청 완료)
    update_request_2 = VideoProgressUpdateRequest(
        user_id="user-short",
        training_id="EDU-4TYPE-SHORT",
        current_position=195,  # 마지막
        watched_seconds=192,  # 96%
    )
    response_2 = service.update_progress(update_request_2)

    complete_request = VideoCompleteRequest(
        user_id="user-short",
        training_id="EDU-4TYPE-SHORT",
        final_position=200,
        total_watched_seconds=192,
    )
    complete_response = service.complete_video(complete_request)

    # Assert
    assert complete_response.completed is True
    assert complete_response.quiz_unlocked is True


# =============================================================================
# Phase 22 추가 테스트: COMPLETED 상태에서 업데이트 no-op (accepted=True)
# =============================================================================


def test_completed_video_update_noop_accepted():
    """완료된 영상에 추가 업데이트 시 no-op으로 accepted=True 반환."""
    # Arrange
    service = VideoProgressService(store=VideoProgressStore())

    start_request = VideoPlayStartRequest(
        user_id="user-comp",
        training_id="EDU-4TYPE-COMP",
        total_duration=600,
        is_mandatory_edu=True,
    )
    service.start_video(start_request)

    # 마지막 구간 시청 및 완료
    update_request = VideoProgressUpdateRequest(
        user_id="user-comp",
        training_id="EDU-4TYPE-COMP",
        current_position=590,
        watched_seconds=580,
    )
    service.update_progress(update_request)

    complete_request = VideoCompleteRequest(
        user_id="user-comp",
        training_id="EDU-4TYPE-COMP",
        final_position=600,
        total_watched_seconds=580,
    )
    complete_response = service.complete_video(complete_request)
    assert complete_response.completed is True

    # Act: 이미 완료된 영상에 추가 업데이트
    noop_update = VideoProgressUpdateRequest(
        user_id="user-comp",
        training_id="EDU-4TYPE-COMP",
        current_position=100,  # 뒤로 감기 시도
        watched_seconds=100,
    )
    noop_response = service.update_progress(noop_update)

    # Assert: accepted=True (no-op), message 포함
    assert noop_response.accepted is True
    assert noop_response.message is not None
    # 메시지에 "완료" 또는 "already completed" 포함 확인
    assert "완료" in noop_response.message or "completed" in noop_response.message.lower()
    assert noop_response.state == VideoProgressState.COMPLETED


# =============================================================================
# Phase 22 추가 테스트: JWT/body user_id 불일치 → 403
# =============================================================================


@pytest.mark.anyio
async def test_user_id_mismatch_403():
    """JWT와 body의 user_id가 다르면 403 Forbidden."""
    from unittest.mock import MagicMock

    from fastapi import HTTPException

    from app.api.v1.dependencies import get_actor_user_id

    # Arrange: JWT user_id와 body user_id가 다른 경우
    mock_request = MagicMock()
    mock_request.state = MagicMock()
    mock_request.state.user_id = "jwt-user-001"

    # Act & Assert: 불일치 시 403 발생
    with pytest.raises(HTTPException) as exc_info:
        await get_actor_user_id(
            request=mock_request,
            body_user_id="different-user-002",  # 다른 user_id
        )

    assert exc_info.value.status_code == 403
    assert "mismatch" in exc_info.value.detail.lower()


@pytest.mark.anyio
async def test_user_id_jwt_priority():
    """JWT user_id가 있으면 JWT 값 우선 사용."""
    from unittest.mock import MagicMock

    from app.api.v1.dependencies import get_actor_user_id

    # Arrange: JWT user_id 있음
    mock_request = MagicMock()
    mock_request.state = MagicMock()
    mock_request.state.user_id = "jwt-user-001"

    # Act: JWT와 body 동일
    result = await get_actor_user_id(
        request=mock_request,
        body_user_id="jwt-user-001",  # 동일
    )

    # Assert
    assert result == "jwt-user-001"


@pytest.mark.anyio
async def test_user_id_body_fallback():
    """JWT 없으면 body user_id 사용 (dev fallback)."""
    from unittest.mock import MagicMock

    from app.api.v1.dependencies import get_actor_user_id

    # Arrange: JWT user_id 없음
    mock_request = MagicMock()
    mock_request.state = MagicMock(spec=[])  # user_id 속성 없음

    # Act: body user_id만 있음
    result = await get_actor_user_id(
        request=mock_request,
        body_user_id="body-user-001",
    )

    # Assert
    assert result == "body-user-001"
