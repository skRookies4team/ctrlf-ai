"""
텍스트 클리너 - 개행/공백 정리
"""
import logging

logger = logging.getLogger(__name__)


def clean_text(text: str) -> str:
    """
    텍스트 클리닝 수행

    Args:
        text: 원본 텍스트

    Returns:
        str: 클리닝된 텍스트
    """
    if not text:
        return ""

    logger.info(f"Cleaning text. Original length: {len(text)}")

    # 1. \r → \n 치환
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    # 2. 줄 단위로 split → strip() → 완전히 빈 줄은 제거
    lines = text.split('\n')
    cleaned_lines = []

    for line in lines:
        stripped = line.strip()
        # 완전히 빈 줄은 건너뛰기
        if stripped:
            cleaned_lines.append(stripped)

    # 3. 줄바꿈으로 다시 합치기
    text = '\n'.join(cleaned_lines)

    # 4. 양 끝 공백 제거
    text = text.strip()

    logger.info(f"Cleaning completed. Cleaned length: {len(text)}")

    return text


# 향후 확장: 헤더/푸터 제거 등의 후처리 함수를 여기에 추가 가능
def remove_headers_footers(text: str, header_pattern: str = None, footer_pattern: str = None) -> str:
    """
    헤더/푸터 제거 (향후 구현 예정)

    Args:
        text: 텍스트
        header_pattern: 헤더 패턴 (정규식)
        footer_pattern: 푸터 패턴 (정규식)

    Returns:
        str: 처리된 텍스트
    """
    # TODO: 정규식 기반 헤더/푸터 제거 로직 추가
    return text
