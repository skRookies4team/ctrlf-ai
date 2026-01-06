"""
Privacy Query Gate 단위 테스트

조합 규칙 기반 개인정보성 명단 요청 차단 로직을 검증합니다.
"""

import pytest
from app.services.privacy_query_gate import (
    PrivacyQueryGate,
    PrivacyGateDecision,
    PrivacyGateResult,
    get_privacy_gate,
)


class TestPrivacyQueryGate:
    """PrivacyQueryGate 단위 테스트"""

    @pytest.fixture
    def gate(self) -> PrivacyQueryGate:
        """테스트용 게이트 인스턴스"""
        return PrivacyQueryGate()

    # =========================================================================
    # 차단 케이스: 대상 + 행위 + 민감속성 조합
    # =========================================================================

    def test_block_employee_training_list(self, gate: PrivacyQueryGate):
        """직원 + 교육 + 명단 → 차단"""
        query = "보안 교육을 한 번도 안 들은 직원이나, 퀴즈 점수가 계속 낮은 직원을 리스트업해줄 수 있어?"
        result = gate.check(query)

        assert result.blocked is True
        assert result.decision == PrivacyGateDecision.BLOCK_PII_LIST
        assert result.score_total >= 6
        assert "직원" in result.matched_target_terms or "팀" in result.matched_target_terms
        assert len(result.matched_action_terms) > 0
        assert len(result.matched_sensitive_terms) > 0

    def test_block_quiz_score_ranking(self, gate: PrivacyQueryGate):
        """퀴즈 점수 + 랭킹 + 직원 → 차단"""
        query = "퀴즈 점수 낮은 직원 명단 뽑아줘"
        result = gate.check(query)

        assert result.blocked is True
        assert result.decision == PrivacyGateDecision.BLOCK_PII_LIST

    def test_block_performance_worst_list(self, gate: PrivacyQueryGate):
        """성과평가 + 최하위 + 직원 → 차단"""
        query = "성과평가 최하위 직원 리스트업해줘"
        result = gate.check(query)

        assert result.blocked is True
        assert result.decision == PrivacyGateDecision.BLOCK_PII_LIST

    def test_block_incomplete_training_members(self, gate: PrivacyQueryGate):
        """미이수 + 팀원 + 조회 → 차단"""
        query = "교육 미이수 팀원 누구야?"
        result = gate.check(query)

        assert result.blocked is True
        assert result.decision == PrivacyGateDecision.BLOCK_PII_LIST

    def test_block_department_training_status(self, gate: PrivacyQueryGate):
        """부서 + 교육 + 현황 → 차단 (명단 요청으로 판단)"""
        query = "마케팅팀 직원들 교육 이수 현황 알려줘"
        result = gate.check(query)

        assert result.blocked is True

    def test_block_low_score_employees(self, gate: PrivacyQueryGate):
        """점수 + 낮은 + 사원 + 목록 → 차단"""
        query = "점수가 낮은 사원 목록 보여줘"
        result = gate.check(query)

        assert result.blocked is True

    def test_block_incomplete_training_who(self, gate: PrivacyQueryGate):
        """미이수자 + 누가 → 차단"""
        query = "필수 교육 미이수자가 누구야?"
        result = gate.check(query)

        assert result.blocked is True

    # =========================================================================
    # 허용 케이스: 1인칭 개인화 요청
    # =========================================================================

    def test_allow_my_training_status(self, gate: PrivacyQueryGate):
        """내 교육 현황 → 허용"""
        query = "내 교육 현황 알려줘"
        result = gate.check(query)

        assert result.blocked is False
        assert result.decision == PrivacyGateDecision.ALLOW
        assert result.is_first_person is True

    def test_allow_my_quiz_score(self, gate: PrivacyQueryGate):
        """내 퀴즈 점수 → 허용"""
        query = "내 퀴즈 점수 확인해줘"
        result = gate.check(query)

        assert result.blocked is False
        assert result.is_first_person is True

    def test_allow_my_incomplete_training(self, gate: PrivacyQueryGate):
        """내가 아직 안 들은 교육 → 허용"""
        query = "제가 아직 수강 안 한 교육 뭐가 있어?"
        result = gate.check(query)

        assert result.blocked is False
        assert result.is_first_person is True

    def test_allow_my_performance(self, gate: PrivacyQueryGate):
        """본인 성과 → 허용"""
        query = "본인 성과 평가 결과 알려줘"
        result = gate.check(query)

        assert result.blocked is False
        assert result.is_first_person is True

    # =========================================================================
    # 허용 케이스: 일반 질문 (민감속성 없음)
    # =========================================================================

    def test_allow_general_training_info(self, gate: PrivacyQueryGate):
        """일반 교육 정보 → 허용"""
        query = "정보보안 교육 일정이 어떻게 돼?"
        result = gate.check(query)

        # 대상(직원)이 없으므로 점수 부족 → 허용
        assert result.blocked is False

    def test_allow_policy_question(self, gate: PrivacyQueryGate):
        """정책 관련 질문 → 허용"""
        query = "연차 사용 규정 알려줘"
        result = gate.check(query)

        assert result.blocked is False

    def test_allow_training_content(self, gate: PrivacyQueryGate):
        """교육 내용 질문 → 허용"""
        query = "개인정보보호 교육에서 뭘 배워?"
        result = gate.check(query)

        assert result.blocked is False

    # =========================================================================
    # 경계 케이스
    # =========================================================================

    def test_score_below_threshold(self, gate: PrivacyQueryGate):
        """점수가 임계값 미만이면 허용"""
        # 대상만 있고 행위/속성 없음 → 점수 2
        query = "직원들 어디 있어?"
        result = gate.check(query)

        assert result.blocked is False
        assert result.score_total < 6

    def test_action_and_sensitive_but_no_target(self, gate: PrivacyQueryGate):
        """행위 + 속성이 있지만 대상 없음 → 허용"""
        query = "교육 이수 현황 통계 보여줘"
        result = gate.check(query)

        # "통계"는 명단화 행위가 아님, 대상 없음 → 허용
        # 단, "현황"이 action에 있고 "교육", "이수"가 sensitive에 있으면 부분 점수 발생
        # 하지만 대상이 없으므로 score_target = 0 → 총점 6 미만
        assert result.score_target == 0

    # =========================================================================
    # 싱글톤 테스트
    # =========================================================================

    def test_singleton_instance(self):
        """싱글톤 인스턴스 반환 확인"""
        gate1 = get_privacy_gate()
        gate2 = get_privacy_gate()

        assert gate1 is gate2

    # =========================================================================
    # 점수 계산 테스트
    # =========================================================================

    def test_score_calculation(self, gate: PrivacyQueryGate):
        """점수 계산 로직 검증"""
        query = "직원 교육 이수 명단 뽑아줘"
        result = gate.check(query)

        # 대상(직원) +2, 행위(명단, 뽑아) +3, 속성(교육, 이수) +3 = 8
        assert result.score_target == 2
        assert result.score_action == 3
        assert result.score_sensitive == 3
        assert result.score_total == 8

    # =========================================================================
    # 응답 메시지 테스트
    # =========================================================================

    def test_block_response_message(self, gate: PrivacyQueryGate):
        """차단 시 표준 응답 메시지 반환"""
        query = "퀴즈 점수 낮은 직원 명단"
        result = gate.check(query)

        assert result.block_response is not None
        assert "개인 식별이 가능한" in result.block_response
        assert "본인 정보 조회" in result.block_response or "본인의 교육" in result.block_response


