"""
씬 단위 RAG 스크립트 생성기 (Scene-Based RAG Script Generator)

문서를 통째로 프롬프트에 싣지 않고, 씬 단위로 필요한 청크만 검색하여
컨텍스트 제한을 우회하는 스크립트 생성기입니다.

흐름:
1. 아웃라인 생성: 문서 메타데이터로 씬 목차/장면 설계 (1회 LLM)
2. 씬별 RAG 검색 + 생성: 각 씬 키워드로 Top-K 검색 후 씬 스크립트 생성 (N회 LLM)
3. 일관성 다듬기: 씬 요약들로 톤/문체 통일 (1회 LLM, Optional)

장점:
- 문서 길이에 관계없이 컨텍스트 8K 이내 유지
- 각 씬에 사용된 청크 출처 추적 가능 (source_refs)
- FAQ/채팅에서 사용 중인 Top-K RAG 패턴 재활용
"""

import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.clients.llm_client import LLMClient
from app.clients.milvus_client import MilvusSearchClient, get_milvus_client
from app.core.logging import get_logger
from app.models.source_set import (
    GeneratedChapter,
    GeneratedScene,
    GeneratedScript,
    SourceRef,
)

logger = get_logger(__name__)


# =============================================================================
# Phase 55: 한국어 출력 검증 및 강제
# =============================================================================

# 한국어 비율 최소 기준 (narration 텍스트에서 한글 비율)
MIN_KOREAN_RATIO = 0.3  # 30% 이상 한글이어야 함

# 재시도 설정
MAX_KOREAN_RETRY = 1  # 한국어 검증 실패 시 재시도 횟수
RETRY_TEMPERATURE = 0.2  # 재시도 시 더 낮은 temperature


def _count_korean_chars(text: str) -> int:
    """한글 문자 수를 계산합니다."""
    return len(re.findall(r'[\u3131-\u3163\uac00-\ud7a3]', text))


def _is_korean_output(text: str, min_ratio: float = MIN_KOREAN_RATIO) -> bool:
    """텍스트가 충분한 한국어 비율을 가지는지 검사합니다.

    Args:
        text: 검사할 텍스트
        min_ratio: 최소 한국어 비율 (기본 30%)

    Returns:
        한국어 비율이 기준 이상이면 True
    """
    if not text or len(text) < 10:
        return True  # 너무 짧은 텍스트는 통과

    korean_chars = _count_korean_chars(text)
    # 공백 제외한 전체 문자 수
    total_chars = len(re.sub(r'\s', '', text))

    if total_chars == 0:
        return True

    ratio = korean_chars / total_chars
    return ratio >= min_ratio


def _has_english_start(text: str) -> bool:
    """텍스트가 영어 시작 문구로 시작하는지 검사합니다."""
    english_starts = [
        "I'd", "I would", "I can", "I'll", "I will",
        "According to", "Based on", "As per", "Per the",
        "Sure", "Of course", "Thank you", "Thanks",
        "Let me", "Here", "This", "The document",
        "In this", "Welcome", "Hello", "Hi,",
    ]
    text_lower = text.strip().lower()
    return any(text_lower.startswith(e.lower()) for e in english_starts)


# 강화된 한국어 강제 지침 (프롬프트 끝에 추가)
KOREAN_ENFORCEMENT = """

[절대 금지 - 영어 출력]
다음과 같은 영어 문구로 절대 시작하지 마세요:
- "I'd be happy to...", "I can help...", "According to...", "Based on..."
- "Sure", "Of course", "Thank you", "Let me...", "Here is..."

[필수 - 한국어 출력]
- 모든 필드(narration, caption, visual_text, visual_description)는 반드시 한국어로 작성
- 영어 단어/문장 포함 시 실패로 간주됩니다
- 전문 용어만 괄호 안에 영어 병기 허용. 예: 개인정보보호(Privacy)
"""


# =============================================================================
# Phase 55: 실패 원인 표준화 (fail_reason)
# =============================================================================


