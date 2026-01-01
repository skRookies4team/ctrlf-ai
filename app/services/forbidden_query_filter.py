# app/services/forbidden_query_filter.py
"""
금지질문 필터 서비스

기능:
- JSON 룰셋 로드 및 버전 로깅
- 질문 정규화 후 exact match 수행
- Step 4: exact miss 시 fuzzy matching (rapidfuzz) 수행
- Step 5: fuzzy miss 시 embedding matching (FAISS/numpy) 수행 (선택적)
- 금지질문 판정 결과 반환 (skip_rag, reason_code 등)

사용법:
    from app.services.forbidden_query_filter import ForbiddenQueryFilter

    filter = ForbiddenQueryFilter(profile="A")
    result = filter.check("질문 내용")
    if result.is_forbidden:
        # 검색 스킵 처리

    # Step 5: Embedding matching 사용 시
    filter = ForbiddenQueryFilter(
        profile="A",
        embedding_enabled=True,
        embedding_threshold=0.85,
        embedding_top_k=3,
    )
    filter.set_embedding_function(my_embed_fn)  # 임베딩 함수 주입
    filter.load()  # 룰셋 로드 시 임베딩 인덱스도 빌드
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

# Step 4: Fuzzy matching
from rapidfuzz import fuzz, process

# Step 5: Embedding matching
from app.services.embedding_matcher import (
    EmbeddingMatcher,
    EmbeddingMatchResult,
    SecondaryCondition,
)

# Step 6: PII sanitization for matching
from app.services.pii_sanitizer import sanitize_query_for_forbidden_check

# Step 6: Manifest validation
from app.services.ruleset_manifest import load_manifest, RulesetManifest

logger = logging.getLogger(__name__)


# =============================================================================
# 결과 데이터 클래스
# =============================================================================


@dataclass
class ForbiddenCheckResult:
    """금지질문 체크 결과."""

    is_forbidden: bool = False
    skip_rag: bool = False
    skip_backend_api: bool = False

    # 매칭된 룰 정보
    matched_rule_id: Optional[str] = None
    decision: Optional[str] = None  # FORBIDDEN_PII, RESTRICTED_SECURITY 등
    reason: Optional[str] = None
    sub_reason: Optional[str] = None
    response_mode: Optional[str] = None  # 거절, 제한 등
    example_response: Optional[str] = None

    # 디버그 정보
    ruleset_version: Optional[str] = None
    query_hash: Optional[str] = None  # 원문 대신 해시로 로깅

    # Step 4/5: Match engine 정보
    # match_type: "exact" | "fuzzy" | "embedding"
    match_type: Optional[str] = None
    fuzzy_score: Optional[float] = None  # fuzzy match 시 유사도 점수 (0-100, rapidfuzz)
    embedding_score: Optional[float] = None  # embedding match 시 코사인 유사도 (0-1)


@dataclass
class ForbiddenRule:
    """단일 금지질문 룰."""

    rule_id: str
    profile: str
    question: str
    question_norm: str
    decision: str
    reason: str = ""
    sub_reason: str = ""
    response_mode: str = ""
    example_response: str = ""
    # Step 3: 차단 범위 플래그
    skip_rag: bool = True  # RAG 스킵 여부 (기본: True)
    skip_backend_api: bool = True  # Backend API 스킵 여부 (기본: True = BOTH 차단)


@dataclass
class ForbiddenRuleset:
    """금지질문 룰셋."""

    version: str
    profile: str
    mode: str  # "strict" or "practical"
    rules: List[ForbiddenRule] = field(default_factory=list)
    source_sha256: str = ""

    # 정규화된 질문 → 룰 인덱스 (O(1) 룩업용)
    _norm_index: Dict[str, ForbiddenRule] = field(default_factory=dict, repr=False)

    def build_index(self) -> None:
        """정규화된 질문으로 인덱스 빌드."""
        self._norm_index = {r.question_norm: r for r in self.rules}

    def lookup(self, query_norm: str) -> Optional[ForbiddenRule]:
        """정규화된 질문으로 룰 조회 (O(1))."""
        return self._norm_index.get(query_norm)


# =============================================================================
# 금지질문 필터 서비스
# =============================================================================


class ForbiddenQueryFilter:
    """금지질문 필터 서비스.

    JSON 룰셋을 로드하고, 질문이 금지질문인지 판정합니다.
    Step 4: exact match miss 시 fuzzy matching 수행.
    Step 5: fuzzy miss 시 embedding matching 수행 (선택적).
    """

    DEFAULT_RESOURCES_DIR = Path(__file__).parent.parent / "resources" / "forbidden_queries"

    # 임베딩 함수 타입: str -> np.ndarray (D,)
    EmbeddingFunction = Callable[[str], np.ndarray]

    def __init__(
        self,
        profile: str = "A",
        resources_dir: Optional[Path] = None,
        fuzzy_enabled: bool = True,
        fuzzy_threshold: int = 92,
        embedding_enabled: bool = False,
        embedding_threshold: float = 0.85,
        embedding_top_k: int = 3,
        validate_manifest: bool = False,
    ):
        """
        Args:
            profile: 사용할 프로필 ("A" 또는 "B")
            resources_dir: 룰셋 JSON 디렉토리 (기본: app/resources/forbidden_queries)
            fuzzy_enabled: fuzzy matching 활성화 여부 (기본: True)
            fuzzy_threshold: fuzzy matching 임계값 0-100 (기본: 92)
            embedding_enabled: embedding matching 활성화 여부 (기본: False)
            embedding_threshold: embedding matching 임계값 0-1 (기본: 0.85)
            embedding_top_k: embedding 검색 시 반환할 최대 후보 수 (기본: 3)
            validate_manifest: manifest 체크섬 검증 여부 (기본: False)
        """
        self._profile = profile
        self._resources_dir = resources_dir or self.DEFAULT_RESOURCES_DIR
        self._ruleset: Optional[ForbiddenRuleset] = None
        self._loaded = False

        # Step 4: Fuzzy matching 설정
        self._fuzzy_enabled = fuzzy_enabled
        self._fuzzy_threshold = fuzzy_threshold

        # Step 5: Embedding matching 설정
        self._embedding_enabled = embedding_enabled
        self._embedding_threshold = embedding_threshold
        self._embedding_top_k = embedding_top_k
        self._embedding_function: Optional[ForbiddenQueryFilter.EmbeddingFunction] = None
        self._embedding_matcher: Optional[EmbeddingMatcher] = None
        self._embeddings_loaded = False

        # Step 6: Manifest 검증 설정
        self._validate_manifest = validate_manifest
        self._manifest: Optional[RulesetManifest] = None
        self._manifest_failed = False  # fail-closed 플래그

    def load(self) -> None:
        """룰셋을 로드합니다. 앱 시작 시 호출 권장."""
        if self._loaded:
            return

        # Step 6: Manifest 검증 (활성화된 경우)
        # fail-closed 정책: manifest 검증 실패 시 모든 쿼리를 차단
        if self._validate_manifest:
            self._manifest = load_manifest(
                resources_dir=self._resources_dir,
                profile=self._profile,
            )

            if self._manifest is not None and not self._manifest.is_valid:
                logger.error(
                    f"Manifest validation failed (FAIL-CLOSED mode): "
                    f"errors={self._manifest.validation_errors}"
                )
                # fail-closed: manifest 검증 실패 플래그 설정
                self._manifest_failed = True
                self._loaded = True
                return

        json_path = self._resources_dir / f"forbidden_ruleset.{self._profile}.json"

        if not json_path.exists():
            logger.warning(
                f"ForbiddenRuleset not found: path={json_path}, "
                f"forbidden query filter will be disabled"
            )
            self._loaded = True
            return

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 룰셋 파싱
            rules = []
            for r in data.get("rules", []):
                match = r.get("match", {})
                rules.append(ForbiddenRule(
                    rule_id=r.get("rule_id", ""),
                    profile=r.get("profile", self._profile),
                    question=match.get("question", ""),
                    question_norm=match.get("question_norm", ""),
                    decision=r.get("decision", ""),
                    reason=r.get("reason", ""),
                    sub_reason=r.get("sub_reason", ""),
                    response_mode=r.get("response_mode", ""),
                    example_response=r.get("example_response", ""),
                    # Step 3: 차단 범위 플래그 (기본값: True = BOTH 차단)
                    skip_rag=r.get("skip_rag", True),
                    skip_backend_api=r.get("skip_backend_api", True),
                ))

            self._ruleset = ForbiddenRuleset(
                version=data.get("version", "unknown"),
                profile=data.get("profile", self._profile),
                mode=data.get("mode", "unknown"),
                rules=rules,
                source_sha256=data.get("source", {}).get("sha256", ""),
            )
            self._ruleset.build_index()

            # 로드 성공 로그 (운영 확인용)
            logger.info(
                f"ForbiddenRuleset loaded: version={self._ruleset.version}, "
                f"profile={self._ruleset.profile}, mode={self._ruleset.mode}, "
                f"rules={len(self._ruleset.rules)}"
            )

            self._loaded = True

            # Step 5: 임베딩 인덱스 빌드 (활성화된 경우)
            if self._embedding_enabled and self._embedding_function is not None:
                self._build_embedding_index()

        except Exception as e:
            logger.error(f"Failed to load ForbiddenRuleset: {e}")
            self._loaded = True  # 실패해도 재시도 방지

    def set_embedding_function(self, embed_fn: EmbeddingFunction) -> None:
        """임베딩 함수를 설정합니다.

        Args:
            embed_fn: 텍스트를 임베딩 벡터로 변환하는 함수 (str -> np.ndarray)
        """
        self._embedding_function = embed_fn
        logger.info("Embedding function set for ForbiddenQueryFilter")

        # 이미 룰셋 로드된 상태면 임베딩 인덱스 빌드
        if self._loaded and self._ruleset is not None and self._embedding_enabled:
            self._build_embedding_index()

    def _build_embedding_index(self) -> None:
        """룰셋 쿼리의 임베딩 인덱스를 빌드합니다."""
        if self._ruleset is None or not self._ruleset.rules:
            logger.warning("Cannot build embedding index: no ruleset loaded")
            return

        if self._embedding_function is None:
            logger.warning("Cannot build embedding index: no embedding function set")
            return

        try:
            # 모든 룰의 정규화된 질문 임베딩
            rule_texts = [r.question_norm for r in self._ruleset.rules]
            embeddings_list = []

            for text in rule_texts:
                emb = self._embedding_function(text)
                embeddings_list.append(emb)

            embeddings = np.array(embeddings_list, dtype=np.float32)
            rule_indices = list(range(len(self._ruleset.rules)))

            # 임베딩 매처 생성 및 인덱스 빌드
            self._embedding_matcher = EmbeddingMatcher(
                threshold=self._embedding_threshold,
                top_k=self._embedding_top_k,
                use_faiss=True,
            )
            self._embedding_matcher.build_index(embeddings, rule_indices)
            self._embeddings_loaded = True

            logger.info(
                f"Embedding index built: count={len(rule_indices)}, "
                f"dimension={embeddings.shape[1]}, threshold={self._embedding_threshold}"
            )

        except Exception as e:
            logger.error(f"Failed to build embedding index: {e}")
            self._embeddings_loaded = False

    def load_embeddings_from_file(self, embeddings_path: Path) -> None:
        """사전 계산된 임베딩 파일에서 인덱스를 빌드합니다.

        Args:
            embeddings_path: 임베딩 파일 경로 (.npy 형식, shape: N x D)
        """
        if self._ruleset is None:
            logger.warning("Cannot load embeddings: ruleset not loaded")
            return

        try:
            embeddings = np.load(embeddings_path)
            rule_indices = list(range(len(self._ruleset.rules)))

            if embeddings.shape[0] != len(self._ruleset.rules):
                logger.error(
                    f"Embedding count mismatch: file={embeddings.shape[0]}, "
                    f"rules={len(self._ruleset.rules)}"
                )
                return

            self._embedding_matcher = EmbeddingMatcher(
                threshold=self._embedding_threshold,
                top_k=self._embedding_top_k,
                use_faiss=True,
            )
            self._embedding_matcher.build_index(embeddings, rule_indices)
            self._embeddings_loaded = True

            logger.info(
                f"Embeddings loaded from file: path={embeddings_path}, "
                f"count={len(rule_indices)}, dimension={embeddings.shape[1]}"
            )

        except Exception as e:
            logger.error(f"Failed to load embeddings from file: {e}")
            self._embeddings_loaded = False

    def check(self, query: str) -> ForbiddenCheckResult:
        """질문이 금지질문인지 판정합니다.

        매칭 순서: exact match → fuzzy match → embedding match

        Args:
            query: 원본 질문 (raw_query, PII 마스킹 전)

        Returns:
            ForbiddenCheckResult: 판정 결과
        """
        # 룰셋 미로드 시 자동 로드
        if not self._loaded:
            self.load()

        # Step 6: fail-closed - manifest 검증 실패 시 모든 쿼리 차단
        if self._manifest_failed:
            logger.warning(
                f"FAIL-CLOSED: Blocking query due to manifest validation failure: "
                f"query_hash={self._hash_query(query)}"
            )
            return ForbiddenCheckResult(
                is_forbidden=True,
                skip_rag=True,
                skip_backend_api=True,
                matched_rule_id="MANIFEST_VALIDATION_FAILED",
                decision="SYSTEM_INTEGRITY_ERROR",
                reason="Manifest validation failed - all queries blocked",
                response_mode="거절",
                example_response="시스템 정합성 오류가 발생했습니다. 관리자에게 문의해 주세요.",
                ruleset_version="unknown",
                query_hash=self._hash_query(query),
                match_type="system",
            )

        # 룰셋 없으면 통과
        if self._ruleset is None:
            return ForbiddenCheckResult(
                is_forbidden=False,
                query_hash=self._hash_query(query),
            )

        # 질문 정규화
        query_norm = self._normalize(query)
        query_hash = self._hash_query(query)

        # Step 1: exact match 조회 (원본 정규화 쿼리 사용)
        rule = self._ruleset.lookup(query_norm)

        if rule is not None:
            # exact match 성공
            return self._build_match_result(
                rule=rule,
                query_hash=query_hash,
                match_type="exact",
                fuzzy_score=None,
                embedding_score=None,
            )

        # Step 6: PII 정제 (fuzzy/embedding 매칭 전)
        # 이메일/전화번호 등 PII를 <PII>로 치환하여 노이즈 제거 및 안전성 향상
        query_sanitized = sanitize_query_for_forbidden_check(query_norm)

        # Step 2: exact miss → fuzzy match 시도 (활성화된 경우)
        if self._fuzzy_enabled:
            fuzzy_result = self._fuzzy_match(query_sanitized)
            if fuzzy_result is not None:
                rule, score = fuzzy_result
                return self._build_match_result(
                    rule=rule,
                    query_hash=query_hash,
                    match_type="fuzzy",
                    fuzzy_score=score,
                    embedding_score=None,
                )

        # Step 3: fuzzy miss → embedding match 시도 (활성화된 경우)
        if self._embedding_enabled and self._embeddings_loaded:
            # embedding match에는 정제된 쿼리 사용 (PII 노출 방지)
            embedding_result = self._embedding_match(query_sanitized, query)
            if embedding_result is not None:
                rule, score = embedding_result
                return self._build_match_result(
                    rule=rule,
                    query_hash=query_hash,
                    match_type="embedding",
                    fuzzy_score=None,
                    embedding_score=score,
                )

        # 매칭 없음 - 통과
        return ForbiddenCheckResult(
            is_forbidden=False,
            ruleset_version=self._ruleset.version,
            query_hash=query_hash,
        )

    def _build_match_result(
        self,
        rule: ForbiddenRule,
        query_hash: str,
        match_type: str,
        fuzzy_score: Optional[float],
        embedding_score: Optional[float],
    ) -> ForbiddenCheckResult:
        """매칭된 룰로 결과를 생성합니다."""
        result = ForbiddenCheckResult(
            is_forbidden=True,
            skip_rag=rule.skip_rag,
            skip_backend_api=rule.skip_backend_api,
            matched_rule_id=rule.rule_id,
            decision=rule.decision,
            reason=rule.reason,
            sub_reason=rule.sub_reason,
            response_mode=rule.response_mode,
            example_response=rule.example_response,
            ruleset_version=self._ruleset.version,
            query_hash=query_hash,
            match_type=match_type,
            fuzzy_score=fuzzy_score,
            embedding_score=embedding_score,
        )

        # 로그 (원문 제외, 해시로만)
        if match_type == "embedding":
            logger.warning(
                f"Forbidden query matched (embedding): rule_id={rule.rule_id}, "
                f"score={embedding_score:.4f}, threshold={self._embedding_threshold}, "
                f"decision={rule.decision}, reason={rule.reason}, "
                f"skip_rag={rule.skip_rag}, skip_backend_api={rule.skip_backend_api}, "
                f"query_hash={query_hash}, version={self._ruleset.version}"
            )
        elif match_type == "fuzzy":
            logger.warning(
                f"Forbidden query matched (fuzzy): rule_id={rule.rule_id}, "
                f"score={fuzzy_score:.1f}, threshold={self._fuzzy_threshold}, "
                f"decision={rule.decision}, reason={rule.reason}, "
                f"skip_rag={rule.skip_rag}, skip_backend_api={rule.skip_backend_api}, "
                f"query_hash={query_hash}, version={self._ruleset.version}"
            )
        else:
            logger.warning(
                f"Forbidden query matched (exact): rule_id={rule.rule_id}, "
                f"decision={rule.decision}, reason={rule.reason}, "
                f"skip_rag={rule.skip_rag}, skip_backend_api={rule.skip_backend_api}, "
                f"query_hash={query_hash}, version={self._ruleset.version}"
            )

        return result

    def _embedding_match(
        self,
        query_norm: str,
        query_raw: str,
    ) -> Optional[Tuple[ForbiddenRule, float]]:
        """Embedding matching으로 가장 유사한 룰을 찾습니다.

        Args:
            query_norm: 정규화된 질문
            query_raw: 원본 질문 (2차 조건 체크용)

        Returns:
            (ForbiddenRule, score) 또는 None (threshold 미달 또는 2차 조건 미통과 시)
        """
        if self._embedding_matcher is None:
            return None

        if self._embedding_function is None:
            return None

        try:
            # 쿼리 임베딩 생성
            query_embedding = self._embedding_function(query_norm)

            # 매칭 검색
            match_result = self._embedding_matcher.get_best_match(
                query_embedding=query_embedding,
                query_text=query_raw,
            )

            if match_result is None or not match_result.matched:
                return None

            # 해당 인덱스의 룰 반환
            rule = self._ruleset.rules[match_result.rule_idx]

            logger.debug(
                f"Embedding match candidate: score={match_result.score:.4f}, "
                f"threshold={self._embedding_threshold}, rule_id={rule.rule_id}, "
                f"secondary_passed={match_result.passed_secondary_check}"
            )

            return rule, match_result.score

        except Exception as e:
            logger.error(f"Embedding match failed: {e}")
            return None

    def _fuzzy_match(self, query_norm: str) -> Optional[Tuple[ForbiddenRule, float]]:
        """Fuzzy matching으로 가장 유사한 룰을 찾습니다.

        Args:
            query_norm: 정규화된 질문

        Returns:
            (ForbiddenRule, score) 또는 None (threshold 미달 시)
        """
        if self._ruleset is None or not self._ruleset.rules:
            return None

        # 모든 룰의 정규화된 질문 목록
        choices = [r.question_norm for r in self._ruleset.rules]

        # rapidfuzz.process.extractOne: 가장 유사한 항목 찾기
        # scorer=fuzz.ratio: 기본 Levenshtein 비율 (0-100)
        result = process.extractOne(
            query_norm,
            choices,
            scorer=fuzz.ratio,
            score_cutoff=self._fuzzy_threshold,
        )

        if result is None:
            return None

        matched_text, score, idx = result

        # 해당 인덱스의 룰 반환
        rule = self._ruleset.rules[idx]

        logger.debug(
            f"Fuzzy match candidate: score={score:.1f}, "
            f"threshold={self._fuzzy_threshold}, rule_id={rule.rule_id}"
        )

        return rule, score

    def get_ruleset_info(self) -> Dict[str, Any]:
        """현재 로드된 룰셋 정보 반환 (디버그용)."""
        if not self._loaded:
            self.load()

        if self._ruleset is None:
            return {"loaded": False, "error": "ruleset not found"}

        info = {
            "loaded": True,
            "version": self._ruleset.version,
            "profile": self._ruleset.profile,
            "mode": self._ruleset.mode,
            "rules_count": len(self._ruleset.rules),
            "source_sha256": self._ruleset.source_sha256[:16] + "...",
            # Step 4: Fuzzy matching 설정 정보
            "fuzzy_enabled": self._fuzzy_enabled,
            "fuzzy_threshold": self._fuzzy_threshold,
            # Step 5: Embedding matching 설정 정보
            "embedding_enabled": self._embedding_enabled,
            "embedding_threshold": self._embedding_threshold,
            "embedding_top_k": self._embedding_top_k,
            "embeddings_loaded": self._embeddings_loaded,
            # Step 6: Manifest 검증 정보
            "validate_manifest": self._validate_manifest,
            "manifest_valid": self._manifest.is_valid if self._manifest else None,
            "manifest_failed": self._manifest_failed,  # fail-closed 상태
        }

        # EmbeddingMatcher 상세 정보
        if self._embedding_matcher is not None:
            info["embedding_matcher"] = self._embedding_matcher.get_info()

        # Manifest 상세 정보
        if self._manifest is not None:
            info["manifest"] = {
                "version": self._manifest.version,
                "is_valid": self._manifest.is_valid,
                "files": list(self._manifest.files.keys()),
                "errors": self._manifest.validation_errors,
            }

        return info

    @staticmethod
    def _normalize(text: str) -> str:
        """질문 정규화: 소문자 + 공백 정리."""
        if not text:
            return ""
        text = str(text).strip().lower()
        text = re.sub(r"\s+", " ", text)
        return text

    @staticmethod
    def _hash_query(query: str) -> str:
        """질문 해시 (로깅용, 원문 노출 방지)."""
        return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]


# =============================================================================
# 싱글톤 인스턴스 (선택적)
# =============================================================================

_default_filter: Optional[ForbiddenQueryFilter] = None


def get_forbidden_query_filter(
    profile: Optional[str] = None,
    fuzzy_enabled: Optional[bool] = None,
    fuzzy_threshold: Optional[int] = None,
    embedding_enabled: Optional[bool] = None,
    embedding_threshold: Optional[float] = None,
    embedding_top_k: Optional[int] = None,
) -> ForbiddenQueryFilter:
    """기본 ForbiddenQueryFilter 인스턴스 반환.

    앱 전체에서 동일한 인스턴스를 사용하려면 이 함수 사용.
    config에서 설정을 자동으로 읽어옵니다.

    Args:
        profile: 프로필 (기본: config에서 읽음)
        fuzzy_enabled: fuzzy matching 활성화 (기본: config에서 읽음)
        fuzzy_threshold: fuzzy matching 임계값 (기본: config에서 읽음)
        embedding_enabled: embedding matching 활성화 (기본: config에서 읽음)
        embedding_threshold: embedding matching 임계값 (기본: config에서 읽음)
        embedding_top_k: embedding 검색 top_k (기본: config에서 읽음)
    """
    global _default_filter

    # config에서 기본값 로드
    from app.core.config import get_settings
    settings = get_settings()

    _profile = profile if profile is not None else settings.FORBIDDEN_QUERY_PROFILE
    _fuzzy_enabled = fuzzy_enabled if fuzzy_enabled is not None else settings.FORBIDDEN_QUERY_FUZZY_ENABLED
    _fuzzy_threshold = fuzzy_threshold if fuzzy_threshold is not None else settings.FORBIDDEN_QUERY_FUZZY_THRESHOLD
    _embedding_enabled = embedding_enabled if embedding_enabled is not None else settings.FORBIDDEN_QUERY_EMBEDDING_ENABLED
    _embedding_threshold = embedding_threshold if embedding_threshold is not None else settings.FORBIDDEN_QUERY_EMBEDDING_THRESHOLD
    _embedding_top_k = embedding_top_k if embedding_top_k is not None else settings.FORBIDDEN_QUERY_EMBEDDING_TOP_K

    # 기존 인스턴스가 없거나 설정이 다르면 새로 생성
    if (
        _default_filter is None
        or _default_filter._profile != _profile
        or _default_filter._fuzzy_enabled != _fuzzy_enabled
        or _default_filter._fuzzy_threshold != _fuzzy_threshold
        or _default_filter._embedding_enabled != _embedding_enabled
        or _default_filter._embedding_threshold != _embedding_threshold
        or _default_filter._embedding_top_k != _embedding_top_k
    ):
        _default_filter = ForbiddenQueryFilter(
            profile=_profile,
            fuzzy_enabled=_fuzzy_enabled,
            fuzzy_threshold=_fuzzy_threshold,
            embedding_enabled=_embedding_enabled,
            embedding_threshold=_embedding_threshold,
            embedding_top_k=_embedding_top_k,
        )
        _default_filter.load()

        logger.info(
            f"ForbiddenQueryFilter created: profile={_profile}, "
            f"fuzzy_enabled={_fuzzy_enabled}, fuzzy_threshold={_fuzzy_threshold}, "
            f"embedding_enabled={_embedding_enabled}, embedding_threshold={_embedding_threshold}"
        )

    return _default_filter
