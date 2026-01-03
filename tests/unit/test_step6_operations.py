# tests/unit/test_step6_operations.py
"""
Step 6: 운영 안정화 테스트

테스트 목표:
1. PII sanitizer 기능
2. Manifest 체크섬 검증
3. EmbeddingMatcher is_local/require_index 검증
4. ChatAnswerMeta forbidden 필드
"""
import pytest
import tempfile
import json
from pathlib import Path

import numpy as np

from app.services.pii_sanitizer import (
    sanitize_for_matching,
    sanitize_with_info,
    has_pii_pattern,
    sanitize_query_for_forbidden_check,
)
from app.services.ruleset_manifest import (
    RulesetManifest,
    FileChecksum,
    compute_sha256,
    load_manifest,
    create_manifest,
    save_manifest,
)
from app.services.embedding_matcher import (
    EmbeddingMatcher,
    EmbeddingProviderError,
)
from app.models.chat import ChatAnswerMeta


# =============================================================================
# Test 1: PII Sanitizer
# =============================================================================


class TestPiiSanitizer:
    """PII 치환 기능 테스트."""

    def test_sanitize_email(self):
        """이메일 주소 치환."""
        text = "내 이메일은 test@example.com 입니다"
        result = sanitize_for_matching(text)
        assert "test@example.com" not in result
        assert "<PII>" in result

    def test_sanitize_phone_korean(self):
        """한국 휴대폰 번호 치환."""
        text = "연락처는 010-1234-5678 입니다"
        result = sanitize_for_matching(text)
        assert "010-1234-5678" not in result
        assert "<PII>" in result

    def test_sanitize_phone_no_dash(self):
        """대시 없는 전화번호 치환."""
        text = "전화번호 01012345678"
        result = sanitize_for_matching(text)
        assert "01012345678" not in result

    def test_sanitize_ssn(self):
        """주민등록번호 치환."""
        text = "주민번호 900101-1234567"
        result = sanitize_for_matching(text)
        assert "900101-1234567" not in result
        assert "<PII>" in result

    def test_sanitize_card_number(self):
        """카드번호 치환."""
        text = "카드번호 1234-5678-9012-3456"
        result = sanitize_for_matching(text)
        assert "1234-5678-9012-3456" not in result
        assert "<PII>" in result

    def test_sanitize_url(self):
        """URL 치환."""
        text = "사이트 주소 https://example.com/path?query=1"
        result = sanitize_for_matching(text)
        assert "https://example.com" not in result
        assert "<PII>" in result

    def test_sanitize_ip_address(self):
        """IP 주소 치환."""
        text = "서버 IP는 192.168.1.100 입니다"
        result = sanitize_for_matching(text)
        assert "192.168.1.100" not in result

    def test_sanitize_multiple_pii(self):
        """여러 PII 동시 치환."""
        text = "이메일 test@example.com, 전화 010-1234-5678"
        result = sanitize_for_matching(text)
        assert "test@example.com" not in result
        assert "010-1234-5678" not in result
        # 두 개의 <PII>가 있어야 함
        assert result.count("<PII>") >= 2

    def test_sanitize_with_info_returns_patterns(self):
        """발견된 패턴 정보 반환."""
        text = "이메일 test@example.com 전화 010-1234-5678"
        result, patterns = sanitize_with_info(text)
        assert "email" in patterns
        assert "phone" in patterns

    def test_sanitize_no_pii(self):
        """PII 없는 텍스트."""
        text = "연봉 정보 알려줘"
        result = sanitize_for_matching(text)
        assert result == text

    def test_has_pii_pattern_true(self):
        """PII 패턴 존재 확인."""
        assert has_pii_pattern("email: test@example.com") is True
        assert has_pii_pattern("phone: 010-1234-5678") is True

    def test_has_pii_pattern_false(self):
        """PII 패턴 없음 확인."""
        assert has_pii_pattern("연봉 정보 알려줘") is False
        assert has_pii_pattern("회사 기밀 문서") is False

    def test_custom_replacement(self):
        """커스텀 치환 문자열."""
        text = "email: test@example.com"
        result = sanitize_for_matching(text, replacement="[REDACTED]")
        assert "[REDACTED]" in result
        assert "<PII>" not in result

    def test_empty_input(self):
        """빈 입력."""
        assert sanitize_for_matching("") == ""
        assert sanitize_for_matching(None) is None

    def test_sanitize_query_for_forbidden_check(self):
        """forbidden check용 정제 함수."""
        text = "연봉 정보 알려줘 email@test.com"
        result = sanitize_query_for_forbidden_check(text)
        assert "email@test.com" not in result


