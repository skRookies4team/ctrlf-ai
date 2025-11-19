"""
청킹 - 다양한 청킹 전략 구현
"""
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)


def chunk_text(
    text: str,
    max_chars: int = 1000,
    overlap_chars: int = 200
) -> List[str]:
    """
    텍스트를 character_window 방식으로 청킹

    Args:
        text: 정규화된 텍스트
        max_chars: 청크 최대 문자 수
        overlap_chars: 청크 간 겹침 문자 수

    Returns:
        List[str]: 청크 리스트

    Raises:
        ValueError: max_chars <= overlap_chars인 경우
    """
    # 방어 로직
    if max_chars <= overlap_chars:
        raise ValueError(
            f"max_chars ({max_chars}) must be greater than overlap_chars ({overlap_chars})"
        )

    # 입력 텍스트 길이가 0이면 빈 리스트 반환
    if not text or len(text) == 0:
        logger.warning("Empty text provided for chunking")
        return []

    logger.info(
        f"Chunking text. Length: {len(text)}, max_chars: {max_chars}, overlap: {overlap_chars}"
    )

    chunks = []
    start = 0
    text_length = len(text)

    while start < text_length:
        # end = start + max_chars
        end = start + max_chars

        # 청크 추출
        chunk = text[start:end]
        chunks.append(chunk)

        # 다음 시작 위치 계산
        # start = end - overlap_chars
        start = end - overlap_chars

        # 마지막 청크 도달 시 종료
        if end >= text_length:
            break

    logger.info(f"Created {len(chunks)} chunks")
    return chunks


def chunk_by_paragraphs(
    sections: List[Dict[str, str]],
    max_chars: int = 1000,
    overlap_sections: int = 1
) -> List[str]:
    """
    문단(paragraph) 기반 청킹

    - sections는 apply_structure()의 결과
    - 각 섹션의 content를 문단 단위로 분리
    - 문단을 max_chars 이내로 병합
    - 섹션 간 overlap_sections만큼 겹침 허용

    Args:
        sections: 섹션 리스트 [{"section": "제목", "content": "내용"}, ...]
        max_chars: 청크 최대 문자 수
        overlap_sections: 섹션 간 겹침 개수

    Returns:
        List[str]: 청크 리스트
    """
    from core.structure import split_paragraphs

    logger.info(f"Chunking by paragraphs: {len(sections)} sections, max_chars={max_chars}")

    chunks = []

    for section in sections:
        section_title = section.get("section", "")
        section_content = section.get("content", "")

        # 섹션 내용이 없으면 건너뛰기
        if not section_content.strip():
            continue

        # 문단 분리
        paragraphs = split_paragraphs(section_content, min_paragraph_len=50)

        # 문단을 max_chars 이내로 병합
        current_chunk = ""
        if section_title:
            current_chunk = f"{section_title}\n\n"

        for para in paragraphs:
            # 현재 청크 + 새 문단이 max_chars를 초과하면 청크 완성
            if current_chunk and len(current_chunk) + len(para) + 2 > max_chars:
                chunks.append(current_chunk.strip())
                current_chunk = ""

            # 문단 추가
            if current_chunk:
                current_chunk += "\n\n" + para
            else:
                current_chunk = para

        # 마지막 청크 추가
        if current_chunk.strip():
            chunks.append(current_chunk.strip())

    logger.info(f"Created {len(chunks)} chunks (paragraph-based)")
    return chunks


def chunk_by_headings(
    sections: List[Dict[str, str]],
    max_chars: int = 2000
) -> List[str]:
    """
    제목(heading) 기반 청킹

    - 각 섹션을 하나의 청크로 처리
    - 섹션이 max_chars를 초과하면 character_window로 분할
    - 제목은 각 청크 앞에 포함

    Args:
        sections: 섹션 리스트 [{"section": "제목", "content": "내용"}, ...]
        max_chars: 청크 최대 문자 수 (기본값: 2000, character_window보다 크게 설정)

    Returns:
        List[str]: 청크 리스트
    """
    logger.info(f"Chunking by headings: {len(sections)} sections, max_chars={max_chars}")

    chunks = []

    for section in sections:
        section_title = section.get("section", "")
        section_content = section.get("content", "")

        # 섹션 전체 텍스트
        if section_title:
            full_text = f"{section_title}\n\n{section_content}"
        else:
            full_text = section_content

        # 섹션이 비어있으면 건너뛰기
        if not full_text.strip():
            continue

        # max_chars 이내면 하나의 청크로
        if len(full_text) <= max_chars:
            chunks.append(full_text.strip())
        else:
            # max_chars 초과 시 character_window로 분할
            logger.info(f"Section exceeds max_chars, splitting with character_window")

            # 겹침 없이 분할 (제목이 포함되어 있으므로)
            start = 0
            while start < len(full_text):
                end = start + max_chars
                chunk = full_text[start:end]
                chunks.append(chunk.strip())
                start = end

    logger.info(f"Created {len(chunks)} chunks (heading-based)")
    return chunks
