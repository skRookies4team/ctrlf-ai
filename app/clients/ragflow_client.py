"""
RAGFlow 클라이언트 모듈 (RAGFlow Client Module)

ctrlf-ragflow 서비스와 통신하는 클라이언트입니다.
실제 문서 전처리/인덱싱은 ctrlf-ragflow 레포에서 수행되며,
이 클라이언트는 "검색 API만 사용하는 클라이언트" 역할을 합니다.

Phase 9 업데이트:
- /retrieval_test 엔드포인트에 맞춰 search() 메서드 수정
- dataset → kb_id 변환 헬퍼 메서드 추가 (_dataset_to_kb_id)
- 응답 형식 변환: chunks → RagDocument
- 나중에 /search 래퍼가 생기면 엔드포인트만 교체 가능한 구조

Phase 12 업데이트:
- UpstreamServiceError로 에러 래핑 (옵션: raise_on_error)
- 재시도 로직 추가 (1회)
- 개별 latency 측정

사용 방법:
    from app.clients.ragflow_client import RagflowClient

    client = RagflowClient()

    # 헬스체크
    is_healthy = await client.health()

    # 문서 검색 (현재: /retrieval_test, 향후: /search)
    docs = await client.search(
        query="연차휴가 이월 규정",
        top_k=5,
        dataset="POLICY"
    )
"""

import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.clients.http_client import get_async_http_client
from app.core.config import get_settings
from app.core.exceptions import ErrorType, ServiceType, UpstreamServiceError
from app.core.logging import get_logger
from app.core.retry import DEFAULT_RAGFLOW_TIMEOUT, RAGFLOW_RETRY_CONFIG, retry_async_operation
from app.models.chat import ChatSource
from app.models.ingest import IngestRequest, IngestResponse
from app.models.rag import RagDocument, RagProcessRequest, RagProcessResponse

logger = get_logger(__name__)


class RagflowError(Exception):
    """RAGFlow 클라이언트 관련 기본 예외."""
    pass


class RagflowConnectionError(RagflowError):
    """RAGFlow 서버 연결 실패 예외."""
    pass


class RagflowSearchError(RagflowError):
    """RAGFlow 검색 실패 예외."""
    pass


