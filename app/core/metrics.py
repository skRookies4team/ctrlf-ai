"""
모니터링 및 지표 수집 모듈 (Metrics Module)

Phase 12: AI Gateway 안정성/품질 Hardening
- 에러/타임아웃/재시도 횟수 카운터
- 서비스별 latency 통계
- 로그 패턴 정의

향후 Prometheus/Grafana 연동 시 이 모듈을 확장합니다.

사용 방법:
    from app.core.metrics import metrics

    # 카운터 증가
    metrics.increment_error("RAG_TIMEOUT")

    # latency 기록
    metrics.record_latency("llm", 1500)

    # 통계 조회
    stats = metrics.get_stats()
"""

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# 로그 태그 상수 (구조화된 로그 분석용)
# =============================================================================

# 에러 타입 태그
LOG_TAG_RAG_TIMEOUT = "RAG_TIMEOUT"
LOG_TAG_RAG_ERROR = "RAG_ERROR"
LOG_TAG_LLM_TIMEOUT = "LLM_TIMEOUT"
LOG_TAG_LLM_ERROR = "LLM_ERROR"
LOG_TAG_LLM_RETRY = "LLM_RETRY"
LOG_TAG_BACKEND_TIMEOUT = "BACKEND_TIMEOUT"
LOG_TAG_BACKEND_ERROR = "BACKEND_ERROR"

# Fallback 태그
LOG_TAG_RAG_FALLBACK = "RAG_FALLBACK"
LOG_TAG_BACKEND_FALLBACK = "BACKEND_FALLBACK"
LOG_TAG_LLM_FALLBACK = "LLM_FALLBACK"


@dataclass
class LatencyStats:
    """Latency 통계."""

    count: int = 0
    total_ms: int = 0
    min_ms: Optional[int] = None
    max_ms: Optional[int] = None

    @property
    def avg_ms(self) -> float:
        """평균 latency (ms)."""
        if self.count == 0:
            return 0.0
        return self.total_ms / self.count

    def record(self, latency_ms: int) -> None:
        """latency를 기록합니다."""
        self.count += 1
        self.total_ms += latency_ms
        if self.min_ms is None or latency_ms < self.min_ms:
            self.min_ms = latency_ms
        if self.max_ms is None or latency_ms > self.max_ms:
            self.max_ms = latency_ms


@dataclass
class MetricsCollector:
    """
    간단한 in-memory 지표 수집기.

    스레드 안전성을 보장하며, 애플리케이션 전역에서 사용됩니다.

    Attributes:
        error_counts: 에러 타입별 카운터
        retry_counts: 서비스별 재시도 카운터
        latency_stats: 서비스별 latency 통계
        request_counts: 라우트별 요청 카운터
    """

    error_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    retry_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    latency_stats: Dict[str, LatencyStats] = field(
        default_factory=lambda: defaultdict(LatencyStats)
    )
    request_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def increment_error(self, error_tag: str) -> None:
        """
        에러 카운터를 증가시킵니다.

        Args:
            error_tag: 에러 태그 (예: RAG_TIMEOUT, LLM_ERROR)
        """
        with self._lock:
            self.error_counts[error_tag] += 1
        logger.info(f"[METRIC] ERROR {error_tag} count={self.error_counts[error_tag]}")

    def increment_retry(self, service: str) -> None:
        """
        재시도 카운터를 증가시킵니다.

        Args:
            service: 서비스 이름 (예: llm, ragflow, backend)
        """
        with self._lock:
            self.retry_counts[service] += 1
        logger.info(f"[METRIC] RETRY {service} count={self.retry_counts[service]}")

    def record_latency(self, service: str, latency_ms: int) -> None:
        """
        서비스 latency를 기록합니다.

        Args:
            service: 서비스 이름 (예: llm, ragflow, backend)
            latency_ms: latency (밀리초)
        """
        with self._lock:
            self.latency_stats[service].record(latency_ms)

    def increment_request(self, route: str) -> None:
        """
        라우트별 요청 카운터를 증가시킵니다.

        Args:
            route: 라우트 이름 (예: RAG_INTERNAL, BACKEND_API)
        """
        with self._lock:
            self.request_counts[route] += 1

    def get_stats(self) -> Dict:
        """
        현재 지표 통계를 반환합니다.

        Returns:
            Dict: 에러 카운트, 재시도 카운트, latency 통계, 요청 카운트
        """
        with self._lock:
            return {
                "error_counts": dict(self.error_counts),
                "retry_counts": dict(self.retry_counts),
                "latency_stats": {
                    service: {
                        "count": stats.count,
                        "avg_ms": round(stats.avg_ms, 2),
                        "min_ms": stats.min_ms,
                        "max_ms": stats.max_ms,
                    }
                    for service, stats in self.latency_stats.items()
                },
                "request_counts": dict(self.request_counts),
            }

    def reset(self) -> None:
        """모든 지표를 리셋합니다 (테스트용)."""
        with self._lock:
            self.error_counts.clear()
            self.retry_counts.clear()
            self.latency_stats.clear()
            self.request_counts.clear()


# 전역 싱글턴 인스턴스
_metrics_instance: Optional[MetricsCollector] = None
_metrics_lock = threading.Lock()


def get_metrics() -> MetricsCollector:
    """
    전역 MetricsCollector 인스턴스를 반환합니다.

    Returns:
        MetricsCollector: 싱글턴 인스턴스
    """
    global _metrics_instance
    if _metrics_instance is None:
        with _metrics_lock:
            if _metrics_instance is None:
                _metrics_instance = MetricsCollector()
    return _metrics_instance


# 편의를 위한 별칭
metrics = get_metrics()
