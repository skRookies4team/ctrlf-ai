"""
Phase 37: Visual Plan 모듈

씬 정보에서 시각적 요소(title, body, highlight_terms)를 추출하는 deterministic 룰.

VisualPlan:
- title: 씬의 제목 (on_screen_text 또는 caption에서 추출)
- body: 본문 텍스트 (narration에서 추출, 줄여서)
- highlight_terms: 강조할 키워드 목록 (deterministic 룰로 추출)

추출 룰 (LLM 없이 결정적):
1. title: on_screen_text > caption > narration 첫 문장
2. body: narration에서 핵심 문장 (최대 2문장)
3. highlight_terms: 따옴표로 감싼 단어, 대문자 약어, 숫자+단위 조합
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional

from app.services.video_composer import SceneInfo


@dataclass
class VisualPlan:
    """씬의 시각적 계획.

    Attributes:
        scene_id: 씬 ID
        title: 화면에 표시할 제목
        body: 화면에 표시할 본문 (요약)
        highlight_terms: 강조할 키워드 목록
        duration_sec: 씬 길이 (초)
    """

    scene_id: int
    title: str
    body: str
    highlight_terms: List[str] = field(default_factory=list)
    duration_sec: Optional[float] = None


class VisualPlanExtractor:
    """SceneInfo에서 VisualPlan을 추출하는 클래스.

    Deterministic 룰만 사용 (LLM 호출 없음).
    """

    # 강조 키워드 추출 패턴
    # 1. 따옴표로 감싼 텍스트: "키워드", '키워드'
    QUOTED_PATTERN = re.compile(r'["\']([^"\']{2,20})["\']')

    # 2. 대문자 약어 (2자 이상): USB, VPN, API 등
    # Note: \b는 한글과 영어 사이에서 작동하지 않으므로 lookbehind/lookahead 사용
    ACRONYM_PATTERN = re.compile(r'(?<![A-Za-z])([A-Z]{2,10})(?![A-Za-z])')

    # 3. 숫자+단위 조합: 100MB, 30일, 5개 등
    # Note: 한글 텍스트에서도 동작하도록 수정
    NUMBER_UNIT_PATTERN = re.compile(r'(?<!\d)(\d+(?:\.\d+)?(?:MB|GB|TB|KB|일|개|회|초|분|시간|%|원|명|건))(?!\d)')

    # 4. 한글 강조 패턴 (예: **중요**, [핵심])
    EMPHASIS_PATTERN = re.compile(r'\*\*([^*]+)\*\*|\[([^\]]+)\]')

    def __init__(
        self,
        max_title_length: int = 50,
        max_body_length: int = 100,
        max_highlight_terms: int = 5,
    ):
        """추출기 초기화.

        Args:
            max_title_length: 제목 최대 길이
            max_body_length: 본문 최대 길이
            max_highlight_terms: 최대 강조 키워드 수
        """
        self.max_title_length = max_title_length
        self.max_body_length = max_body_length
        self.max_highlight_terms = max_highlight_terms

    def extract(self, scene: SceneInfo) -> VisualPlan:
        """SceneInfo에서 VisualPlan 추출.

        Args:
            scene: 씬 정보

        Returns:
            VisualPlan: 시각적 계획
        """
        # 1. Title 추출
        title = self._extract_title(scene)

        # 2. Body 추출
        body = self._extract_body(scene)

        # 3. Highlight terms 추출
        highlight_terms = self._extract_highlight_terms(scene)

        return VisualPlan(
            scene_id=scene.scene_id,
            title=title,
            body=body,
            highlight_terms=highlight_terms,
            duration_sec=scene.duration_sec,
        )

    def extract_all(self, scenes: List[SceneInfo]) -> List[VisualPlan]:
        """여러 씬에서 VisualPlan 목록 추출.

        Args:
            scenes: 씬 정보 목록

        Returns:
            List[VisualPlan]: 시각적 계획 목록
        """
        return [self.extract(scene) for scene in scenes]

    def _extract_title(self, scene: SceneInfo) -> str:
        """제목 추출.

        우선순위: on_screen_text > caption > narration 첫 문장
        """
        # 1. on_screen_text가 있으면 사용
        if scene.on_screen_text and scene.on_screen_text.strip():
            return self._truncate(scene.on_screen_text.strip(), self.max_title_length)

        # 2. caption이 있으면 사용
        if scene.caption and scene.caption.strip():
            return self._truncate(scene.caption.strip(), self.max_title_length)

        # 3. narration의 첫 문장 사용
        if scene.narration:
            first_sentence = self._get_first_sentence(scene.narration)
            return self._truncate(first_sentence, self.max_title_length)

        return f"씬 {scene.scene_id}"

    def _extract_body(self, scene: SceneInfo) -> str:
        """본문 추출.

        narration에서 핵심 문장 추출 (title과 다른 부분).
        """
        if not scene.narration:
            return ""

        narration = scene.narration.strip()

        # 첫 문장을 제외한 나머지에서 추출
        sentences = self._split_sentences(narration)

        if len(sentences) <= 1:
            # 문장이 1개뿐이면 그대로 사용
            return self._truncate(narration, self.max_body_length)

        # 2번째 문장부터 (최대 2문장)
        body_sentences = sentences[1:3]
        body = " ".join(body_sentences)

        return self._truncate(body, self.max_body_length)

    def _extract_highlight_terms(self, scene: SceneInfo) -> List[str]:
        """강조할 키워드 추출.

        Deterministic 룰:
        1. 따옴표로 감싼 텍스트
        2. 대문자 약어
        3. 숫자+단위 조합
        4. **강조** 또는 [강조] 패턴
        """
        # 모든 텍스트 소스 결합
        all_text = " ".join(
            filter(
                None,
                [scene.narration, scene.caption, scene.on_screen_text],
            )
        )

        if not all_text:
            return []

        terms: List[str] = []

        # 1. 따옴표로 감싼 텍스트
        for match in self.QUOTED_PATTERN.finditer(all_text):
            term = match.group(1).strip()
            if term and term not in terms:
                terms.append(term)

        # 2. 대문자 약어
        for match in self.ACRONYM_PATTERN.finditer(all_text):
            term = match.group(1)
            if term and term not in terms:
                terms.append(term)

        # 3. 숫자+단위 조합
        for match in self.NUMBER_UNIT_PATTERN.finditer(all_text):
            term = match.group(1)
            if term and term not in terms:
                terms.append(term)

        # 4. 강조 패턴 (**강조**, [강조])
        for match in self.EMPHASIS_PATTERN.finditer(all_text):
            term = match.group(1) or match.group(2)
            if term and term.strip() and term.strip() not in terms:
                terms.append(term.strip())

        # 최대 개수로 제한
        return terms[: self.max_highlight_terms]

    def _get_first_sentence(self, text: str) -> str:
        """텍스트에서 첫 문장 추출."""
        sentences = self._split_sentences(text)
        return sentences[0] if sentences else text

    def _split_sentences(self, text: str) -> List[str]:
        """텍스트를 문장으로 분리."""
        # 한국어/영어 문장 구분자: . ! ? 다
        # 단, 숫자 뒤의 점은 제외 (예: 3.14)
        pattern = r'(?<![0-9])(?<!\s[a-zA-Z])(?<!\s)[.!?](?=\s|$)'
        parts = re.split(pattern, text)

        sentences = []
        for part in parts:
            part = part.strip()
            if part:
                sentences.append(part)

        return sentences if sentences else [text.strip()]

    def _truncate(self, text: str, max_length: int) -> str:
        """텍스트를 최대 길이로 자르고 말줄임표 추가."""
        text = text.strip()
        if len(text) <= max_length:
            return text
        return text[: max_length - 3].rstrip() + "..."


# =============================================================================
# Singleton Instance
# =============================================================================


_extractor: Optional[VisualPlanExtractor] = None


def get_visual_plan_extractor() -> VisualPlanExtractor:
    """VisualPlanExtractor 싱글톤 인스턴스 반환."""
    global _extractor
    if _extractor is None:
        _extractor = VisualPlanExtractor()
    return _extractor


def clear_visual_plan_extractor() -> None:
    """VisualPlanExtractor 싱글톤 초기화 (테스트용)."""
    global _extractor
    _extractor = None
