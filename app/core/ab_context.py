"""
A/B 테스트 컨텍스트 관리 모듈

Backend → AI로 전달된 모델 선택 정보를 관리합니다.
- requestId별로 모델 설정을 저장
- MilvusSearchClient에서 모델에 따라 임베딩/컬렉션 분기

사용 흐름:
1. Backend → AI: POST /internal/ai/context/model {"requestId": "uuid", "model": "sroberta"}
2. AI: set_ab_model(request_id, model) 호출
3. MilvusSearchClient: get_ab_model(request_id)로 모델 조회
4. model에 따라 임베딩/컬렉션 분기:
   - openai: OpenAI text-embedding-3-large, ragflow_chunks
   - sroberta: sentence-roberta 임베딩, ragflow_chunks_sroberta
"""

import time
from enum import Enum
from threading import Lock
from typing import Dict, Literal, Optional, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# A/B Test Model Types
# =============================================================================


class ABModelType(str, Enum):
    """A/B 테스트 모델 타입."""

    OPENAI = "openai"
    SROBERTA = "sroberta"


# 허용된 모델 타입 (Backend에서 전달 가능한 값)
ALLOWED_MODEL_TYPES = {ABModelType.OPENAI.value, ABModelType.SROBERTA.value}


# =============================================================================
# Model Configuration Mapping
# =============================================================================

# 모델별 설정 매핑
# model → (embedding_model, embedding_dim, collection_name)
# ⚠️ RAGFlow(ctrlf-ragflow) 설정과 동기화 필수!
#    - RAGFlow sample/main.py의 MODEL_DIM_MAP, COLLECTION_NAME_MAP 참조
#    - RAGFlow sample/embedding_provider.py의 모델명 참조
MODEL_CONFIG: Dict[str, Tuple[str, int, str]] = {
    ABModelType.OPENAI.value: (
        "text-embedding-3-large",  # OpenAI 임베딩 모델
        3072,                       # OpenAI 임베딩 차원
        "ragflow_chunks",          # Milvus 컬렉션
    ),
    ABModelType.SROBERTA.value: (
        "jhgan/ko-sroberta-multitask",  # RAGFlow와 동일한 sRoBERTa 모델
        768,                             # RAGFlow와 동일한 차원 (768)
        "ragflow_chunks_sroberta",      # Milvus 컬렉션
    ),
}


# =============================================================================
# In-Memory A/B Context Store
# =============================================================================

# requestId → {"model": str, "timestamp": float}
_ab_context_store: Dict[str, Dict[str, any]] = {}
_store_lock = Lock()

# 캐시 설정
_CACHE_TTL_SECONDS = 3600  # 1시간 (요청 처리 완료 후 정리)
_CACHE_MAX_SIZE = 10000    # 최대 10,000개 요청


def _cleanup_expired_context() -> None:
    """만료된 컨텍스트 정리."""
    now = time.time()
    expired_keys = []

    for key, value in _ab_context_store.items():
        if now - value.get("timestamp", 0) > _CACHE_TTL_SECONDS:
            expired_keys.append(key)

    for key in expired_keys:
        del _ab_context_store[key]

    if expired_keys:
        logger.debug(f"Cleaned up {len(expired_keys)} expired A/B contexts")


def _enforce_cache_size_limit() -> None:
    """캐시 크기 제한 (LRU)."""
    if len(_ab_context_store) <= _CACHE_MAX_SIZE:
        return

    # timestamp 기준 정렬하여 오래된 항목 삭제
    sorted_keys = sorted(
        _ab_context_store.keys(),
        key=lambda k: _ab_context_store[k].get("timestamp", 0)
    )

    excess_count = len(_ab_context_store) - _CACHE_MAX_SIZE
    for key in sorted_keys[:excess_count]:
        del _ab_context_store[key]

    logger.info(f"A/B context cache size enforced: removed {excess_count} oldest entries")


# =============================================================================
# Public API
# =============================================================================


def set_ab_model(request_id: str, model: str) -> bool:
    """
    A/B 테스트 모델을 설정합니다.

    Args:
        request_id: 요청 ID (UUID)
        model: 모델 타입 ("openai" | "sroberta")

    Returns:
        bool: 설정 성공 여부

    Raises:
        ValueError: 허용되지 않은 모델 타입
    """
    if model not in ALLOWED_MODEL_TYPES:
        raise ValueError(
            f"Invalid model type: {model}. "
            f"Allowed: {list(ALLOWED_MODEL_TYPES)}"
        )

    with _store_lock:
        _cleanup_expired_context()

        _ab_context_store[request_id] = {
            "model": model,
            "timestamp": time.time(),
        }

        _enforce_cache_size_limit()

    logger.info(f"[A/B] Set model for request_id={request_id}: model={model}")
    return True


def get_ab_model(request_id: str) -> Optional[str]:
    """
    요청에 대한 A/B 테스트 모델을 조회합니다.

    Args:
        request_id: 요청 ID (UUID)

    Returns:
        Optional[str]: 모델 타입 또는 None (설정 안됨)
    """
    with _store_lock:
        context = _ab_context_store.get(request_id)
        if context:
            return context.get("model")
        return None


def get_model_config(model: str) -> Optional[Tuple[str, int, str]]:
    """
    모델에 대한 설정을 반환합니다.

    Args:
        model: 모델 타입 ("openai" | "sroberta")

    Returns:
        Optional[Tuple[str, int, str]]: (embedding_model, embedding_dim, collection_name) 또는 None
    """
    return MODEL_CONFIG.get(model)


def get_model_config_by_request(
    request_id: Optional[str],
) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """
    요청 ID로 모델 설정을 조회합니다.

    Args:
        request_id: 요청 ID (UUID)

    Returns:
        Tuple[Optional[str], Optional[int], Optional[str]]:
            (embedding_model, embedding_dim, collection_name)
            설정이 없으면 (None, None, None)
    """
    if not request_id:
        return (None, None, None)

    model = get_ab_model(request_id)
    if not model:
        return (None, None, None)

    config = get_model_config(model)
    if not config:
        return (None, None, None)

    return config


def clear_ab_model(request_id: str) -> None:
    """
    요청에 대한 A/B 테스트 모델을 삭제합니다.

    Args:
        request_id: 요청 ID (UUID)
    """
    with _store_lock:
        if request_id in _ab_context_store:
            del _ab_context_store[request_id]
            logger.debug(f"[A/B] Cleared model for request_id={request_id}")


def clear_all_ab_context() -> None:
    """모든 A/B 컨텍스트를 삭제합니다 (테스트용)."""
    with _store_lock:
        _ab_context_store.clear()
        logger.info("[A/B] Cleared all A/B contexts")


def get_ab_context_stats() -> Dict[str, any]:
    """
    A/B 컨텍스트 통계를 반환합니다.

    Returns:
        Dict: 통계 정보
    """
    with _store_lock:
        model_counts = {}
        for context in _ab_context_store.values():
            model = context.get("model", "unknown")
            model_counts[model] = model_counts.get(model, 0) + 1

        return {
            "total": len(_ab_context_store),
            "by_model": model_counts,
        }
