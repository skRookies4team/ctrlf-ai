"""
FAQ 후보 선정 서비스 (FAQ Candidate Selection Service)

질문 클러스터를 분석하여 FAQ 후보를 선정합니다.

주요 기능:
- 빈도 점수 계산
- 최근성 점수 계산
- 종합 점수 기반 후보 선정
"""

from datetime import datetime, timedelta
from typing import List, Optional

from app.core.logging import get_logger
from app.models.faq_candidate import FaqCandidate, QuestionCluster

logger = get_logger(__name__)


class FaqCandidateService:
    """
    FAQ 후보 선정 서비스.
    
    질문 클러스터를 분석하여 자주 묻는 질문으로 FAQ 후보를 선정합니다.
    """

    def __init__(
        self,
        frequency_weight: float = 0.6,
        recency_weight: float = 0.4,
        recency_days: int = 7,
    ) -> None:
        """
        FaqCandidateService 초기화.
        
        Args:
            frequency_weight: 빈도 가중치 (0~1, 기본 0.6)
            recency_weight: 최근성 가중치 (0~1, 기본 0.4)
            recency_days: 최근성 기준 일수 (기본 7일)
        """
        self._frequency_weight = frequency_weight
        self._recency_weight = recency_weight
        self._recency_days = recency_days

    def select_candidates(
        self,
        clusters: List[QuestionCluster],
        min_frequency: int = 3,
        max_candidates: int = 20,
    ) -> List[FaqCandidate]:
        """
        FAQ 후보를 선정합니다.
        
        Args:
            clusters: 질문 클러스터 목록
            min_frequency: 최소 질문 빈도 (기본 3회)
            max_candidates: 최대 후보 수 (기본 20개)
            
        Returns:
            List[FaqCandidate]: 선정된 FAQ 후보 목록
        """
        if not clusters:
            logger.info("No clusters to select candidates from")
            return []

        logger.info(
            f"Selecting FAQ candidates from {len(clusters)} clusters "
            f"(min_frequency={min_frequency}, max={max_candidates})"
        )

        # 1. 최소 빈도 필터링
        filtered_clusters = [
            c for c in clusters
            if c.total_count >= min_frequency
        ]

        if not filtered_clusters:
            logger.info(f"No clusters meet minimum frequency threshold ({min_frequency})")
            return []

        # 2. 점수 계산
        candidates: List[FaqCandidate] = []
        max_count = max(c.total_count for c in filtered_clusters)
        now = datetime.utcnow()

        for cluster in filtered_clusters:
            # 빈도 점수 (0~1)
            frequency_score = min(cluster.total_count / max_count, 1.0) if max_count > 0 else 0.0

            # 최근성 점수 (0~1)
            # 가장 최근 질문의 날짜 기준
            recent_logs = [
                log for log in cluster.question_logs
                if log.created_at and (now - log.created_at).days <= self._recency_days
            ]
            recent_count = sum(log.count for log in recent_logs)
            recency_score = min(recent_count / cluster.total_count, 1.0) if cluster.total_count > 0 else 0.0

            # 종합 점수
            total_score = (
                self._frequency_weight * frequency_score +
                self._recency_weight * recency_score
            )

            candidate = FaqCandidate(
                candidate_id=f"candidate-{cluster.cluster_id}",
                cluster=cluster,
                frequency_score=frequency_score,
                recency_score=recency_score,
                total_score=total_score,
            )
            candidates.append(candidate)

        # 3. 점수순 정렬 및 상위 N개 선택
        candidates.sort(key=lambda x: x.total_score, reverse=True)
        selected = candidates[:max_candidates]

        logger.info(
            f"Selected {len(selected)} FAQ candidates "
            f"(from {len(filtered_clusters)} clusters meeting threshold)"
        )

        return selected

