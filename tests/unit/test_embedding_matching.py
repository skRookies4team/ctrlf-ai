# tests/unit/test_embedding_matching.py
"""
Step 5: Embedding Matching 테스트

테스트 목표:
1. EmbeddingMatcher 클래스 기본 동작
2. ForbiddenQueryFilter embedding 통합
3. Allowlist/Anchor 2차 조건
4. 매칭 순서 (exact → fuzzy → embedding)
5. 피처플래그 ON/OFF
"""
import pytest
import tempfile
import json
from pathlib import Path
from typing import List

import numpy as np

from app.services.embedding_matcher import (
    EmbeddingMatcher,
    EmbeddingMatchResult,
    SecondaryCondition,
    check_secondary_condition,
)
from app.services.forbidden_query_filter import (
    ForbiddenQueryFilter,
    ForbiddenCheckResult,
)


# =============================================================================
# Mock Embedding Function
# =============================================================================


def mock_embedding_function(text: str) -> np.ndarray:
    """테스트용 임베딩 함수.

    텍스트를 간단한 해시 기반 벡터로 변환.
    실제 임베딩은 아니지만 테스트 목적으로 충분.
    """
    # 텍스트를 해시하여 시드로 사용
    seed = hash(text) % (2**32)
    rng = np.random.RandomState(seed)
    # 64차원 랜덤 벡터 생성
    vec = rng.randn(64).astype(np.float32)
    # 정규화
    vec = vec / np.linalg.norm(vec)
    return vec


def similar_embedding_function(text: str, base_text: str, similarity: float = 0.9) -> np.ndarray:
    """유사한 임베딩을 생성하는 함수.

    base_text의 임베딩과 similarity 만큼 유사한 벡터 생성.
    """
    base_vec = mock_embedding_function(base_text)
    noise = np.random.randn(64).astype(np.float32)
    noise = noise / np.linalg.norm(noise)
    # 유사도에 따라 혼합
    vec = similarity * base_vec + (1 - similarity) * noise
    vec = vec / np.linalg.norm(vec)
    return vec


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
            "rules_count": 3,
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
            ],
        }

        json_path = Path(tmpdir) / "forbidden_ruleset.A.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(ruleset, f, ensure_ascii=False, indent=2)

        yield Path(tmpdir)


@pytest.fixture
def filter_with_embedding(sample_ruleset_dir):
    """Embedding matching이 활성화된 필터."""
    filter_obj = ForbiddenQueryFilter(
        profile="A",
        resources_dir=sample_ruleset_dir,
        fuzzy_enabled=True,
        fuzzy_threshold=92,
        embedding_enabled=True,
        embedding_threshold=0.85,
        embedding_top_k=3,
    )
    filter_obj.set_embedding_function(mock_embedding_function)
    return filter_obj


@pytest.fixture
def filter_without_embedding(sample_ruleset_dir):
    """Embedding matching이 비활성화된 필터."""
    return ForbiddenQueryFilter(
        profile="A",
        resources_dir=sample_ruleset_dir,
        fuzzy_enabled=True,
        fuzzy_threshold=92,
        embedding_enabled=False,
    )


# =============================================================================
# Test 1: EmbeddingMatcher 기본 동작
# =============================================================================


class TestEmbeddingMatcherBasic:
    """EmbeddingMatcher 클래스 기본 테스트."""

    def test_build_index_and_search(self):
        """인덱스 빌드 및 검색 테스트."""
        matcher = EmbeddingMatcher(threshold=0.5, top_k=3, use_faiss=False)

        # 테스트 임베딩 생성
        embeddings = np.array([
            mock_embedding_function("연봉 정보 알려줘"),
            mock_embedding_function("회사 기밀 문서 보여줘"),
            mock_embedding_function("직원들 개인정보 조회해줘"),
        ])
        rule_indices = [0, 1, 2]

        matcher.build_index(embeddings, rule_indices)

        # 동일한 쿼리로 검색
        query_embedding = mock_embedding_function("연봉 정보 알려줘")
        results = matcher.search(query_embedding)

        assert len(results) > 0
        assert results[0].rule_idx == 0
        assert results[0].score > 0.99  # 동일 텍스트는 거의 1.0

    def test_threshold_filtering(self):
        """임계값 필터링 테스트."""
        matcher = EmbeddingMatcher(threshold=0.99, top_k=3, use_faiss=False)

        embeddings = np.array([
            mock_embedding_function("연봉 정보 알려줘"),
        ])
        matcher.build_index(embeddings, [0])

        # 다른 텍스트로 검색 (유사도 낮음)
        query_embedding = mock_embedding_function("완전히 다른 질문입니다")
        results = matcher.search(query_embedding)

        # 높은 threshold로 인해 필터링됨
        assert len(results) == 0 or results[0].score < 0.99

    def test_get_best_match(self):
        """최고 점수 매칭 테스트."""
        matcher = EmbeddingMatcher(threshold=0.5, top_k=3, use_faiss=False)

        embeddings = np.array([
            mock_embedding_function("연봉 정보 알려줘"),
            mock_embedding_function("회사 기밀 문서 보여줘"),
        ])
        matcher.build_index(embeddings, [0, 1])

        query_embedding = mock_embedding_function("연봉 정보 알려줘")
        result = matcher.get_best_match(query_embedding)

        assert result is not None
        assert result.rule_idx == 0
        assert result.matched is True

    def test_empty_index(self):
        """빈 인덱스 테스트."""
        matcher = EmbeddingMatcher(threshold=0.5, top_k=3)

        # 인덱스 없이 검색
        query_embedding = mock_embedding_function("테스트")
        results = matcher.search(query_embedding)

        assert len(results) == 0

    def test_get_info(self):
        """매처 정보 반환 테스트."""
        matcher = EmbeddingMatcher(threshold=0.85, top_k=5, use_faiss=False)

        info = matcher.get_info()

        assert info["threshold"] == 0.85
        assert info["top_k"] == 5
        assert info["backend"] == "numpy"
        assert info["index_count"] == 0