class FailReason(str, Enum):
    """스크립트 생성 실패 원인 (표준화된 코드)."""
    OUTLINE_PARSE_ERROR = "OUTLINE_PARSE_ERROR"  # 아웃라인 JSON 파싱 실패
    OUTLINE_EMPTY = "OUTLINE_EMPTY"              # 아웃라인 생성 결과 없음
    RETRIEVE_EMPTY = "RETRIEVE_EMPTY"            # RAG 검색 결과 없음
    SCENE_PARSE_ERROR = "SCENE_PARSE_ERROR"      # 씬 JSON 파싱 실패
    NON_KOREAN_OUTPUT = "NON_KOREAN_OUTPUT"      # 한국어 검증 실패
    LLM_ERROR = "LLM_ERROR"                      # LLM API 호출 실패
    UNKNOWN = "UNKNOWN"                          # 기타 알 수 없는 오류


@dataclass
class GenerationMetrics:
    """스크립트 생성 메트릭 (관측용)."""
    outline_ms: float = 0.0           # 아웃라인 생성 시간
    total_retrieve_ms: float = 0.0    # 전체 RAG 검색 시간
    total_scene_llm_ms: float = 0.0   # 전체 씬 LLM 시간
    total_ms: float = 0.0             # 전체 생성 시간
    scene_count: int = 0              # 생성된 씬 수
    failed_scene_count: int = 0       # 실패한 씬 수
    korean_validation_pass: int = 0   # 한국어 검증 통과 수
    korean_validation_fail: int = 0   # 한국어 검증 실패 수
    retry_count: int = 0              # 재시도 횟수
    fail_reasons: List[str] = field(default_factory=list)  # 실패 원인 목록


# =============================================================================
# 데이터 클래스
# =============================================================================


@dataclass
class SceneOutline:
    """씬 아웃라인 (목차/장면 설계)."""
    scene_index: int
    title: str
    purpose: str  # 도입/설명/사례/정리 등
    keywords: List[str]  # RAG 검색용 키워드
    target_duration_sec: float = 30.0


@dataclass
class ChapterOutline:
    """챕터 아웃라인."""
    chapter_index: int
    title: str
    scenes: List[SceneOutline] = field(default_factory=list)


@dataclass
class ScriptOutline:
    """전체 스크립트 아웃라인."""
    title: str
    chapters: List[ChapterOutline] = field(default_factory=list)
    total_scenes: int = 0


@dataclass
class GenerationState:
    """씬 생성 상태 (다음 씬 생성 시 전달)."""
    current_scene_index: int = 0
    tone: str = "친근하고 전문적인"
    key_terms: List[str] = field(default_factory=list)
    last_narration_ending: str = ""


# =============================================================================
# 씬 단위 RAG 스크립트 생성기
# =============================================================================


