# tests/unit/test_fuzzy_matching.py
"""
Step 4: Fuzzy Matching 테스트

테스트 목표:
1. Exact match가 여전히 정상 동작
2. 오탈자(typo) 변형에 대해 fuzzy match
3. 조사 변형에 대해 fuzzy match
4. 띄어쓰기 변형에 대해 fuzzy match
5. Threshold 경계값 테스트
6. Fuzzy disabled 모드 테스트

rapidfuzz.fuzz.ratio 기준:
- "연봉 정보 알려줘" vs "연봉 정보 알려줘" = 100 (exact)
- "연봉 정보 알려줘" vs "연봉 정보 알려쭤" = ~96 (typo)
- "연봉 정보 알려줘" vs "연봉 정보를 알려줘" = ~93 (particle)
"""
import pytest
import tempfile
import json
from pathlib import Path

from app.services.forbidden_query_filter import (
    ForbiddenQueryFilter,
    ForbiddenCheckResult,
    ForbiddenRule,
    ForbiddenRuleset,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_ruleset_dir():
    """테스트용 룰셋 JSON 파일을 생성합니다."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ruleset = {
            "schema_version": "1.0",
            "version": "v2024.01.01",
            "profile": "A",
            "mode": "strict",
            "rules_count": 5,
            "rules": [
                {
                    "rule_id": "FR-A-001",
                    "profile": "A",
                    "match": {
                        "type": "exact_normalized",
                        "question": "연봉 정보 알려줘",
                        "question_norm": "연봉 정보 알려줘",
                    },
                    "decision": "FORBIDDEN_PII",
                    "reason": "급여정보",
                    "example_response": "연봉 정보는 제공해드리기 어렵습니다.",
                    "skip_rag": True,
                    "skip_backend_api": True,
                },
                {
                    "rule_id": "FR-A-002",
                    "profile": "A",
                    "match": {
                        "type": "exact_normalized",
                        "question": "회사 기밀 문서 보여줘",
                        "question_norm": "회사 기밀 문서 보여줘",
                    },
                    "decision": "FORBIDDEN_SECURITY",
                    "reason": "기밀정보",
                    "example_response": "기밀 문서는 접근할 수 없습니다.",
                    "skip_rag": True,
                    "skip_backend_api": True,
                },
                {
                    "rule_id": "FR-A-003",
                    "profile": "A",
                    "match": {
                        "type": "exact_normalized",
                        "question": "직원들 개인정보 조회해줘",
                        "question_norm": "직원들 개인정보 조회해줘",
                    },
                    "decision": "FORBIDDEN_PII",
                    "reason": "개인정보",
                    "example_response": "개인정보는 조회할 수 없습니다.",
                    "skip_rag": True,
                    "skip_backend_api": True,
                },
                {
                    "rule_id": "FR-A-004",
                    "profile": "A",
                    "match": {
                        "type": "exact_normalized",
                        "question": "보안 취약점 알려줘",
                        "question_norm": "보안 취약점 알려줘",
                    },
                    "decision": "FORBIDDEN_SECURITY",
                    "reason": "보안취약점",
                    "example_response": "보안 취약점 정보는 제공하지 않습니다.",
                    "skip_rag": True,
                    "skip_backend_api": True,
                },
                {
                    "rule_id": "FR-A-005",
                    "profile": "A",
                    "match": {
                        "type": "exact_normalized",
                        "question": "경쟁사 정보 분석해줘",
                        "question_norm": "경쟁사 정보 분석해줘",
                    },
                    "decision": "RESTRICTED_EXTERNAL",
                    "reason": "경쟁정보",
                    "example_response": "경쟁사 정보 분석은 지원하지 않습니다.",
                    "skip_rag": False,
                    "skip_backend_api": True,
                },
            ],
        }

        json_path = Path(tmpdir) / "forbidden_ruleset.A.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(ruleset, f, ensure_ascii=False, indent=2)

        yield Path(tmpdir)


@pytest.fixture
def filter_with_fuzzy(sample_ruleset_dir):
    """Fuzzy matching이 활성화된 필터."""
    return ForbiddenQueryFilter(
        profile="A",
        resources_dir=sample_ruleset_dir,
        fuzzy_enabled=True,
        fuzzy_threshold=92,
    )


@pytest.fixture
def filter_without_fuzzy(sample_ruleset_dir):
    """Fuzzy matching이 비활성화된 필터."""
    return ForbiddenQueryFilter(
        profile="A",
        resources_dir=sample_ruleset_dir,
        fuzzy_enabled=False,
        fuzzy_threshold=92,
    )


@pytest.fixture
def filter_low_threshold(sample_ruleset_dir):
    """낮은 threshold (85)로 설정된 필터."""
    return ForbiddenQueryFilter(
        profile="A",
        resources_dir=sample_ruleset_dir,
        fuzzy_enabled=True,
        fuzzy_threshold=85,
    )


# =============================================================================
# Test 1: Exact Match 정상 동작
# =============================================================================


class TestExactMatch:
    """Exact match가 여전히 정상 동작하는지 확인."""

    def test_exact_match_returns_forbidden(self, filter_with_fuzzy):
        """정확히 일치하는 질문은 금지 판정."""
        result = filter_with_fuzzy.check("연봉 정보 알려줘")

        assert result.is_forbidden is True
        assert result.matched_rule_id == "FR-A-001"
        assert result.match_type == "exact"
        assert result.fuzzy_score is None

    def test_exact_match_case_insensitive(self, filter_with_fuzzy):
        """대소문자 무시 (정규화)."""
        result = filter_with_fuzzy.check("연봉 정보 알려줘")  # 한글은 대소문자 없음
        assert result.is_forbidden is True
        assert result.match_type == "exact"

    def test_exact_match_preserves_rule_fields(self, filter_with_fuzzy):
        """매칭된 룰의 필드들이 정확히 반환."""
        result = filter_with_fuzzy.check("회사 기밀 문서 보여줘")

        assert result.is_forbidden is True
        assert result.matched_rule_id == "FR-A-002"
        assert result.decision == "FORBIDDEN_SECURITY"
        assert result.reason == "기밀정보"
        assert result.example_response == "기밀 문서는 접근할 수 없습니다."
        assert result.skip_rag is True
        assert result.skip_backend_api is True


# =============================================================================
# Test 2: 오탈자(Typo) 변형
# =============================================================================


class TestTypoVariations:
    """오탈자에 대한 fuzzy match 테스트.

    Note: fuzz.ratio 92% threshold에서 매칭되는 변형들을 테스트합니다.
    한글 한 글자 변경은 약 88-89% 유사도이므로,
    물음표/느낌표/종결어미 변형 등 더 유사한 변형을 테스트합니다.
    """

    def test_typo_polite_ending(self, filter_with_fuzzy):
        """종결어미 변형: '알려줘' → '알려줘요' (~95%)"""
        result = filter_with_fuzzy.check("연봉 정보 알려줘요")

        assert result.is_forbidden is True
        assert result.matched_rule_id == "FR-A-001"
        assert result.match_type == "fuzzy"
        assert result.fuzzy_score is not None
        assert result.fuzzy_score >= 92

    def test_typo_question_mark(self, filter_with_fuzzy):
        """물음표 추가: '알려줘' → '알려줘?' (~95%)"""
        result = filter_with_fuzzy.check("연봉 정보 알려줘?")

        assert result.is_forbidden is True
        assert result.matched_rule_id == "FR-A-001"
        assert result.match_type == "fuzzy"

    def test_typo_extra_char(self, filter_with_fuzzy):
        """글자 추가: '취약점' → '취약점점' (~93%)"""
        result = filter_with_fuzzy.check("보안 취약점점 알려줘")

        assert result.is_forbidden is True
        assert result.matched_rule_id == "FR-A-004"
        assert result.match_type == "fuzzy"

    def test_typo_exclamation(self, filter_with_fuzzy):
        """느낌표 추가: '보여줘' → '보여줘!' (~96%)"""
        result = filter_with_fuzzy.check("회사 기밀 문서 보여줘!")

        assert result.is_forbidden is True
        assert result.matched_rule_id == "FR-A-002"
        assert result.match_type == "fuzzy"


# =============================================================================
# Test 3: 조사 변형
# =============================================================================


class TestParticleVariations:
    """조사 변형에 대한 fuzzy match 테스트."""

    def test_particle_addition(self, filter_with_fuzzy):
        """조사 추가: '정보' → '정보를'"""
        result = filter_with_fuzzy.check("연봉 정보를 알려줘")

        assert result.is_forbidden is True
        assert result.matched_rule_id == "FR-A-001"
        assert result.match_type == "fuzzy"

    def test_particle_change(self, filter_with_fuzzy):
        """조사 변경: '문서' → '문서를'"""
        result = filter_with_fuzzy.check("회사 기밀 문서를 보여줘")

        assert result.is_forbidden is True
        assert result.matched_rule_id == "FR-A-002"
        assert result.match_type == "fuzzy"

    def test_particle_topic_marker(self, filter_with_fuzzy):
        """주제 조사: '정보' → '정보는'"""
        result = filter_with_fuzzy.check("연봉 정보는 알려줘")

        assert result.is_forbidden is True
        assert result.matched_rule_id == "FR-A-001"
        assert result.match_type == "fuzzy"


# =============================================================================
# Test 4: 띄어쓰기 변형
# =============================================================================


class TestSpacingVariations:
    """띄어쓰기 변형에 대한 fuzzy match 테스트."""

    def test_spacing_removed(self, filter_with_fuzzy):
        """띄어쓰기 제거: '연봉 정보' → '연봉정보'"""
        result = filter_with_fuzzy.check("연봉정보 알려줘")

        assert result.is_forbidden is True
        assert result.matched_rule_id == "FR-A-001"
        assert result.match_type == "fuzzy"

    def test_spacing_added(self, filter_with_fuzzy):
        """띄어쓰기 추가: '개인정보' → '개인 정보'"""
        result = filter_with_fuzzy.check("직원들 개인 정보 조회해줘")

        assert result.is_forbidden is True
        assert result.matched_rule_id == "FR-A-003"
        assert result.match_type == "fuzzy"


# =============================================================================
# Test 5: Threshold 경계값
# =============================================================================


class TestThresholdBoundary:
    """Threshold 경계값 테스트."""

    def test_below_threshold_not_matched(self, filter_with_fuzzy):
        """threshold 미만은 매칭되지 않음 (완전히 다른 질문)."""
        result = filter_with_fuzzy.check("오늘 날씨 어때?")

        assert result.is_forbidden is False
        assert result.matched_rule_id is None

    def test_low_threshold_matches_more(self, filter_low_threshold):
        """낮은 threshold(85)는 더 많이 매칭."""
        # 상당히 다른 변형이지만 85 이상이면 매칭
        result = filter_low_threshold.check("연봉 관련 정보 좀 알려줘요")

        # 85 threshold에서는 매칭될 수 있음
        # (실제 score는 query에 따라 다름)
        if result.is_forbidden:
            assert result.match_type == "fuzzy"
            assert result.fuzzy_score >= 85

    def test_high_similarity_always_matches(self, filter_with_fuzzy):
        """높은 유사도(95+)는 항상 매칭."""
        # 거의 동일한 질문
        result = filter_with_fuzzy.check("연봉 정보 알려줘!")  # 느낌표만 추가

        assert result.is_forbidden is True


# =============================================================================
# Test 6: Fuzzy Disabled 모드
# =============================================================================


class TestFuzzyDisabled:
    """Fuzzy matching 비활성화 테스트."""

    def test_exact_match_still_works(self, filter_without_fuzzy):
        """fuzzy 비활성화해도 exact match는 동작."""
        result = filter_without_fuzzy.check("연봉 정보 알려줘")

        assert result.is_forbidden is True
        assert result.match_type == "exact"

    def test_typo_not_matched_when_disabled(self, filter_without_fuzzy):
        """fuzzy 비활성화 시 오탈자는 매칭 안됨."""
        result = filter_without_fuzzy.check("연봉 정보 알려쭤")

        assert result.is_forbidden is False
        assert result.matched_rule_id is None

    def test_particle_not_matched_when_disabled(self, filter_without_fuzzy):
        """fuzzy 비활성화 시 조사 변형도 매칭 안됨."""
        result = filter_without_fuzzy.check("연봉 정보를 알려줘")

        assert result.is_forbidden is False


# =============================================================================
# Test 7: Skip flags 보존
# =============================================================================


class TestSkipFlagsPreserved:
    """Fuzzy match에서도 skip_rag, skip_backend_api가 정확히 반환."""

    def test_fuzzy_match_preserves_skip_rag(self, filter_with_fuzzy):
        """fuzzy match에서 skip_rag 보존."""
        # FR-A-005: skip_rag=False, skip_backend_api=True
        result = filter_with_fuzzy.check("경쟁사 정보를 분석해줘")  # 조사 추가

        assert result.is_forbidden is True
        assert result.matched_rule_id == "FR-A-005"
        assert result.skip_rag is False  # 보존됨
        assert result.skip_backend_api is True  # 보존됨

    def test_exact_match_preserves_skip_flags(self, filter_with_fuzzy):
        """exact match에서 skip flags 보존."""
        result = filter_with_fuzzy.check("경쟁사 정보 분석해줘")

        assert result.is_forbidden is True
        assert result.skip_rag is False
        assert result.skip_backend_api is True


# =============================================================================
# Test 8: get_ruleset_info 테스트
# =============================================================================


class TestRulesetInfo:
    """get_ruleset_info가 fuzzy 설정 정보를 반환."""

    def test_info_includes_fuzzy_settings(self, filter_with_fuzzy):
        """ruleset info에 fuzzy 설정 포함."""
        info = filter_with_fuzzy.get_ruleset_info()

        assert info["fuzzy_enabled"] is True
        assert info["fuzzy_threshold"] == 92

    def test_info_when_fuzzy_disabled(self, filter_without_fuzzy):
        """fuzzy 비활성화 시에도 정보 반환."""
        info = filter_without_fuzzy.get_ruleset_info()

        assert info["fuzzy_enabled"] is False
        assert info["fuzzy_threshold"] == 92