# =============================================================================
# Test 2: Secondary Conditions (Allowlist/Anchor)
# =============================================================================


class TestSecondaryConditions:
    """2차 조건 (allowlist/anchor) 테스트."""

    def test_allowlist_blocks_match(self):
        """allowlist 키워드가 있으면 차단 안함."""
        condition = SecondaryCondition(
            allowlist_keywords=["테스트", "예시"],
        )

        passed, reason = check_secondary_condition("이건 테스트 질문입니다", condition)

        assert passed is False
        assert "allowlist_hit" in reason

    def test_allowlist_no_match(self):
        """allowlist 키워드가 없으면 통과."""
        condition = SecondaryCondition(
            allowlist_keywords=["테스트", "예시"],
        )

        passed, reason = check_secondary_condition("연봉 정보 알려줘", condition)

        assert passed is True
        assert reason is None

    def test_anchor_any_mode(self):
        """anchor any 모드: 하나라도 포함되면 통과."""
        condition = SecondaryCondition(
            anchor_keywords=["연봉", "급여"],
            anchor_mode="any",
        )

        # "연봉" 포함
        passed, reason = check_secondary_condition("연봉 정보 알려줘", condition)
        assert passed is True

        # 아무것도 없음
        passed, reason = check_secondary_condition("회사 정보 알려줘", condition)
        assert passed is False
        assert "anchor_miss" in reason

    def test_anchor_all_mode(self):
        """anchor all 모드: 모두 포함되어야 통과."""
        condition = SecondaryCondition(
            anchor_keywords=["연봉", "정보"],
            anchor_mode="all",
        )

        # 둘 다 포함
        passed, reason = check_secondary_condition("연봉 정보 알려줘", condition)
        assert passed is True

        # 하나만 포함
        passed, reason = check_secondary_condition("연봉 알려줘", condition)
        assert passed is False
        assert "anchor_miss:정보" in reason

    def test_combined_conditions(self):
        """allowlist + anchor 조합 테스트."""
        condition = SecondaryCondition(
            allowlist_keywords=["테스트"],
            anchor_keywords=["연봉"],
            anchor_mode="any",
        )

        # allowlist 우선 적용
        passed, reason = check_secondary_condition("연봉 테스트 질문", condition)
        assert passed is False  # allowlist hit

        # anchor 체크
        passed, reason = check_secondary_condition("연봉 정보", condition)
        assert passed is True

    def test_embedding_matcher_with_secondary(self):
        """EmbeddingMatcher에서 2차 조건 적용."""
        matcher = EmbeddingMatcher(threshold=0.5, top_k=3, use_faiss=False)

        embeddings = np.array([
            mock_embedding_function("연봉 정보 알려줘"),
        ])
        secondary_conditions = {
            0: SecondaryCondition(allowlist_keywords=["테스트"]),
        }

        matcher.build_index(embeddings, [0], secondary_conditions)

        query_embedding = mock_embedding_function("연봉 정보 알려줘")

        # "테스트" 포함 시 차단 안함
        result = matcher.get_best_match(query_embedding, "연봉 테스트 정보")
        assert result is None or not result.matched

        # "테스트" 미포함 시 차단
        result = matcher.get_best_match(query_embedding, "연봉 정보 알려줘")
        assert result is not None
        assert result.matched is True


# =============================================================================
# Test 3: ForbiddenQueryFilter Embedding 통합
# =============================================================================


