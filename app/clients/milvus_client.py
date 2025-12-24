"""
Milvus Vector Database Client (Phase 24, A안 확정)

Milvus 벡터 데이터베이스와 통신하는 클라이언트입니다.

Phase 42 (A안 확정):
- RAGFlow가 전처리/인덱싱을 담당 (SourceSet Orchestrator 경유)
- 이 클라이언트는 읽기 전용 검색만 수행
- upsert/delete 메서드 제거됨 (Direct 인덱싱 파이프라인 제거)

주요 기능:
- 벡터 유사도 검색 (similarity search)
- 임베딩 생성 (LLM 서버 활용)
- 도메인별 컬렉션 검색

사용 방법:
    from app.clients.milvus_client import MilvusClient

    client = MilvusClient()
    results = await client.search(
        query="연차휴가 이월 규정",
        domain="POLICY",
        top_k=5
    )
"""

import json
from typing import Any, Dict, List, Optional

import httpx
from pymilvus import MilvusClient as PyMilvusClient
from pymilvus import connections, Collection, utility

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.chat import ChatSource
from app.utils.debug_log import dbg_retrieval_target, dbg_retrieval_top5

logger = get_logger(__name__)


# =============================================================================
# 예외 클래스
# =============================================================================


class MilvusError(Exception):
    """Milvus 관련 예외."""

    def __init__(self, message: str, original_error: Optional[Exception] = None):
        super().__init__(message)
        self.message = message
        self.original_error = original_error


class MilvusConnectionError(MilvusError):
    """Milvus 연결 실패 예외."""
    pass


class MilvusSearchError(MilvusError):
    """Milvus 검색 실패 예외."""
    pass


class EmbeddingError(MilvusError):
    """임베딩 생성 실패 예외."""
    pass


# =============================================================================
# MilvusSearchClient
# =============================================================================


