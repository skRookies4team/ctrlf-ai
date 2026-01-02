# app/services/embedding_matcher.py
"""
Step 5: Embedding Matcher 서비스

기능:
- 룰셋 쿼리 임베딩 로컬 인덱스 (FAISS 또는 numpy 기반)
- 코사인 유사도 기반 매칭
- 원자적 인덱스 swap (reload 시)
- 피처플래그 ON/OFF
- allowlist/anchor 2차 조건 (오탐 방지)

사용법:
    from app.services.embedding_matcher import EmbeddingMatcher

    matcher = EmbeddingMatcher(threshold=0.85, top_k=3)
    matcher.build_index(rules, embeddings)
    result = matcher.search(query_embedding)
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# FAISS는 선택적 의존성 (없으면 numpy fallback)
try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False

logger = logging.getLogger(__name__)


# =============================================================================
# 결과 데이터 클래스
# =============================================================================


@dataclass
class EmbeddingMatchResult:
    """임베딩 매칭 결과."""

    matched: bool = False
    rule_idx: int = -1
    score: float = 0.0  # 코사인 유사도 (0-1)

    # 2차 조건 결과
    passed_secondary_check: bool = True
    secondary_check_reason: Optional[str] = None


@dataclass
class EmbeddingIndex:
    """임베딩 인덱스 (원자적 swap용)."""

    # 임베딩 행렬 (N x D)
    embeddings: np.ndarray

    # 룰 인덱스 매핑
    rule_indices: List[int]

    # FAISS 인덱스 (사용 가능 시)
    faiss_index: Any = None

    # 메타데이터
    dimension: int = 0
    count: int = 0


# =============================================================================
# Allowlist / Anchor 2차 조건
# =============================================================================


@dataclass
class SecondaryCondition:
    """2차 조건 설정 (오탐 방지용)."""

    # allowlist: 이 키워드가 포함되면 차단 안함
    allowlist_keywords: List[str] = field(default_factory=list)

    # anchor: 이 키워드가 반드시 포함되어야 차단
    anchor_keywords: List[str] = field(default_factory=list)

    # anchor_mode: "any" (하나라도 포함) 또는 "all" (모두 포함)
    anchor_mode: str = "any"


def check_secondary_condition(
    query: str,
    condition: Optional[SecondaryCondition],
) -> Tuple[bool, Optional[str]]:
    """2차 조건 체크.

    Args:
        query: 원본 질문
        condition: 2차 조건 설정

    Returns:
        (passed, reason): 통과 여부와 사유
    """
    if condition is None:
        return True, None

    query_lower = query.lower()

    # 1. Allowlist 체크: 포함되면 통과 (차단 안함)
    for keyword in condition.allowlist_keywords:
        if keyword.lower() in query_lower:
            return False, f"allowlist_hit:{keyword}"

    # 2. Anchor 체크: 필수 키워드 포함 확인
    if condition.anchor_keywords:
        if condition.anchor_mode == "all":
            # 모든 anchor가 포함되어야 함
            for keyword in condition.anchor_keywords:
                if keyword.lower() not in query_lower:
                    return False, f"anchor_miss:{keyword}"
        else:  # "any"
            # 하나라도 포함되어야 함
            found = any(
                keyword.lower() in query_lower
                for keyword in condition.anchor_keywords
            )
            if not found:
                return False, "anchor_miss:none_found"

    return True, None


# =============================================================================
# Embedding Matcher
# =============================================================================


class EmbeddingProviderError(Exception):
    """Embedding provider 검증 실패 시 발생하는 예외."""

    pass


class EmbeddingMatcher:
    """임베딩 기반 매처.

    FAISS 사용 가능 시 FAISS 인덱스, 없으면 numpy 기반 brute-force.
    인덱스 swap은 원자적으로 수행 (thread-safe).

    Step 6: 로컬 임베딩 검증 및 성능 보호 기능 추가.
    """

    def __init__(
        self,
        threshold: float = 0.85,
        top_k: int = 3,
        use_faiss: bool = True,
        require_index: bool = False,
        rule_count_threshold: int = 1000,
        provider_name: str = "unknown",
        is_local: bool = True,
        require_local: bool = True,
    ):
        """
        Args:
            threshold: 코사인 유사도 임계값 (0-1)
            top_k: 검색 시 반환할 최대 후보 수
            use_faiss: FAISS 사용 여부 (설치된 경우)
            require_index: FAISS 인덱스 필수 여부 (True면 FAISS 없을 시 비활성화)
            rule_count_threshold: 룰 개수 임계치 (초과 시 경고)
            provider_name: 임베딩 provider 이름 (로깅용)
            is_local: 임베딩 함수가 로컬인지 여부
            require_local: 로컬 임베딩 강제 여부 (True면 non-local 시 오류)
        """
        self._threshold = threshold
        self._top_k = top_k
        self._require_index = require_index
        self._rule_count_threshold = rule_count_threshold
        self._provider_name = provider_name
        self._is_local = is_local
        self._require_local = require_local

        # Step 6: 로컬 검증
        if self._require_local and not self._is_local:
            raise EmbeddingProviderError(
                f"Embedding provider '{provider_name}' is not local. "
                f"Forbidden query filter requires local-only embedding. "
                f"Set require_local=False to allow non-local providers."
            )

        # FAISS 사용 결정
        self._use_faiss = use_faiss and FAISS_AVAILABLE

        # Step 6: require_index 검증
        if self._require_index and not self._use_faiss:
            if FAISS_AVAILABLE:
                logger.warning(
                    "FAISS index required but use_faiss=False. Disabling matcher."
                )
            else:
                logger.warning(
                    "FAISS index required but FAISS not installed. Disabling matcher."
                )
            self._disabled = True
        else:
            self._disabled = False

        # 현재 인덱스 (원자적 swap용)
        self._index: Optional[EmbeddingIndex] = None
        self._index_lock = threading.Lock()

        # 룰별 2차 조건 (rule_idx -> SecondaryCondition)
        self._secondary_conditions: Dict[int, SecondaryCondition] = {}

        if self._disabled:
            logger.warning(
                f"EmbeddingMatcher disabled: require_index={require_index}, "
                f"faiss_available={FAISS_AVAILABLE}"
            )
        elif self._use_faiss:
            logger.info(
                f"EmbeddingMatcher initialized with FAISS backend: "
                f"provider={provider_name}, is_local={is_local}"
            )
        else:
            logger.info(
                f"EmbeddingMatcher initialized with numpy backend: "
                f"provider={provider_name}, is_local={is_local}, "
                f"FAISS available={FAISS_AVAILABLE}"
            )

    def build_index(
        self,
        embeddings: np.ndarray,
        rule_indices: List[int],
        secondary_conditions: Optional[Dict[int, SecondaryCondition]] = None,
    ) -> bool:
        """임베딩 인덱스 빌드 (원자적 swap).

        Args:
            embeddings: 임베딩 행렬 (N x D), float32 권장
            rule_indices: 각 임베딩에 대응하는 룰 인덱스
            secondary_conditions: 룰별 2차 조건

        Returns:
            True if index was built successfully, False otherwise
        """
        # Step 6: disabled 상태 체크
        if self._disabled:
            logger.warning("EmbeddingMatcher is disabled, skipping index build")
            return False

        if embeddings.shape[0] == 0:
            logger.warning("Empty embeddings provided, skipping index build")
            return False

        # Step 6: 룰 개수 임계치 체크
        rule_count = embeddings.shape[0]
        if self._rule_count_threshold > 0 and rule_count > self._rule_count_threshold:
            if not self._use_faiss:
                logger.warning(
                    f"Rule count ({rule_count}) exceeds threshold ({self._rule_count_threshold}) "
                    f"but FAISS is not available. Brute-force may cause performance issues."
                )
            else:
                logger.info(
                    f"Large rule count ({rule_count}), using FAISS index for performance"
                )

        # float32로 변환 (FAISS 요구사항)
        embeddings = embeddings.astype(np.float32)

        # L2 정규화 (코사인 유사도용)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)  # 0 방지
        embeddings_normalized = embeddings / norms

        dimension = embeddings.shape[1]
        count = embeddings.shape[0]

        # FAISS 인덱스 생성 (사용 가능 시)
        faiss_index = None
        if self._use_faiss:
            # Inner Product로 코사인 유사도 계산 (정규화된 벡터)
            faiss_index = faiss.IndexFlatIP(dimension)
            faiss_index.add(embeddings_normalized)
            logger.debug(f"FAISS index built: dimension={dimension}, count={count}")

        # 새 인덱스 생성
        new_index = EmbeddingIndex(
            embeddings=embeddings_normalized,
            rule_indices=rule_indices,
            faiss_index=faiss_index,
            dimension=dimension,
            count=count,
        )

        # 원자적 swap
        with self._index_lock:
            old_index = self._index
            self._index = new_index
            self._secondary_conditions = secondary_conditions or {}

        # 이전 인덱스 정리 (GC에 맡김)
        if old_index is not None:
            logger.debug("Previous index swapped out")

        logger.info(
            f"EmbeddingMatcher index built: count={count}, dimension={dimension}, "
            f"use_faiss={self._use_faiss}, threshold={self._threshold}, top_k={self._top_k}"
        )

        return True

    def search(
        self,
        query_embedding: np.ndarray,
        query_text: Optional[str] = None,
    ) -> List[EmbeddingMatchResult]:
        """쿼리 임베딩으로 유사한 룰 검색.

        Args:
            query_embedding: 쿼리 임베딩 벡터 (D,) 또는 (1, D)
            query_text: 원본 쿼리 텍스트 (2차 조건 체크용)

        Returns:
            매칭 결과 리스트 (score 내림차순, threshold 이상만)
        """
        # Step 6: disabled 상태 체크
        if self._disabled:
            return []

        # 인덱스 스냅샷 (thread-safe 읽기)
        with self._index_lock:
            index = self._index
            secondary_conditions = self._secondary_conditions.copy()

        if index is None or index.count == 0:
            return []

        # 쿼리 벡터 정규화
        query = query_embedding.astype(np.float32).reshape(1, -1)
        norm = np.linalg.norm(query)
        if norm > 0:
            query = query / norm

        # 검색 수행
        if self._use_faiss and index.faiss_index is not None:
            # FAISS 검색
            scores, indices = index.faiss_index.search(query, min(self._top_k, index.count))
            scores = scores[0]  # (top_k,)
            indices = indices[0]  # (top_k,)
        else:
            # Numpy brute-force
            similarities = np.dot(index.embeddings, query.T).flatten()
            top_indices = np.argsort(similarities)[::-1][:self._top_k]
            scores = similarities[top_indices]
            indices = top_indices

        # 결과 필터링 및 반환
        results = []
        for score, idx in zip(scores, indices):
            if idx == -1:  # FAISS 패딩
                continue
            if score < self._threshold:
                continue

            rule_idx = index.rule_indices[idx]

            # 2차 조건 체크
            passed = True
            reason = None
            if query_text and rule_idx in secondary_conditions:
                passed, reason = check_secondary_condition(
                    query_text,
                    secondary_conditions[rule_idx]
                )

            results.append(EmbeddingMatchResult(
                matched=passed,
                rule_idx=rule_idx,
                score=float(score),
                passed_secondary_check=passed,
                secondary_check_reason=reason,
            ))

            logger.debug(
                f"Embedding match candidate: rule_idx={rule_idx}, score={score:.4f}, "
                f"threshold={self._threshold}, passed_secondary={passed}"
            )

        return results

    def get_best_match(
        self,
        query_embedding: np.ndarray,
        query_text: Optional[str] = None,
    ) -> Optional[EmbeddingMatchResult]:
        """가장 유사한 매칭 결과 반환.

        Args:
            query_embedding: 쿼리 임베딩 벡터
            query_text: 원본 쿼리 텍스트 (2차 조건 체크용)

        Returns:
            최고 점수 결과 (2차 조건 통과한 것 중) 또는 None
        """
        results = self.search(query_embedding, query_text)

        # 2차 조건 통과한 결과 중 최고 점수
        passed_results = [r for r in results if r.matched]
        if passed_results:
            return passed_results[0]  # 이미 score 내림차순

        return None

    def get_info(self) -> Dict[str, Any]:
        """현재 매처 상태 정보 반환."""
        with self._index_lock:
            index = self._index

        return {
            "enabled": not self._disabled,
            "disabled": self._disabled,
            "backend": "faiss" if self._use_faiss else "numpy",
            "faiss_available": FAISS_AVAILABLE,
            "threshold": self._threshold,
            "top_k": self._top_k,
            "index_count": index.count if index else 0,
            "index_dimension": index.dimension if index else 0,
            # Step 6: 검증 관련 정보
            "provider_name": self._provider_name,
            "is_local": self._is_local,
            "require_local": self._require_local,
            "require_index": self._require_index,
            "rule_count_threshold": self._rule_count_threshold,
        }

    def clear_index(self) -> None:
        """인덱스 초기화."""
        with self._index_lock:
            self._index = None
            self._secondary_conditions = {}
        logger.info("EmbeddingMatcher index cleared")
