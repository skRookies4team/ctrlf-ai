"""
Personalization Mapper Tests (개인화 매퍼 테스트)

personalization_mapper.py의 SubIntentId -> Q1-Q20 변환 로직 테스트.
"""

import pytest

from app.services.personalization_mapper import (
    to_personalization_q,
    is_personalization_q,
    is_personalization_request,
    _classify_edu_status,
    _classify_hr_leave,
    extract_period_from_query,
    SUBINTENT_TO_Q,
)


# =============================================================================
# is_personalization_q 테스트
# =============================================================================


class TestIsPersonalizationQ:
    """is_personalization_q 함수 테스트."""

    def test_valid_q_ids(self):
        """유효한 Q ID (Q1-Q20) 테스트."""
        assert is_personalization_q("Q1") is True
        assert is_personalization_q("Q11") is True
        assert is_personalization_q("Q20") is True

    def test_invalid_q_ids(self):
        """유효하지 않은 Q ID 테스트."""
        assert is_personalization_q("Q0") is False  # 0은 범위 밖
        assert is_personalization_q("Q21") is False  # 21은 범위 밖
        assert is_personalization_q("Q100") is False
        assert is_personalization_q("HR_LEAVE_CHECK") is False
        assert is_personalization_q("") is False
        assert is_personalization_q(None) is False


# =============================================================================
# to_personalization_q 테스트
# =============================================================================


class TestToPersonalizationQ:
    """to_personalization_q 함수 테스트."""

    def test_already_q_format(self):
        """이미 Q 형식인 경우 그대로 반환."""
        assert to_personalization_q("Q11", "연차 며칠?") == "Q11"
        assert to_personalization_q("Q1", "미이수 교육") == "Q1"
        assert to_personalization_q("Q20", "할 일") == "Q20"

    def test_hr_leave_check_default(self):
        """HR_LEAVE_CHECK + 연차 키워드 -> Q11 (기본)."""
        assert to_personalization_q("HR_LEAVE_CHECK", "내 연차 며칠?") == "Q11"
        assert to_personalization_q("HR_LEAVE_CHECK", "연차 잔여일") == "Q11"

    def test_hr_leave_check_welfare(self):
        """HR_LEAVE_CHECK + 복지 키워드 -> Q14."""
        assert to_personalization_q("HR_LEAVE_CHECK", "복지포인트 잔액") == "Q14"
        assert to_personalization_q("HR_LEAVE_CHECK", "복지 포인트 얼마야") == "Q14"
        assert to_personalization_q("HR_LEAVE_CHECK", "식대 조회") == "Q14"

    def test_hr_leave_check_attendance(self):
        """HR_LEAVE_CHECK + 근태 키워드 -> Q10."""
        assert to_personalization_q("HR_LEAVE_CHECK", "근태 현황 조회") == "Q10"
        assert to_personalization_q("HR_LEAVE_CHECK", "출근 기록") == "Q10"
        assert to_personalization_q("HR_LEAVE_CHECK", "퇴근 시간") == "Q10"

    def test_hr_welfare_check(self):
        """HR_WELFARE_CHECK -> Q14 변환."""
        assert to_personalization_q("HR_WELFARE_CHECK", "복지 포인트") == "Q14"

    def test_hr_attendance_check(self):
        """HR_ATTENDANCE_CHECK -> Q10 변환 (내 근태 현황)."""
        assert to_personalization_q("HR_ATTENDANCE_CHECK", "근태 현황") == "Q10"

    def test_edu_status_check_q1(self):
        """EDU_STATUS_CHECK + 미이수 키워드 -> Q1."""
        assert to_personalization_q("EDU_STATUS_CHECK", "미이수 교육 조회") == "Q1"
        assert to_personalization_q("EDU_STATUS_CHECK", "안 들은 교육") == "Q1"
        assert to_personalization_q("EDU_STATUS_CHECK", "필수 교육 미이수") == "Q1"

    def test_edu_status_check_q3(self):
        """EDU_STATUS_CHECK + 마감 키워드 -> Q3."""
        assert to_personalization_q("EDU_STATUS_CHECK", "이번 달 마감 교육") == "Q3"
        assert to_personalization_q("EDU_STATUS_CHECK", "데드라인 언제야") == "Q3"
        assert to_personalization_q("EDU_STATUS_CHECK", "이달 내 마감") == "Q3"

    def test_edu_status_check_q9(self):
        """EDU_STATUS_CHECK + 이번 주 키워드 -> Q9."""
        assert to_personalization_q("EDU_STATUS_CHECK", "이번 주 할 일") == "Q9"
        assert to_personalization_q("EDU_STATUS_CHECK", "이번주 해야 할 거") == "Q9"
        assert to_personalization_q("EDU_STATUS_CHECK", "금주 할 일") == "Q9"

    def test_edu_status_check_q2_default(self):
        """EDU_STATUS_CHECK + 그 외 -> Q2 (기본)."""
        assert to_personalization_q("EDU_STATUS_CHECK", "교육 현황 확인") == "Q2"
        assert to_personalization_q("EDU_STATUS_CHECK", "수료 현황") == "Q2"
        assert to_personalization_q("EDU_STATUS_CHECK", "진도 확인") == "Q2"

    def test_non_personalization_subintent(self):
        """개인화 대상이 아닌 SubIntentId -> None."""
        assert to_personalization_q("QUIZ_START", "퀴즈 시작") is None
        assert to_personalization_q("QUIZ_SUBMIT", "답안 제출") is None
        assert to_personalization_q("POLICY_QA", "정책 질문") is None

    def test_empty_subintent(self):
        """빈 SubIntentId -> None."""
        assert to_personalization_q("", "연차 질문") is None
        assert to_personalization_q(None, "연차 질문") is None


