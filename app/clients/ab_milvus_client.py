"""
A/B 테스트용 Milvus 클라이언트

A/B 테스트 모델 선택에 따라 임베딩/컬렉션을 동적으로 전환하는 Milvus 클라이언트입니다.

사용 흐름 (방식 B - 권장):
1. Backend → AI: POST /chat {session_id, query, model: "sroberta"}
2. AI: get_milvus_client_by_model(model)로 직접 클라이언트 선택
3. 모델에 따라 적절한 Milvus 클라이언트 인스턴스 반환:
   - openai: OpenAI text-embedding-3-large, ragflow_chunks_openai
   - sroberta: SRoberta 임베딩, ragflow_chunks_sroberta

Phase AB: A/B 테스트 지원
- 기존 MilvusSearchClient를 래핑하여 A/B 분기 제공
- 동적으로 컬렉션/임베딩 모델 전환
- model 필드를 직접 사용하는 간소화된 API
"""

import json
from typing import Any, Dict, List, Optional

import anyio
import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.ab_context import (
    get_ab_model,
    get_model_config,
    ABModelType,
    MODEL_CONFIG,
)
from app.models.chat import ChatSource
from app.clients.milvus_client import (
    MilvusSearchClient,
    MilvusError,
    MilvusSearchError,
    EmbeddingError,
    get_milvus_client,
)

logger = get_logger(__name__)


# =============================================================================
# A/B Test Milvus Client Factory
# =============================================================================

# 모델별 클라이언트 캐시 (싱글톤)
_ab_clients: Dict[str, MilvusSearchClient] = {}


def _create_client_for_model(model: str) -> MilvusSearchClient:
    """
    특정 A/B 모델에 대한 Milvus 클라이언트를 생성합니다.

    Args:
        model: A/B 모델 타입 ("openai" | "sroberta")

    Returns:
        MilvusSearchClient: 모델별 설정이 적용된 클라이언트
    """
    settings = get_settings()
    config = MODEL_CONFIG.get(model)

    if not config:
        # 알 수 없는 모델이면 기본 클라이언트 반환
        logger.warning(f"[A/B] Unknown model '{model}', using default client")
        return get_milvus_client()

    embedding_model, embedding_dim, collection_name = config

    logger.info(
        f"[A/B] Creating client for model={model}: "
        f"embedding={embedding_model}, dim={embedding_dim}, collection={collection_name}"
    )

    # 새 클라이언트 생성
    client = MilvusSearchClient(
        host=settings.MILVUS_HOST,
        port=settings.MILVUS_PORT,
        collection_name=collection_name,
        embedding_model=embedding_model,
    )

    # 모델별 임베딩 설정 오버라이드
    if model == ABModelType.OPENAI.value:
        # OpenAI 임베딩 사용
        client._openai_api_key = settings.OPENAI_API_KEY
        client._llm_base_url = "https://api.openai.com"
        client._embedding_model = settings.OPENAI_EMBED_MODEL
        client._embedding_dim = settings.OPENAI_EMBED_DIM
    elif model == ABModelType.SROBERTA.value:
        # SRoberta 임베딩 사용 (별도 서버 또는 로컬)
        client._openai_api_key = None  # OpenAI 사용 안함
        sroberta_url = getattr(settings, "SROBERTA_EMBED_URL", None)
        client._llm_base_url = sroberta_url or settings.embedding_base_url
        client._embedding_model = getattr(
            settings, "SROBERTA_EMBED_MODEL",
            "jhgan/ko-sroberta-multitask"  # RAGFlow와 동일
        )
        client._embedding_dim = getattr(settings, "SROBERTA_EMBED_DIM", 768)  # RAGFlow와 동일

    return client


def get_milvus_client_by_model(model: Optional[str] = None) -> MilvusSearchClient:
    """
    모델 타입에 따른 Milvus 클라이언트를 반환합니다. (권장 API)

    방식 B: 요청에 포함된 model 필드를 직접 사용하여 클라이언트를 선택합니다.
    별도의 A/B context API 호출 없이 단일 API 호출로 A/B 테스트를 수행할 수 있습니다.

    Args:
        model: A/B 모델 타입 ("openai" | "sroberta" | None)
               None이면 기본 클라이언트 반환

    Returns:
        MilvusSearchClient: 모델에 맞는 클라이언트

    사용 예시:
        ```python
        # ChatRequest.model 직접 사용
        client = get_milvus_client_by_model(req.model)
        sources = await client.search_as_sources(query, domain, ...)
        ```
    """
    global _ab_clients

    # model이 없으면 기본 클라이언트 반환
    if not model:
        logger.debug("[A/B] No model specified, using default client")
        return get_milvus_client()

    logger.info(f"[A/B] Using model={model}")

    # 캐시된 클라이언트 반환 또는 새로 생성
    if model not in _ab_clients:
        _ab_clients[model] = _create_client_for_model(model)

    return _ab_clients[model]


