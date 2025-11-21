"""
청킹 - 다양한 청킹 전략 + Smart Chunk (헤더/문단 기반)
"""
import logging
import re
from typing import List, Dict

logger = logging.getLogger(__name__)


# -----------------------------------------------------------
# 1) Character Window Chunking (슬라이딩 윈도우)
# -----------------------------------------------------------
def chunk_text(
    text: str,
    max_chars: int = 1000,
    overlap_chars: int = 200
) -> List[str]:
    """
    텍스트를 character_window 방식으로 청킹
    """
    if max_chars <= overlap_chars:
        raise ValueError(
            f"max_chars ({max_chars}) must be greater than overlap_chars ({overlap_chars})"
        )

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
        end = start + max_chars
        chunk = text[start:end]
        chunks.append(chunk)
        start = end - overlap_chars

        if end >= text_length:
            break

    logger.info(f"Created {len(chunks)} chunks")
    return chunks


# -----------------------------------------------------------
# 2) Paragraph 기반 청킹
# -----------------------------------------------------------
def chunk_by_paragraphs(
    sections: List[Dict[str, str]],
    max_chars: int = 1000,
    overlap_sections: int = 1
) -> List[str]:
    """
    문단(paragraph) 기반 청킹
    """
    from core.structure import split_paragraphs

    logger.info(f"Chunking by paragraphs: {len(sections)} sections, max_chars={max_chars}")

    chunks = []

    for section in sections:
        section_title = section.get("section", "")
        section_content = section.get("content", "")

        if not section_content.strip():
            continue

        paragraphs = split_paragraphs(section_content, min_paragraph_len=50)

        current_chunk = ""
        if section_title:
            current_chunk = f"{section_title}\n\n"

        for para in paragraphs:
            if current_chunk and len(current_chunk) + len(para) + 2 > max_chars:
                chunks.append(current_chunk.strip())
                current_chunk = ""

            if current_chunk:
                current_chunk += "\n\n" + para
            else:
                current_chunk = para

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

    logger.info(f"Created {len(chunks)} chunks (paragraph-based)")
    return chunks


# -----------------------------------------------------------
# 3) Heading 기반 청킹
# -----------------------------------------------------------
def chunk_by_headings(
    sections: List[Dict[str, str]],
    max_chars: int = 2000
) -> List[str]:
    """
    제목(heading) 기반 청킹
    """
    logger.info(f"Chunking by headings: {len(sections)} sections, max_chars={max_chars}")

    chunks = []

    for section in sections:
        section_title = section.get("section", "")
        section_content = section.get("content", "")

        if section_title:
            full_text = f"{section_title}\n\n{section_content}"
        else:
            full_text = section_content

        if not full_text.strip():
            continue

        if len(full_text) <= max_chars:
            chunks.append(full_text.strip())
        else:
            logger.info("Section exceeds max_chars → splitting")

            start = 0
            while start < len(full_text):
                end = start + max_chars
                chunk = full_text[start:end]
                chunks.append(chunk.strip())
                start = end

    logger.info(f"Created {len(chunks)} chunks (heading-based)")
    return chunks


# -----------------------------------------------------------
# 4) Smart Chunk (헤더 + 문단 기반 청킹) ⬅⬅⬅ 네가 요청한 핵심
# -----------------------------------------------------------
def smart_chunk(text: str, max_len: int = 1200) -> List[str]:
    """
    헤더 기반 + 문단 기반 + 길이 제한까지 고려하는 최적 청킹
    """
    # 1. 헤더 기준으로 문서를 큰 섹션으로 분리
    sections = re.split(r"(#+ .*|^[0-9]+\..*)", text, flags=re.MULTILINE)

    chunks = []
    buffer = ""

    for sec in sections:
        if len(sec.strip()) == 0:
            continue

        # 2. 문단 단위 분리
        paragraphs = [p.strip() for p in sec.split("\n\n") if p.strip()]

        for p in paragraphs:
            if len(buffer) + len(p) < max_len:
                buffer += "\n" + p
            else:
                chunks.append(buffer.strip())
                buffer = p

    if buffer:
        chunks.append(buffer.strip())

    logger.info(f"Created {len(chunks)} chunks (smart)")
    return chunks


# -----------------------------------------------------------
# 5) Auto Chunk Strategy (문서 형식 자동 감지)
# -----------------------------------------------------------
def auto_chunk_strategy(text: str, doc_type: str):
    """
    문서 타입에 따라 자동 청킹
    """
    # 스마트 청킹이 가장 우선 → 기본 전략
    if len(text) > 2000:
        return smart_chunk(text)

    if doc_type in ["pdf", "docx", "hwp", "hwpx"]:
        return chunk_by_paragraph(text)

    if doc_type == "image":
        return chunk_by_lines(text)

    if len(text) > 8000:
        return chunk_by_sections(text)

    return chunk_by_default(text)


# -----------------------------------------------------------
# 6) 단순 청킹 전략들 (fallback)
# -----------------------------------------------------------
def chunk_by_paragraph(text):
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def chunk_by_lines(text):
    return [line for line in text.split("\n") if line.strip()]


def chunk_by_sections(text):
    sections = []
    current = []
    for line in text.split("\n"):
        if any(s in line for s in ["Chapter", "SECTION", "섹션", "목차"]):
            if current:
                sections.append("\n".join(current))
                current = []
        current.append(line)

    if current:
        sections.append("\n".join(current))

    return sections


def chunk_by_default(text):
    size = 500
    return [text[i:i + size] for i in range(0, len(text), size)]

def chunk_by_smart_structure(text: str, max_chars: int = 1500):
    """
    목차 기반 + 조항 기반 + 헤딩 기반을 모두 사용하는 정밀 구조 청킹.
    (문서가 법령/매뉴얼/규정/교재일 때 매우 효과적)
    """

    heading_regex = re.compile(
        r"(^제\s*\d+\s*장)|"
        r"(^제\s*\d+\s*절)|"
        r"(^제\s*\d+조)|"
        r"(^제\s*\d+항)|"
        r"(^\d+\.\d+)|"
        r"([①②③④⑤⑥⑦⑧⑨])",
        re.MULTILINE
    )

    sections = []
    current = []

    for line in text.split("\n"):
        if heading_regex.match(line.strip()):
            # 새로운 섹션 시작
            if current:
                sections.append("\n".join(current).strip())
                current = []
        current.append(line)

    if current:
        sections.append("\n".join(current).strip())

    # 섹션이 너무 길면 다시 max_chars로 슬라이싱
    final_chunks = []
    for sec in sections:
        if len(sec) <= max_chars:
            final_chunks.append(sec)
        else:
            # 긴 섹션은 문자 윈도우 방식으로 추가 분할
            for i in range(0, len(sec), max_chars):
                final_chunks.append(sec[i:i+max_chars])

    return final_chunks
