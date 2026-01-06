"""
Search Merger - 검색 결과 병합 및 순위 조정

일반 검색 + doc_id filter 검색 결과를 병합하고 순위 조정.

설계 원칙 (E):
- fallback_with_boost(doc_id filter)는 단독 반환 금지
- 항상 병합 후보로만 사용
- 점수 가산 대신 순위 기반 승급 (rank bump)

품질 게이트 (F):
- top1 점수 하한
- top1-top2 격차
- keyword coverage (보조)
"""

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.chat import ChatSource
    from app.models.conversation_state import ConversationState

logger = logging.getLogger(__name__)


# =============================================================================
# 설정
# =============================================================================

@dataclass
class SearchMergerConfig:
    """검색 병합 설정"""

    # 병합 활성화
    merge_enabled: bool = True

    # Rank bump 설정
    rank_bump_max: int = 2  # 최대 순위 승급 칸 수
    rank_bump_protected_top: int = 1  # 보호할 상위 순위 (1등은 건드리지 않음)

    # 품질 게이트
    quality_top1_threshold: float = 0.55
    quality_gap_threshold: float = 0.05
    quality_coverage_threshold: float = 0.3  # 보조 지표


def get_search_merger_config() -> SearchMergerConfig:
    """설정에서 SearchMergerConfig 로드"""
    try:
        from app.core.config import get_settings
        settings = get_settings()
        return SearchMergerConfig(
            merge_enabled=getattr(settings, "SEARCH_MERGE_ENABLED", True),
            rank_bump_max=getattr(settings, "SEARCH_RANK_BUMP_MAX", 2),
            quality_top1_threshold=getattr(settings, "QUALITY_TOP1_THRESHOLD", 0.55),
            quality_gap_threshold=getattr(settings, "QUALITY_GAP_THRESHOLD", 0.05),
        )
    except Exception:
        return SearchMergerConfig()


# =============================================================================
# 품질 평가 (F)
# =============================================================================

class QualityAction(Enum):
    """품질에 따른 액션"""

    PROCEED = "proceed"          # 정상 진행
    FALLBACK_BOOST = "fallback"  # 부스팅 재검색
    CLARIFY = "clarify"          # 되묻기 고려


@dataclass
class QualityAssessment:
    """검색 결과 품질 평가"""

    # 지표 1: Top1 점수
    top1_score: float

    # 지표 2: Top1-Top2 격차
    score_gap: float

    # 지표 3: Keyword coverage (보조)
    keyword_coverage: float

    # 설정
    config: SearchMergerConfig = field(default_factory=SearchMergerConfig)

    @property
    def is_low_quality(self) -> bool:
        """저품질 여부 판정"""
        # 점수 기반 (primary)
        if self.top1_score < self.config.quality_top1_threshold:
            return True

        # 격차 기반 (애매한 결과)
        if (
            self.score_gap < self.config.quality_gap_threshold
            and self.top1_score < 0.7
        ):
            return True

        return False

    @property
    def action(self) -> QualityAction:
        """품질에 따른 액션 결정"""
        if self.top1_score < 0.4:
            return QualityAction.FALLBACK_BOOST
        elif self.is_low_quality:
            return QualityAction.CLARIFY
        else:
            return QualityAction.PROCEED

    def __repr__(self) -> str:
        return (
            f"QualityAssessment(top1={self.top1_score:.3f}, "
            f"gap={self.score_gap:.3f}, "
            f"coverage={self.keyword_coverage:.2f}, "
            f"action={self.action.value})"
        )


def assess_quality(
    results: List["ChatSource"],
    query: str,
    config: Optional[SearchMergerConfig] = None,
) -> QualityAssessment:
    """
    검색 결과 품질 평가

    Args:
        results: 검색 결과 목록
        query: 원본 쿼리
        config: 설정

    Returns:
        QualityAssessment: 품질 평가 결과
    """
    config = config or get_search_merger_config()

    if not results:
        return QualityAssessment(
            top1_score=0.0,
            score_gap=0.0,
            keyword_coverage=0.0,
            config=config,
        )

    # Top1, Top2 점수
    top1_score = results[0].score or 0.0
    top2_score = results[1].score if len(results) > 1 and results[1].score else 0.0
    score_gap = top1_score - top2_score

    # Keyword coverage
    keywords = extract_keywords(query)
    coverage = calculate_keyword_coverage(results[:3], keywords)

    return QualityAssessment(
        top1_score=top1_score,
        score_gap=score_gap,
        keyword_coverage=coverage,
        config=config,
    )


