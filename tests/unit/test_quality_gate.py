"""
Quality Gate Unit Tests - Phase 58

L2 Distance 기반 품질 게이트 단위 테스트

테스트 케이스:
1. min_l2=1.2 → OK/PROCEED
2. min_l2=1.5 → LOW/PROCEED_WITH_WARNING
3. min_l2=1.7 → INSUFFICIENT/REJECT
4. sources=[] → INSUFFICIENT/REJECT
5. 거리 필드명 다양성 테스트 (score, l2_distance, distance)
"""

import pytest
from dataclasses import dataclass
from typing import Optional

from app.services.chat.quality_gate import (
    QualityGrade,
    QualityAction,
    QualityGateDecision,
    extract_distance,
    calculate_distance_stats,
    evaluate_sources_quality,
    build_clarification_suggestions,
    build_clarification_response,
)


# =============================================================================
# Mock Source 클래스
# =============================================================================

@dataclass
class MockSource:
    """테스트용 Mock Source"""
    doc_id: str
    title: str
    snippet: str
    score: Optional[float] = None
    l2_distance: Optional[float] = None
    distance: Optional[float] = None


# =============================================================================
# extract_distance 테스트
# =============================================================================

class TestExtractDistance:
    """거리 추출 유틸리티 테스트"""

    def test_extract_from_score_field(self):
        """score 필드에서 거리 추출"""
        source = MockSource("doc1", "Title", "Snippet", score=1.23)
        assert extract_distance(source) == 1.23

    def test_extract_from_l2_distance_field(self):
        """l2_distance 필드에서 거리 추출"""
        source = MockSource("doc1", "Title", "Snippet", l2_distance=0.89)
        # score가 우선순위 높으므로 score=None이어야 l2_distance 사용
        source.score = None
        assert extract_distance(source) == 0.89

    def test_extract_from_distance_field(self):
        """distance 필드에서 거리 추출"""
        source = MockSource("doc1", "Title", "Snippet", distance=1.45)
        source.score = None
        source.l2_distance = None
        assert extract_distance(source) == 1.45

    def test_extract_from_dict(self):
        """dict에서 거리 추출"""
        source = {"doc_id": "doc1", "score": 0.95}
        assert extract_distance(source) == 0.95

    def test_extract_returns_none_when_missing(self):
        """거리 필드 없으면 None 반환"""
        source = MockSource("doc1", "Title", "Snippet")
        assert extract_distance(source) is None

    def test_score_priority_over_l2_distance(self):
        """score가 l2_distance보다 우선"""
        source = MockSource("doc1", "Title", "Snippet", score=1.0, l2_distance=2.0)
        assert extract_distance(source) == 1.0


# =============================================================================
# calculate_distance_stats 테스트
# =============================================================================

class TestCalculateDistanceStats:
    """거리 통계 계산 테스트"""

    def test_basic_stats(self):
        """기본 통계 계산"""
        sources = [
            MockSource("d1", "T1", "S1", score=1.0),
            MockSource("d2", "T2", "S2", score=1.5),
            MockSource("d3", "T3", "S3", score=2.0),
        ]
        min_d, avg_d, max_d = calculate_distance_stats(sources)

        assert min_d == 1.0
        assert avg_d == 1.5
        assert max_d == 2.0

    def test_empty_sources(self):
        """빈 소스 리스트"""
        min_d, avg_d, max_d = calculate_distance_stats([])

        assert min_d == float('inf')
        assert avg_d == float('inf')
        assert max_d == float('inf')

    def test_sources_without_distance(self):
        """거리 값 없는 소스"""
        sources = [
            MockSource("d1", "T1", "S1"),
            MockSource("d2", "T2", "S2"),
        ]
        min_d, avg_d, max_d = calculate_distance_stats(sources)

        assert min_d == float('inf')


# =============================================================================
# evaluate_sources_quality 테스트 (핵심)
# =============================================================================