def get_ab_milvus_client(request_id: Optional[str] = None) -> MilvusSearchClient:
    """
    A/B 테스트 컨텍스트에 따른 Milvus 클라이언트를 반환합니다.

    .. deprecated::
        방식 A (별도 context API) 사용 시에만 필요합니다.
        방식 B (권장)에서는 get_milvus_client_by_model()을 사용하세요.

    Args:
        request_id: 요청 ID (A/B 컨텍스트 조회용)

    Returns:
        MilvusSearchClient: A/B 모델에 맞는 클라이언트
    """
    global _ab_clients

    # request_id가 없으면 기본 클라이언트 반환
    if not request_id:
        logger.debug("[A/B] No request_id, using default client")
        return get_milvus_client()

    # A/B 컨텍스트 조회
    model = get_ab_model(request_id)

    if not model:
        # A/B 설정이 없으면 기본 클라이언트 반환
        logger.debug(f"[A/B] No A/B context for request_id={request_id}, using default")
        return get_milvus_client()

    logger.info(f"[A/B] Request {request_id} using model={model}")

    # 캐시된 클라이언트 반환 또는 새로 생성
    if model not in _ab_clients:
        _ab_clients[model] = _create_client_for_model(model)

    return _ab_clients[model]


def clear_ab_milvus_clients() -> None:
    """A/B 테스트 클라이언트 캐시를 클리어합니다 (테스트용)."""
    global _ab_clients

    for client in _ab_clients.values():
        try:
            client.disconnect()
        except Exception:
            pass

    _ab_clients.clear()
    logger.info("[A/B] Cleared all A/B Milvus clients")


# =============================================================================
# A/B Test Search Helper Functions
# =============================================================================


async def ab_search_as_sources(
    query: str,
    domain: Optional[str] = None,
    user_role: Optional[str] = None,
    department: Optional[str] = None,
    top_k: int = 5,
    request_id: Optional[str] = None,
) -> List[ChatSource]:
    """
    A/B 테스트 컨텍스트를 고려한 벡터 검색을 수행합니다.

    기존 search_as_sources의 A/B 래퍼입니다.
    request_id로 A/B 컨텍스트를 조회하여 적절한 클라이언트를 선택합니다.

    Args:
        query: 검색 쿼리
        domain: 도메인 필터
        user_role: 사용자 역할 (현재 미사용)
        department: 부서 (현재 미사용)
        top_k: 반환할 최대 결과 수
        request_id: 요청 ID (A/B 컨텍스트 조회용)

    Returns:
        List[ChatSource]: 검색 결과
    """
    client = get_ab_milvus_client(request_id)

    # A/B 모델 정보 로깅
    model = get_ab_model(request_id) if request_id else None
    if model:
        logger.info(
            f"[A/B Search] request_id={request_id}, model={model}, "
            f"collection={client._collection_name}, "
            f"embedding={client._embedding_model}"
        )

    return await client.search_as_sources(
        query=query,
        domain=domain,
        user_role=user_role,
        department=department,
        top_k=top_k,
        request_id=request_id,
    )


def get_client_info_by_model(model: Optional[str] = None) -> Dict[str, Any]:
    """
    모델 타입에 대한 클라이언트 정보를 반환합니다. (권장 API)

    디버깅/모니터링용.

    Args:
        model: A/B 모델 타입 ("openai" | "sroberta" | None)

    Returns:
        Dict: 클라이언트 설정 정보
    """
    config = get_model_config(model) if model else None

    if config:
        embedding_model, embedding_dim, collection_name = config
        return {
            "model": model,
            "embedding_model": embedding_model,
            "embedding_dim": embedding_dim,
            "collection_name": collection_name,
            "is_ab_test": True,
        }

    # 기본 설정
    settings = get_settings()
    return {
        "model": None,
        "embedding_model": settings.OPENAI_EMBED_MODEL if settings.OPENAI_API_KEY else settings.EMBEDDING_MODEL_NAME,
        "embedding_dim": settings.OPENAI_EMBED_DIM if settings.OPENAI_API_KEY else settings.EMBEDDING_DIMENSION,
        "collection_name": settings.MILVUS_COLLECTION_NAME,
        "is_ab_test": False,
    }


def get_ab_client_info(request_id: Optional[str] = None) -> Dict[str, Any]:
    """
    현재 A/B 컨텍스트의 클라이언트 정보를 반환합니다.

    .. deprecated::
        방식 A (별도 context API) 사용 시에만 필요합니다.
        방식 B (권장)에서는 get_client_info_by_model()을 사용하세요.

    Args:
        request_id: 요청 ID

    Returns:
        Dict: 클라이언트 설정 정보
    """
    model = get_ab_model(request_id) if request_id else None
    info = get_client_info_by_model(model)
    info["request_id"] = request_id
    return info