# =============================================================================
# _classify_edu_status 테스트
# =============================================================================


class TestClassifyEduStatus:
    """_classify_edu_status 함수 테스트."""

    def test_q1_keywords(self):
        """미이수 관련 키워드 -> Q1."""
        assert _classify_edu_status("미이수 교육 알려줘") == "Q1"
        assert _classify_edu_status("안 들은 교육") == "Q1"
        assert _classify_edu_status("필수 미이수") == "Q1"

    def test_q3_keywords(self):
        """마감 관련 키워드 -> Q3."""
        assert _classify_edu_status("이번 달 마감") == "Q3"
        assert _classify_edu_status("데드라인") == "Q3"
        assert _classify_edu_status("곧 마감되는 교육") == "Q3"

    def test_q9_keywords(self):
        """이번 주 관련 키워드 -> Q9."""
        assert _classify_edu_status("이번 주 할 일") == "Q9"
        assert _classify_edu_status("해야 할 교육") == "Q9"
        assert _classify_edu_status("금주 교육") == "Q9"

    def test_q2_default(self):
        """그 외 -> Q2 (기본)."""
        assert _classify_edu_status("교육 현황") == "Q2"
        assert _classify_edu_status("내 교육 진도") == "Q2"
        assert _classify_edu_status("수료증 확인") == "Q2"


# =============================================================================
# _classify_hr_leave 테스트
# =============================================================================


class TestClassifyHrLeave:
    """_classify_hr_leave 함수 테스트."""

    def test_q14_welfare_keywords(self):
        """복지/식대 관련 키워드 -> Q14."""
        assert _classify_hr_leave("복지포인트 잔액") == "Q14"
        assert _classify_hr_leave("복지 포인트 얼마야") == "Q14"
        assert _classify_hr_leave("식대 조회") == "Q14"
        assert _classify_hr_leave("선택복지 현황") == "Q14"
        assert _classify_hr_leave("포인트 잔액") == "Q14"

    def test_q10_attendance_keywords(self):
        """근태 관련 키워드 -> Q10."""
        assert _classify_hr_leave("근태 현황") == "Q10"
        assert _classify_hr_leave("출근 기록") == "Q10"
        assert _classify_hr_leave("퇴근 시간") == "Q10"
        assert _classify_hr_leave("근태현황 조회") == "Q10"

    def test_q11_default(self):
        """그 외 (연차) -> Q11 (기본)."""
        assert _classify_hr_leave("연차 며칠?") == "Q11"
        assert _classify_hr_leave("내 연차 잔여일") == "Q11"
        assert _classify_hr_leave("휴가 현황") == "Q11"


