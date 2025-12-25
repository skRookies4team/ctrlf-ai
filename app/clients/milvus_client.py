"""
Milvus Vector Database Client (Phase 24 + Option 3 통합)

Milvus 벡터 데이터베이스와 통신하는 클라이언트입니다.

Phase 42 (A안 확정):
- RAGFlow가 전처리/인덱싱을 담당 (SourceSet Orchestrator 경유)
- 이 클라이언트는 읽기 전용 검색만 수행

Option 3 (B안) 통합:
- 검색/텍스트 모두 Milvus에서 직접 조회
- Spring DB 읽기 API 불필요
- RETRIEVAL_BACKEND=milvus 시 활성화

주요 개선사항:
1. 임베딩 계약 검증 (Fail-fast): 앱 시작 시 dim 불일치 감지
2. Pagination: get_document_chunks에서 모든 청크 조회
3. doc_id 안전성: expr escape 적용
4. 성능: pymilvus sync 호출을 anyio.to_thread로 래핑
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import anyio
import httpx
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


class EmbeddingContractError(MilvusError):
    """임베딩 차원 불일치 예외 (Fail-fast)."""
    pass


# =============================================================================
# Utility Functions
# =============================================================================


def escape_milvus_string(value: str) -> str:
    """
    Milvus expr에서 사용할 문자열을 안전하게 escape합니다.

    doc_id 등 사용자 입력이 포함될 수 있는 값에 적용.
    SQL Injection과 유사한 expr injection 방지.

    Args:
        value: escape할 문자열

    Returns:
        str: escape된 문자열
    """
    # 백슬래시 먼저 escape (다른 escape 문자와 충돌 방지)
    value = value.replace("\\", "\\\\")
    # 큰따옴표 escape
    value = value.replace('"', '\\"')
    # 작은따옴표 escape (일부 Milvus 버전에서 사용)
    value = value.replace("'", "\\'")
    return value


def is_safe_doc_id(doc_id: str) -> bool:
    """
    doc_id가 안전한 형식인지 확인합니다.

    UUID, 해시값, 또는 안전한 파일명 패턴만 허용.

    Args:
        doc_id: 검사할 doc_id

    Returns:
        bool: 안전하면 True
    """
    # UUID 패턴 (8-4-4-4-12)
    uuid_pattern = r'^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$'
    # 해시 패턴 (32자 이상 hex)
    hash_pattern = r'^[a-fA-F0-9]{32,}$'
    # 안전한 파일명 패턴 (알파벳, 숫자, 한글, 밑줄, 하이픈, 마침표, 공백)
    safe_filename_pattern = r'^[\w\s가-힣._-]+$'

    if re.match(uuid_pattern, doc_id):
        return True
    if re.match(hash_pattern, doc_id):
        return True
    if re.match(safe_filename_pattern, doc_id) and len(doc_id) <= 500:
        return True

    return False


# =============================================================================
# MilvusSearchClient
# =============================================================================


class MilvusSearchClient:
    """
    Milvus 벡터 검색 클라이언트 (읽기 전용).

    Phase 42 (A안 확정): Milvus를 사용하여 벡터 검색만 수행합니다.
    Option 3: 검색/텍스트 모두 Milvus에서 직접 조회합니다.

    주요 개선사항:
    1. verify_embedding_contract(): 앱 시작 시 dim 검증
    2. get_document_chunks(): pagination으로 전체 청크 조회
    3. escape_milvus_string(): doc_id expr injection 방지
    4. anyio.to_thread: sync pymilvus를 async로 래핑
    """

    DEFAULT_TIMEOUT = 10.0  # 10초
    QUERY_BATCH_SIZE = 1000  # 청크 조회 배치 크기

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
        self._collection_dim: Optional[int] = None  # 실제 컬렉션 dim (검증용)

        logger.info(
            f"MilvusSearchClient initialized: host={self._host}:{self._port}, "
            f"collection={self._collection_name}, embedding_model={self._embedding_model}"
        )

    # =========================================================================
    # Connection Management (sync, wrapped for async)
    # =========================================================================

    def _ensure_connection_sync(self) -> None:
        """
        Milvus 연결을 확인하고 필요시 연결합니다 (sync).

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

    async def _ensure_connection(self) -> None:
        """Milvus 연결을 확인하고 필요시 연결합니다 (async wrapper)."""
        await anyio.to_thread.run_sync(self._ensure_connection_sync)

    def _get_collection_sync(self) -> Collection:
        """
        컬렉션 객체를 반환합니다 (sync).

        Returns:
            Collection: Milvus 컬렉션

        Raises:
            MilvusError: 컬렉션이 존재하지 않을 때
        """
        self._ensure_connection_sync()

        if self._collection is not None:
            return self._collection

        # 컬렉션 존재 확인
        if not utility.has_collection(self._collection_name):
            raise MilvusError(
                f"Collection '{self._collection_name}' does not exist in Milvus"
            )

        self._collection = Collection(self._collection_name)
        self._collection.load()

        # 컬렉션 스키마에서 embedding dim 추출
        for field in self._collection.schema.fields:
            if hasattr(field, 'dim') and field.dim:
                self._collection_dim = field.dim
                logger.info(f"Collection embedding dim: {self._collection_dim}")
                break

        logger.info(f"Loaded collection: {self._collection_name}")
        return self._collection

    def _get_collection(self) -> Collection:
        """컬렉션 객체를 반환합니다 (sync, 기존 호환성)."""
        return self._get_collection_sync()

    async def _get_collection_async(self) -> Collection:
        """컬렉션 객체를 반환합니다 (async wrapper)."""
        return await anyio.to_thread.run_sync(self._get_collection_sync)

    # =========================================================================
    # Embedding Contract Verification (Fail-fast)
    # =========================================================================

    async def verify_embedding_contract(self) -> Tuple[bool, str]:
        """
        임베딩 계약을 검증합니다 (Fail-fast).

        앱 시작 시 호출하여:
        1. Milvus 컬렉션 스키마에서 embedding dim 읽기
        2. 임베딩 서버에서 샘플 임베딩 생성
        3. dim 비교 → 불일치 시 명확한 오류 반환

        Returns:
            Tuple[bool, str]: (성공 여부, 메시지)

        Raises:
            EmbeddingContractError: dim 불일치 시 (fail-fast 모드)
        """
        logger.info("Verifying embedding contract...")

        try:
            # 1. 컬렉션에서 dim 확인
            collection = await self._get_collection_async()

            if self._collection_dim is None:
                # 스키마에서 embedding 필드 찾기
                for field in collection.schema.fields:
                    if hasattr(field, 'dim') and field.dim:
                        self._collection_dim = field.dim
                        break

            if self._collection_dim is None:
                return False, "Collection has no vector field with dimension"

            # 2. 샘플 임베딩 생성
            sample_embedding = await self.generate_embedding("테스트 임베딩 검증")
            actual_dim = len(sample_embedding)

            # 3. dim 비교
            if actual_dim != self._collection_dim:
                error_msg = (
                    f"Embedding dimension mismatch! "
                    f"Collection expects {self._collection_dim}, "
                    f"but embedder produced {actual_dim}. "
                    f"Check EMBEDDING_MODEL_NAME and EMBEDDING_DIMENSION in .env"
                )
                logger.error(error_msg)
                raise EmbeddingContractError(error_msg)

            success_msg = (
                f"Embedding contract verified: dim={actual_dim}, "
                f"collection={self._collection_name}, "
                f"model={self._embedding_model}"
            )
            logger.info(success_msg)
            return True, success_msg

        except EmbeddingContractError:
            raise

        except Exception as e:
            error_msg = f"Embedding contract verification failed: {e}"
            logger.error(error_msg)
            return False, error_msg

    # =========================================================================
    # Embedding Generation
    # =========================================================================

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
            raise EmbeddingError("EMBEDDING_BASE_URL is not configured")

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
                embeddings_data = data.get("data", [])
                if not embeddings_data:
                    raise EmbeddingError("Embedding response has no data")

                embedding = embeddings_data[0].get("embedding", [])
                if not embedding:
                    raise EmbeddingError("Embedding response has empty embedding")

                logger.debug(f"Generated embedding with dimension {len(embedding)}")
                return embedding

        except httpx.TimeoutException as e:
            logger.error("Embedding generation timeout")
            raise EmbeddingError("Embedding generation timeout", original_error=e)

        except httpx.RequestError as e:
            logger.error(f"Embedding generation request error: {e}")
            raise EmbeddingError(f"Embedding request error: {e}", original_error=e)

        except EmbeddingError:
            raise

        except Exception as e:
            logger.exception("Embedding generation unexpected error")
            raise EmbeddingError(f"Unexpected error: {e}", original_error=e)

    # =========================================================================
    # Vector Search
    # =========================================================================

    def _extract_domain_from_dataset_id(self, dataset_id: str) -> str:
        """dataset_id에서 domain을 추출합니다."""
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

    def _search_sync(
        self,
        query_embedding: List[float],
        top_k: int,
        expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Milvus 검색 (sync)."""
        collection = self._get_collection_sync()
        search_params = self._search_params.copy()

        results = collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=top_k,
            expr=expr,
            output_fields=["text", "doc_id", "dataset_id", "chunk_id"],
        )

        output = []
        for hits in results:
            for hit in hits:
                entity = hit.entity
                dataset_id = entity.get("dataset_id", "")
                domain_guess = self._extract_domain_from_dataset_id(dataset_id)

                output.append({
                    "id": str(hit.id),
                    "content": entity.get("text", ""),
                    "title": entity.get("doc_id", "unknown"),
                    "domain": domain_guess,
                    "doc_id": entity.get("doc_id", ""),
                    "score": hit.score,
                    "metadata": {
                        "dataset_id": dataset_id,
                        "chunk_id": entity.get("chunk_id"),
                    },
                })

        return output

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
            domain: 도메인 필터 (현재 후처리용, Milvus expr 미사용)
            top_k: 반환할 최대 결과 수
            filter_expr: 추가 필터 표현식 (Milvus expression)

        Returns:
            List[Dict[str, Any]]: 검색 결과 리스트
        """
        top_k = top_k or self._top_k

        logger.info(
            f"MilvusSearchClient.search: query='{query[:50]}...', "
            f"domain={domain}, top_k={top_k}"
        )

        try:
            # 1. 쿼리 임베딩 생성
            query_embedding = await self.generate_embedding(query)

            # 2. Milvus 검색 (sync → async)
            output = await anyio.to_thread.run_sync(
                lambda: self._search_sync(query_embedding, top_k, filter_expr)
            )

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
        """
        # Phase 41: [B] retrieval_target 디버그 로그
        if request_id:
            dbg_retrieval_target(
                request_id=request_id,
                collection=self._collection_name,
                partition=None,
                filter_expr=None,
                top_k=top_k,
                domain=domain,
            )

        try:
            results = await self.search(query, domain=domain, top_k=top_k)

            sources = []
            for result in results:
                metadata = result.get("metadata", {})

                # source_type 결정
                source_type = metadata.get("source_type")
                if not source_type:
                    result_domain = result.get("domain", "")
                    if result_domain == "TRAINING" or "training" in result.get("doc_id", "").lower():
                        source_type = "TRAINING_SCRIPT"
                    elif result_domain == "POLICY" or "policy" in result.get("doc_id", "").lower():
                        source_type = "POLICY"
                    else:
                        source_type = "DOCUMENT"

                source = ChatSource(
                    doc_id=result.get("doc_id", result.get("id", "")),
                    title=result.get("title", "Unknown"),
                    snippet=result.get("content", "")[:500],
                    score=result.get("score"),
                    page=metadata.get("page"),
                    article_label=metadata.get("article_label"),
                    article_path=metadata.get("article_path"),
                    source_type=source_type,
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
            return []

    # =========================================================================
    # Document Chunks (Pagination 지원)
    # =========================================================================

    def _query_chunks_sync(
        self,
        expr: str,
        output_fields: List[str],
        offset: int = 0,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """청크 조회 (sync, 단일 배치)."""
        collection = self._get_collection_sync()
        return collection.query(
            expr=expr,
            output_fields=output_fields,
            offset=offset,
            limit=limit,
        )

    async def get_document_chunks(
        self,
        doc_id: str,
        dataset_id: Optional[str] = None,
        max_chunks: int = 10000,
    ) -> List[Dict[str, Any]]:
        """
        문서의 모든 청크를 chunk_id 순서로 조회합니다 (Pagination 지원).

        Args:
            doc_id: 문서 ID (파일명 또는 문서 식별자)
            dataset_id: 데이터셋 ID (선택)
            max_chunks: 최대 청크 수 (안전장치, 기본 10000)

        Returns:
            List[Dict[str, Any]]: chunk_id로 정렬된 청크 리스트

        Raises:
            MilvusError: 조회 실패 시
        """
        logger.info(f"get_document_chunks: doc_id='{doc_id}', dataset_id={dataset_id}")

        # doc_id 안전성 검사 및 escape
        if not is_safe_doc_id(doc_id):
            logger.warning(f"Potentially unsafe doc_id, escaping: {doc_id[:50]}")
        safe_doc_id = escape_milvus_string(doc_id)

        try:
            # 필터 표현식 구성
            expr = f'doc_id == "{safe_doc_id}"'
            if dataset_id:
                safe_dataset_id = escape_milvus_string(dataset_id)
                expr = f'{expr} && dataset_id == "{safe_dataset_id}"'

            output_fields = ["chunk_id", "text", "doc_id", "dataset_id"]
            all_chunks: List[Dict[str, Any]] = []
            offset = 0

            # Pagination으로 전체 청크 조회
            while len(all_chunks) < max_chunks:
                batch = await anyio.to_thread.run_sync(
                    lambda: self._query_chunks_sync(
                        expr, output_fields, offset, self.QUERY_BATCH_SIZE
                    )
                )

                if not batch:
                    break

                all_chunks.extend(batch)
                offset += len(batch)

                # 배치 크기보다 적게 반환되면 끝
                if len(batch) < self.QUERY_BATCH_SIZE:
                    break

                logger.debug(f"Fetched {len(all_chunks)} chunks so far...")

            if not all_chunks:
                logger.warning(f"No chunks found for doc_id='{doc_id}'")
                return []

            # chunk_id로 정렬
            sorted_chunks = sorted(all_chunks, key=lambda x: x.get("chunk_id", 0))

            logger.info(f"Retrieved {len(sorted_chunks)} chunks for doc_id='{doc_id}'")
            return sorted_chunks

        except MilvusError:
            raise

        except Exception as e:
            logger.exception(f"Failed to get document chunks: {e}")
            raise MilvusError(f"Failed to get document chunks: {e}", original_error=e)

    async def get_full_document_text(
        self,
        doc_id: str,
        dataset_id: Optional[str] = None,
    ) -> str:
        """
        문서의 전체 텍스트를 chunk_id 순서로 합쳐서 반환합니다.

        Args:
            doc_id: 문서 ID
            dataset_id: 데이터셋 ID (선택)

        Returns:
            str: 전체 문서 텍스트 (청크 순서대로 연결)
        """
        chunks = await self.get_document_chunks(doc_id, dataset_id)
        if not chunks:
            return ""

        texts = [chunk.get("text", "") for chunk in chunks]
        return "\n\n".join(texts)

    # =========================================================================
    # Health Check
    # =========================================================================

    async def health_check(self) -> bool:
        """Milvus 서비스 상태를 확인합니다."""
        try:
            await self._ensure_connection()

            def check_sync():
                return utility.has_collection(self._collection_name)

            has_collection = await anyio.to_thread.run_sync(check_sync)

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
    """MilvusSearchClient 싱글턴 인스턴스를 반환합니다."""
    global _milvus_client

    if _milvus_client is None:
        _milvus_client = MilvusSearchClient()

    return _milvus_client


def clear_milvus_client() -> None:
    """MilvusSearchClient 싱글턴 인스턴스를 제거합니다 (테스트용)."""
    global _milvus_client

    if _milvus_client is not None:
        _milvus_client.disconnect()
        _milvus_client = None
