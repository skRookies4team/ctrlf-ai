# app/services/forbidden_query_filter.py
"""
금지질문 필터 서비스

기능:
- JSON 룰셋 로드 및 버전 로깅
- 질문 정규화 후 exact match 수행
- 금지질문 판정 결과 반환 (skip_rag, reason_code 등)

사용법:
    from app.services.forbidden_query_filter import ForbiddenQueryFilter

    filter = ForbiddenQueryFilter(profile="A")
    result = filter.check("질문 내용")
    if result.is_forbidden:
        # 검색 스킵 처리
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    """

    DEFAULT_RESOURCES_DIR = Path(__file__).parent.parent / "resources" / "forbidden_queries"

    def __init__(
        self,
        profile: str = "A",
        resources_dir: Optional[Path] = None,
    ):
        """
        Args:
            profile: 사용할 프로필 ("A" 또는 "B")
            resources_dir: 룰셋 JSON 디렉토리 (기본: app/resources/forbidden_queries)
        """
        self._profile = profile
        self._resources_dir = resources_dir or self.DEFAULT_RESOURCES_DIR
        self._ruleset: Optional[ForbiddenRuleset] = None
        self._loaded = False

    def load(self) -> None:
        """룰셋을 로드합니다. 앱 시작 시 호출 권장."""
        if self._loaded:
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

        except Exception as e:
            logger.error(f"Failed to load ForbiddenRuleset: {e}")
            self._loaded = True  # 실패해도 재시도 방지

    def check(self, query: str) -> ForbiddenCheckResult:
        """질문이 금지질문인지 판정합니다.

        Args:
            query: 원본 질문 (raw_query, PII 마스킹 전)

        Returns:
            ForbiddenCheckResult: 판정 결과
        """
        # 룰셋 미로드 시 자동 로드
        if not self._loaded:
            self.load()

        # 룰셋 없으면 통과
        if self._ruleset is None:
            return ForbiddenCheckResult(
                is_forbidden=False,
                query_hash=self._hash_query(query),
            )

        # 질문 정규화
        query_norm = self._normalize(query)

        # exact match 조회
        rule = self._ruleset.lookup(query_norm)

        if rule is None:
            return ForbiddenCheckResult(
                is_forbidden=False,
                ruleset_version=self._ruleset.version,
                query_hash=self._hash_query(query),
            )

        # 매칭됨 - 금지질문
        result = ForbiddenCheckResult(
            is_forbidden=True,
            skip_rag=True,  # 기본적으로 RAG 스킵
            skip_backend_api=False,  # BACKEND_API는 정책에 따라 결정
            matched_rule_id=rule.rule_id,
            decision=rule.decision,
            reason=rule.reason,
            sub_reason=rule.sub_reason,
            response_mode=rule.response_mode,
            example_response=rule.example_response,
            ruleset_version=self._ruleset.version,
            query_hash=self._hash_query(query),
        )

        # 로그 (원문 제외, 해시로만)
        logger.warning(
            f"Forbidden query matched: rule_id={rule.rule_id}, "
            f"decision={rule.decision}, reason={rule.reason}, "
            f"query_hash={result.query_hash}, version={self._ruleset.version}"
        )

        return result

    def get_ruleset_info(self) -> Dict[str, Any]:
        """현재 로드된 룰셋 정보 반환 (디버그용)."""
        if not self._loaded:
            self.load()

        if self._ruleset is None:
            return {"loaded": False, "error": "ruleset not found"}

        return {
            "loaded": True,
            "version": self._ruleset.version,
            "profile": self._ruleset.profile,
            "mode": self._ruleset.mode,
            "rules_count": len(self._ruleset.rules),
            "source_sha256": self._ruleset.source_sha256[:16] + "...",
        }

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


def get_forbidden_query_filter(profile: str = "A") -> ForbiddenQueryFilter:
    """기본 ForbiddenQueryFilter 인스턴스 반환.

    앱 전체에서 동일한 인스턴스를 사용하려면 이 함수 사용.
    """
    global _default_filter
    if _default_filter is None or _default_filter._profile != profile:
        _default_filter = ForbiddenQueryFilter(profile=profile)
        _default_filter.load()
    return _default_filter
