"""
구조적 정리 - 텍스트 구조 분석 및 정규화
"""
import logging
import re
from typing import List, Dict, Tuple

logger = logging.getLogger(__name__)


def split_paragraphs(text: str, min_paragraph_len: int = 50) -> List[str]:
    """
    텍스트를 문단(paragraph) 단위로 분리

    - 빈 줄(\n\n)을 기준으로 문단 분리
    - 너무 짧은 문단(< min_paragraph_len)은 다음 문단과 병합

    Args:
        text: 입력 텍스트
        min_paragraph_len: 최소 문단 길이 (기본값: 50)

    Returns:
        List[str]: 문단 리스트
    """
    # 빈 줄로 분리
    paragraphs = re.split(r'\n\s*\n', text)

    # 빈 문단 제거 및 공백 정리
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    # 짧은 문단 병합
    merged = []
    buffer = ""

    for para in paragraphs:
        if buffer:
            buffer += "\n\n" + para
        else:
            buffer = para

        # 버퍼가 충분히 길면 추가
        if len(buffer) >= min_paragraph_len:
            merged.append(buffer)
            buffer = ""

    # 남은 버퍼 처리
    if buffer:
        if merged:
            # 마지막 문단에 병합
            merged[-1] += "\n\n" + buffer
        else:
            # 버퍼만 있는 경우
            merged.append(buffer)

    return merged


def detect_headings(text: str) -> List[Tuple[int, str]]:
    """
    텍스트에서 제목(Heading) 패턴 탐지

    탐지 패턴:
    - 1. 2. 3. (숫자 + 마침표)
    - 1.1, 1.2, 2.1 (계층적 번호)
    - 제1장, 제2절, 제3항 (한글 제목)
    - [제1절], [개요] (대괄호)
    - ■, ●, ◆ (특수 기호)

    Args:
        text: 입력 텍스트

    Returns:
        List[Tuple[int, str]]: (줄 번호, 제목 텍스트) 리스트
    """
    lines = text.split('\n')
    headings = []

    # 제목 패턴 정의
    patterns = [
        r'^\s*제\s*\d+\s*장\s+',  # 제 1 장, 제1장
        r'^\s*제\s*\d+\s*조\s+',  # 제 1 조, 제1조
        r'^\s*제\s*\d+\s*[절항편부]\s+',  # 제1절, 제2항
        r'^\s*\d+\.\s+\S',  # 1. 제목
        r'^\s*\d+\.\d+\s+\S',  # 1.1 제목
        r'^\s*\[.+?\]\s*',  # [제목]
        r'^\s*[■●◆]\s+\S',  # ■ 제목
    ]

    for i, line in enumerate(lines):
        line_stripped = line.strip()

        # 빈 줄 무시
        if not line_stripped:
            continue

        # 패턴 매칭
        for pattern in patterns:
            if re.match(pattern, line_stripped):
                headings.append((i, line_stripped))
                break

    return headings


def apply_structure(text: str) -> List[Dict[str, str]]:
    """
    텍스트에 구조를 적용하여 섹션 단위로 분리

    - 제목을 기준으로 섹션 분리
    - 각 섹션은 {"section": "제목", "content": "내용"} 형태
    - 제목이 없으면 전체를 하나의 섹션으로 처리

    Args:
        text: 입력 텍스트

    Returns:
        List[Dict[str, str]]: 섹션 리스트
    """
    logger.info("Applying structure analysis")

    # 제목 탐지
    headings = detect_headings(text)

    # 제목이 없으면 전체를 하나의 섹션으로
    if not headings:
        logger.info("No headings detected, treating as single section")
        return [{"section": "", "content": text}]

    # 줄 단위로 분리
    lines = text.split('\n')
    sections = []

    # 제목 위치를 기준으로 섹션 분리
    for i, (line_num, heading_text) in enumerate(headings):
        # 섹션 시작과 끝 결정
        start_line = line_num

        # 다음 제목이 있으면 그 전까지, 없으면 끝까지
        if i + 1 < len(headings):
            end_line = headings[i + 1][0]
        else:
            end_line = len(lines)

        # 섹션 내용 추출 (제목 제외)
        section_lines = lines[start_line + 1:end_line]
        section_content = '\n'.join(section_lines).strip()

        sections.append({
            "section": heading_text,
            "content": section_content
        })

    # 첫 번째 제목 이전에 내용이 있으면 추가
    if headings[0][0] > 0:
        preamble_lines = lines[:headings[0][0]]
        preamble_content = '\n'.join(preamble_lines).strip()
        if preamble_content:
            sections.insert(0, {
                "section": "",
                "content": preamble_content
            })

    logger.info(f"Detected {len(sections)} sections")
    return sections


def normalize_structure(text: str) -> str:
    """
    텍스트 구조 정규화 (하위 호환성 유지)

    기존 코드와의 호환성을 위해 유지.
    새로운 구조 분석은 apply_structure() 사용.

    Args:
        text: 클리닝된 텍스트

    Returns:
        str: 정규화된 텍스트 (현재는 passthrough)
    """
    logger.info("Normalizing structure (passthrough for compatibility)")
    return text