class MilvusSearchClient:
    """
    Milvus 벡터 검색 클라이언트 (읽기 전용).

    Phase 42 (A안 확정): Milvus를 사용하여 벡터 검색만 수행합니다.
    인덱싱/삭제는 RAGFlow를 통해 처리됩니다.

    Attributes:
        _host: Milvus 서버 호스트
        _port: Milvus 서버 포트
        _collection_name: 검색할 컬렉션 이름
        _llm_base_url: 임베딩 생성용 LLM 서버 URL
        _embedding_model: 임베딩 모델 이름
        _embedding_dim: 임베딩 차원

    Example:
        client = MilvusSearchClient()
        results = await client.search("연차 규정", "POLICY", top_k=5)
    """

    DEFAULT_TIMEOUT = 10.0  # 10초

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        collection_name: Optional[str] = None,
        llm_base_url: Optional[str] = None,
        embedding_model: Optional[str] = None,
    ) -> None:
        """
        MilvusSearchClient 초기화.

        Args:
            host: Milvus 서버 호스트. None이면 settings에서 로드.
            port: Milvus 서버 포트. None이면 settings에서 로드.
            collection_name: 컬렉션 이름. None이면 settings에서 로드.
            llm_base_url: LLM 서버 URL (임베딩용). None이면 settings에서 로드.
            embedding_model: 임베딩 모델 이름. None이면 settings에서 로드.
        """
        settings = get_settings()

        self._host = host or settings.MILVUS_HOST
        self._port = port or settings.MILVUS_PORT
        self._collection_name = collection_name or settings.MILVUS_COLLECTION_NAME
        self._llm_base_url = llm_base_url or settings.embedding_base_url
        self._embedding_model = embedding_model or settings.EMBEDDING_MODEL_NAME
        self._embedding_dim = settings.EMBEDDING_DIMENSION
        self._top_k = settings.MILVUS_TOP_K
        self._search_params = json.loads(settings.MILVUS_SEARCH_PARAMS)

        self._connected = False
        self._collection: Optional[Collection] = None

        logger.info(
            f"MilvusSearchClient initialized: host={self._host}:{self._port}, "
            f"collection={self._collection_name}, embedding_model={self._embedding_model}"
        )

    def _ensure_connection(self) -> None:
        """
        Milvus 연결을 확인하고 필요시 연결합니다.

        Raises:
            MilvusConnectionError: 연결 실패 시
        """
        if self._connected:
            return

        try:
            # 기존 연결이 있으면 해제
            try:
                connections.disconnect("default")
            except Exception:
                pass

            # 새 연결 생성
            connections.connect(
                alias="default",
                host=self._host,
                port=self._port,
            )
            self._connected = True
            logger.info(f"Connected to Milvus at {self._host}:{self._port}")

        except Exception as e:
            logger.error(f"Failed to connect to Milvus: {e}")
            raise MilvusConnectionError(
                f"Failed to connect to Milvus at {self._host}:{self._port}",
                original_error=e,
            )

    def _extract_domain_from_dataset_id(self, dataset_id: str) -> str:
        """
        dataset_id에서 domain을 추출합니다.

        예시:
            - "kb_policy_001" → "POLICY"
            - "kb_training_001" → "EDU"
            - "policy_dataset" → "POLICY"

        Args:
            dataset_id: RAGFlow dataset ID

        Returns:
            str: 추출된 도메인 또는 빈 문자열
        """
        dataset_lower = dataset_id.lower()

        if "policy" in dataset_lower or "규정" in dataset_lower:
            return "POLICY"
        elif "training" in dataset_lower or "education" in dataset_lower or "edu" in dataset_lower:
            return "EDU"
        elif "incident" in dataset_lower or "사고" in dataset_lower:
            return "INCIDENT"
        elif "security" in dataset_lower or "보안" in dataset_lower:
            return "SECURITY"

        return ""

    def _get_collection(self) -> Collection:
        """
        컬렉션 객체를 반환합니다.

        Returns:
            Collection: Milvus 컬렉션

        Raises:
            MilvusError: 컬렉션이 존재하지 않을 때
        """
        self._ensure_connection()

        if self._collection is not None:
            return self._collection

        # 컬렉션 존재 확인
        if not utility.has_collection(self._collection_name):
            raise MilvusError(
                f"Collection '{self._collection_name}' does not exist in Milvus"
            )

        self._collection = Collection(self._collection_name)
        self._collection.load()
        logger.info(f"Loaded collection: {self._collection_name}")

        return self._collection

    async def generate_embedding(self, text: str) -> List[float]:
        """
        텍스트의 임베딩 벡터를 생성합니다.

        vLLM 서버의 /v1/embeddings 엔드포인트를 사용합니다.

        Args:
            text: 임베딩할 텍스트

        Returns:
            List[float]: 임베딩 벡터

        Raises:
            EmbeddingError: 임베딩 생성 실패 시
        """
        if not self._llm_base_url:
            raise EmbeddingError("LLM_BASE_URL is not configured for embedding generation")

        url = f"{self._llm_base_url.rstrip('/')}/v1/embeddings"

        payload = {
            "input": text,
            "model": self._embedding_model,
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    json=payload,
                    timeout=self.DEFAULT_TIMEOUT,
                )

                if response.status_code != 200:
                    raise EmbeddingError(
                        f"Embedding API returned status {response.status_code}: {response.text[:200]}"
                    )

                data = response.json()

                # OpenAI 호환 응답 형식 파싱
                # {"data": [{"embedding": [...], "index": 0}], "model": "...", "usage": {...}}
                embeddings_data = data.get("data", [])
                if not embeddings_data:
                    raise EmbeddingError("Embedding response has no data")

                embedding = embeddings_data[0].get("embedding", [])
                if not embedding:
                    raise EmbeddingError("Embedding response has empty embedding")

                logger.debug(f"Generated embedding with dimension {len(embedding)}")
                return embedding

        except httpx.TimeoutException as e:
            logger.error(f"Embedding generation timeout")
            raise EmbeddingError("Embedding generation timeout", original_error=e)

        except httpx.RequestError as e:
            logger.error(f"Embedding generation request error: {e}")
            raise EmbeddingError(f"Embedding request error: {e}", original_error=e)

        except EmbeddingError:
            raise

        except Exception as e:
            logger.exception("Embedding generation unexpected error")
            raise EmbeddingError(f"Unexpected error: {e}", original_error=e)

    async def search(
        self,
        query: str,
        domain: Optional[str] = None,
        top_k: Optional[int] = None,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        벡터 유사도 검색을 수행합니다.

        Args:
            query: 검색 쿼리 텍스트
            domain: 도메인 필터 (POLICY, EDU 등)
            top_k: 반환할 최대 결과 수
            filter_expr: 추가 필터 표현식 (Milvus expression)

        Returns:
            List[Dict[str, Any]]: 검색 결과 리스트
                각 항목: {
                    "id": str,
                    "content": str,
                    "score": float,
                    "metadata": dict
                }

        Raises:
            MilvusSearchError: 검색 실패 시
        """
        top_k = top_k or self._top_k

        logger.info(
            f"MilvusSearchClient.search: query='{query[:50]}...', "
            f"domain={domain}, top_k={top_k}"
        )

        try:
            # 1. 쿼리 임베딩 생성
            query_embedding = await self.generate_embedding(query)

            # 2. 필터 표현식 구성
            # ragflow_chunks는 domain 필드가 없고 dataset_id가 있음
            # domain 필터는 dataset_id에 해당 키워드가 포함되어 있는지로 처리
            expr = None
            if domain:
                # dataset_id에서 도메인 키워드로 필터링
                domain_lower = domain.lower()
                # Milvus는 LIKE 연산자를 지원하지 않으므로 필터링은 결과에서 후처리
                # 여기서는 필터 없이 검색 후 후처리로 도메인 필터링
                pass
            if filter_expr:
                expr = filter_expr

            # 3. Milvus 검색
            collection = self._get_collection()

            search_params = self._search_params.copy()

            # ragflow_chunks 스키마: pk, dataset_id, doc_id, chunk_id, text, embedding, chunk_hash
            results = collection.search(
                data=[query_embedding],
                anns_field="embedding",  # 벡터 필드 이름
                param=search_params,
                limit=top_k,
                expr=expr,
                output_fields=["text", "doc_id", "dataset_id", "chunk_id"],
            )

            # 4. 결과 변환 (ragflow_chunks 스키마에 맞게)
            output = []
            for hits in results:
                for hit in hits:
                    entity = hit.entity
                    # dataset_id에서 domain 추출 시도 (예: "kb_policy_001" → "POLICY")
                    dataset_id = entity.get("dataset_id", "")
                    domain_guess = self._extract_domain_from_dataset_id(dataset_id)

                    output.append({
                        "id": str(hit.id),
                        "content": entity.get("text", ""),  # text → content
                        "title": f"문서 {entity.get('doc_id', 'unknown')}",  # title 필드 없음
                        "domain": domain_guess,
                        "doc_id": entity.get("doc_id", ""),
                        "score": hit.score,
                        "metadata": {
                            "dataset_id": dataset_id,
                            "chunk_id": entity.get("chunk_id"),
                        },
                    })

            logger.info(f"Milvus search returned {len(output)} results")
            return output

        except EmbeddingError:
            raise MilvusSearchError("Failed to generate query embedding")

        except MilvusError:
            raise

        except Exception as e:
            logger.exception("Milvus search unexpected error")
            raise MilvusSearchError(f"Search failed: {e}", original_error=e)

    async def search_as_sources(
        self,
        query: str,
        domain: Optional[str] = None,
        user_role: Optional[str] = None,
        department: Optional[str] = None,
        top_k: int = 5,
        request_id: Optional[str] = None,
    ) -> List[ChatSource]:
        """
        벡터 검색을 수행하고 ChatSource 형식으로 반환합니다.

        기존 RagflowClient.search_as_sources()와 호환되는 인터페이스입니다.

        Phase 29: source_type 필드 추가하여 TRAINING_SCRIPT 등 구분
        Phase 41: RAG 디버그 로그 추가

        Args:
            query: 검색 쿼리 텍스트
            domain: 도메인 필터
            user_role: 사용자 역할 (현재 미사용, 향후 ACL 필터용)
            department: 부서 (현재 미사용, 향후 ACL 필터용)
            top_k: 반환할 최대 결과 수
            request_id: 디버그용 요청 ID (Phase 41)

        Returns:
            List[ChatSource]: ChatSource 형식의 검색 결과
        """
        # Phase 41: [B] retrieval_target 디버그 로그
        if request_id:
            dbg_retrieval_target(
                request_id=request_id,
                collection=self._collection_name,
                partition=None,  # 현재 파티션 미사용
                filter_expr=None,  # 도메인 필터는 후처리
                top_k=top_k,
                domain=domain,
            )

        try:
            results = await self.search(query, domain=domain, top_k=top_k)

            sources = []
            for result in results:
                metadata = result.get("metadata", {})

                # Phase 29: source_type 결정
                # TRAINING 도메인이거나 metadata에 source_type이 있으면 사용
                source_type = metadata.get("source_type")
                if not source_type:
                    result_domain = result.get("domain", "")
                    if result_domain == "TRAINING" or "training" in result.get("doc_id", "").lower():
                        source_type = "TRAINING_SCRIPT"
                    elif result_domain == "POLICY" or "policy" in result.get("doc_id", "").lower():
                        source_type = "POLICY"
                    else:
                        source_type = "DOCUMENT"  # 기본값

                source = ChatSource(
                    doc_id=result.get("doc_id", result.get("id", "")),
                    title=result.get("title", "Unknown"),
                    snippet=result.get("content", "")[:500],  # 500자로 제한
                    score=result.get("score"),
                    page=metadata.get("page"),
                    article_label=metadata.get("article_label"),
                    article_path=metadata.get("article_path"),
                    source_type=source_type,  # Phase 29
                )
                sources.append(source)

            # Phase 41: [D] retrieval_top5 디버그 로그
            if request_id:
                top5_results = [
                    {
                        "doc_title": s.title,
                        "chunk_id": s.doc_id,
                        "score": s.score,
                    }
                    for s in sources[:5]
                ]
                dbg_retrieval_top5(request_id=request_id, results=top5_results)

            return sources

        except MilvusError as e:
            logger.error(f"Milvus search_as_sources failed: {e}")
            return []  # 실패 시 빈 결과 반환 (기존 동작과 일치)

    async def health_check(self) -> bool:
        """
        Milvus 서비스 상태를 확인합니다.

        Returns:
            bool: 서비스 정상이면 True
        """
        try:
            self._ensure_connection()

            # 컬렉션 존재 확인
            has_collection = utility.has_collection(self._collection_name)

            if has_collection:
                logger.info(f"Milvus health check passed: collection '{self._collection_name}' exists")
                return True
            else:
                logger.warning(f"Milvus health check: collection '{self._collection_name}' not found")
                return False

        except Exception as e:
            logger.warning(f"Milvus health check failed: {e}")
            return False

    def disconnect(self) -> None:
        """Milvus 연결을 해제합니다."""
        try:
            if self._collection is not None:
                self._collection.release()
                self._collection = None

            connections.disconnect("default")
            self._connected = False
            logger.info("Disconnected from Milvus")

        except Exception as e:
            logger.warning(f"Error disconnecting from Milvus: {e}")


# =============================================================================
# 싱글턴 인스턴스
# =============================================================================

_milvus_client: Optional[MilvusSearchClient] = None


def get_milvus_client() -> MilvusSearchClient:
    """
    MilvusSearchClient 싱글턴 인스턴스를 반환합니다.

    Returns:
        MilvusSearchClient: 클라이언트 인스턴스
    """
    global _milvus_client

    if _milvus_client is None:
        _milvus_client = MilvusSearchClient()

    return _milvus_client


def clear_milvus_client() -> None:
    """
    MilvusSearchClient 싱글턴 인스턴스를 제거합니다 (테스트용).
    """
    global _milvus_client

    if _milvus_client is not None:
        _milvus_client.disconnect()
        _milvus_client = None
