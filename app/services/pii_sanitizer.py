# app/services/pii_sanitizer.py
"""
Step 6: PII Sanitizer for Forbidden Query Matching

금지질문 매칭 전 PII 패턴을 <PII>로 치환하여:
1. 임베딩 입력에서 PII 노출 방지 (로컬이어도 안전)
2. fuzzy/embedding 매칭 시 노이즈 제거 (정확도 향상)

지원 패턴:
- 이메일
- 전화번호 (한국, 국제)
- 주민등록번호
- 카드번호
- 계좌번호
- URL

사용법:
    from app.services.pii_sanitizer import sanitize_for_matching

    sanitized = sanitize_for_matching("내 이메일은 test@example.com 이야")
    # -> "내 이메일은 <PII> 이야"
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Pattern, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# PII 패턴 정의
# =============================================================================


@dataclass
class PiiPattern:
    """PII 패턴 정의."""

    name: str
    pattern: Pattern[str]
    replacement: str = "<PII>"


# 한국 전화번호 패턴 (다양한 형식 지원)
# 010-1234-5678, 010.1234.5678, 01012345678, +82-10-1234-5678
PHONE_PATTERN = re.compile(
    r"""
    (?:
        # 국제 형식 (+82, +1 등)
        \+\d{1,3}[-.\s]?\d{1,4}[-.\s]?\d{3,4}[-.\s]?\d{4}
        |
        # 한국 휴대폰 (010, 011, 016, 017, 018, 019)
        01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}
        |
        # 한국 지역번호 (02, 031-099)
        0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}
    )
    """,
    re.VERBOSE,
)

# 이메일 패턴
EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

# 주민등록번호 패턴 (6자리-7자리)
SSN_PATTERN = re.compile(
    r"\d{6}[-\s]?[1-4]\d{6}",
)

# 신용카드 번호 패턴 (16자리, 다양한 구분자)
CARD_PATTERN = re.compile(
    r"""
    (?:
        # 4-4-4-4 형식
        \d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}
        |
        # 연속 16자리
        \d{16}
    )
    """,
    re.VERBOSE,
)

# 계좌번호 패턴 (10-14자리 숫자, 구분자 포함)
ACCOUNT_PATTERN = re.compile(
    r"\d{3,4}[-\s]?\d{2,6}[-\s]?\d{2,6}[-\s]?\d{0,4}",
)

# URL 패턴
URL_PATTERN = re.compile(
    r"https?://[^\s<>\"']+",
    re.IGNORECASE,
)

# IPv4 주소 패턴
IP_PATTERN = re.compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
)


# 패턴 목록 (우선순위 순서)
PII_PATTERNS: List[PiiPattern] = [
    PiiPattern(name="email", pattern=EMAIL_PATTERN),
    PiiPattern(name="url", pattern=URL_PATTERN),
    PiiPattern(name="ssn", pattern=SSN_PATTERN),
    PiiPattern(name="card", pattern=CARD_PATTERN),
    PiiPattern(name="phone", pattern=PHONE_PATTERN),
    PiiPattern(name="ip", pattern=IP_PATTERN),
    # 계좌번호는 가장 마지막 (오탐 가능성 높음)
    PiiPattern(name="account", pattern=ACCOUNT_PATTERN),
]


# =============================================================================
# Sanitizer 함수
# =============================================================================


def sanitize_for_matching(
    text: str,
    replacement: str = "<PII>",
    patterns: List[PiiPattern] = None,
) -> str:
    """PII 패턴을 치환하여 매칭 안전성을 높입니다.

    Args:
        text: 원본 텍스트
        replacement: 치환 문자열 (기본: <PII>)
        patterns: 사용할 패턴 목록 (기본: 모든 패턴)

    Returns:
        PII가 치환된 텍스트
    """
    if not text:
        return text

    patterns = patterns or PII_PATTERNS
    result = text

    for pii_pattern in patterns:
        # 각 패턴별 치환
        result = pii_pattern.pattern.sub(replacement, result)

    return result


def sanitize_with_info(
    text: str,
    replacement: str = "<PII>",
    patterns: List[PiiPattern] = None,
) -> Tuple[str, List[str]]:
    """PII 패턴을 치환하고 발견된 패턴 타입을 반환합니다.

    Args:
        text: 원본 텍스트
        replacement: 치환 문자열 (기본: <PII>)
        patterns: 사용할 패턴 목록 (기본: 모든 패턴)

    Returns:
        (치환된 텍스트, 발견된 패턴 이름 목록)
    """
    if not text:
        return text, []

    patterns = patterns or PII_PATTERNS
    result = text
    found_patterns: List[str] = []

    for pii_pattern in patterns:
        # 매칭 확인
        if pii_pattern.pattern.search(result):
            found_patterns.append(pii_pattern.name)
            result = pii_pattern.pattern.sub(replacement, result)

    return result, found_patterns


def has_pii_pattern(text: str, patterns: List[PiiPattern] = None) -> bool:
    """텍스트에 PII 패턴이 있는지 확인합니다.

    Args:
        text: 확인할 텍스트
        patterns: 사용할 패턴 목록 (기본: 모든 패턴)

    Returns:
        PII 패턴이 있으면 True
    """
    if not text:
        return False

    patterns = patterns or PII_PATTERNS

    for pii_pattern in patterns:
        if pii_pattern.pattern.search(text):
            return True

    return False


# =============================================================================
# 통합: ForbiddenQueryFilter에서 사용
# =============================================================================


def sanitize_query_for_forbidden_check(query: str) -> str:
    """금지질문 체크 전 쿼리를 정제합니다.

    이 함수는 ForbiddenQueryFilter.check() 내부에서 사용됩니다.
    fuzzy/embedding 매칭 전에 PII를 제거하여:
    1. 임베딩 입력에서 PII 노출 방지
    2. 매칭 정확도 향상 (숫자/특수문자 노이즈 제거)

    Args:
        query: 원본 쿼리

    Returns:
        정제된 쿼리
    """
    sanitized, found = sanitize_with_info(query)

    if found:
        logger.debug(
            f"PII sanitized for forbidden check: patterns={found}"
        )

    return sanitized