# =============================================================================
# Test 2: Manifest Validation
# =============================================================================


class TestManifestValidation:
    """Manifest 체크섬 검증 테스트."""

    def test_compute_sha256(self):
        """SHA256 해시 계산."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("test content")
            f.flush()
            path = Path(f.name)

        sha256 = compute_sha256(path)
        assert len(sha256) == 64
        assert sha256.isalnum()

        path.unlink()

    def test_file_checksum_valid(self):
        """파일 체크섬 유효성."""
        checksum = FileChecksum(
            path="test.json",
            sha256="abc123",
            exists=True,
            actual_sha256="abc123",
        )
        assert checksum.is_valid is True

    def test_file_checksum_invalid(self):
        """파일 체크섬 불일치."""
        checksum = FileChecksum(
            path="test.json",
            sha256="abc123",
            exists=True,
            actual_sha256="def456",
        )
        assert checksum.is_valid is False

    def test_file_checksum_not_exists(self):
        """파일 미존재."""
        checksum = FileChecksum(
            path="test.json",
            sha256="abc123",
            exists=False,
            actual_sha256=None,
        )
        assert checksum.is_valid is False

    def test_manifest_validation_pass(self):
        """매니페스트 검증 성공."""
        manifest = RulesetManifest(
            version="v1.0.0",
            profile="A",
            files={
                "ruleset": FileChecksum(
                    path="test.json",
                    sha256="abc123",
                    exists=True,
                    actual_sha256="abc123",
                )
            },
        )

        assert manifest.validate() is True
        assert manifest.is_valid is True
        assert len(manifest.validation_errors) == 0

    def test_manifest_validation_fail(self):
        """매니페스트 검증 실패."""
        manifest = RulesetManifest(
            version="v1.0.0",
            profile="A",
            files={
                "ruleset": FileChecksum(
                    path="test.json",
                    sha256="abc123",
                    exists=True,
                    actual_sha256="different",
                )
            },
        )

        assert manifest.validate() is False
        assert manifest.is_valid is False
        assert len(manifest.validation_errors) > 0

    def test_load_manifest_not_found(self):
        """매니페스트 파일 없을 때."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = load_manifest(Path(tmpdir), profile="A")
            assert result is None

    def test_create_and_load_manifest(self):
        """매니페스트 생성 및 로드."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # 테스트 파일 생성
            test_file = tmpdir / "test.json"
            with open(test_file, "w") as f:
                json.dump({"key": "value"}, f)

            # 매니페스트 생성
            manifest_data = create_manifest(
                resources_dir=tmpdir,
                profile="A",
                files={"test": "test.json"},
                version="v1.0.0",
            )

            # 저장
            manifest_path = tmpdir / "manifest.json"
            save_manifest(manifest_data, manifest_path)

            # 로드 및 검증
            manifest = load_manifest(tmpdir, profile="A")
            assert manifest is not None
            assert manifest.is_valid is True

    def test_manifest_validate_single_file(self):
        """특정 파일만 검증."""
        manifest = RulesetManifest(
            version="v1.0.0",
            profile="A",
            files={
                "ruleset": FileChecksum(
                    path="test.json",
                    sha256="abc123",
                    exists=True,
                    actual_sha256="abc123",
                ),
                "embeddings": FileChecksum(
                    path="test.npy",
                    sha256="def456",
                    exists=False,
                    actual_sha256=None,
                ),
            },
        )

        assert manifest.validate_file("ruleset") is True
        assert manifest.validate_file("embeddings") is False
        assert manifest.validate_file("nonexistent") is True  # 없으면 통과


class TestManifestFailClosed:
    """Manifest 검증 실패 시 fail-closed 동작 테스트."""

    @pytest.fixture
    def forbidden_filter(self):
        """ForbiddenQueryFilter 인스턴스 생성 (rapidfuzz 의존성 필요)."""
        pytest.importorskip("rapidfuzz")
        from app.services.forbidden_query_filter import ForbiddenQueryFilter
        return ForbiddenQueryFilter

    def test_manifest_failure_blocks_all_queries(self, forbidden_filter):
        """manifest 검증 실패 시 모든 쿼리 차단 (fail-closed)."""
        ForbiddenQueryFilter = forbidden_filter

        # validate_manifest=True로 필터 생성
        filter = ForbiddenQueryFilter(
            profile="A",
            validate_manifest=True,
        )

        # manifest 검증 실패 상태 시뮬레이션
        filter._loaded = True
        filter._manifest_failed = True
        filter._ruleset = None

        # 아무 쿼리나 체크
        result = filter.check("테스트 쿼리입니다")

        # fail-closed: 모든 쿼리가 차단되어야 함
        assert result.is_forbidden is True
        assert result.skip_rag is True
        assert result.skip_backend_api is True
        assert result.matched_rule_id == "MANIFEST_VALIDATION_FAILED"
        assert result.decision == "SYSTEM_INTEGRITY_ERROR"
        assert result.match_type == "system"

    def test_manifest_success_allows_normal_queries(self, forbidden_filter):
        """manifest 검증 성공 시 정상 쿼리 허용."""
        ForbiddenQueryFilter = forbidden_filter

        # validate_manifest=True로 필터 생성
        filter = ForbiddenQueryFilter(
            profile="A",
            validate_manifest=True,
        )

        # 정상 상태: manifest 검증 성공
        filter._loaded = True
        filter._manifest_failed = False
        filter._ruleset = None  # 룰셋 없음 = 통과

        # 쿼리 체크
        result = filter.check("테스트 쿼리입니다")

        # 룰셋 없으면 통과
        assert result.is_forbidden is False

    def test_get_ruleset_info_includes_manifest_failed(self, forbidden_filter):
        """get_ruleset_info에 manifest_failed 상태 포함."""
        from unittest.mock import MagicMock
        ForbiddenQueryFilter = forbidden_filter

        filter = ForbiddenQueryFilter(
            profile="A",
            validate_manifest=True,
        )
        filter._loaded = True
        filter._manifest_failed = True

        # Mock ruleset to pass the early return check in get_ruleset_info()
        mock_ruleset = MagicMock()
        mock_ruleset.version = "v1"
        mock_ruleset.profile = "A"
        mock_ruleset.mode = "strict"
        mock_ruleset.rules = []
        mock_ruleset.source_sha256 = "abc123456789012345678901234567890"
        filter._ruleset = mock_ruleset

        info = filter.get_ruleset_info()

        assert "manifest_failed" in info
        assert info["manifest_failed"] is True


# =============================================================================
# Test 3: EmbeddingMatcher Validation
# =============================================================================


class TestEmbeddingMatcherValidation:
    """EmbeddingMatcher 검증 기능 테스트."""

    def test_require_local_pass(self):
        """로컬 임베딩 검증 통과."""
        matcher = EmbeddingMatcher(
            threshold=0.85,
            provider_name="local-model",
            is_local=True,
            require_local=True,
        )
        assert matcher.get_info()["is_local"] is True

    def test_require_local_fail(self):
        """로컬 임베딩 검증 실패."""
        with pytest.raises(EmbeddingProviderError) as exc_info:
            EmbeddingMatcher(
                threshold=0.85,
                provider_name="remote-api",
                is_local=False,
                require_local=True,
            )

        assert "not local" in str(exc_info.value)
        assert "remote-api" in str(exc_info.value)

    def test_require_local_disabled(self):
        """로컬 검증 비활성화."""
        matcher = EmbeddingMatcher(
            threshold=0.85,
            provider_name="remote-api",
            is_local=False,
            require_local=False,
        )
        assert matcher.get_info()["is_local"] is False

    def test_require_index_no_faiss(self):
        """FAISS 없이 require_index 설정."""
        matcher = EmbeddingMatcher(
            threshold=0.85,
            require_index=True,
            use_faiss=False,  # FAISS 강제 비활성화
        )

        info = matcher.get_info()
        assert info["disabled"] is True
        assert info["require_index"] is True

    def test_rule_count_threshold(self):
        """룰 개수 임계치 설정."""
        matcher = EmbeddingMatcher(
            threshold=0.85,
            rule_count_threshold=100,
        )
        assert matcher.get_info()["rule_count_threshold"] == 100

    def test_disabled_matcher_returns_empty(self):
        """비활성화된 매처는 빈 결과 반환."""
        matcher = EmbeddingMatcher(
            threshold=0.85,
            require_index=True,
            use_faiss=False,
        )

        query = np.random.randn(64).astype(np.float32)
        results = matcher.search(query)
        assert results == []

    def test_disabled_matcher_build_fails(self):
        """비활성화된 매처는 인덱스 빌드 실패."""
        matcher = EmbeddingMatcher(
            threshold=0.85,
            require_index=True,
            use_faiss=False,
        )

        embeddings = np.random.randn(10, 64).astype(np.float32)
        result = matcher.build_index(embeddings, list(range(10)))
        assert result is False


# =============================================================================
# Test 4: ChatAnswerMeta Forbidden Fields
# =============================================================================


class TestChatAnswerMetaForbiddenFields:
    """ChatAnswerMeta forbidden 필드 테스트."""

    def test_forbidden_fields_default(self):
        """기본값 확인."""
        meta = ChatAnswerMeta()

        assert meta.forbidden_match_type is None
        assert meta.forbidden_score is None
        assert meta.forbidden_ruleset_version is None
        assert meta.forbidden_rule_id is None

    def test_forbidden_fields_set(self):
        """필드 설정."""
        meta = ChatAnswerMeta(
            forbidden_match_type="fuzzy",
            forbidden_score=95.5,
            forbidden_ruleset_version="v2024.01.01",
            forbidden_rule_id="FR-A-001",
        )

        assert meta.forbidden_match_type == "fuzzy"
        assert meta.forbidden_score == 95.5
        assert meta.forbidden_ruleset_version == "v2024.01.01"
        assert meta.forbidden_rule_id == "FR-A-001"

    def test_forbidden_fields_serialization(self):
        """직렬화 확인."""
        meta = ChatAnswerMeta(
            forbidden_match_type="embedding",
            forbidden_score=0.92,
            forbidden_ruleset_version="v1.0.0",
            forbidden_rule_id="FR-A-002",
        )

        data = meta.model_dump()

        assert data["forbidden_match_type"] == "embedding"
        assert data["forbidden_score"] == 0.92
        assert data["forbidden_ruleset_version"] == "v1.0.0"
        assert data["forbidden_rule_id"] == "FR-A-002"

    def test_retrieval_and_backend_skipped(self):
        """기존 skip 필드와 함께 사용."""
        meta = ChatAnswerMeta(
            retrieval_skipped=True,
            retrieval_skip_reason="FORBIDDEN_QUERY:FR-A-001",
            backend_skipped=True,
            backend_skip_reason="FORBIDDEN_BACKEND:FR-A-001",
            forbidden_match_type="exact",
            forbidden_rule_id="FR-A-001",
        )

        assert meta.retrieval_skipped is True
        assert meta.backend_skipped is True
        assert meta.forbidden_match_type == "exact"