def extract_keywords(query: str) -> List[str]:
    """쿼리에서 키워드 추출 (간단한 휴리스틱)"""
    # 불용어 제거
    stopwords = {
        "이", "가", "은", "는", "을", "를", "의", "에", "에서", "로", "으로",
        "와", "과", "하고", "그리고", "또는", "및", "대한", "대해", "것",
        "수", "등", "뭐", "어떻게", "왜", "언제", "어디", "무엇", "알려",
        "해줘", "줘", "주세요", "알고", "싶어", "궁금",
    }

    # 단어 분리
    words = re.findall(r"[가-힣a-zA-Z0-9]+", query)

    # 불용어 제거 + 2자 이상
    keywords = [w for w in words if w not in stopwords and len(w) >= 2]

    return keywords


def calculate_keyword_coverage(
    results: List["ChatSource"],
    keywords: List[str],
) -> float:
    """키워드 커버리지 계산"""
    if not keywords:
        return 1.0  # 키워드 없으면 패스

    # 상위 결과의 텍스트 결합
    combined_text = " ".join(
        (r.snippet or "") + " " + (r.title or "")
        for r in results
    ).lower()

    # 키워드 매칭
    matched = sum(1 for kw in keywords if kw.lower() in combined_text)

    return matched / len(keywords)


# =============================================================================
# 검색 결과 병합 (E)
# =============================================================================

def merge_results(
    general_results: List["ChatSource"],
    filter_results: List["ChatSource"],
) -> List["ChatSource"]:
    """
    일반 검색 + filter 검색 결과 병합

    중복 제거하고 score 기준 정렬.
    동점 시 general 결과 우선.

    Args:
        general_results: 일반 검색 결과
        filter_results: doc_id filter 검색 결과

    Returns:
        List[ChatSource]: 병합된 결과
    """
    seen_ids: Set[str] = set()
    merged: List["ChatSource"] = []

    # General 결과 먼저 추가 (우선순위 높음)
    for r in general_results:
        doc_id = _get_doc_id(r)
        if doc_id not in seen_ids:
            seen_ids.add(doc_id)
            merged.append(r)

    # Filter 결과 추가 (중복 제외)
    for r in filter_results:
        doc_id = _get_doc_id(r)
        if doc_id not in seen_ids:
            seen_ids.add(doc_id)
            merged.append(r)

    # Score 기준 정렬 (내림차순)
    merged.sort(key=lambda x: x.score or 0.0, reverse=True)

    logger.debug(
        f"Merged results: general={len(general_results)}, "
        f"filter={len(filter_results)} → merged={len(merged)}"
    )

    return merged


def _get_doc_id(source: "ChatSource") -> str:
    """ChatSource에서 doc_id 추출"""
    # doc_id 필드가 있으면 사용, 없으면 title 기반 해시
    if hasattr(source, "doc_id") and source.doc_id:
        return source.doc_id
    return source.title or str(id(source))


# =============================================================================
# Rank Bump
# =============================================================================

def apply_rank_bump(
    results: List["ChatSource"],
    target_doc_id: str,
    config: Optional[SearchMergerConfig] = None,
) -> List["ChatSource"]:
    """
    순위 기반 승급 (점수 가산 아님)

    target_doc_id가 결과에 있으면 최대 N칸 위로 이동.
    상위 M개는 보호 (건드리지 않음).

    Args:
        results: 검색 결과
        target_doc_id: 승급할 문서 ID
        config: 설정

    Returns:
        List[ChatSource]: 순위 조정된 결과
    """
    if not results or not target_doc_id:
        return results

    config = config or get_search_merger_config()

    # target 위치 찾기
    target_idx = next(
        (i for i, r in enumerate(results) if _get_doc_id(r) == target_doc_id),
        None,
    )

    if target_idx is None:
        logger.debug(f"Target doc not found in results: {target_doc_id}")
        return results

    if target_idx <= config.rank_bump_protected_top:
        logger.debug(f"Target already in top-{config.rank_bump_protected_top + 1}")
        return results

    # 새 위치 계산
    new_idx = max(
        target_idx - config.rank_bump_max,
        config.rank_bump_protected_top,
    )

    if new_idx >= target_idx:
        return results  # 이동 없음

    # 순위 조정
    result = results.copy()
    item = result.pop(target_idx)
    result.insert(new_idx, item)

    logger.debug(f"Rank bump: {target_doc_id} moved {target_idx} → {new_idx}")

    return result


def apply_rank_bump_multi(
    results: List["ChatSource"],
    target_doc_ids: List[str],
    config: Optional[SearchMergerConfig] = None,
) -> List["ChatSource"]:
    """
    여러 문서에 대해 순위 승급 적용

    우선순위 순으로 적용 (첫 번째가 가장 높은 우선순위)
    """
    for doc_id in target_doc_ids:
        results = apply_rank_bump(results, doc_id, config)
    return results


