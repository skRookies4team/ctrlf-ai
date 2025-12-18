"""
HTTP 클라이언트 모듈 (Clients Module)

외부 서비스와 통신하기 위한 클라이언트 계층입니다.

구성:
    - http_client: 공용 httpx.AsyncClient 싱글턴 관리
    - ragflow_client: ctrlf-ragflow 서비스 연동 클라이언트
    - llm_client: LLM 서비스 연동 클라이언트
    - milvus_client: Milvus 벡터 검색 클라이언트 (Phase 24)
"""

from app.clients.http_client import (
    close_async_http_client,
    get_async_http_client,
)
from app.clients.llm_client import LLMClient
from app.clients.milvus_client import (
    MilvusSearchClient,
    get_milvus_client,
    clear_milvus_client,
    MilvusError,
    MilvusConnectionError,
    MilvusSearchError,
    EmbeddingError,
)
from app.clients.ragflow_client import RagflowClient

__all__ = [
    "get_async_http_client",
    "close_async_http_client",
    "RagflowClient",
    "LLMClient",
    # Phase 24: Milvus
    "MilvusSearchClient",
    "get_milvus_client",
    "clear_milvus_client",
    "MilvusError",
    "MilvusConnectionError",
    "MilvusSearchError",
    "EmbeddingError",
]
