"""
Phase 40: 문장 분할 유틸리티 (TTS용)

문장 단위로 텍스트를 분할하여 TTS 합성에 사용합니다.
기존 토큰 청킹(Phase 29)과 충돌하지 않게 "TTS용 분할"로만 사용됩니다.

분할 규칙:
1. 개행(\n) 기준 1차 분할 후 trim
2. . ? ! … 및 한국어 종결 표현("다." "요." "죠." 등) 뒤에서 2차 분할
3. 빈 문장 제거
4. 너무 긴 문장(MAX_SENTENCE_LENGTH 이상)은 쉼표/공백 기준으로 추가 분할

Usage:
    from app.utils.text_splitter import split_sentences

    sentences = split_sentences("첫 번째 문장입니다. 두 번째 문장이에요!")
    # ["첫 번째 문장입니다.", "두 번째 문장이에요!"]
"""

import re
from typing import List

from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Constants
# =============================================================================

# 너무 긴 문장 임계값 (자 기준)
MAX_SENTENCE_LENGTH = 300

# 문장 종결 패턴 (한국어 + 영어)
# - 마침표, 물음표, 느낌표, 말줄임표
# - 한국어 종결 어미 뒤 마침표: 다. 요. 죠. 까. 나. 네. 세. 지. 라. 오. 소.
SENTENCE_END_PATTERN = re.compile(
    r'(?<=[.?!…。])'  # 기본 구두점 뒤
    r'|(?<=[다요죠까나네세지라오소습][.])'  # 한국어 종결 어미 + 마침표
    r'|(?<=[ㅂ니][다])'  # 합니다, 입니다 등 (마침표 없이도 분할 가능)
)

# 긴 문장 분할용 패턴 (쉼표, 세미콜론, 콜론 뒤)
LONG_SENTENCE_SPLIT_PATTERN = re.compile(
    r'(?<=[,;:，；：])\s*'  # 쉼표/세미콜론/콜론 뒤 공백
)

# 공백 기준 분할 (최후의 수단)
SPACE_SPLIT_PATTERN = re.compile(r'\s+')


# =============================================================================
# Main Function
# =============================================================================


def split_sentences(text: str, max_length: int = MAX_SENTENCE_LENGTH) -> List[str]:
    """텍스트를 문장 단위로 분할합니다 (TTS용).

    Args:
        text: 분할할 텍스트
        max_length: 최대 문장 길이 (초과 시 추가 분할)

    Returns:
        List[str]: 분할된 문장 리스트

    Examples:
        >>> split_sentences("안녕하세요. 반갑습니다!")
        ["안녕하세요.", "반갑습니다!"]

        >>> split_sentences("첫째로, 이것이 중요합니다. 둘째로, 저것도 중요해요.")
        ["첫째로, 이것이 중요합니다.", "둘째로, 저것도 중요해요."]
    """
    if not text or not text.strip():
        return []

    # Step 1: 개행 기준 1차 분할
    lines = text.split('\n')
    lines = [line.strip() for line in lines]
    lines = [line for line in lines if line]  # 빈 줄 제거

    # Step 2: 각 라인에서 문장 종결 패턴으로 2차 분할
    sentences = []
    for line in lines:
        # 문장 종결 패턴으로 분할
        parts = _split_by_sentence_end(line)
        sentences.extend(parts)

    # Step 3: 빈 문장 제거 및 정리
    sentences = [s.strip() for s in sentences]
    sentences = [s for s in sentences if s]

    # Step 4: 너무 긴 문장 추가 분할
    final_sentences = []
    for sentence in sentences:
        if len(sentence) > max_length:
            # 긴 문장 분할
            split_parts = _split_long_sentence(sentence, max_length)
            final_sentences.extend(split_parts)
        else:
            final_sentences.append(sentence)

    logger.debug(
        f"Split text into {len(final_sentences)} sentences "
        f"(original length: {len(text)})"
    )

    return final_sentences


def _split_by_sentence_end(text: str) -> List[str]:
    """문장 종결 패턴으로 분할합니다.

    Args:
        text: 분할할 텍스트

    Returns:
        List[str]: 분할된 문장 리스트
    """
    if not text:
        return []

    # 정규식 split은 구분자를 포함하지 않으므로
    # findall로 매칭 위치를 찾고 수동으로 분할

    # 문장 종결 위치 찾기
    endings = []

    # . ? ! … 뒤에서 분할 (공백이 따라오는 경우)
    for match in re.finditer(r'[.?!…。]\s+', text):
        endings.append(match.end())

    # 분할
    if not endings:
        return [text]

    sentences = []
    start = 0
    for end in endings:
        sentence = text[start:end].strip()
        if sentence:
            sentences.append(sentence)
        start = end

    # 마지막 부분
    if start < len(text):
        remaining = text[start:].strip()
        if remaining:
            sentences.append(remaining)

    return sentences if sentences else [text]


def _split_long_sentence(sentence: str, max_length: int) -> List[str]:
    """긴 문장을 쉼표/공백 기준으로 분할합니다.

    Args:
        sentence: 분할할 문장
        max_length: 최대 길이

    Returns:
        List[str]: 분할된 문장 리스트
    """
    if len(sentence) <= max_length:
        return [sentence]

    result = []

    # 1차 시도: 쉼표/세미콜론/콜론 기준 분할
    parts = LONG_SENTENCE_SPLIT_PATTERN.split(sentence)

    current = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue

        if len(current) + len(part) + 2 <= max_length:
            if current:
                current += ", " + part
            else:
                current = part
        else:
            if current:
                result.append(current)
            current = part

    if current:
        result.append(current)

    # 여전히 긴 문장이 있으면 공백 기준으로 추가 분할
    final_result = []
    for part in result:
        if len(part) > max_length:
            # 공백 기준 분할
            words = SPACE_SPLIT_PATTERN.split(part)
            current_chunk = ""

            for word in words:
                if len(current_chunk) + len(word) + 1 <= max_length:
                    if current_chunk:
                        current_chunk += " " + word
                    else:
                        current_chunk = word
                else:
                    if current_chunk:
                        final_result.append(current_chunk)
                    current_chunk = word

            if current_chunk:
                final_result.append(current_chunk)
        else:
            final_result.append(part)

    return final_result if final_result else [sentence]


# =============================================================================
# Helper Functions
# =============================================================================


def count_sentences(text: str) -> int:
    """텍스트의 문장 수를 반환합니다.

    Args:
        text: 텍스트

    Returns:
        int: 문장 수
    """
    return len(split_sentences(text))


def get_sentence_lengths(text: str) -> List[int]:
    """각 문장의 길이를 반환합니다.

    Args:
        text: 텍스트

    Returns:
        List[int]: 문장별 길이 리스트
    """
    sentences = split_sentences(text)
    return [len(s) for s in sentences]


def estimate_tts_duration(
    text: str,
    chars_per_second: float = 2.5,
) -> float:
    """TTS 예상 재생 시간을 계산합니다.

    Args:
        text: 텍스트
        chars_per_second: 초당 문자 수 (한국어 기준 약 2.5)

    Returns:
        float: 예상 재생 시간 (초)
    """
    sentences = split_sentences(text)
    total_chars = sum(len(s) for s in sentences)
    return total_chars / chars_per_second if chars_per_second > 0 else 0.0