class SceneBasedScriptGenerator:
    """씬 단위 RAG 스크립트 생성기.

    Attributes:
        _llm_client: LLM 클라이언트
        _milvus_client: Milvus 검색 클라이언트
        _model: 사용할 LLM 모델
        _top_k: RAG 검색 시 가져올 청크 수
    """

    # LLM 호출 설정
    DEFAULT_MODEL = "LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct"
    MAX_TOKENS_OUTLINE = 1500  # 아웃라인 생성용
    MAX_TOKENS_SCENE = 800    # 씬 생성용
    MAX_TOKENS_POLISH = 1000  # 다듬기용

    # RAG 설정
    DEFAULT_TOP_K = 3  # 씬당 검색할 청크 수

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        milvus_client: Optional[MilvusSearchClient] = None,
        model: Optional[str] = None,
        top_k: int = DEFAULT_TOP_K,
    ):
        """초기화.

        Args:
            llm_client: LLM 클라이언트 (None이면 새로 생성)
            milvus_client: Milvus 클라이언트 (None이면 싱글톤 사용)
            model: 사용할 LLM 모델 (None이면 기본값)
            top_k: 씬당 검색할 청크 수
        """
        self._llm_client = llm_client or LLMClient()
        self._milvus_client = milvus_client or get_milvus_client()
        self._model = model or self.DEFAULT_MODEL
        self._top_k = top_k

        logger.info(
            f"SceneBasedScriptGenerator initialized: model={self._model}, top_k={self._top_k}"
        )

    # =========================================================================
    # 메인 API
    # =========================================================================

    async def generate_script(
        self,
        source_set_id: str,
        video_id: str,
        education_id: Optional[str],
        documents: List[Dict[str, Any]],
        document_chunks: Dict[str, List[Dict[str, Any]]],
    ) -> GeneratedScript:
        """씬 단위 RAG로 스크립트를 생성합니다.

        Args:
            source_set_id: 소스셋 ID
            video_id: 비디오 ID
            education_id: 교육 ID (선택)
            documents: 문서 정보 리스트 [{document_id, title, domain}, ...]
            document_chunks: 문서별 청크 {doc_id: [{chunk_index, chunk_text}, ...]}

        Returns:
            GeneratedScript: 생성된 스크립트
        """
        script_id = f"script-{uuid.uuid4().hex[:12]}"
        start_time = time.perf_counter()

        # Phase 55: 메트릭 초기화
        metrics = GenerationMetrics()

        logger.info(
            f"Starting scene-based script generation: "
            f"source_set_id={source_set_id}, documents={len(documents)}"
        )

        # 문서 메타데이터 준비
        doc_titles = [d.get("title", "문서") for d in documents]
        doc_ids = [d.get("document_id", "") for d in documents]

        # 전체 청크 텍스트 준비 (검색용 인덱스)
        all_chunks = self._prepare_chunk_index(document_chunks)

        try:
            # 1단계: 아웃라인 생성
            logger.info("Step 1: Generating outline...")
            outline_start = time.perf_counter()
            outline = await self._generate_outline(doc_titles, all_chunks)
            metrics.outline_ms = (time.perf_counter() - outline_start) * 1000

            if not outline or not outline.chapters:
                metrics.fail_reasons.append(FailReason.OUTLINE_EMPTY.value)
                logger.warning(
                    f"Outline generation failed: fail_reason={FailReason.OUTLINE_EMPTY.value}, "
                    f"outline_ms={metrics.outline_ms:.0f}"
                )
                return self._generate_fallback_script(
                    script_id, source_set_id, education_id, doc_titles
                )

            logger.info(
                f"Outline generated: {len(outline.chapters)} chapters, "
                f"{outline.total_scenes} scenes, outline_ms={metrics.outline_ms:.0f}"
            )

            # 2단계: 씬별 스크립트 생성
            logger.info("Step 2: Generating scenes with RAG...")
            chapters, scene_metrics = await self._generate_scenes_with_rag_metrics(
                outline, all_chunks, doc_ids
            )

            # 메트릭 병합
            metrics.total_retrieve_ms = scene_metrics.total_retrieve_ms
            metrics.total_scene_llm_ms = scene_metrics.total_scene_llm_ms
            metrics.scene_count = scene_metrics.scene_count
            metrics.failed_scene_count = scene_metrics.failed_scene_count
            metrics.korean_validation_pass = scene_metrics.korean_validation_pass
            metrics.korean_validation_fail = scene_metrics.korean_validation_fail
            metrics.retry_count = scene_metrics.retry_count
            metrics.fail_reasons.extend(scene_metrics.fail_reasons)

            if not chapters:
                logger.warning(
                    f"Scene generation failed: fail_reasons={metrics.fail_reasons}"
                )
                return self._generate_fallback_script(
                    script_id, source_set_id, education_id, doc_titles
                )

            # 3단계: 일관성 다듬기 (Optional - 현재는 스킵)
            # chapters = await self._polish_script(chapters)

            # 최종 스크립트 조립
            total_duration = sum(ch.duration_sec for ch in chapters)
            metrics.total_ms = (time.perf_counter() - start_time) * 1000

            script = GeneratedScript(
                script_id=script_id,
                education_id=education_id,
                source_set_id=source_set_id,
                title=outline.title,
                total_duration_sec=total_duration,
                version=1,
                llm_model=self._model,
                chapters=chapters,
            )

            # Phase 55: 최종 메트릭 로그
            logger.info(
                f"Script generation completed: script_id={script_id}, "
                f"chapters={len(chapters)}, duration={total_duration:.0f}s | "
                f"METRICS: outline_ms={metrics.outline_ms:.0f}, "
                f"retrieve_ms={metrics.total_retrieve_ms:.0f}, "
                f"scene_llm_ms={metrics.total_scene_llm_ms:.0f}, "
                f"total_ms={metrics.total_ms:.0f} | "
                f"scenes={metrics.scene_count}, failed={metrics.failed_scene_count}, "
                f"korean_pass={metrics.korean_validation_pass}, "
                f"korean_fail={metrics.korean_validation_fail}, "
                f"retries={metrics.retry_count}"
            )

            return script

        except Exception as e:
            metrics.total_ms = (time.perf_counter() - start_time) * 1000
            metrics.fail_reasons.append(FailReason.UNKNOWN.value)
            logger.exception(
                f"Script generation failed: {e} | "
                f"fail_reasons={metrics.fail_reasons}, total_ms={metrics.total_ms:.0f}"
            )
            return self._generate_fallback_script(
                script_id, source_set_id, education_id, doc_titles
            )

    # =========================================================================
    # 1단계: 아웃라인 생성
    # =========================================================================

    async def _generate_outline(
        self,
        doc_titles: List[str],
        all_chunks: List[Dict[str, Any]],
    ) -> Optional[ScriptOutline]:
        """문서 메타데이터로 씬 아웃라인을 생성합니다.

        Args:
            doc_titles: 문서 제목 리스트
            all_chunks: 전체 청크 리스트 (핵심 키워드 추출용)

        Returns:
            ScriptOutline 또는 None
        """
        # 문서 요약 정보 추출 (첫 500자 정도만)
        doc_summaries = []
        for title in doc_titles:
            doc_summaries.append(f"- {title}")

        # 청크에서 핵심 키워드 추출 (처음 3개 청크만)
        sample_content = ""
        for chunk in all_chunks[:3]:
            sample_content += chunk.get("text", "")[:300] + "\n"

        system_prompt = """당신은 한국 기업의 법정의무교육 영상 스크립트 기획 전문가입니다.
주어진 문서 정보를 바탕으로 교육 영상의 씬 아웃라인(목차)을 JSON 형식으로 생성해주세요.

[중요] 모든 출력은 반드시 한국어로 작성하세요. 영어 사용 금지.

출력 JSON 스키마:
{
  "title": "사내 보안 교육",
  "chapters": [
    {
      "chapter_index": 0,
      "title": "보안사고 예방의 중요성",
      "scenes": [
        {
          "scene_index": 0,
          "title": "최근 보안사고 사례",
          "purpose": "도입",
          "keywords": ["보안사고", "정보유출", "예방"],
          "target_duration_sec": 30
        }
      ]
    }
  ]
}

규칙:
1. 전체 3-5개 챕터, 챕터당 2-4개 씬으로 구성
2. keywords는 해당 씬에서 다룰 핵심 내용을 검색할 수 있는 한국어 키워드 2-3개
3. 각 씬은 20-40초 분량으로 설계
4. 반드시 유효한 JSON만 출력 (설명 없이)
5. title, chapters, scenes의 모든 텍스트는 반드시 한국어로 작성
""" + KOREAN_ENFORCEMENT

        user_prompt = f"""다음 교육 자료의 씬 아웃라인을 생성해주세요.

문서 목록:
{chr(10).join(doc_summaries)}

문서 내용 샘플:
{sample_content[:800]}

JSON 아웃라인:"""

        try:
            response = await self._llm_client.generate_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=self._model,
                temperature=0.3,
                max_tokens=self.MAX_TOKENS_OUTLINE,
            )

            outline_json = self._parse_json(response)
            if not outline_json:
                return None

            return self._parse_outline(outline_json)

        except Exception as e:
            logger.error(f"Outline generation failed: {e}")
            return None

    def _parse_outline(self, data: Dict[str, Any]) -> Optional[ScriptOutline]:
        """JSON을 ScriptOutline으로 파싱합니다."""
        try:
            chapters = []
            total_scenes = 0

            for ch_data in data.get("chapters", []):
                scenes = []
                for sc_data in ch_data.get("scenes", []):
                    scene = SceneOutline(
                        scene_index=sc_data.get("scene_index", len(scenes)),
                        title=sc_data.get("title", ""),
                        purpose=sc_data.get("purpose", "설명"),
                        keywords=sc_data.get("keywords", []),
                        target_duration_sec=sc_data.get("target_duration_sec", 30),
                    )
                    scenes.append(scene)
                    total_scenes += 1

                chapter = ChapterOutline(
                    chapter_index=ch_data.get("chapter_index", len(chapters)),
                    title=ch_data.get("title", f"챕터 {len(chapters) + 1}"),
                    scenes=scenes,
                )
                chapters.append(chapter)

            return ScriptOutline(
                title=data.get("title", "교육 스크립트"),
                chapters=chapters,
                total_scenes=total_scenes,
            )

        except Exception as e:
            logger.error(f"Outline parsing failed: {e}")
            return None

    # =========================================================================
    # 2단계: 씬별 RAG 검색 + 스크립트 생성
    # =========================================================================

    async def _generate_scenes_with_rag(
        self,
        outline: ScriptOutline,
        all_chunks: List[Dict[str, Any]],
        doc_ids: List[str],
    ) -> List[GeneratedChapter]:
        """각 씬에 대해 RAG 검색 후 스크립트를 생성합니다.

        Args:
            outline: 씬 아웃라인
            all_chunks: 전체 청크 리스트
            doc_ids: 문서 ID 리스트

        Returns:
            GeneratedChapter 리스트
        """
        state = GenerationState()
        chapters = []
        global_scene_index = 0

        for ch_outline in outline.chapters:
            scenes = []
            chapter_duration = 0

            for sc_outline in ch_outline.scenes:
                # 씬 키워드로 관련 청크 검색
                relevant_chunks = await self._search_chunks_for_scene(
                    sc_outline, all_chunks
                )

                # 씬 스크립트 생성
                scene = await self._generate_single_scene(
                    sc_outline,
                    relevant_chunks,
                    state,
                    outline.title,
                    ch_outline.title,
                )

                if scene:
                    scenes.append(scene)
                    chapter_duration += scene.duration_sec

                    # 상태 업데이트
                    state.current_scene_index = global_scene_index
                    state.last_narration_ending = scene.narration[-50:] if scene.narration else ""

                global_scene_index += 1

            if scenes:
                chapter = GeneratedChapter(
                    chapter_index=ch_outline.chapter_index,
                    title=ch_outline.title,
                    duration_sec=chapter_duration,
                    scenes=scenes,
                )
                chapters.append(chapter)

        return chapters

    async def _generate_scenes_with_rag_metrics(
        self,
        outline: ScriptOutline,
        all_chunks: List[Dict[str, Any]],
        doc_ids: List[str],
    ) -> Tuple[List[GeneratedChapter], GenerationMetrics]:
        """Phase 55: 메트릭 수집과 함께 씬을 생성합니다.

        Args:
            outline: 씬 아웃라인
            all_chunks: 전체 청크 리스트
            doc_ids: 문서 ID 리스트

        Returns:
            Tuple[GeneratedChapter 리스트, GenerationMetrics]
        """
        state = GenerationState()
        chapters = []
        metrics = GenerationMetrics()
        global_scene_index = 0

        for ch_outline in outline.chapters:
            scenes = []
            chapter_duration = 0

            for sc_outline in ch_outline.scenes:
                # 씬 키워드로 관련 청크 검색
                retrieve_start = time.perf_counter()
                relevant_chunks = await self._search_chunks_for_scene(
                    sc_outline, all_chunks
                )
                retrieve_ms = (time.perf_counter() - retrieve_start) * 1000
                metrics.total_retrieve_ms += retrieve_ms

                if not relevant_chunks:
                    metrics.fail_reasons.append(FailReason.RETRIEVE_EMPTY.value)
                    logger.warning(
                        f"Scene '{sc_outline.title}': RETRIEVE_EMPTY, retrieve_ms={retrieve_ms:.0f}"
                    )

                # 씬 스크립트 생성
                scene_start = time.perf_counter()
                scene, scene_fail_reason, retries, korean_passed = await self._generate_single_scene_with_metrics(
                    sc_outline,
                    relevant_chunks,
                    state,
                    outline.title,
                    ch_outline.title,
                )
                scene_llm_ms = (time.perf_counter() - scene_start) * 1000
                metrics.total_scene_llm_ms += scene_llm_ms

                # 메트릭 업데이트
                metrics.scene_count += 1
                metrics.retry_count += retries

                if korean_passed:
                    metrics.korean_validation_pass += 1
                else:
                    metrics.korean_validation_fail += 1

                if scene:
                    scenes.append(scene)
                    chapter_duration += scene.duration_sec

                    # 상태 업데이트
                    state.current_scene_index = global_scene_index
                    state.last_narration_ending = scene.narration[-50:] if scene.narration else ""

                    logger.debug(
                        f"Scene '{sc_outline.title}' generated: "
                        f"retrieve_ms={retrieve_ms:.0f}, llm_ms={scene_llm_ms:.0f}, "
                        f"chunks={len(relevant_chunks)}, narration_len={len(scene.narration)}"
                    )
                else:
                    metrics.failed_scene_count += 1
                    if scene_fail_reason:
                        metrics.fail_reasons.append(scene_fail_reason)

                global_scene_index += 1

            if scenes:
                chapter = GeneratedChapter(
                    chapter_index=ch_outline.chapter_index,
                    title=ch_outline.title,
                    duration_sec=chapter_duration,
                    scenes=scenes,
                )
                chapters.append(chapter)

        return chapters, metrics

    async def _generate_single_scene_with_metrics(
        self,
        scene_outline: SceneOutline,
        relevant_chunks: List[Dict[str, Any]],
        state: GenerationState,
        script_title: str,
        chapter_title: str,
    ) -> Tuple[Optional[GeneratedScene], Optional[str], int, bool]:
        """Phase 55: 메트릭과 함께 단일 씬을 생성합니다.

        Returns:
            Tuple[씬 또는 None, 실패원인 또는 None, 재시도 횟수, 한국어 검증 통과 여부]
        """
        scene = await self._generate_single_scene(
            scene_outline, relevant_chunks, state, script_title, chapter_title
        )

        if scene:
            # 성공: 재시도 없이 한국어 검증 통과
            return scene, None, 0, True
        else:
            # 실패: NON_KOREAN_OUTPUT으로 가정 (재시도 했으나 실패)
            return None, FailReason.NON_KOREAN_OUTPUT.value, MAX_KOREAN_RETRY, False

    async def _search_chunks_for_scene(
        self,
        scene: SceneOutline,
        all_chunks: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """씬 키워드로 관련 청크를 검색합니다.

        Milvus 벡터 검색을 사용하여 의미적으로 관련된 청크를 찾습니다.

        Args:
            scene: 씬 아웃라인
            all_chunks: 전체 청크 리스트 (폴백용)

        Returns:
            관련 청크 리스트
        """
        # 검색 쿼리 구성: 씬 제목 + 키워드
        query = f"{scene.title} {' '.join(scene.keywords)}"

        try:
            # Milvus 벡터 검색
            results = await self._milvus_client.search(
                query=query,
                top_k=self._top_k,
            )

            if results:
                logger.debug(
                    f"RAG search for scene '{scene.title}': {len(results)} chunks found"
                )
                return [
                    {
                        "doc_id": r.get("doc_id", ""),
                        "chunk_index": r.get("metadata", {}).get("chunk_id", 0),
                        "text": r.get("content", ""),
                        "score": r.get("score", 0),
                    }
                    for r in results
                ]

        except Exception as e:
            logger.warning(f"Milvus search failed, using keyword fallback: {e}")

        # 폴백: 키워드 기반 텍스트 매칭
        return self._keyword_search_fallback(scene.keywords, all_chunks)

    def _keyword_search_fallback(
        self,
        keywords: List[str],
        all_chunks: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """키워드 기반 폴백 검색."""
        scored_chunks = []

        for chunk in all_chunks:
            text = chunk.get("text", "").lower()
            score = sum(1 for kw in keywords if kw.lower() in text)
            if score > 0:
                scored_chunks.append((score, chunk))

        # 점수 높은 순으로 정렬
        scored_chunks.sort(key=lambda x: x[0], reverse=True)

        return [chunk for _, chunk in scored_chunks[:self._top_k]]

    async def _generate_single_scene(
        self,
        scene_outline: SceneOutline,
        relevant_chunks: List[Dict[str, Any]],
        state: GenerationState,
        script_title: str,
        chapter_title: str,
    ) -> Optional[GeneratedScene]:
        """단일 씬 스크립트를 생성합니다.

        Args:
            scene_outline: 씬 아웃라인
            relevant_chunks: 관련 청크 리스트
            state: 생성 상태
            script_title: 스크립트 제목
            chapter_title: 챕터 제목

        Returns:
            GeneratedScene 또는 None
        """
        # 청크 텍스트 준비 (컨텍스트)
        chunk_context = ""
        source_refs = []

        for i, chunk in enumerate(relevant_chunks):
            chunk_text = chunk.get("text", "")[:500]  # 청크당 최대 500자
            chunk_context += f"[근거 {i+1}] {chunk_text}\n\n"

            source_refs.append(SourceRef(
                document_id=chunk.get("doc_id", "unknown"),
                chunk_index=chunk.get("chunk_index", 0),
            ))

        system_prompt = f"""당신은 한국 기업의 법정의무교육 영상 스크립트 작성 전문가입니다.
주어진 근거 자료를 바탕으로 하나의 씬(장면)에 대한 완전한 영상 스크립트를 JSON으로 생성해주세요.

[중요] 모든 출력은 반드시 한국어로 작성하세요. 영어 사용 금지.

현재 스크립트: {script_title}
현재 챕터: {chapter_title}
톤/스타일: {state.tone}

출력 JSON 스키마 (한국어 예시):
{{
  "narration": "안녕하세요, 여러분. 오늘은 사내 보안의 중요성에 대해 알아보겠습니다. 최근 우리 회사에서는 외부 AI 서비스 사용으로 인한 정보 유출 사고가 발생했습니다.",
  "caption": "사내 보안 교육 시작",
  "visual_type": "KEY_POINTS",
  "visual_text": "1. 보안사고 예방\\n2. 정보 유출 방지\\n3. 안전한 AI 사용",
  "highlight_terms": ["보안사고", "정보유출", "AI 사용"],
  "visual_description": "핵심 포인트 3가지가 순차적으로 나타나는 애니메이션",
  "transition": "fade",
  "duration_sec": {scene_outline.target_duration_sec}
}}

필드 설명:
- narration: TTS로 읽을 나레이션 텍스트 (150-250자)
- caption: 화면 하단에 표시할 자막 (30자 이내)
- visual_type: 시각 자료 유형 (TITLE_SLIDE|KEY_POINTS|COMPARISON|DIAGRAM|EXAMPLE|WARNING|SUMMARY)
- visual_text: 화면에 표시할 텍스트 (bullet point는 \\n으로 구분)
- highlight_terms: 강조 표시할 핵심 용어 (3-5개)
- visual_description: 영상 편집자를 위한 시각 자료 설명
- transition: 화면 전환 효과 (fade|slide|zoom|none)
- duration_sec: 씬 길이 (초)

규칙:
1. 나레이션은 자연스러운 한국어 구어체로 작성
2. 근거 자료의 내용만 사용 (창작 금지)
3. visual_type은 씬 목적에 맞게 선택:
   - 도입: TITLE_SLIDE
   - 설명: KEY_POINTS 또는 DIAGRAM
   - 비교: COMPARISON
   - 사례: EXAMPLE
   - 주의: WARNING
   - 정리: SUMMARY
4. highlight_terms는 나레이션에 등장하는 핵심 용어만 포함
5. 반드시 유효한 JSON만 출력
""" + KOREAN_ENFORCEMENT

        user_prompt = f"""씬 정보:
- 제목: {scene_outline.title}
- 목적: {scene_outline.purpose}
- 목표 길이: {scene_outline.target_duration_sec}초

근거 자료:
{chunk_context if chunk_context else "(근거 자료 없음 - 일반적인 내용으로 작성)"}

JSON 스크립트:"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # Phase 55: 한국어 검증 및 재시도 로직
        attempt = 0
        max_attempts = 1 + MAX_KOREAN_RETRY
        temperature = 0.4

        while attempt < max_attempts:
            try:
                response = await self._llm_client.generate_chat_completion(
                    messages=messages,
                    model=self._model,
                    temperature=temperature,
                    max_tokens=self.MAX_TOKENS_SCENE,
                )

                scene_json = self._parse_json(response)
                if not scene_json:
                    attempt += 1
                    temperature = RETRY_TEMPERATURE
                    continue

                narration = scene_json.get("narration", "")

                # 한국어 검증
                is_korean = _is_korean_output(narration)
                has_english = _has_english_start(narration)

                if not is_korean or has_english:
                    korean_ratio = _count_korean_chars(narration) / max(1, len(narration))
                    logger.warning(
                        f"Scene '{scene_outline.title}' failed Korean validation: "
                        f"korean_ratio={korean_ratio:.2%}, english_start={has_english}, "
                        f"attempt={attempt + 1}/{max_attempts}"
                    )
                    attempt += 1
                    temperature = RETRY_TEMPERATURE
                    continue

                # 검증 통과
                return GeneratedScene(
                    scene_index=scene_outline.scene_index,
                    purpose=scene_outline.purpose,
                    narration=narration,
                    caption=scene_json.get("caption"),
                    visual=scene_json.get("visual_description"),  # 레거시 호환
                    visual_type=scene_json.get("visual_type"),
                    visual_text=scene_json.get("visual_text"),
                    visual_description=scene_json.get("visual_description"),
                    highlight_terms=scene_json.get("highlight_terms", []),
                    transition=scene_json.get("transition"),
                    duration_sec=int(scene_json.get("duration_sec", scene_outline.target_duration_sec)),
                    confidence_score=0.8 if attempt == 0 else 0.6,
                    source_refs=source_refs,
                )

            except Exception as e:
                logger.error(f"Scene generation attempt {attempt + 1} failed: {e}")
                attempt += 1
                temperature = RETRY_TEMPERATURE

        # 모든 시도 실패
        logger.error(
            f"Scene '{scene_outline.title}' FAILED after {max_attempts} attempts - NON_KOREAN_OUTPUT"
        )
        return None

    # =========================================================================
    # 유틸리티
    # =========================================================================

    def _prepare_chunk_index(
        self,
        document_chunks: Dict[str, List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """문서별 청크를 검색용 인덱스로 준비합니다."""
        all_chunks = []

        for doc_id, chunks in document_chunks.items():
            for chunk in chunks:
                all_chunks.append({
                    "doc_id": doc_id,
                    "chunk_index": chunk.get("chunk_index", 0),
                    "text": chunk.get("chunk_text", ""),
                })

        return all_chunks

    def _parse_json(self, response: str) -> Optional[Dict[str, Any]]:
        """LLM 응답에서 JSON을 파싱합니다."""
        import json
        import re

        # JSON 블록 추출 시도
        json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
        if json_match:
            response = json_match.group(1)

        # { } 블록 추출
        brace_match = re.search(r'\{.*\}', response, re.DOTALL)
        if brace_match:
            response = brace_match.group(0)

        try:
            return json.loads(response)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error: {e}")
            return None

    def _generate_fallback_script(
        self,
        script_id: str,
        source_set_id: str,
        education_id: Optional[str],
        doc_titles: List[str],
    ) -> GeneratedScript:
        """생성 실패 시 폴백 스크립트를 반환합니다."""
        title = doc_titles[0] if doc_titles else "교육 스크립트"

        scenes = [
            GeneratedScene(
                scene_index=0,
                purpose="도입",
                narration=f"{title}에 대한 교육을 시작합니다.",
                caption="교육 시작",
                visual="타이틀 슬라이드",
                duration_sec=15,
                confidence_score=0.3,
                source_refs=[],
            ),
        ]

        chapters = [
            GeneratedChapter(
                chapter_index=0,
                title="교육 내용",
                duration_sec=15,
                scenes=scenes,
            ),
        ]

        return GeneratedScript(
            script_id=script_id,
            education_id=education_id,
            source_set_id=source_set_id,
            title=f"{title} (자동 생성 실패 - 폴백)",
            total_duration_sec=15,
            version=1,
            llm_model="fallback",
            chapters=chapters,
        )


# =============================================================================
# 싱글턴
# =============================================================================


_generator: Optional[SceneBasedScriptGenerator] = None


def get_scene_based_script_generator() -> SceneBasedScriptGenerator:
    """SceneBasedScriptGenerator 싱글톤 인스턴스를 반환합니다."""
    global _generator
    if _generator is None:
        _generator = SceneBasedScriptGenerator()
    return _generator


def clear_scene_based_script_generator() -> None:
    """싱글톤 인스턴스를 초기화합니다 (테스트용)."""
    global _generator
    _generator = None