class RagflowClient:
    """
    ctrlf-ragflow 서비스와 통신하는 클라이언트.

    RAGFlow 서버의 검색 API를 호출하여 관련 문서를 검색합니다.
    검색 결과를 RagDocument 또는 ChatSource 모델로 정규화하여 반환합니다.

    현재 ctrlf-ragflow는 /retrieval_test 엔드포인트를 제공하며,
    향후 /search 래퍼가 추가되면 USE_SEARCH_WRAPPER 플래그로 전환 가능합니다.

    Phase 12 업데이트:
    - search_with_latency() 메서드 추가
    - 에러 발생 시 UpstreamServiceError 래핑 (옵션)
    - 재시도 로직 추가

    Attributes:
        _base_url: RAGFlow 서비스 기본 URL (RAGFLOW_BASE_URL 환경변수)
        _client: httpx.AsyncClient 인스턴스
        _timeout: HTTP 요청 타임아웃 (초)

    Example:
        client = RagflowClient()
        docs = await client.search(query="연차 규정", top_k=5)
        is_healthy = await client.health()
    """

    # 기본 타임아웃 설정 (초)
    DEFAULT_TIMEOUT = DEFAULT_RAGFLOW_TIMEOUT

    # RAGFlow /search 래퍼 사용 (2024-12 추가됨)
    # False로 설정하면 기존 /retrieval_test 직접 호출
    USE_SEARCH_WRAPPER = True

    # dataset(도메인) → kb_id 매핑 테이블
    # TODO: 실제 kb_id가 확정되면 업데이트 필요
    # TODO: 향후 .env 또는 설정 파일에서 로드하도록 개선 가능
    DATASET_TO_KB_ID: Dict[str, str] = {
        "POLICY": "kb_policy_001",
        "INCIDENT": "kb_incident_001",
        "EDUCATION": "kb_education_001",
    }

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """
        RagflowClient 초기화.

        Args:
            base_url: RAGFlow 서비스 URL. None이면 settings.ragflow_base_url 사용.
            timeout: HTTP 요청 타임아웃 (초). 기본값 10초.
            client: httpx.AsyncClient 인스턴스. None이면 공용 클라이언트 사용.

        Note:
            Phase 9: AI_ENV 환경변수에 따라 mock/real URL이 자동 선택됩니다.
        """
        settings = get_settings()
        # Phase 9: ragflow_base_url 프로퍼티 사용 (mock/real 모드 자동 선택)
        self._base_url = base_url if base_url is not None else settings.ragflow_base_url
        self._timeout = timeout
        self._client = client or get_async_http_client()

        if not self._base_url:
            logger.warning(
                "RAGFlow URL is not configured. "
                "RAGFlow API calls will be skipped and return empty results."
            )

    def _dataset_to_kb_id(self, dataset: Optional[str]) -> str:
        """
        도메인(dataset)을 지식베이스 ID(kb_id)로 변환합니다.

        Args:
            dataset: 도메인 이름 (예: "POLICY", "INCIDENT", "EDUCATION")
                     None이면 "POLICY"로 처리

        Returns:
            str: 해당 도메인의 지식베이스 ID

        Note:
            매핑 테이블은 DATASET_TO_KB_ID 클래스 변수에 정의되어 있습니다.
            RAGFlow 팀에서 실제 kb_id를 확정하면 이 매핑을 업데이트해야 합니다.
        """
        key = (dataset or "POLICY").upper()
        return self.DATASET_TO_KB_ID.get(key, self.DATASET_TO_KB_ID["POLICY"])

    async def health(self) -> bool:
        """
        RAGFlow 서비스 상태를 헬스체크합니다.

        BASE_URL이 설정되지 않은 경우 False를 반환합니다.

        Returns:
            bool: RAGFlow 서비스가 정상이면 True, 그렇지 않으면 False
        """
        if not self._base_url:
            logger.warning("RAGFLOW_BASE_URL is not set, skipping health check")
            return False

        try:
            url = f"{self._base_url}/health"
            resp = await self._client.get(url, timeout=self._timeout)
            ok = resp.status_code == 200
            if not ok:
                logger.error(
                    "RAGFlow health check failed: status=%s", resp.status_code
                )
            return ok
        except httpx.TimeoutException:
            logger.error("RAGFlow health check timeout")
            return False
        except Exception as e:
            logger.exception("RAGFlow health check error: %s", e)
            return False

    # Alias for backward compatibility
    async def health_check(self) -> bool:
        """health() 메서드의 별칭 (하위 호환성)."""
        return await self.health()

    async def search(
        self,
        query: str,
        top_k: int = 5,
        dataset: Optional[str] = None,
        domain: Optional[str] = None,
        user_role: Optional[str] = None,
        department: Optional[str] = None,
    ) -> List[RagDocument]:
        """
        RAGFlow에서 관련 문서를 검색합니다.

        현재 ctrlf-ragflow의 /retrieval_test 엔드포인트를 호출합니다.
        향후 /search 래퍼가 추가되면 USE_SEARCH_WRAPPER 플래그로 전환 가능합니다.

        Args:
            query: 검색 쿼리 텍스트
            top_k: 반환할 최대 문서 수 (기본값: 5)
            dataset: 검색할 데이터셋/컬렉션 이름 (예: "POLICY", "INCIDENT")
            domain: dataset의 별칭 (dataset이 None일 때 사용)
            user_role: ACL 필터용 사용자 역할 (현재 미사용, 향후 확장)
            department: ACL 필터용 부서 (현재 미사용, 향후 확장)

        Returns:
            List[RagDocument]: 검색된 문서 리스트

        Note:
            - BASE_URL 미설정 시 빈 리스트 반환 (예외 발생 안 함)
            - HTTP 에러 발생 시 빈 리스트 반환하고 로그에 경고 기록
            - 이 동작은 ChatService에서 RAG 없이 LLM-only로 진행할 수 있게 함
        """
        if not self._base_url:
            logger.warning("RAGFlow search skipped: base_url not configured")
            return []

        # dataset과 domain 중 하나를 사용 (dataset 우선)
        effective_dataset = dataset or domain

        # 엔드포인트 선택: /search (래퍼) 또는 /retrieval_test (현재)
        if self.USE_SEARCH_WRAPPER:
            return await self._search_via_wrapper(
                query=query,
                top_k=top_k,
                dataset=effective_dataset,
                user_role=user_role,
                department=department,
            )
        else:
            return await self._search_via_retrieval_test(
                query=query,
                top_k=top_k,
                dataset=effective_dataset,
            )

    async def _search_via_retrieval_test(
        self,
        query: str,
        top_k: int,
        dataset: Optional[str],
        raise_on_error: bool = False,
    ) -> List[RagDocument]:
        """
        /retrieval_test 엔드포인트를 통한 검색 (현재 사용 중).

        ctrlf-ragflow의 실제 API 스펙에 맞춰 요청을 구성합니다.

        Phase 12: raise_on_error=True이면 에러 시 UpstreamServiceError를 raise합니다.

        요청 형식:
            POST /retrieval_test
            {
                "question": "검색 쿼리",
                "kb_id": "지식베이스 ID",
                "size": 5,
                "page": 1
            }

        응답 형식:
            {
                "chunks": [
                    {
                        "chunk_id": "...",
                        "doc_name": "...",
                        "page_num": 12,
                        "similarity": 0.92,
                        "content": "..."
                    }
                ]
            }
        """
        # dataset → kb_id 변환
        kb_id = self._dataset_to_kb_id(dataset)

        url = f"{self._base_url}/v1/chunk/retrieval_test"
        payload: Dict[str, Any] = {
            "question": query,  # query → question
            "kb_id": kb_id,     # dataset → kb_id
            "size": top_k,      # top_k → size
            "page": 1,          # 첫 페이지 고정
        }

        logger.info(
            f"Searching RAGFlow (retrieval_test): query='{query[:50]}...', "
            f"kb_id={kb_id}, size={top_k}"
        )

        try:
            # Phase 12: 재시도 로직 적용
            response = await retry_async_operation(
                self._client.post,
                url,
                json=payload,
                timeout=self._timeout,
                config=RAGFLOW_RETRY_CONFIG,
                operation_name="ragflow_search",
            )
            response.raise_for_status()
            data = response.json()

            # 응답 파싱: chunks → RagDocument
            chunks = data.get("chunks", [])
            documents: List[RagDocument] = []

            for chunk in chunks:
                try:
                    # Phase 13: metadata에서 조항 정보 추출
                    # RAGFlow 응답의 metadata/fields/extra에 조항 정보가 있을 수 있음
                    metadata = chunk.get("metadata", {}) or {}
                    fields = chunk.get("fields", {}) or {}
                    extra = chunk.get("extra", {}) or {}

                    # 조항 정보를 여러 가능한 키에서 추출 (graceful degradation)
                    section_label = (
                        metadata.get("section_title")
                        or metadata.get("section_label")
                        or fields.get("section_title")
                        or extra.get("section_title")
                    )
                    section_path = (
                        metadata.get("section_path")
                        or fields.get("section_path")
                        or extra.get("section_path")
                    )
                    article_id = (
                        metadata.get("article_number")
                        or metadata.get("article_id")
                        or fields.get("article_number")
                        or extra.get("article_number")
                    )
                    clause_id = (
                        metadata.get("clause_number")
                        or metadata.get("clause_id")
                        or fields.get("clause_number")
                        or extra.get("clause_number")
                    )

                    doc = RagDocument(
                        doc_id=chunk.get("chunk_id") or chunk.get("id", "unknown"),
                        title=chunk.get("doc_name") or chunk.get("document_name", "Untitled"),
                        page=chunk.get("page_num") or chunk.get("page"),
                        score=chunk.get("similarity") or chunk.get("score", 0.0),
                        snippet=chunk.get("content") or chunk.get("text", ""),
                        # Phase 13: 조항 메타데이터
                        section_label=section_label,
                        section_path=section_path,
                        article_id=article_id,
                        clause_id=clause_id,
                    )
                    documents.append(doc)
                except Exception as e:
                    logger.warning(f"Failed to parse chunk: {e}")
                    continue

            # TODO: RAGFlow 검색 응답 metadata에
            #  - section_title / section_path / article_number / clause_number
            #  등이 추가되면 여기서 RagDocument에 자동으로 매핑됨.
            #  현재 RAGFlow에서 해당 메타를 보내지 않으면 모두 None으로 처리됨.

            logger.info(f"RAGFlow search returned {len(documents)} documents")
            return documents

        except httpx.TimeoutException as e:
            logger.error(f"RAGFlow search timeout after {self._timeout}s")
            if raise_on_error:
                raise UpstreamServiceError(
                    service=ServiceType.RAGFLOW,
                    error_type=ErrorType.UPSTREAM_TIMEOUT,
                    message=f"RAGFlow timeout after {self._timeout}s",
                    is_timeout=True,
                    original_error=e,
                )
            return []

        except httpx.HTTPStatusError as e:
            logger.error(
                f"RAGFlow search HTTP error: status={e.response.status_code}, "
                f"detail={e.response.text[:200] if e.response.text else 'N/A'}"
            )
            if raise_on_error:
                raise UpstreamServiceError(
                    service=ServiceType.RAGFLOW,
                    error_type=ErrorType.UPSTREAM_ERROR,
                    message=f"RAGFlow HTTP {e.response.status_code}",
                    status_code=e.response.status_code,
                    original_error=e,
                )
            return []

        except httpx.RequestError as e:
            logger.error(f"RAGFlow search request error: {e}")
            if raise_on_error:
                raise UpstreamServiceError(
                    service=ServiceType.RAGFLOW,
                    error_type=ErrorType.UPSTREAM_ERROR,
                    message=f"RAGFlow request error: {type(e).__name__}",
                    original_error=e,
                )
            return []

        except Exception as e:
            logger.exception("RAGFlow search unexpected error")
            if raise_on_error:
                raise UpstreamServiceError(
                    service=ServiceType.RAGFLOW,
                    error_type=ErrorType.INTERNAL_ERROR,
                    message=f"RAGFlow unexpected error: {type(e).__name__}",
                    original_error=e,
                )
            return []

    async def search_with_latency(
        self,
        query: str,
        top_k: int = 5,
        dataset: Optional[str] = None,
        domain: Optional[str] = None,
        user_role: Optional[str] = None,
        department: Optional[str] = None,
    ) -> Tuple[List[RagDocument], int]:
        """
        RAGFlow 검색을 수행하고 결과와 latency를 함께 반환합니다.

        Phase 12: 개별 서비스 latency 측정을 위해 추가.

        Args:
            query: 검색 쿼리 텍스트
            top_k: 반환할 최대 문서 수
            dataset: 데이터셋/컬렉션 이름
            domain: dataset의 별칭
            user_role: ACL 필터용 사용자 역할
            department: ACL 필터용 부서

        Returns:
            Tuple[List[RagDocument], int]: (검색 결과, latency_ms)
        """
        start_time = time.perf_counter()
        try:
            result = await self.search(
                query=query,
                top_k=top_k,
                dataset=dataset,
                domain=domain,
                user_role=user_role,
                department=department,
            )
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            return result, latency_ms
        except Exception:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            logger.debug(f"RAGFlow search failed after {latency_ms}ms")
            raise

    async def _search_via_wrapper(
        self,
        query: str,
        top_k: int,
        dataset: Optional[str],
        user_role: Optional[str],
        department: Optional[str],
    ) -> List[RagDocument]:
        """
        /search 래퍼 엔드포인트를 통한 검색 (향후 사용).

        RAGFlow 팀이 /search 래퍼를 추가하면 이 메서드가 사용됩니다.
        USE_SEARCH_WRAPPER = True로 설정하면 활성화됩니다.

        요청 형식:
            POST /search
            {
                "query": "검색 쿼리",
                "top_k": 5,
                "dataset": "POLICY",
                "user_role": "EMPLOYEE",
                "department": "개발팀"
            }

        응답 형식:
            {
                "results": [
                    {
                        "doc_id": "...",
                        "title": "...",
                        "page": 12,
                        "score": 0.92,
                        "snippet": "..."
                    }
                ]
            }
        """
        url = f"{self._base_url}/v1/chunk/search"
        payload: Dict[str, Any] = {
            "query": query,
            "top_k": top_k,
        }
        if dataset:
            payload["dataset"] = dataset
        if user_role:
            payload["user_role"] = user_role
        if department:
            payload["department"] = department

        logger.info(
            f"Searching RAGFlow (wrapper): query='{query[:50]}...', "
            f"dataset={dataset}, top_k={top_k}"
        )

        try:
            response = await self._client.post(
                url,
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()

            # 응답 파싱: results → RagDocument
            items = data.get("results", [])
            documents: List[RagDocument] = []

            for item in items:
                try:
                    doc = RagDocument(
                        doc_id=item.get("doc_id", "unknown"),
                        title=item.get("title", "Untitled"),
                        page=item.get("page"),
                        score=item.get("score", 0.0),
                        snippet=item.get("snippet"),
                    )
                    documents.append(doc)
                except Exception as e:
                    logger.warning(f"Failed to parse search result item: {e}")
                    continue

            logger.info(f"RAGFlow search returned {len(documents)} documents")
            return documents

        except httpx.TimeoutException:
            logger.error(f"RAGFlow search timeout after {self._timeout}s")
            return []

        except httpx.HTTPStatusError as e:
            logger.error(
                f"RAGFlow search HTTP error: status={e.response.status_code}, "
                f"detail={e.response.text[:200] if e.response.text else 'N/A'}"
            )
            return []

        except httpx.RequestError as e:
            logger.error(f"RAGFlow search request error: {e}")
            return []

        except Exception as e:
            logger.exception("RAGFlow search unexpected error")
            return []

    async def search_as_sources(
        self,
        query: str,
        domain: Optional[str],
        user_role: str,
        department: Optional[str],
        top_k: int = 5,
    ) -> List[ChatSource]:
        """
        RAGFlow에서 관련 문서를 검색하고 ChatSource 리스트로 반환합니다.

        ChatService에서 직접 사용하는 메서드입니다.
        내부적으로 search()를 호출한 후 RagDocument를 ChatSource로 변환합니다.

        Args:
            query: 검색 쿼리 텍스트 (PII 마스킹된 텍스트 권장)
            domain: 검색 도메인 (예: "POLICY", "INCIDENT")
            user_role: ACL 필터용 사용자 역할
            department: ACL 필터용 부서
            top_k: 반환할 최대 문서 수

        Returns:
            List[ChatSource]: ChatResponse.sources에 바로 사용 가능한 리스트
        """
        documents = await self.search(
            query=query,
            top_k=top_k,
            dataset=domain,
            user_role=user_role,
            department=department,
        )

        return [self._to_chat_source(doc) for doc in documents]

    @staticmethod
    def _to_chat_source(doc: RagDocument) -> ChatSource:
        """
        RagDocument를 ChatSource로 변환합니다.

        Phase 13: 조항 메타데이터(article_label, article_path) 매핑 추가.
        - article_label: section_label 우선, 없으면 article_id + clause_id 조합
        - article_path: section_path 그대로 사용

        Args:
            doc: RagDocument 인스턴스

        Returns:
            ChatSource 인스턴스
        """
        # Phase 13: article_label 생성
        # 우선순위: section_label > article_id + clause_id 조합
        article_label = doc.section_label
        if not article_label and (doc.article_id or doc.clause_id):
            # article_id와 clause_id를 조합하여 라벨 생성
            parts = []
            if doc.article_id:
                parts.append(doc.article_id)
            if doc.clause_id:
                parts.append(doc.clause_id)
            article_label = " ".join(parts) if parts else None

        return ChatSource(
            doc_id=doc.doc_id,
            title=doc.title,
            page=doc.page,
            score=doc.score,
            snippet=doc.snippet,
            # Phase 13: 조항 메타데이터
            article_label=article_label,
            article_path=doc.section_path,
        )

    # ========================================
    # 문서 처리 관련 메서드 (기존 호환성 유지)
    # ========================================

    async def process_document(
        self,
        *,
        doc_id: str,
        file_url: str,
        domain: str,
        acl: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        RAG 문서 처리 요청을 RAGFlow에 위임합니다.

        Args:
            doc_id: 문서 ID
            file_url: 문서 파일 URL
            domain: 문서 도메인 (POLICY, INCIDENT, EDUCATION 등)
            acl: 접근 제어 설정 (roles, departments)

        Returns:
            Dict[str, Any]: RAGFlow 응답 JSON

        Raises:
            RuntimeError: RAGFLOW_BASE_URL이 설정되지 않은 경우
            httpx.HTTPStatusError: HTTP 요청 실패 시
        """
        if not self._base_url:
            raise RuntimeError("RAGFLOW_BASE_URL is not configured")

        payload: Dict[str, Any] = {
            "doc_id": doc_id,
            "file_url": file_url,
            "domain": domain,
            "acl": acl or {},
        }
        url = f"{self._base_url}/api/rag/process"
        resp = await self._client.post(url, json=payload, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    async def process_document_request(
        self, req: RagProcessRequest
    ) -> RagProcessResponse:
        """
        RagProcessRequest를 받아 문서 처리를 요청합니다.

        Args:
            req: RagProcessRequest 객체

        Returns:
            RagProcessResponse 객체
        """
        if not self._base_url:
            logger.warning("RAGFlow process_document skipped: base_url not configured")
            return RagProcessResponse(
                doc_id=req.doc_id,
                success=False,
                message="RAGFlow service not configured (RAGFLOW_BASE_URL is empty)",
            )

        url = f"{self._base_url}/api/rag/process"
        payload: Dict[str, Any] = {
            "doc_id": req.doc_id,
            "file_url": str(req.file_url),
            "domain": req.domain,
        }
        if req.acl:
            payload["acl"] = {
                "roles": req.acl.roles,
                "departments": req.acl.departments,
            }

        logger.info(f"Sending document to RAGFlow: doc_id={req.doc_id}, url={url}")

        try:
            response = await self._client.post(url, json=payload, timeout=self._timeout)
            response.raise_for_status()
            data = response.json()
            logger.info(f"RAGFlow process_document success: doc_id={req.doc_id}")

            return RagProcessResponse(
                doc_id=req.doc_id,
                success=data.get("success", True),
                message=data.get("message", "Document processed successfully via RAGFlow"),
            )

        except httpx.HTTPStatusError as e:
            logger.error(
                f"RAGFlow process_document HTTP error: doc_id={req.doc_id}, "
                f"status={e.response.status_code}"
            )
            return RagProcessResponse(
                doc_id=req.doc_id,
                success=False,
                message=f"RAGFlow request failed: HTTP {e.response.status_code}",
            )

        except httpx.RequestError as e:
            logger.error(
                f"RAGFlow process_document request error: doc_id={req.doc_id}, error={e}"
            )
            return RagProcessResponse(
                doc_id=req.doc_id,
                success=False,
                message=f"RAGFlow request failed: {type(e).__name__}",
            )

        except Exception as e:
            logger.exception(
                f"RAGFlow process_document unexpected error: doc_id={req.doc_id}"
            )
            return RagProcessResponse(
                doc_id=req.doc_id,
                success=False,
                message=f"RAGFlow integration failed: {type(e).__name__}",
            )

    # ========================================
    # 문서 인덱싱 관련 메서드 (Phase 19)
    # ========================================

    def _source_type_to_kb_id(self, source_type: str) -> str:
        """
        source_type을 kb_id로 변환합니다.

        _dataset_to_kb_id와 동일한 매핑 로직을 사용합니다.
        search/ingest 양쪽에서 일관된 매핑을 보장합니다.

        Args:
            source_type: 문서 유형 (예: "policy", "training", "incident")

        Returns:
            str: 해당 source_type의 kb_id

        Note:
            매핑 테이블은 DATASET_TO_KB_ID 클래스 변수에 정의되어 있습니다.
            매핑되지 않는 경우 기본값(POLICY)으로 fallback 합니다.
        """
        return self._dataset_to_kb_id(source_type)

    def is_valid_source_type(self, source_type: str) -> bool:
        """
        source_type이 유효한 매핑을 가지는지 확인합니다.

        Args:
            source_type: 문서 유형 (예: "policy", "training", "incident")

        Returns:
            bool: 유효한 source_type이면 True
        """
        key = source_type.upper() if source_type else ""
        return key in self.DATASET_TO_KB_ID

    async def ingest_document(self, request: IngestRequest) -> IngestResponse:
        """
        RAGFlow에 문서 인덱싱을 요청합니다.

        Spring 백엔드에서 문서 업로드 후 이 메서드를 통해
        RAGFlow의 인덱싱 API를 호출합니다.

        Args:
            request: IngestRequest 객체
                - doc_id: 문서 ID
                - source_type: 문서 유형 (policy, training, incident 등)
                - storage_url: 파일 다운로드 URL
                - file_name: 파일명
                - mime_type: MIME 타입
                - department: 부서 (선택)
                - acl: 접근 제어 리스트 (선택)
                - tags: 태그 리스트 (선택)
                - version: 버전 (기본값: 1)

        Returns:
            IngestResponse: 인덱싱 결과
                - task_id: 작업 ID
                - status: 상태 (DONE, QUEUED, PROCESSING, FAILED)

        Raises:
            UpstreamServiceError: RAGFlow 서비스 오류 시

        Note:
            TODO: 실제 RAGFlow 인덱싱 API 스펙 확정 시 업데이트 필요
        """
        if not self._base_url:
            logger.warning("RAGFlow ingest skipped: base_url not configured")
            return IngestResponse(
                task_id=f"skip-{request.doc_id}",
                status="FAILED",
            )

        # source_type → kb_id 변환
        kb_id = self._source_type_to_kb_id(request.source_type)

        # TODO: 실제 RAGFlow 인덱싱 API 스펙 확정 필요
        url = f"{self._base_url}/v1/chunk/ingest"

        # 메타데이터 구성
        metadata: Dict[str, Any] = {}
        if request.department:
            metadata["department"] = request.department
        if request.acl:
            metadata["acl"] = request.acl
        if request.tags:
            metadata["tags"] = request.tags
        if request.version:
            metadata["version"] = request.version

        payload: Dict[str, Any] = {
            "kb_id": kb_id,
            "doc_id": request.doc_id,
            "storage_url": str(request.storage_url),
            "file_name": request.file_name,
            "mime_type": request.mime_type,
            "metadata": metadata,
        }

        logger.info(
            f"Ingesting document to RAGFlow: doc_id={request.doc_id}, "
            f"kb_id={kb_id}, file_name={request.file_name}"
        )

        try:
            response = await retry_async_operation(
                self._client.post,
                url,
                json=payload,
                timeout=self._timeout,
                config=RAGFLOW_RETRY_CONFIG,
                operation_name="ragflow_ingest",
            )
            response.raise_for_status()
            data = response.json()

            # 응답 파싱
            # 예상 응답: {"code": 0, "data": {"task_id": "...", "status": "DONE"}}
            if data.get("code") == 0 and "data" in data:
                result_data = data["data"]
                task_id = result_data.get("task_id", f"ingest-{request.doc_id}")
                status = result_data.get("status", "DONE")
            else:
                # code가 0이 아닌 경우 에러 처리
                error_msg = data.get("message", "Unknown error")
                logger.error(f"RAGFlow ingest error: {error_msg}")
                task_id = f"error-{request.doc_id}"
                status = "FAILED"

            logger.info(
                f"RAGFlow ingest completed: doc_id={request.doc_id}, "
                f"task_id={task_id}, status={status}"
            )

            return IngestResponse(task_id=task_id, status=status)

        except httpx.TimeoutException as e:
            logger.error(f"RAGFlow ingest timeout after {self._timeout}s")
            raise UpstreamServiceError(
                service=ServiceType.RAGFLOW,
                error_type=ErrorType.UPSTREAM_TIMEOUT,
                message=f"RAGFlow ingest timeout after {self._timeout}s",
                is_timeout=True,
                original_error=e,
            )

        except httpx.HTTPStatusError as e:
            logger.error(
                f"RAGFlow ingest HTTP error: status={e.response.status_code}, "
                f"detail={e.response.text[:200] if e.response.text else 'N/A'}"
            )
            raise UpstreamServiceError(
                service=ServiceType.RAGFLOW,
                error_type=ErrorType.UPSTREAM_ERROR,
                message=f"RAGFlow ingest HTTP {e.response.status_code}",
                status_code=e.response.status_code,
                original_error=e,
            )

        except httpx.RequestError as e:
            logger.error(f"RAGFlow ingest request error: {e}")
            raise UpstreamServiceError(
                service=ServiceType.RAGFLOW,
                error_type=ErrorType.UPSTREAM_ERROR,
                message=f"RAGFlow ingest request error: {type(e).__name__}",
                original_error=e,
            )

        except Exception as e:
            logger.exception("RAGFlow ingest unexpected error")
            raise UpstreamServiceError(
                service=ServiceType.RAGFLOW,
                error_type=ErrorType.INTERNAL_ERROR,
                message=f"RAGFlow ingest unexpected error: {type(e).__name__}",
                original_error=e,
            )