class TestEvaluateSourcesQuality:
    """품질 평가 핵심 테스트"""

    def test_high_quality_ok(self):
        """L2 < 1.4 → OK/PROCEED"""
        sources = [
            MockSource("d1", "T1", "S1", score=1.2),
            MockSource("d2", "T2", "S2", score=1.3),
        ]
        decision = evaluate_sources_quality(
            sources,
            warn_threshold=1.4,
            reject_threshold=1.6,
        )

        assert decision.grade == QualityGrade.OK
        assert decision.action == QualityAction.PROCEED
        assert decision.min_l2_distance == 1.2
        assert decision.clarify_message is None
        assert decision.warning_message is None

    def test_medium_quality_low(self):
        """1.4 < L2 <= 1.6 → LOW/PROCEED_WITH_WARNING"""
        sources = [
            MockSource("d1", "T1", "S1", score=1.5),
            MockSource("d2", "T2", "S2", score=1.55),
        ]
        decision = evaluate_sources_quality(
            sources,
            warn_threshold=1.4,
            reject_threshold=1.6,
        )

        assert decision.grade == QualityGrade.LOW
        assert decision.action == QualityAction.PROCEED_WITH_WARNING
        assert decision.min_l2_distance == 1.5
        assert decision.warning_message is not None
        assert "제한" in decision.warning_message or "낮을" in decision.warning_message

    def test_low_quality_insufficient(self):
        """L2 > 1.6 → INSUFFICIENT/REJECT"""
        sources = [
            MockSource("d1", "T1", "S1", score=1.7),
            MockSource("d2", "T2", "S2", score=1.8),
        ]
        decision = evaluate_sources_quality(
            sources,
            warn_threshold=1.4,
            reject_threshold=1.6,
        )

        assert decision.grade == QualityGrade.INSUFFICIENT
        assert decision.action == QualityAction.REJECT
        assert decision.min_l2_distance == 1.7
        assert decision.clarify_message is not None
        assert "근거" in decision.clarify_message or "구체화" in decision.clarify_message

    def test_empty_sources_insufficient(self):
        """sources=[] → INSUFFICIENT/REJECT"""
        decision = evaluate_sources_quality(
            [],
            warn_threshold=1.4,
            reject_threshold=1.6,
        )

        assert decision.grade == QualityGrade.INSUFFICIENT
        assert decision.action == QualityAction.REJECT
        assert decision.sources_count == 0
        assert decision.clarify_message is not None

    def test_boundary_warn_threshold(self):
        """경계값 1.4 정확히 → OK (<=1.4)"""
        sources = [MockSource("d1", "T1", "S1", score=1.4)]
        decision = evaluate_sources_quality(
            sources,
            warn_threshold=1.4,
            reject_threshold=1.6,
        )

        assert decision.grade == QualityGrade.OK
        assert decision.action == QualityAction.PROCEED

    def test_boundary_reject_threshold(self):
        """경계값 1.6 정확히 → LOW (<=1.6)"""
        sources = [MockSource("d1", "T1", "S1", score=1.6)]
        decision = evaluate_sources_quality(
            sources,
            warn_threshold=1.4,
            reject_threshold=1.6,
        )

        assert decision.grade == QualityGrade.LOW
        assert decision.action == QualityAction.PROCEED_WITH_WARNING

    def test_just_above_reject_threshold(self):
        """경계값 1.601 → INSUFFICIENT"""
        sources = [MockSource("d1", "T1", "S1", score=1.601)]
        decision = evaluate_sources_quality(
            sources,
            warn_threshold=1.4,
            reject_threshold=1.6,
        )

        assert decision.grade == QualityGrade.INSUFFICIENT
        assert decision.action == QualityAction.REJECT

    def test_no_distance_info_defaults_to_ok(self):
        """거리 정보 없으면 OK (레거시 호환)"""
        sources = [
            MockSource("d1", "T1", "S1"),
            MockSource("d2", "T2", "S2"),
        ]
        decision = evaluate_sources_quality(
            sources,
            warn_threshold=1.4,
            reject_threshold=1.6,
        )

        assert decision.grade == QualityGrade.OK
        assert decision.action == QualityAction.PROCEED

    def test_custom_thresholds(self):
        """커스텀 임계값 적용"""
        sources = [MockSource("d1", "T1", "S1", score=1.3)]

        # 기본값으로는 OK
        decision1 = evaluate_sources_quality(
            sources,
            warn_threshold=1.4,
            reject_threshold=1.6,
        )
        assert decision1.grade == QualityGrade.OK

        # 임계값 낮추면 LOW
        decision2 = evaluate_sources_quality(
            sources,
            warn_threshold=1.2,
            reject_threshold=1.4,
        )
        assert decision2.grade == QualityGrade.LOW

        # 더 낮추면 INSUFFICIENT
        decision3 = evaluate_sources_quality(
            sources,
            warn_threshold=1.0,
            reject_threshold=1.2,
        )
        assert decision3.grade == QualityGrade.INSUFFICIENT


# =============================================================================
# build_clarification_suggestions 테스트
# =============================================================================

