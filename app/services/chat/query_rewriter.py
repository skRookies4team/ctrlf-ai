"""
Query Rewriter - 검색용 쿼리 확장 (Query Expansion)

Phase 57: 고급 RAG 기법 #1
- 짧거나 모호한 질문을 검색에 최적화된 키워드로 확장
- LLM을 사용하여 도메인별 공식 용어/동의어/관련 키워드 생성

Phase 58: YAML 기반 확장 규칙
- config/query_expansion_rules.yaml에서 확장 규칙 로드
- 핫 리로드 지원 (reload_expansion_rules())
- 20개 핵심 키워드 + 동의어/관련어 매핑

설계 원칙:
- RAG route일 때만 동작 (일상대화/개인화 API는 제외)
- 조건부 적용: 짧은 쿼리(40자 미만)만 확장
- 출력은 검색용 키워드 3~8개 (장문 재작성 금지)
- 개인정보/마스킹 토큰은 절대 생성하지 않음
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any

import yaml

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# =============================================================================
# 설정
# =============================================================================

# 확장 대상 쿼리 최대 길이 (이보다 길면 확장 불필요)
EXPANSION_MAX_QUERY_LENGTH = 40

# 확장 제외 패턴 (마스킹 토큰이 많으면 확장하지 않음)
MASKING_TOKEN_PATTERN = re.compile(
    r'\[(PERSON|NAME|PHONE|EMAIL|ADDRESS|SSN|CARD|ACCOUNT|DATE|ORG)\]',
    re.IGNORECASE
)

# 도메인별 확장 힌트 (LLM 버전용)
DOMAIN_HINTS = {
    "POLICY": "사내규정, 인사규정, 복무규정, 근태, 휴가, 급여, 복리후생",
    "EDU": "교육, 이수, 수료, 필수교육, 법정교육, 직무교육",
    "INCIDENT": "장애, 사고, 보안사고, 인시던트, 대응, 복구",
}

# YAML 규칙 파일 경로
EXPANSION_RULES_PATH = Path(__file__).parent.parent.parent.parent / "config" / "query_expansion_rules.yaml"


# =============================================================================
# YAML 규칙 로더 (캐시 지원)
# =============================================================================

class ExpansionRulesLoader:
    """
    YAML 확장 규칙 로더

    싱글톤 패턴으로 규칙을 캐시하여 매 요청마다 파일을 읽지 않음.
    reload()로 핫 리로드 지원.
    """

    _instance = None
    _rules: Dict[str, Dict[str, Any]] = {}
    _settings: Dict[str, Any] = {}
    _loaded: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load(self, force_reload: bool = False) -> Dict[str, Dict[str, Any]]:
        """
        규칙 로드 (캐시 사용)

        Args:
            force_reload: True면 캐시 무시하고 다시 로드

        Returns:
            확장 규칙 딕셔너리
        """
        if self._loaded and not force_reload:
            return self._rules

        try:
            if not EXPANSION_RULES_PATH.exists():
                logger.warning(f"[ExpansionRules] File not found: {EXPANSION_RULES_PATH}")
                self._rules = {}
                self._settings = {}
                self._loaded = True
                return self._rules

            with open(EXPANSION_RULES_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            self._rules = data.get("rules", {})
            self._settings = data.get("settings", {})
            self._loaded = True

            logger.info(
                f"[ExpansionRules] Loaded {len(self._rules)} rules from {EXPANSION_RULES_PATH.name}"
            )
            return self._rules

        except Exception as e:
            logger.error(f"[ExpansionRules] Failed to load: {e}")
            self._rules = {}
            self._settings = {}
            self._loaded = True
            return self._rules

    def reload(self) -> Dict[str, Dict[str, Any]]:
        """규칙 핫 리로드"""
        logger.info("[ExpansionRules] Reloading rules...")
        return self.load(force_reload=True)

    def get_rules(self) -> Dict[str, Dict[str, Any]]:
        """캐시된 규칙 반환"""
        if not self._loaded:
            self.load()
        return self._rules

    def get_settings(self) -> Dict[str, Any]:
        """캐시된 설정 반환"""
        if not self._loaded:
            self.load()
        return self._settings

    def build_expansion_string(self, keyword: str, query: str) -> Optional[str]:
        """
        키워드에 대한 확장 문자열 생성

        Args:
            keyword: 매칭된 키워드
            query: 원본 쿼리

        Returns:
            확장된 쿼리 문자열 (매칭 안 되면 None)
        """
        rules = self.get_rules()
        settings = self.get_settings()

        if keyword not in rules:
            return None

        rule = rules[keyword]
        synonyms = rule.get("synonyms", [])
        related = rule.get("related", [])

        # 설정에 따라 포함 여부 결정
        include_synonyms = settings.get("include_synonyms", True)
        include_related = settings.get("include_related", True)
        max_keywords = settings.get("max_expansion_keywords", 8)

        # 확장 키워드 수집
        expansion_parts = []

        if include_synonyms:
            expansion_parts.extend(synonyms[:4])  # 동의어 최대 4개

        if include_related:
            remaining = max_keywords - len(expansion_parts)
            expansion_parts.extend(related[:remaining])  # 나머지 관련어

        if not expansion_parts:
            return None

        # 확장 문자열 생성 (원본 + 키워드 + 확장)
        expansion_str = " ".join(expansion_parts[:max_keywords])
        return f"{query} {keyword} {expansion_str}"


# 싱글톤 인스턴스
_rules_loader = ExpansionRulesLoader()


def get_expansion_rules() -> Dict[str, Dict[str, Any]]:
    """확장 규칙 반환 (외부 사용)"""
    return _rules_loader.get_rules()


def reload_expansion_rules() -> Dict[str, Dict[str, Any]]:
    """확장 규칙 핫 리로드 (외부 사용)"""
    return _rules_loader.reload()


# =============================================================================
# 데이터 클래스
# =============================================================================

@dataclass
class RewriteResult:
    """쿼리 확장 결과"""
    used: bool           # 확장이 적용되었는지
    original: str        # 원본 쿼리
    rewritten: str       # 확장된 쿼리 (미적용시 원본과 동일)
    reason: str          # 적용/미적용 사유
    matched_keyword: Optional[str] = None  # 매칭된 키워드 (Phase 58)


# =============================================================================
# 확장 조건 판단
# =============================================================================

def _should_expand(query: str) -> tuple[bool, str]:
    """
    쿼리 확장 필요 여부 판단

    Returns:
        (should_expand, reason)
    """
    q = query.strip()

    # 빈 쿼리
    if not q:
        return False, "empty_query"

    # 너무 긴 쿼리 (이미 충분히 구체적)
    if len(q) > EXPANSION_MAX_QUERY_LENGTH:
        return False, "too_long"

    # 마스킹 토큰이 많으면 개인정보 관련 질문 → 확장 위험
    masking_count = len(MASKING_TOKEN_PATTERN.findall(q))
    if masking_count >= 2:
        return False, "too_many_masking_tokens"

    # 단어 수가 너무 적으면 확장 필요
    words = re.findall(r'[가-힣a-zA-Z0-9]+', q)
    if len(words) <= 2:
        return True, "short_query"

    # 그 외 짧은 쿼리
    if len(q) <= 20:
        return True, "short_query"

    return True, "normal_expansion"


# =============================================================================
# 동기 버전 (규칙 기반) - Phase 58 YAML 지원
# =============================================================================

def expand_query_sync(query: str, domain: str) -> RewriteResult:
    """
    동기 버전 쿼리 확장 (YAML 규칙 기반)

    Phase 58: config/query_expansion_rules.yaml에서 규칙 로드
    LLM 호출 없이 빠르고 안정적인 확장.

    Args:
        query: 원본 쿼리
        domain: 검색 도메인

    Returns:
        RewriteResult: 확장 결과
    """
    q = query.strip()

    should_expand, reason = _should_expand(q)
    if not should_expand:
        return RewriteResult(
            used=False,
            original=query,
            rewritten=query,
            reason=reason
        )

    # YAML 규칙에서 매칭 시도
    # 긴 키워드 우선 매칭 (예: "보안사고" > "보안", "성희롱" > "신고")
    rules = _rules_loader.get_rules()
    sorted_keywords = sorted(rules.keys(), key=len, reverse=True)

    for keyword in sorted_keywords:
        if keyword in q:
            expanded = _rules_loader.build_expansion_string(keyword, q)
            if expanded:
                logger.debug(
                    f"[QueryExpansion] YAML rule matched: '{keyword}' in '{q[:20]}...'"
                )
                return RewriteResult(
                    used=True,
                    original=query,
                    rewritten=expanded,
                    reason="yaml_rule_expansion",
                    matched_keyword=keyword
                )

    # 매칭되는 규칙 없음
    return RewriteResult(
        used=False,
        original=query,
        rewritten=query,
        reason="no_matching_rule"
    )


# =============================================================================
# LLM 버전 (비동기)
# =============================================================================

def _build_expansion_prompt(query: str, domain: str) -> str:
    """확장 프롬프트 생성"""
    domain_hint = DOMAIN_HINTS.get(domain, "")

    prompt = f"""너는 사내 문서 검색용 '키워드 확장기'다.