# =============================================================================
# 통합 검색 함수
# =============================================================================

@dataclass
class MergedSearchResult:
    """병합 검색 결과"""

    results: List["ChatSource"]
    quality: QualityAssessment
    boost_applied: bool = False
    fallback_used: bool = False


async def search_with_merge(
    query: str,
    state: "ConversationState",
    search_fn: Callable,
    filter_search_fn: Optional[Callable] = None,
    boost_enabled: bool = True,
    top_k: int = 5,
    config: Optional[SearchMergerConfig] = None,
) -> MergedSearchResult:
    """
    병합 전략으로 검색 수행

    1. 일반 검색 (top_k * 2)
    2. 조건 충족 시 doc_id filter 검색
    3. 병합 + 중복 제거
    4. 품질 평가
    5. 저품질 시 fallback
    6. Rank bump 적용

    Args:
        query: 검색 쿼리
        state: 대화 상태
        search_fn: 일반 검색 함수 (async)
        filter_search_fn: filter 검색 함수 (async, optional)
        boost_enabled: 부스팅 활성화 여부
        top_k: 반환할 결과 수
        config: 설정

    Returns:
        MergedSearchResult: 병합 검색 결과
    """
    config = config or get_search_merger_config()

    # 1. 일반 검색
    general_results = await search_fn(query, top_k=top_k * 2)

    # 2. Filter 검색 (조건 충족 시)
    filter_results = []
    if (
        config.merge_enabled
        and boost_enabled
        and filter_search_fn
        and state.recent_docs
    ):
        for doc in state.recent_docs[:2]:
            try:
                filtered = await filter_search_fn(
                    query,
                    doc_id=doc.doc_id,
                    top_k=3,
                )
                filter_results.extend(filtered)
            except Exception as e:
                logger.warning(f"Filter search failed for {doc.doc_id}: {e}")

    # 3. 병합
    merged = merge_results(general_results, filter_results)

    # 4. 품질 평가
    quality = assess_quality(merged, query, config)
    logger.debug(f"Search quality: {quality}")

    # 5. 저품질 fallback
    fallback_used = False
    if quality.action == QualityAction.FALLBACK_BOOST and filter_search_fn:
        fallback_results = await _fallback_with_merge(
            query, state, search_fn, filter_search_fn, top_k
        )
        if fallback_results:
            merged = fallback_results
            quality = assess_quality(merged, query, config)
            fallback_used = True
            logger.debug(f"Fallback applied, new quality: {quality}")

    # 6. Rank bump (조건부)
    boost_applied = False
    if boost_enabled and state.recent_docs:
        target_ids = [doc.doc_id for doc in state.recent_docs[:2]]
        bumped = apply_rank_bump_multi(merged, target_ids, config)
        if bumped != merged:
            merged = bumped
            boost_applied = True

    return MergedSearchResult(
        results=merged[:top_k],
        quality=quality,
        boost_applied=boost_applied,
        fallback_used=fallback_used,
    )


async def _fallback_with_merge(
    query: str,
    state: "ConversationState",
    search_fn: Callable,
    filter_search_fn: Callable,
    top_k: int,
) -> List["ChatSource"]:
    """
    Fallback 검색 (filter + 일반 병합)

    단독 filter 반환 금지, 항상 일반 결과와 병합
    """
    if not state.recent_docs:
        return []

    # Filter 검색
    filter_results = []
    for doc in state.recent_docs[:2]:
        try:
            filtered = await filter_search_fn(
                query,
                doc_id=doc.doc_id,
                top_k=top_k,
            )
            filter_results.extend(filtered)
        except Exception as e:
            logger.warning(f"Fallback filter search failed: {e}")

    if not filter_results:
        return []

    # 일반 검색 (더 넓은 범위)
    try:
        general_results = await search_fn(query, top_k=top_k * 2)
    except Exception:
        general_results = []

    # 병합 (filter 결과가 있어도 일반 결과와 함께)
    return merge_results(general_results, filter_results)


# =============================================================================
# 유틸리티
# =============================================================================

def rerank_by_relevance(
    results: List["ChatSource"],
    query: str,
    top_k: int = 5,
) -> List["ChatSource"]:
    """
    간단한 relevance 기반 재정렬

    키워드 매칭 + 기존 score 조합
    """
    keywords = extract_keywords(query)

    def relevance_score(source: "ChatSource") -> float:
        base_score = source.score or 0.0

        # 키워드 매칭 보너스
        text = ((source.snippet or "") + " " + (source.title or "")).lower()
        keyword_bonus = sum(0.05 for kw in keywords if kw.lower() in text)

        return base_score + keyword_bonus

    sorted_results = sorted(results, key=relevance_score, reverse=True)
    return sorted_results[:top_k]
