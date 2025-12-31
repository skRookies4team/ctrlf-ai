"""
질문 클러스터링 서비스 (Question Clustering Service)

사용자 질문 로그를 분석하여 유사한 질문들을 클러스터로 그룹화합니다.

주요 기능:
- 질문 유사도 계산 (임베딩 기반)
- 유사 질문 클러스터링
- 대표 질문 선정
"""

import uuid
from collections import defaultdict
from typing import List, Optional

from app.clients.llm_client import LLMClient
from app.clients.milvus_client import MilvusSearchClient, get_milvus_client
from app.core.logging import get_logger
from app.models.faq_candidate import QuestionCluster, QuestionLog

logger = get_logger(__name__)


class QuestionClusteringService:
    """
    질문 클러스터링 서비스.
    
    사용자 질문 로그를 분석하여 유사한 질문들을 클러스터로 그룹화합니다.
    """

    def __init__(
        self,
        milvus_client: Optional[MilvusSearchClient] = None,
        llm_client: Optional[LLMClient] = None,
        similarity_threshold: float = 0.75,
    ) -> None:
        """
        QuestionClusteringService 초기화.
        
        Args:
            milvus_client: Milvus 클라이언트 (임베딩 생성용)
            llm_client: LLM 클라이언트 (대표 질문 선정용)
            similarity_threshold: 유사도 임계값 (0~1, 기본 0.75)
        """
        self._milvus_client = milvus_client or get_milvus_client()
        self._llm_client = llm_client or LLMClient()
        self._similarity_threshold = similarity_threshold

    async def cluster_questions(
        self,
        question_logs: List[QuestionLog],
        domain: Optional[str] = None,
    ) -> List[QuestionCluster]:
        """
        질문 로그를 클러스터로 그룹화합니다.
        
        Args:
            question_logs: 질문 로그 목록
            domain: 도메인 필터 (선택)
            
        Returns:
            List[QuestionCluster]: 클러스터 목록
        """
        if not question_logs:
            logger.info("No question logs to cluster")
            return []

        logger.info(
            f"Clustering {len(question_logs)} questions "
            f"(domain={domain}, threshold={self._similarity_threshold})"
        )

        # 1. 질문별 빈도 집계
        question_counts = defaultdict(int)
        question_log_map = defaultdict(list)
        
        for log in question_logs:
            question = log.question_masked.strip()
            if question:
                question_counts[question] += log.count
                question_log_map[question].append(log)

        # 2. 빈도순 정렬
        sorted_questions = sorted(
            question_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )

        # 3. 유사도 기반 클러스터링
        clusters: List[QuestionCluster] = []
        processed_questions = set()

        for question, count in sorted_questions:
            if question in processed_questions:
                continue

            # 새 클러스터 생성
            cluster_questions = [question]
            cluster_logs = question_log_map[question].copy()
            processed_questions.add(question)

            # 유사한 질문 찾기
            for other_question, other_count in sorted_questions:
                if other_question in processed_questions:
                    continue
                if other_question == question:
                    continue

                # 간단한 유사도 계산 (문자열 기반)
                similarity = self._calculate_similarity(question, other_question)
                
                if similarity >= self._similarity_threshold:
                    cluster_questions.append(other_question)
                    cluster_logs.extend(question_log_map[other_question])
                    processed_questions.add(other_question)

            # 대표 질문 선정 (가장 빈도 높은 질문)
            canonical_question = question
            
            # 샘플 질문 선택 (최대 5개)
            sample_questions = cluster_questions[:5]
            if canonical_question not in sample_questions:
                sample_questions = [canonical_question] + sample_questions[:4]

            # 클러스터 생성
            cluster = QuestionCluster(
                cluster_id=str(uuid.uuid4()),
                canonical_question=canonical_question,
                sample_questions=sample_questions,
                question_logs=cluster_logs,
                total_count=sum(log.count for log in cluster_logs),
                domain=domain or question_logs[0].domain if question_logs else "UNKNOWN",
            )
            clusters.append(cluster)

        logger.info(f"Created {len(clusters)} clusters from {len(question_logs)} questions")
        return clusters

    def _calculate_similarity(self, q1: str, q2: str) -> float:
        """
        두 질문의 유사도를 계산합니다 (간단한 문자열 기반).
        
        향후 개선: 임베딩 기반 유사도 계산으로 교체 가능
        
        Args:
            q1: 질문 1
            q2: 질문 2
            
        Returns:
            float: 유사도 (0~1)
        """
        # 간단한 Jaccard 유사도 (단어 기반)
        words1 = set(q1.lower().split())
        words2 = set(q2.lower().split())
        
        if not words1 or not words2:
            return 0.0
        
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        
        if union == 0:
            return 0.0
        
        return intersection / union