도메인: {domain}
{f"관련 주제: {domain_hint}" if domain_hint else ""}
원문 질문: {query}

규칙:
1. 개인정보(이름, 사번, 전화번호 등)는 절대 생성하지 말 것
2. [PERSON], [PHONE] 같은 마스킹 토큰은 그대로 유지하거나 제외할 것
3. 원문 의미를 바꾸지 말 것
4. 검색에 도움되는 공식 용어/동의어/관련 키워드 3~8개를 공백으로만 반환
5. 문장 형태 금지, 따옴표 금지, 키워드만 나열

출력 예시: 연차휴가 사용 규정 신청 방법 잔여일수

출력:"""

    return prompt


async def expand_query_for_search(
    llm_client,
    query: str,
    domain: str,
) -> RewriteResult:
    """
    검색용 쿼리를 확장합니다 (LLM 버전).

    YAML 규칙에 없는 쿼리도 LLM으로 확장 가능.
    지연이 발생할 수 있으므로 조건부 사용 권장.

    Args:
        llm_client: LLM 클라이언트 (generate 메서드 필요)
        query: 원본 쿼리
        domain: 검색 도메인 (POLICY, EDU, INCIDENT 등)

    Returns:
        RewriteResult: 확장 결과
    """
    settings = get_settings()

    # 기능 비활성화 체크
    if not getattr(settings, 'QUERY_EXPANSION_ENABLED', True):
        return RewriteResult(
            used=False,
            original=query,
            rewritten=query,
            reason="feature_disabled"
        )

    q = query.strip()

    # 확장 필요 여부 판단
    should_expand, reason = _should_expand(q)
    if not should_expand:
        logger.debug(f"[QueryRewriter] Skip expansion: reason={reason}, query='{q[:30]}...'")
        return RewriteResult(
            used=False,
            original=query,
            rewritten=query,
            reason=reason
        )

    # 먼저 YAML 규칙 시도 (빠름)
    yaml_result = expand_query_sync(query, domain)
    if yaml_result.used:
        return yaml_result

    # YAML에 없으면 LLM 호출
    try:
        prompt = _build_expansion_prompt(q, domain)

        # LLM 호출 (짧은 응답 기대)
        expanded = await llm_client.generate(
            prompt=prompt,
            max_tokens=100,  # 키워드 몇 개만 필요
            temperature=0.3,  # 일관성 유지
        )

        expanded = expanded.strip()

        # 빈 응답 체크
        if not expanded:
            logger.warning(f"[QueryRewriter] Empty expansion result for: '{q[:30]}...'")
            return RewriteResult(
                used=False,
                original=query,
                rewritten=query,
                reason="empty_result"
            )

        # 너무 긴 응답 (문장 생성됨) → 실패 처리
        if len(expanded) > 200:
            logger.warning(f"[QueryRewriter] Expansion too long ({len(expanded)} chars), discarding")
            return RewriteResult(
                used=False,
                original=query,
                rewritten=query,
                reason="result_too_long"
            )

        # 마스킹 토큰이 새로 생성되었는지 체크 (개인정보 생성 방지)
        original_masks = set(MASKING_TOKEN_PATTERN.findall(query))
        expanded_masks = set(MASKING_TOKEN_PATTERN.findall(expanded))
        if expanded_masks - original_masks:
            logger.warning(f"[QueryRewriter] New masking tokens detected, discarding expansion")
            return RewriteResult(
                used=False,
                original=query,
                rewritten=query,
                reason="new_masking_tokens"
            )

        logger.info(
            f"[QueryRewriter] LLM Expanded: '{q[:20]}...' → '{expanded[:50]}...' "
            f"(original_len={len(q)}, expanded_len={len(expanded)})"
        )

        return RewriteResult(
            used=True,
            original=query,
            rewritten=expanded,
            reason="llm_expansion"
        )

    except Exception as e:
        logger.error(f"[QueryRewriter] Expansion failed: {e}")
        return RewriteResult(
            used=False,
            original=query,
            rewritten=query,
            reason=f"error:{type(e).__name__}"
        )