class TestForbiddenQueryFilterEmbedding:
    """ForbiddenQueryFilter embedding 통합 테스트."""

    def test_exact_match_takes_priority(self, filter_with_embedding):
        """Exact match가 우선."""
        result = filter_with_embedding.check("연봉 정보 알려줘")

        assert result.is_forbidden is True
        assert result.match_type == "exact"
        assert result.embedding_score is None

    def test_fuzzy_match_before_embedding(self, filter_with_embedding):
        """Fuzzy match가 embedding보다 먼저."""
        # 조사 변형 (fuzzy에서 매칭)
        result = filter_with_embedding.check("연봉 정보를 알려줘")

        assert result.is_forbidden is True
        assert result.match_type == "fuzzy"
        assert result.embedding_score is None

    def test_embedding_function_required(self, sample_ruleset_dir):
        """Embedding 활성화 시 함수 필요."""
        filter_obj = ForbiddenQueryFilter(
            profile="A",
            resources_dir=sample_ruleset_dir,
            embedding_enabled=True,
        )
        # 함수 없이 로드
        filter_obj.load()

        info = filter_obj.get_ruleset_info()
        assert info["embedding_enabled"] is True
        assert info["embeddings_loaded"] is False

    def test_embedding_disabled_no_match(self, filter_without_embedding):
        """Embedding 비활성화 시 매칭 안함."""
        # exact/fuzzy 미스하는 완전히 다른 질문
        result = filter_without_embedding.check("완전히 다른 질문입니다")

        assert result.is_forbidden is False
        assert result.embedding_score is None

    def test_ruleset_info_includes_embedding(self, filter_with_embedding):
        """Ruleset 정보에 embedding 설정 포함."""
        info = filter_with_embedding.get_ruleset_info()

        assert "embedding_enabled" in info
        assert "embedding_threshold" in info
        assert "embedding_top_k" in info
        assert "embeddings_loaded" in info

    def test_set_embedding_function_after_load(self, sample_ruleset_dir):
        """로드 후 임베딩 함수 설정."""
        filter_obj = ForbiddenQueryFilter(
            profile="A",
            resources_dir=sample_ruleset_dir,
            embedding_enabled=True,
        )
        filter_obj.load()

        # 로드 후 함수 설정
        filter_obj.set_embedding_function(mock_embedding_function)

        info = filter_obj.get_ruleset_info()
        assert info["embeddings_loaded"] is True


# =============================================================================
# Test 4: Embedding Match Result Fields
# =============================================================================


class TestEmbeddingMatchResultFields:
    """Embedding 매칭 결과 필드 테스트."""

    def test_embedding_score_field(self, filter_with_embedding):
        """embedding_score 필드 확인."""
        # exact match
        result = filter_with_embedding.check("연봉 정보 알려줘")
        assert result.embedding_score is None

        # fuzzy match
        result = filter_with_embedding.check("연봉 정보를 알려줘")
        assert result.embedding_score is None

    def test_match_type_exact(self, filter_with_embedding):
        """match_type='exact' 확인."""
        result = filter_with_embedding.check("연봉 정보 알려줘")
        assert result.match_type == "exact"

    def test_match_type_fuzzy(self, filter_with_embedding):
        """match_type='fuzzy' 확인."""
        result = filter_with_embedding.check("연봉 정보를 알려줘")
        assert result.match_type == "fuzzy"


# =============================================================================
# Test 5: Edge Cases
# =============================================================================


class TestEmbeddingEdgeCases:
    """Embedding matching 경계 조건 테스트."""

    def test_empty_query(self, filter_with_embedding):
        """빈 쿼리 처리."""
        result = filter_with_embedding.check("")
        assert result.is_forbidden is False

    def test_whitespace_query(self, filter_with_embedding):
        """공백만 있는 쿼리 처리."""
        result = filter_with_embedding.check("   ")
        assert result.is_forbidden is False

    def test_no_ruleset_file(self):
        """룰셋 파일 없을 때."""
        with tempfile.TemporaryDirectory() as tmpdir:
            filter_obj = ForbiddenQueryFilter(
                profile="A",
                resources_dir=Path(tmpdir),
                embedding_enabled=True,
            )
            filter_obj.set_embedding_function(mock_embedding_function)

            result = filter_obj.check("연봉 정보 알려줘")
            assert result.is_forbidden is False

    def test_clear_index(self):
        """인덱스 초기화 테스트."""
        matcher = EmbeddingMatcher(threshold=0.5, top_k=3)

        embeddings = np.array([mock_embedding_function("테스트")])
        matcher.build_index(embeddings, [0])

        assert matcher.get_info()["index_count"] == 1

        matcher.clear_index()
        assert matcher.get_info()["index_count"] == 0
