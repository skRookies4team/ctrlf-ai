"""
Query Expansion YAML 규칙 테스트

Phase 58: config/query_expansion_rules.yaml 테스트
"""

import pytest
from pathlib import Path


class TestExpansionRulesYAML:
    """YAML 파일 구조 검증"""

    def test_yaml_file_exists(self):
        """YAML 파일 존재 확인"""
        yaml_path = Path(__file__).parent.parent.parent / "config" / "query_expansion_rules.yaml"
        assert yaml_path.exists(), f"YAML file not found: {yaml_path}"

    def test_yaml_valid_structure(self):
        """YAML 파싱 및 구조 검증"""
        import yaml
        yaml_path = Path(__file__).parent.parent.parent / "config" / "query_expansion_rules.yaml"

        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        assert "version" in data, "Missing 'version' field"
        assert "rules" in data, "Missing 'rules' field"
        assert "settings" in data, "Missing 'settings' field"

    def test_minimum_20_keywords(self):
        """최소 20개 키워드 확인"""
        import yaml
        yaml_path = Path(__file__).parent.parent.parent / "config" / "query_expansion_rules.yaml"

        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        rules = data.get("rules", {})
        assert len(rules) >= 20, f"Expected at least 20 keywords, got {len(rules)}"

    def test_rule_structure(self):
        """각 규칙의 구조 검증"""
        import yaml
        yaml_path = Path(__file__).parent.parent.parent / "config" / "query_expansion_rules.yaml"

        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        rules = data.get("rules", {})

        for keyword, rule in rules.items():
            assert "domain" in rule, f"Rule '{keyword}' missing 'domain'"
            assert "synonyms" in rule or "related" in rule, \
                f"Rule '{keyword}' must have 'synonyms' or 'related'"

    def test_category_coverage(self):
        """카테고리 커버리지 확인"""
        import yaml
        yaml_path = Path(__file__).parent.parent.parent / "config" / "query_expansion_rules.yaml"

        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        rules = data.get("rules", {})
        categories = set(rule.get("category") for rule in rules.values() if rule.get("category"))

        required = {"HR", "SECURITY", "EDU", "HARASSMENT", "DISCIPLINE"}
        missing = required - categories
        assert not missing, f"Missing categories: {missing}"


class TestExpansionRulesLoader:
    """YAML 로더 테스트"""

    def test_loader_loads_rules(self):
        """로더가 규칙을 로드하는지 확인"""
        from app.services.chat.query_rewriter import get_expansion_rules

        rules = get_expansion_rules()
        assert isinstance(rules, dict)
        assert len(rules) >= 20

    def test_loader_caches_rules(self):
        """로더가 규칙을 캐시하는지 확인"""
        from app.services.chat.query_rewriter import _rules_loader

        # 첫 번째 로드
        rules1 = _rules_loader.get_rules()
        # 두 번째 호출 (캐시에서)
        rules2 = _rules_loader.get_rules()

        assert rules1 is rules2, "Rules should be cached (same object)"

    def test_reload_refreshes_rules(self):
        """reload가 규칙을 새로 로드하는지 확인"""
        from app.services.chat.query_rewriter import reload_expansion_rules, get_expansion_rules

        rules_before = get_expansion_rules()
        rules_after = reload_expansion_rules()

        # 내용은 같아야 함
        assert len(rules_before) == len(rules_after)


class TestExpansionWithYAML:
    """YAML 기반 확장 테스트"""

    @pytest.mark.parametrize("query,expected_keyword", [
        ("연차", "연차"),
        ("휴가 신청", "휴가"),
        ("비밀번호 변경", "비밀번호"),
        ("보안사고 신고", "보안사고"),
        ("교육 이수", "교육"),
        ("성희롱 예방", "성희롱"),
        ("징계 처분", "징계"),
    ])
    def test_yaml_rule_matching(self, query, expected_keyword):
        """YAML 규칙 매칭 테스트"""
        from app.services.chat.query_rewriter import expand_query_sync

        result = expand_query_sync(query, "POLICY")

        assert result.used, f"Expected expansion for '{query}'"
        assert result.matched_keyword == expected_keyword, \
            f"Expected keyword '{expected_keyword}', got '{result.matched_keyword}'"

    def test_expansion_includes_synonyms(self):
        """확장에 동의어 포함 확인"""
        from app.services.chat.query_rewriter import expand_query_sync

        result = expand_query_sync("연차", "POLICY")

        assert result.used
        # 동의어가 포함되어야 함
        assert "연차휴가" in result.rewritten or "연간휴가" in result.rewritten

    def test_expansion_includes_related(self):
        """확장에 관련어 포함 확인"""
        from app.services.chat.query_rewriter import expand_query_sync

        result = expand_query_sync("비밀번호", "POLICY")

        assert result.used
        # 관련어가 포함되어야 함 (변경, 규칙 등)
        assert "변경" in result.rewritten or "규칙" in result.rewritten

    def test_no_expansion_for_unknown_keyword(self):
        """미등록 키워드는 확장 안 함"""
        from app.services.chat.query_rewriter import expand_query_sync

        result = expand_query_sync("커피", "POLICY")

        # 20개 핵심 키워드에 없는 "커피"는 매칭 안 됨
        assert not result.used
        assert result.reason == "no_matching_rule"

    def test_expansion_preserves_original_query(self):
        """확장 시 원본 쿼리 보존"""
        from app.services.chat.query_rewriter import expand_query_sync

        query = "휴가 신청 방법"
        result = expand_query_sync(query, "POLICY")

        if result.used:
            assert query in result.rewritten, "Original query should be preserved"

    def test_reason_is_yaml_rule(self):
        """이유가 yaml_rule_expansion인지 확인"""
        from app.services.chat.query_rewriter import expand_query_sync

        result = expand_query_sync("급여", "POLICY")

        assert result.used
        assert result.reason == "yaml_rule_expansion"


class TestExpansionSettings:
    """확장 설정 테스트"""

    def test_max_query_length_setting(self):
        """긴 쿼리는 확장 안 함 (40자 초과)"""
        from app.services.chat.query_rewriter import expand_query_sync

        # 40자 초과하는 긴 쿼리 (실제로 50자 이상)
        long_query = "이것은 정말로 매우매우 긴 쿼리입니다 연차휴가를 신청하고 싶은데 도대체 어떻게 해야 하나요 알려주세요"
        assert len(long_query) > 40, f"Query should be > 40 chars, got {len(long_query)}"

        result = expand_query_sync(long_query, "POLICY")

        assert not result.used
        assert result.reason == "too_long"

    def test_empty_query(self):
        """빈 쿼리는 확장 안 함"""
        from app.services.chat.query_rewriter import expand_query_sync

        result = expand_query_sync("", "POLICY")

        assert not result.used
        assert result.reason == "empty_query"

    def test_masking_tokens_prevent_expansion(self):
        """마스킹 토큰 많으면 확장 안 함"""
        from app.services.chat.query_rewriter import expand_query_sync

        query = "[PERSON]님의 [PHONE]으로 연락"
        result = expand_query_sync(query, "POLICY")

        assert not result.used
        assert result.reason == "too_many_masking_tokens"
