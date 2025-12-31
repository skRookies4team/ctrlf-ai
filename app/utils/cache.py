"""
TTL LRU 캐시 모듈 (Phase 20-AI-1)

asyncio 환경에서 안전하게 사용할 수 있는 TTL + LRU 캐시 구현입니다.
외부 라이브러리 의존성 없이 순수 Python으로 구현했습니다.

주요 기능:
- TTL(Time-To-Live) 기반 만료
- LRU(Least Recently Used) 기반 maxsize 제한
- 비동기 Lock으로 thread-safe
- 구조화 로그 지원 (cache_hit/cache_miss)

사용 예시:
    from app.utils.cache import TTLCache

    cache = TTLCache(maxsize=1024, ttl_seconds=300)

    # 캐시 저장
    cache.set("key1", {"data": "value"})

    # 캐시 조회
    result = cache.get("key1")  # 있으면 값, 없으면 None
"""

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Generic, Optional, TypeVar

from app.core.logging import get_logger

logger = get_logger(__name__)

V = TypeVar("V")


@dataclass
class CacheEntry(Generic[V]):
    """캐시 항목을 저장하는 데이터 클래스."""
    value: V
    expires_at: float  # Unix timestamp


class TTLCache(Generic[V]):
    """
    TTL + LRU 기반 캐시.

    TTL(Time-To-Live) 만료와 LRU(Least Recently Used) 교체 정책을 지원합니다.
    asyncio 환경에서 안전하게 사용할 수 있도록 Lock을 사용합니다.

    Attributes:
        maxsize: 최대 캐시 항목 수
        ttl_seconds: 캐시 항목 만료 시간 (초)

    Example:
        cache = TTLCache[List[dict]](maxsize=1024, ttl_seconds=300)
        cache.set("key", [{"id": 1}])
        result = cache.get("key")  # [{"id": 1}] 또는 None
    """

    def __init__(
        self,
        maxsize: int = 1024,
        ttl_seconds: float = 300,
        name: str = "default",
    ) -> None:
        """
        TTLCache 초기화.

        Args:
            maxsize: 최대 캐시 항목 수 (기본값: 1024)
            ttl_seconds: 캐시 항목 만료 시간 (초, 기본값: 300)
            name: 캐시 이름 (로깅용)
        """
        self._maxsize = maxsize
        self._ttl_seconds = ttl_seconds
        self._name = name
        self._cache: OrderedDict[str, CacheEntry[V]] = OrderedDict()
        self._lock = asyncio.Lock()

        # 통계
        self._hits = 0
        self._misses = 0

        logger.info(
            f"TTLCache '{name}' initialized: maxsize={maxsize}, ttl={ttl_seconds}s"
        )

    def _is_expired(self, entry: CacheEntry[V]) -> bool:
        """캐시 항목이 만료되었는지 확인합니다."""
        return time.time() > entry.expires_at

    def _evict_expired(self) -> int:
        """만료된 항목을 제거하고 제거된 수를 반환합니다."""
        now = time.time()
        expired_keys = [
            key for key, entry in self._cache.items()
            if now > entry.expires_at
        ]
        for key in expired_keys:
            del self._cache[key]
        return len(expired_keys)

    def _evict_lru(self) -> None:
        """LRU 정책에 따라 가장 오래된 항목을 제거합니다."""
        while len(self._cache) >= self._maxsize:
            # OrderedDict의 첫 번째 항목(가장 오래된)을 제거
            self._cache.popitem(last=False)

    def get(self, key: str) -> Optional[V]:
        """
        캐시에서 값을 조회합니다.

        Args:
            key: 캐시 키

        Returns:
            캐시된 값 또는 None (캐시 미스 또는 만료 시)
        """
        entry = self._cache.get(key)

        if entry is None:
            self._misses += 1
            logger.debug(
                f"Cache miss: name={self._name}, key={key[:32]}...",
                extra={"event": "cache_miss", "cache_name": self._name}
            )
            return None

        if self._is_expired(entry):
            # 만료된 항목 제거
            del self._cache[key]
            self._misses += 1
            logger.debug(
                f"Cache expired: name={self._name}, key={key[:32]}...",
                extra={"event": "cache_expired", "cache_name": self._name}
            )
            return None

        # LRU: 조회 시 항목을 맨 뒤로 이동
        self._cache.move_to_end(key)
        self._hits += 1
        logger.debug(
            f"Cache hit: name={self._name}, key={key[:32]}...",
            extra={"event": "cache_hit", "cache_name": self._name}
        )
        return entry.value

    def set(self, key: str, value: V) -> None:
        """
        캐시에 값을 저장합니다.

        Args:
            key: 캐시 키
            value: 저장할 값
        """
        # 만료된 항목 정리 (주기적)
        if len(self._cache) % 100 == 0:
            self._evict_expired()

        # 이미 존재하는 키면 삭제 후 재삽입 (LRU 갱신)
        if key in self._cache:
            del self._cache[key]

        # maxsize 초과 시 LRU 제거
        self._evict_lru()

        # 새 항목 추가
        self._cache[key] = CacheEntry(
            value=value,
            expires_at=time.time() + self._ttl_seconds,
        )

        logger.debug(
            f"Cache set: name={self._name}, key={key[:32]}..., size={len(self._cache)}",
            extra={"event": "cache_set", "cache_name": self._name}
        )

    async def get_async(self, key: str) -> Optional[V]:
        """
        비동기 환경에서 캐시를 조회합니다 (Lock 사용).

        Args:
            key: 캐시 키

        Returns:
            캐시된 값 또는 None
        """
        async with self._lock:
            return self.get(key)

    async def set_async(self, key: str, value: V) -> None:
        """
        비동기 환경에서 캐시에 저장합니다 (Lock 사용).

        Args:
            key: 캐시 키
            value: 저장할 값
        """
        async with self._lock:
            self.set(key, value)

    def clear(self) -> None:
        """캐시를 모두 비웁니다."""
        self._cache.clear()
        logger.info(f"Cache '{self._name}' cleared")

    def size(self) -> int:
        """현재 캐시 항목 수를 반환합니다."""
        return len(self._cache)

    def stats(self) -> Dict[str, Any]:
        """캐시 통계를 반환합니다."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        return {
            "name": self._name,
            "size": len(self._cache),
            "maxsize": self._maxsize,
            "ttl_seconds": self._ttl_seconds,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{hit_rate:.1f}%",
        }


def make_cache_key(data: Dict[str, Any]) -> str:
    """
    딕셔너리로부터 캐시 키를 생성합니다.

    Args:
        data: 캐시 키 생성에 사용할 딕셔너리

    Returns:
        SHA256 해시 문자열

    Example:
        key = make_cache_key({"dataset": "POLICY", "query": "연차", "top_k": 5})
        # "a3b4c5d6..."
    """
    json_str = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(json_str.encode("utf-8")).hexdigest()
