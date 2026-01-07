"""
Query Rewriter - 검색용 쿼리 확장 (Query Expansion)

Phase 57: 고급 RAG 기법 #1
- 짧거나 모호한 질문을 검색에 최적화된 키워드로 확장
- LLM을 사용하여 도메인별 공식 용어/동의어/관련 키워드 생성

설계 원칙:
- RAG route일 때만 동작 (일상대화/개인화 API는 제외)
- 조건부 적용: 짧은 쿼리(40자 미만)만 확장
- 출력은 검색용 키워드 3~8개 (장문 재작성 금지)
- 개인정보/마스킹 토큰은 절대 생성하지 않음
"""

import re
from dataclasses import dataclass
from typing import Optional

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 확장 대상 쿼리 최대 길이 (이보다 길면 확장 불필요)
EXPANSION_MAX_QUERY_LENGTH = 40

# 확장 제외 패턴 (마스킹 토큰이 많으면 확장하지 않음)
MASKING_TOKEN_PATTERN = re.compile(
    r'\[(PERSON|NAME|PHONE|EMAIL|ADDRESS|SSN|CARD|ACCOUNT|DATE|ORG)\]',
    re.IGNORECASE
)

# 도메인별 확장 힌트
DOMAIN_HINTS = {
    "POLICY": "사내규정, 인사규정, 복무규정, 근태, 휴가, 급여, 복리후생",
    "EDU": "교육, 이수, 수료, 필수교육, 법정교육, 직무교육",
    "INCIDENT": "장애, 사고, 보안사고, 인시던트, 대응, 복구",
}


@dataclass
class RewriteResult:
    """쿼리 확장 결과"""
    used: bool           # 확장이 적용되었는지
    original: str        # 원본 쿼리
    rewritten: str       # 확장된 쿼리 (미적용시 원본과 동일)
    reason: str          # 적용/미적용 사유


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
    검색용 쿼리를 확장합니다.

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

    # LLM 호출하여 확장
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
            f"[QueryRewriter] Expanded: '{q[:20]}...' → '{expanded[:50]}...' "
            f"(original_len={len(q)}, expanded_len={len(expanded)})"
        )

        return RewriteResult(
            used=True,
            original=query,
            rewritten=expanded,
            reason=reason
        )

    except Exception as e:
        logger.error(f"[QueryRewriter] Expansion failed: {e}")
        return RewriteResult(
            used=False,
            original=query,
            rewritten=query,
            reason=f"error:{type(e).__name__}"
        )


def expand_query_sync(query: str, domain: str) -> RewriteResult:
    """
    동기 버전 쿼리 확장 (LLM 없이 규칙 기반)

    LLM 호출 없이 간단한 규칙으로 확장.
    테스트/폴백용.
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

    # 간단한 규칙 기반 확장 (동의어 추가)
    expansions = {
        "연차": "연차 연차휴가 휴가 사용 규정 신청",
        "휴가": "휴가 연차 휴직 규정 신청 방법",
        "급여": "급여 월급 임금 지급 규정",
        "징계": "징계 처분 규정 절차 종류",
        "교육": "교육 이수 수료 필수교육 법정교육",
        "보안": "보안 정보보안 보안교육 규정",
        "비밀번호": "비밀번호 패스워드 변경 규칙 정책",
    }

    # 키워드 매칭
    for keyword, expansion in expansions.items():
        if keyword in q:
            expanded = f"{q} {expansion}"
            return RewriteResult(
                used=True,
                original=query,
                rewritten=expanded,
                reason="rule_based_expansion"
            )

    return RewriteResult(
        used=False,
        original=query,
        rewritten=query,
        reason="no_matching_rule"
    )
