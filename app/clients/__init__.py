"""
HTTP 클라이언트 모듈 (Clients Module)

외부 서비스와 통신하기 위한 클라이언트 계층입니다.

구성:
    - http_client: 공용 httpx.AsyncClient 싱글턴 관리
    - llm_client: LLM 서비스 연동 클라이언트
    - milvus_client: Milvus 벡터 검색 클라이언트 (Phase 24)
    - personalization_client: 개인화 백엔드 연동 클라이언트

Note:
    Heavy imports (MilvusSearchClient 등) are NOT imported at package level
    to avoid import chain side-effects during testing.
    Import directly from submodules when needed:
        from app.clients.milvus_client import MilvusSearchClient
"""

# Lightweight exports only - http_client is relatively light
from app.clients.http_client import (
    close_async_http_client,
    get_async_http_client,
)

__all__ = [
    "get_async_http_client",
    "close_async_http_client",
    # Heavy modules should be imported directly:
    # from app.clients.llm_client import LLMClient
    # from app.clients.milvus_client import MilvusSearchClient, get_milvus_client, ...
    # from app.clients.personalization_client import PersonalizationClient
]