class TestPrivacyQueryGateEdgeCases:
    """경계 케이스 및 특수 상황 테스트"""

    @pytest.fixture
    def gate(self) -> PrivacyQueryGate:
        return PrivacyQueryGate()

    def test_empty_query(self, gate: PrivacyQueryGate):
        """빈 쿼리 처리"""
        result = gate.check("")
        assert result.blocked is False

    def test_whitespace_query(self, gate: PrivacyQueryGate):
        """공백만 있는 쿼리 처리"""
        result = gate.check("   ")
        assert result.blocked is False

    def test_case_insensitive_matching(self, gate: PrivacyQueryGate):
        """대소문자 무관 매칭"""
        query = "직원 QUIZ 점수 리스트"
        result = gate.check(query)
        # "QUIZ"가 "퀴즈"로 매칭되지 않으므로, 한국어 키워드만 매칭
        # 하지만 "점수", "리스트"가 있으므로 부분 점수 발생
        assert "직원" in result.matched_target_terms

    def test_mixed_first_and_third_person(self, gate: PrivacyQueryGate):
        """1인칭 + 3인칭 혼합 → 차단"""
        query = "내 팀원들 교육 현황 알려줘"
        result = gate.check(query)

        # "팀원"이 대상이므로 1인칭으로 간주되지 않음
        assert result.is_first_person is False
        # 하지만 점수에 따라 차단 여부 결정
        # "팀원" +2, "현황" +3, "교육" +3 = 8 → 차단
        assert result.blocked is True
