"""
Phase 26: Education Expired Gate 및 Reissue Tests

테스트 범위:
1. EXPIRED 판정: due_date 23:59:59 기준
2. EXPIRED 교육 차단: start_video, update_progress, complete_video, quiz/check
3. 재발행: 새 education_id 생성 + 자산/텍스트 복사
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.education_catalog_service import (
    EducationCatalogService,
    EducationMeta,
    EducationStatus,
    SEOUL_TZ,
    clear_education_catalog_service,
    get_education_catalog_service,
)

client = TestClient(app)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def clear_catalog():
    """각 테스트 전후 카탈로그 초기화."""
    clear_education_catalog_service()
    yield
    clear_education_catalog_service()


@pytest.fixture
def catalog() -> EducationCatalogService:
    """EducationCatalogService 인스턴스."""
    return get_education_catalog_service()


# =============================================================================
# 1. EXPIRED 판정 테스트
# =============================================================================


class TestEducationExpiredJudgment:
    """EXPIRED 판정 테스트 클래스."""

    def test_education_expires_after_due_2359(self, catalog: EducationCatalogService):
        """
        test_education_expires_after_due_2359:
        due_date 당일 23:59:00에는 허용, 다음날 00:00:01에는 차단
        """
        # Given: 2025-06-30 마감인 교육
        due = date(2025, 6, 30)
        catalog.register_education(
            education_id="EDU-SEC-2025-001",
            year=2025,
            due_date=due,
            is_mandatory_4type=True,
        )

        # When/Then: due_date 23:59:00에는 ACTIVE
        just_before = datetime(2025, 6, 30, 23, 59, 0, tzinfo=SEOUL_TZ)
        assert not catalog.is_expired("EDU-SEC-2025-001", now=just_before)

        meta = catalog.get_education("EDU-SEC-2025-001")
        assert meta is not None
        assert not meta.is_expired(now=just_before)

        # When/Then: due_date 23:59:59에는 아직 ACTIVE
        at_2359_59 = datetime(2025, 6, 30, 23, 59, 59, tzinfo=SEOUL_TZ)
        assert not catalog.is_expired("EDU-SEC-2025-001", now=at_2359_59)

        # When/Then: 다음날 00:00:01에는 EXPIRED
        next_day = datetime(2025, 7, 1, 0, 0, 1, tzinfo=SEOUL_TZ)
        assert catalog.is_expired("EDU-SEC-2025-001", now=next_day)
        assert meta.is_expired(now=next_day)

    def test_education_status_dynamic_judgment(self, catalog: EducationCatalogService):
        """status 속성이 동적으로 계산되는지 확인."""
        # Given: 오늘 마감인 교육
        today = datetime.now(SEOUL_TZ).date()
        catalog.register_education(
            education_id="EDU-TEST-TODAY",
            year=today.year,
            due_date=today,
        )

        meta = catalog.get_education("EDU-TEST-TODAY")
        assert meta is not None

        # 현재 시각이 마감 시각 이전이면 ACTIVE
        now = datetime.now(SEOUL_TZ)
        if now <= meta.expires_at:
            assert meta.status == EducationStatus.ACTIVE
        else:
            assert meta.status == EducationStatus.EXPIRED

    def test_unregistered_education_not_expired(self, catalog: EducationCatalogService):
        """카탈로그에 등록되지 않은 교육은 만료되지 않은 것으로 간주."""
        # 하위 호환성: 등록되지 않은 교육은 차단하지 않음
        assert not catalog.is_expired("UNREGISTERED-EDU-001")


# =============================================================================
# 2. EXPIRED 교육 차단 테스트 (API Level)
# =============================================================================


class TestExpiredEducationBlockedApi:
    """EXPIRED 교육 차단 API 테스트 클래스."""

    @pytest.fixture
    def expired_education(self, catalog: EducationCatalogService) -> str:
        """만료된 교육 등록."""
        # 작년 마감 (확실히 만료)
        past_due = date(2023, 12, 31)
        catalog.register_education(
            education_id="EDU-SEC-2023-001",
            year=2023,
            due_date=past_due,
            is_mandatory_4type=True,
            video_asset_id="video-asset-001",
        )
        return "EDU-SEC-2023-001"

    def test_start_video_blocked_when_expired(
        self, expired_education: str
    ):
        """
        test_start_video_blocked_when_expired:
        start_video가 404로 막히는지 확인
        """
        response = client.post(
            "/api/video/play/start",
            json={
                "user_id": "user-001",
                "training_id": expired_education,
                "total_duration": 600,
            },
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["reason_code"] == "EDU_EXPIRED"

    def test_update_progress_blocked_when_expired(
        self, expired_education: str
    ):
        """update_progress가 404로 막히는지 확인."""
        response = client.post(
            "/api/video/progress",
            json={
                "user_id": "user-001",
                "training_id": expired_education,
                "current_position": 100,
                "watched_seconds": 100,
            },
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["reason_code"] == "EDU_EXPIRED"

    def test_complete_video_blocked_when_expired(
        self, expired_education: str
    ):
        """complete_video가 404로 막히는지 확인."""
        response = client.post(
            "/api/video/complete",
            json={
                "user_id": "user-001",
                "training_id": expired_education,
                "final_position": 600,
                "total_watched_seconds": 600,
            },
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["reason_code"] == "EDU_EXPIRED"

    def test_can_start_quiz_blocked_when_expired(
        self, expired_education: str
    ):
        """
        test_can_start_quiz_blocked_when_expired:
        퀴즈 언락/시작도 404로 막히는지 확인
        """
        response = client.get(
            "/api/video/quiz/check",
            params={
                "user_id": "user-001",
                "training_id": expired_education,
            },
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["reason_code"] == "EDU_EXPIRED"

    def test_get_video_status_blocked_when_expired(
        self, expired_education: str
    ):
        """get_video_status가 404로 막히는지 확인."""
        response = client.get(
            "/api/video/status",
            params={
                "user_id": "user-001",
                "training_id": expired_education,
            },
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["reason_code"] == "EDU_EXPIRED"

    def test_active_education_not_blocked(self, catalog: EducationCatalogService):
        """활성 교육은 차단되지 않음."""
        # Given: 내년 마감인 교육 (활성)
        future_due = date(2026, 12, 31)
        catalog.register_education(
            education_id="EDU-SEC-2026-001",
            year=2026,
            due_date=future_due,
            is_mandatory_4type=True,
        )

        # When: start_video 호출
        response = client.post(
            "/api/video/play/start",
            json={
                "user_id": "user-001",
                "training_id": "EDU-SEC-2026-001",
                "total_duration": 600,
            },
        )

        # Then: 200 OK (차단되지 않음)
        assert response.status_code == 200


# =============================================================================
# 3. 재발행 테스트
# =============================================================================


class TestEducationReissue:
    """교육 재발행 테스트 클래스."""

    def test_reissue_creates_new_education_copying_assets_and_texts(
        self, catalog: EducationCatalogService
    ):
        """
        test_reissue_creates_new_education_copying_assets_and_texts:
        재발행 시 video_asset_id/script/subtitle이 복사되는지 + 새 id 생성 규칙 확인
        """
        # Given: 원본 교육 (2025년)
        catalog.register_education(
            education_id="EDU-SEC-2025-001",
            year=2025,
            due_date=date(2025, 12, 31),
            is_mandatory_4type=True,
            title="보안교육 2025",
            video_asset_id="video-asset-sec-001",
            script_text="스크립트 내용 전체...",
            subtitle_text="자막 내용 전체...",
            video_ids=["vid-001", "vid-002"],
        )

        # When: 2026년으로 재발행
        new_meta = catalog.reissue(
            source_education_id="EDU-SEC-2025-001",
            target_year=2026,
            new_due_date=date(2026, 12, 31),
        )

        # Then: 새 education_id 생성 규칙 확인
        assert new_meta.education_id == "EDU-SEC-2026-001"

        # Then: 연도/마감일 업데이트
        assert new_meta.year == 2026
        assert new_meta.due_date == date(2026, 12, 31)

        # Then: 자산/텍스트 복사 확인
        assert new_meta.video_asset_id == "video-asset-sec-001"
        assert new_meta.script_text == "스크립트 내용 전체..."
        assert new_meta.subtitle_text == "자막 내용 전체..."

        # Then: 기타 필드 복사 확인
        assert new_meta.is_mandatory_4type is True
        assert new_meta.title == "보안교육 2025"
        assert new_meta.video_ids == ["vid-001", "vid-002"]

        # Then: 새 교육은 ACTIVE
        assert new_meta.status == EducationStatus.ACTIVE

    def test_reissue_conflict_when_target_exists(
        self, catalog: EducationCatalogService
    ):
        """
        test_reissue_conflict_when_target_exists:
        이미 같은 education_id가 존재하면 ValueError
        """
        # Given: 원본 교육
        catalog.register_education(
            education_id="EDU-HAR-2025-001",
            year=2025,
            due_date=date(2025, 12, 31),
        )

        # And: target이 이미 존재
        catalog.register_education(
            education_id="EDU-HAR-2026-001",
            year=2026,
            due_date=date(2026, 12, 31),
        )

        # When/Then: 재발행 시 ValueError
        with pytest.raises(ValueError) as exc_info:
            catalog.reissue(
                source_education_id="EDU-HAR-2025-001",
                target_year=2026,
                new_due_date=date(2026, 12, 31),
            )

        assert "already exists" in str(exc_info.value).lower()

    def test_reissue_source_not_found(self, catalog: EducationCatalogService):
        """source가 없으면 ValueError."""
        with pytest.raises(ValueError) as exc_info:
            catalog.reissue(
                source_education_id="NONEXISTENT-001",
                target_year=2026,
                new_due_date=date(2026, 12, 31),
            )

        assert "not found" in str(exc_info.value).lower()

    def test_reissue_due_date_out_of_range(self, catalog: EducationCatalogService):
        """new_due_date가 target_year 범위를 벗어나면 ValueError."""
        # Given: 원본 교육
        catalog.register_education(
            education_id="EDU-DIS-2025-001",
            year=2025,
            due_date=date(2025, 12, 31),
        )

        # When/Then: due_date가 target_year와 다르면 ValueError
        with pytest.raises(ValueError) as exc_info:
            catalog.reissue(
                source_education_id="EDU-DIS-2025-001",
                target_year=2026,
                new_due_date=date(2027, 6, 30),  # 2027년은 target_year 2026과 다름
            )

        assert "not in target year" in str(exc_info.value).lower()


# =============================================================================
# 4. Admin API 테스트
# =============================================================================


class TestAdminReissueApi:
    """Admin 재발행 API 테스트 클래스."""

    def test_admin_reissue_api_success(self, catalog: EducationCatalogService):
        """POST /api/admin/education/reissue 성공."""
        # Given: 원본 교육
        catalog.register_education(
            education_id="EDU-SAF-2025-001",
            year=2025,
            due_date=date(2025, 12, 31),
            is_mandatory_4type=True,
            video_asset_id="video-asset-saf-001",
            script_text="안전교육 스크립트",
            subtitle_text="안전교육 자막",
        )

        # When: Admin API로 재발행
        response = client.post(
            "/api/admin/education/reissue",
            json={
                "source_education_id": "EDU-SAF-2025-001",
                "target_year": 2026,
                "new_due_date": "2026-12-31",
            },
        )

        # Then: 200 OK
        assert response.status_code == 200
        data = response.json()

        assert data["success"] is True
        assert data["new_education_id"] == "EDU-SAF-2026-001"
        assert data["source_education_id"] == "EDU-SAF-2025-001"
        assert data["target_year"] == 2026
        assert data["due_date"] == "2026-12-31"
        assert data["copied_fields"]["video_asset_id"] == "video-asset-saf-001"
        assert data["copied_fields"]["is_mandatory_4type"] is True

    def test_admin_reissue_api_source_not_found(self):
        """POST /api/admin/education/reissue - source 없음 404."""
        response = client.post(
            "/api/admin/education/reissue",
            json={
                "source_education_id": "NONEXISTENT-001",
                "target_year": 2026,
                "new_due_date": "2026-12-31",
            },
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["reason_code"] == "SOURCE_NOT_FOUND"

    def test_admin_reissue_api_conflict(self, catalog: EducationCatalogService):
        """POST /api/admin/education/reissue - target 중복 409."""
        # Given: 원본 및 target 모두 존재
        catalog.register_education(
            education_id="EDU-SEC-2025-002",
            year=2025,
            due_date=date(2025, 12, 31),
        )
        catalog.register_education(
            education_id="EDU-SEC-2026-002",
            year=2026,
            due_date=date(2026, 12, 31),
        )

        # When: 재발행 시도
        response = client.post(
            "/api/admin/education/reissue",
            json={
                "source_education_id": "EDU-SEC-2025-002",
                "target_year": 2026,
                "new_due_date": "2026-12-31",
            },
        )

        # Then: 409 Conflict
        assert response.status_code == 409
        data = response.json()
        assert data["detail"]["reason_code"] == "TARGET_EXISTS"

    def test_admin_reissue_api_due_date_out_of_range(
        self, catalog: EducationCatalogService
    ):
        """POST /api/admin/education/reissue - due_date 범위 오류 400."""
        # Given: 원본 교육
        catalog.register_education(
            education_id="EDU-HAR-2025-002",
            year=2025,
            due_date=date(2025, 12, 31),
        )

        # When: due_date가 target_year와 다름
        response = client.post(
            "/api/admin/education/reissue",
            json={
                "source_education_id": "EDU-HAR-2025-002",
                "target_year": 2026,
                "new_due_date": "2027-06-30",  # 2027년
            },
        )

        # Then: 400 Bad Request
        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["reason_code"] == "DUE_DATE_OUT_OF_RANGE"

    def test_admin_get_education_meta(self, catalog: EducationCatalogService):
        """GET /api/admin/education/{education_id} 성공."""
        # Given: 교육 등록
        catalog.register_education(
            education_id="EDU-TEST-001",
            year=2025,
            due_date=date(2025, 6, 30),
            is_mandatory_4type=True,
            title="테스트 교육",
            video_asset_id="video-test-001",
            script_text="스크립트",
        )

        # When: 조회
        response = client.get("/api/admin/education/EDU-TEST-001")

        # Then: 200 OK
        assert response.status_code == 200
        data = response.json()

        assert data["education_id"] == "EDU-TEST-001"
        assert data["year"] == 2025
        assert data["due_date"] == "2025-06-30"
        assert data["is_mandatory_4type"] is True
        assert data["title"] == "테스트 교육"
        assert data["video_asset_id"] == "video-test-001"
        assert data["has_script"] is True
        assert data["has_subtitle"] is False

    def test_admin_get_education_meta_not_found(self):
        """GET /api/admin/education/{education_id} - 없음 404."""
        response = client.get("/api/admin/education/NONEXISTENT-001")

        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["reason_code"] == "EDUCATION_NOT_FOUND"


# =============================================================================
# 5. EducationMeta 모델 테스트
# =============================================================================


class TestEducationMeta:
    """EducationMeta dataclass 테스트."""

    def test_expires_at_calculation(self):
        """expires_at이 due_date 23:59:59로 계산되는지 확인."""
        meta = EducationMeta(
            education_id="TEST-001",
            year=2025,
            due_date=date(2025, 6, 30),
        )

        expected = datetime(2025, 6, 30, 23, 59, 59, tzinfo=SEOUL_TZ)
        assert meta.expires_at == expected

    def test_to_dict(self, catalog: EducationCatalogService):
        """to_dict() 메서드 확인."""
        meta = catalog.register_education(
            education_id="TEST-DICT-001",
            year=2025,
            due_date=date(2025, 12, 31),
            is_mandatory_4type=True,
            video_asset_id="vid-001",
        )

        d = meta.to_dict()

        assert d["education_id"] == "TEST-DICT-001"
        assert d["year"] == 2025
        assert d["due_date"] == "2025-12-31"
        assert d["is_mandatory_4type"] is True
        assert d["video_asset_id"] == "vid-001"
        assert "expires_at" in d
        assert "status" in d
