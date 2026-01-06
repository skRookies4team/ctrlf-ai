"""
State Store - ConversationState 저장소

Redis 또는 In-Memory 백엔드를 지원하는 상태 저장소.
멀티 인스턴스 환경에서는 Redis 사용 권장.

주요 기능:
- 키 정책: (user_id, session_id) 조합
- TTL: sliding 갱신 지원
- 동시성: asyncio Lock (memory) / Redis 원자성 (redis)
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, TYPE_CHECKING

from app.models.conversation_state import ConversationState

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)


# =============================================================================
# 설정값 (config.py에서 가져올 예정)
# =============================================================================

@dataclass
class StateStoreConfig:
    """상태 저장소 설정"""

    # 백엔드 선택
    backend: str = "memory"  # "memory" | "redis"
    redis_url: Optional[str] = None

    # TTL 정책
    ttl_seconds: int = 3600  # 기본 60분
    ttl_sliding: bool = True  # 활동 시 TTL 갱신
    ttl_max_seconds: int = 7200  # 최대 2시간 (sliding 상한)

    # 키 prefix
    key_prefix: str = "conversation_state"


def get_state_store_config() -> StateStoreConfig:
    """설정에서 StateStoreConfig 로드"""
    try:
        from app.core.config import get_settings
        settings = get_settings()
        return StateStoreConfig(
            backend=getattr(settings, "STATE_STORE_BACKEND", "memory"),
            redis_url=getattr(settings, "STATE_STORE_REDIS_URL", None),
            ttl_seconds=getattr(settings, "STATE_TTL_SECONDS", 3600),
            ttl_sliding=getattr(settings, "STATE_TTL_SLIDING", True),
            ttl_max_seconds=getattr(settings, "STATE_TTL_MAX_SECONDS", 7200),
        )
    except Exception:
        return StateStoreConfig()


# =============================================================================
# 캐시 엔트리 (Memory 백엔드용)
# =============================================================================

@dataclass
class CacheEntry:
    """메모리 캐시 엔트리"""

    state: ConversationState
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    max_expires_at: float = 0.0  # sliding 상한

    def is_expired(self) -> bool:
        """만료 여부 확인"""
        return time.time() > self.expires_at


# =============================================================================
# 추상 베이스 클래스
# =============================================================================

class BaseStateStore(ABC):
    """상태 저장소 추상 베이스 클래스"""

    @abstractmethod
    async def get(self, user_id: str, session_id: str) -> Optional[ConversationState]:
        """상태 조회"""
        pass

    @abstractmethod
    async def set(
        self,
        user_id: str,
        session_id: str,
        state: ConversationState,
    ) -> None:
        """상태 저장"""
        pass

    @abstractmethod
    async def delete(self, user_id: str, session_id: str) -> None:
        """상태 삭제"""
        pass

    @abstractmethod
    async def touch(self, user_id: str, session_id: str) -> bool:
        """TTL 갱신 (sliding TTL)"""
        pass

    @abstractmethod
    async def exists(self, user_id: str, session_id: str) -> bool:
        """존재 여부 확인"""
        pass

    def _make_key(self, user_id: str, session_id: str) -> str:
        """키 생성"""
        return f"{self.config.key_prefix}:{user_id}:{session_id}"


# =============================================================================
# In-Memory 백엔드
# =============================================================================

class MemoryStateStore(BaseStateStore):
    """
    In-Memory 상태 저장소

    단일 인스턴스 환경용. 개발/테스트에 적합.
    서버 재시작 시 상태 손실.
    """

    def __init__(self, config: Optional[StateStoreConfig] = None) -> None:
        self.config = config or StateStoreConfig()
        self._store: Dict[str, CacheEntry] = {}
        self._lock = asyncio.Lock()
        self._last_cleanup = time.time()
        self._cleanup_interval = 60.0  # 60초마다 만료 항목 정리

    async def get(self, user_id: str, session_id: str) -> Optional[ConversationState]:
        """상태 조회"""
        key = self._make_key(user_id, session_id)

        async with self._lock:
            await self._maybe_cleanup()

            entry = self._store.get(key)
            if entry is None:
                return None

            if entry.is_expired():
                del self._store[key]
                logger.debug(f"State expired: {key}")
                return None

            # Sliding TTL 갱신
            if self.config.ttl_sliding:
                self._extend_ttl(entry)

            return entry.state

    async def set(
        self,
        user_id: str,
        session_id: str,
        state: ConversationState,
    ) -> None:
        """상태 저장"""
        key = self._make_key(user_id, session_id)
        now = time.time()

        async with self._lock:
            existing = self._store.get(key)

            if existing:
                # 기존 엔트리 업데이트 (max_expires_at 유지)
                entry = CacheEntry(
                    state=state,
                    created_at=existing.created_at,
                    expires_at=now + self.config.ttl_seconds,
                    max_expires_at=existing.max_expires_at,
                )
            else:
                # 새 엔트리
                entry = CacheEntry(
                    state=state,
                    created_at=now,
                    expires_at=now + self.config.ttl_seconds,
                    max_expires_at=now + self.config.ttl_max_seconds,
                )

            self._store[key] = entry
            logger.debug(f"State saved: {key}, ttl={self.config.ttl_seconds}s")

    async def delete(self, user_id: str, session_id: str) -> None:
        """상태 삭제"""
        key = self._make_key(user_id, session_id)

        async with self._lock:
            if key in self._store:
                del self._store[key]
                logger.debug(f"State deleted: {key}")

    async def touch(self, user_id: str, session_id: str) -> bool:
        """TTL 갱신 (sliding TTL)"""
        if not self.config.ttl_sliding:
            return False

        key = self._make_key(user_id, session_id)

        async with self._lock:
            entry = self._store.get(key)
            if entry is None or entry.is_expired():
                return False

            self._extend_ttl(entry)
            return True

    async def exists(self, user_id: str, session_id: str) -> bool:
        """존재 여부 확인"""
        key = self._make_key(user_id, session_id)

        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False
            if entry.is_expired():
                del self._store[key]
                return False
            return True

    def _extend_ttl(self, entry: CacheEntry) -> None:
        """TTL 연장 (상한선 적용)"""
        now = time.time()
        new_expires = now + self.config.ttl_seconds

        # 상한선 적용
        if new_expires > entry.max_expires_at:
            new_expires = entry.max_expires_at

        entry.expires_at = new_expires

    async def _maybe_cleanup(self) -> None:
        """만료 항목 주기적 정리"""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return

        self._last_cleanup = now
        expired_keys = [
            key for key, entry in self._store.items()
            if entry.is_expired()
        ]

        for key in expired_keys:
            del self._store[key]

        if expired_keys:
            logger.debug(f"Cleaned up {len(expired_keys)} expired states")

    # 테스트/디버깅용
    async def size(self) -> int:
        """저장된 상태 수"""
        async with self._lock:
            return len(self._store)

    async def clear(self) -> None:
        """모든 상태 삭제"""
        async with self._lock:
            self._store.clear()


# =============================================================================
# Redis 백엔드
# =============================================================================

class RedisStateStore(BaseStateStore):
    """
    Redis 상태 저장소

    멀티 인스턴스 환경용. Production 권장.
    서버 재시작에도 상태 유지.
    """

    def __init__(
        self,
        config: Optional[StateStoreConfig] = None,
        redis_client: Optional["Redis"] = None,
    ) -> None:
        self.config = config or StateStoreConfig()
        self._redis = redis_client
        self._connected = False

    async def _get_redis(self) -> "Redis":
        """Redis 클라이언트 획득 (lazy initialization)"""
        if self._redis is not None:
            return self._redis

        try:
            import redis.asyncio as redis
            self._redis = redis.from_url(
                self.config.redis_url or "redis://localhost:6379",
                encoding="utf-8",
                decode_responses=True,
            )
            self._connected = True
            return self._redis
        except ImportError:
            raise RuntimeError(
                "redis package is required for Redis backend. "
                "Install with: pip install redis"
            )

    async def get(self, user_id: str, session_id: str) -> Optional[ConversationState]:
        """상태 조회"""
        key = self._make_key(user_id, session_id)

        try:
            redis = await self._get_redis()
            data = await redis.get(key)

            if data is None:
                return None

            state = ConversationState.from_json(data)

            # Sliding TTL 갱신
            if self.config.ttl_sliding:
                await self._touch_with_cap(key)

            return state

        except Exception as e:
            logger.error(f"Redis get error: {e}")
            return None

    async def set(
        self,
        user_id: str,
        session_id: str,
        state: ConversationState,
    ) -> None:
        """상태 저장"""
        key = self._make_key(user_id, session_id)
        max_key = f"{key}:max_expires"

        try:
            redis = await self._get_redis()

            # 상태 저장
            await redis.set(
                key,
                state.to_json(),
                ex=self.config.ttl_seconds,
            )

            # max_expires 설정 (없으면)
            if not await redis.exists(max_key):
                await redis.set(
                    max_key,
                    str(time.time() + self.config.ttl_max_seconds),
                    ex=self.config.ttl_max_seconds,
                )

            logger.debug(f"State saved to Redis: {key}")

        except Exception as e:
            logger.error(f"Redis set error: {e}")

    async def delete(self, user_id: str, session_id: str) -> None:
        """상태 삭제"""
        key = self._make_key(user_id, session_id)
        max_key = f"{key}:max_expires"

        try:
            redis = await self._get_redis()
            await redis.delete(key, max_key)
            logger.debug(f"State deleted from Redis: {key}")

        except Exception as e:
            logger.error(f"Redis delete error: {e}")

    async def touch(self, user_id: str, session_id: str) -> bool:
        """TTL 갱신 (sliding TTL)"""
        if not self.config.ttl_sliding:
            return False

        key = self._make_key(user_id, session_id)
        return await self._touch_with_cap(key)

    async def _touch_with_cap(self, key: str) -> bool:
        """상한선 적용 TTL 갱신"""
        max_key = f"{key}:max_expires"

        try:
            redis = await self._get_redis()

            # 현재 max_expires 확인
            max_expires_str = await redis.get(max_key)
            if max_expires_str is None:
                return False

            max_expires = float(max_expires_str)
            now = time.time()
            remaining = max_expires - now

            if remaining <= 0:
                # 상한 도달, 갱신 안 함
                return False

            # 새 TTL = min(기본 TTL, 남은 상한)
            new_ttl = min(self.config.ttl_seconds, int(remaining))
            await redis.expire(key, new_ttl)
            return True

        except Exception as e:
            logger.error(f"Redis touch error: {e}")
            return False

    async def exists(self, user_id: str, session_id: str) -> bool:
        """존재 여부 확인"""
        key = self._make_key(user_id, session_id)

        try:
            redis = await self._get_redis()
            return bool(await redis.exists(key))

        except Exception as e:
            logger.error(f"Redis exists error: {e}")
            return False

    async def close(self) -> None:
        """연결 종료"""
        if self._redis is not None:
            await self._redis.close()
            self._redis = None
            self._connected = False


# =============================================================================
# 팩토리 함수
# =============================================================================

_state_store: Optional[BaseStateStore] = None


def get_state_store() -> BaseStateStore:
    """
    상태 저장소 싱글턴 인스턴스 반환

    설정에 따라 Redis 또는 Memory 백엔드 선택.
    """
    global _state_store

    if _state_store is not None:
        return _state_store

    config = get_state_store_config()

    if config.backend == "redis" and config.redis_url:
        _state_store = RedisStateStore(config)
        logger.info(f"Using Redis state store: {config.redis_url}")
    else:
        _state_store = MemoryStateStore(config)
        logger.info("Using in-memory state store")

    return _state_store


async def get_or_create_state(
    user_id: str,
    session_id: str,
) -> ConversationState:
    """
    상태 조회 또는 생성

    편의 함수: 상태가 없으면 새로 생성하여 반환.
    """
    store = get_state_store()
    state = await store.get(user_id, session_id)

    if state is None:
        state = ConversationState(
            user_id=user_id,
            session_id=session_id,
        )
        await store.set(user_id, session_id, state)
        logger.debug(f"Created new state: user={user_id}, session={session_id}")

    return state


async def save_state(state: ConversationState) -> None:
    """상태 저장 (편의 함수)"""
    store = get_state_store()
    await store.set(state.user_id, state.session_id, state)


def clear_state_store_cache() -> None:
    """상태 저장소 캐시 클리어 (테스트용)"""
    global _state_store
    _state_store = None