class TestBuildClarificationSuggestions:
    """명확화 제안 생성 테스트"""

    def test_hr_vacation_suggestions(self):
        """연차/휴가 관련 제안"""
        suggestions = build_clarification_suggestions("연차 관련 규정", "POLICY")

        assert len(suggestions) >= 2
        assert len(suggestions) <= 4
        # 연차 관련 키워드가 제안에 포함
        combined = " ".join(suggestions)
        assert "연차" in combined or "휴가" in combined or "발생" in combined

    def test_security_password_suggestions(self):
        """비밀번호 관련 제안"""
        suggestions = build_clarification_suggestions("비밀번호 변경", "SECURITY")

        assert len(suggestions) >= 2
        combined = " ".join(suggestions)
        assert "비밀번호" in combined or "변경" in combined or "규칙" in combined

    def test_generic_fallback(self):
        """알 수 없는 쿼리 → 범용 제안"""
        suggestions = build_clarification_suggestions("xyz 관련", "UNKNOWN")

        assert len(suggestions) >= 2
        combined = " ".join(suggestions)
        assert "절차" in combined or "조건" in combined or "신청" in combined

    def test_max_4_suggestions(self):
        """최대 4개 제안"""
        suggestions = build_clarification_suggestions("연차", "POLICY")
        assert len(suggestions) <= 4


# =============================================================================
# build_clarification_response 테스트
# =============================================================================

class TestBuildClarificationResponse:
    """명확화 응답 생성 테스트"""

    def test_response_contains_message_and_suggestions(self):
        """응답에 메시지와 제안 포함"""
        decision = QualityGateDecision(
            grade=QualityGrade.INSUFFICIENT,
            action=QualityAction.REJECT,
            clarify_message="근거를 찾지 못했습니다.",
            suggestions=["제안 1", "제안 2"],
        )

        response = build_clarification_response(decision, "연차", "POLICY")

        assert "근거" in response
        assert "1." in response  # 번호 매기기
        assert "연차" in response or "휴가" in response  # 도메인 맞춤 제안

    def test_response_format(self):
        """응답 형식 확인"""
        decision = QualityGateDecision(
            grade=QualityGrade.INSUFFICIENT,
            action=QualityAction.REJECT,
            clarify_message="테스트 메시지",
        )

        response = build_clarification_response(decision, "테스트", "POLICY")

        # 줄바꿈으로 구분
        lines = response.split("\n")
        assert len(lines) >= 3  # 메시지 + 빈줄 + 제안들


# =============================================================================
# QualityGateDecision 데이터클래스 테스트
# =============================================================================

class TestQualityGateDecision:
    """QualityGateDecision 데이터클래스 테스트"""

    def test_default_values(self):
        """기본값 확인"""
        decision = QualityGateDecision(
            grade=QualityGrade.OK,
            action=QualityAction.PROCEED,
        )

        assert decision.min_l2_distance == 0.0
        assert decision.avg_l2_distance == 0.0
        assert decision.sources_count == 0
        assert decision.warning_message is None
        assert decision.clarify_message is None
        assert decision.suggestions == []
        assert decision.warn_threshold == 1.4
        assert decision.reject_threshold == 1.6

    def test_all_fields_settable(self):
        """모든 필드 설정 가능"""
        decision = QualityGateDecision(
            grade=QualityGrade.LOW,
            action=QualityAction.PROCEED_WITH_WARNING,
            min_l2_distance=1.5,
            avg_l2_distance=1.55,
            sources_count=3,
            warning_message="경고 메시지",
            clarify_message=None,
            suggestions=["제안"],
            warn_threshold=1.3,
            reject_threshold=1.5,
        )

        assert decision.grade == QualityGrade.LOW
        assert decision.action == QualityAction.PROCEED_WITH_WARNING
        assert decision.min_l2_distance == 1.5
        assert decision.warning_message == "경고 메시지"
        assert len(decision.suggestions) == 1


# =============================================================================
# Enum 테스트
# =============================================================================

class TestEnums:
    """Enum 값 테스트"""

    def test_quality_grade_values(self):
        """QualityGrade 값"""
        assert QualityGrade.OK.value == "OK"
        assert QualityGrade.LOW.value == "LOW"
        assert QualityGrade.INSUFFICIENT.value == "INSUFFICIENT"

    def test_quality_action_values(self):
        """QualityAction 값"""
        assert QualityAction.PROCEED.value == "PROCEED"
        assert QualityAction.PROCEED_WITH_WARNING.value == "PROCEED_WITH_WARNING"
        assert QualityAction.REJECT.value == "REJECT"

    def test_grade_is_string_enum(self):
        """str enum이므로 문자열 비교 가능"""
        assert QualityGrade.OK == "OK"
        assert QualityGrade.INSUFFICIENT == "INSUFFICIENT"