# =============================================================================
# is_personalization_request 테스트
# =============================================================================


class TestIsPersonalizationRequest:
    """is_personalization_request 함수 테스트."""

    def test_q_format(self):
        """Q1-Q20 형식 -> True."""
        assert is_personalization_request("Q1") is True
        assert is_personalization_request("Q11") is True
        assert is_personalization_request("Q20") is True

    def test_mappable_subintent(self):
        """매핑 가능한 SubIntentId -> True."""
        assert is_personalization_request("HR_LEAVE_CHECK") is True
        assert is_personalization_request("HR_WELFARE_CHECK") is True
        assert is_personalization_request("EDU_STATUS_CHECK") is True

    def test_non_personalization(self):
        """개인화 대상 아님 -> False."""
        assert is_personalization_request("QUIZ_START") is False
        assert is_personalization_request("POLICY_QA") is False
        assert is_personalization_request("") is False
        assert is_personalization_request(None) is False


# =============================================================================
# SUBINTENT_TO_Q 상수 테스트
# =============================================================================


class TestSubintentToQMapping:
    """SUBINTENT_TO_Q 상수 매핑 테스트."""

    def test_hr_welfare_mapping(self):
        """HR_WELFARE_CHECK -> Q14."""
        assert SUBINTENT_TO_Q["HR_WELFARE_CHECK"] == "Q14"

    def test_hr_attendance_mapping(self):
        """HR_ATTENDANCE_CHECK -> Q10 (내 근태 현황)."""
        assert SUBINTENT_TO_Q["HR_ATTENDANCE_CHECK"] == "Q10"

    def test_hr_leave_not_in_direct_mapping(self):
        """HR_LEAVE_CHECK는 query 기반 분류를 사용하므로 직접 매핑에 없음."""
        assert "HR_LEAVE_CHECK" not in SUBINTENT_TO_Q


# =============================================================================
# extract_period_from_query 테스트
# =============================================================================


class TestExtractPeriodFromQuery:
    """extract_period_from_query 함수 테스트."""

    def test_this_week_keywords(self):
        """이번 주 관련 키워드 -> this-week."""
        assert extract_period_from_query("이번 주 연차 현황") == "this-week"
        assert extract_period_from_query("이번주 할 일") == "this-week"
        assert extract_period_from_query("금주 교육") == "this-week"

    def test_this_month_keywords(self):
        """이번 달 관련 키워드 -> this-month."""
        assert extract_period_from_query("이번 달 교육 마감") == "this-month"
        assert extract_period_from_query("이번달 연차") == "this-month"
        assert extract_period_from_query("이달 현황") == "this-month"

    def test_three_months_keywords(self):
        """3개월 관련 키워드 -> 3m."""
        assert extract_period_from_query("3개월 통계") == "3m"
        assert extract_period_from_query("최근 3개월 현황") == "3m"

    def test_this_year_keywords(self):
        """올해 관련 키워드 -> this-year."""
        assert extract_period_from_query("올해 연차 사용량") == "this-year"
        assert extract_period_from_query("금년 교육 현황") == "this-year"

    def test_no_period(self):
        """기간 키워드 없음 -> None."""
        assert extract_period_from_query("연차 며칠?") is None
        assert extract_period_from_query("내 교육 현황") is None
        assert extract_period_from_query("복지 포인트 조회") is None
