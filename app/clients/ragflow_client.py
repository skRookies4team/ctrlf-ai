"""
RAGFlow 클라이언트 모듈 (RAGFlow Client Module)

ctrlf-ragflow 서비스와 통신하는 클라이언트입니다.
실제 문서 전처리/인덱싱은 ctrlf-ragflow 레포에서 수행되며,
이 클라이언트는 "검색 API만 사용하는 클라이언트" 역할을 합니다.

Phase 6 구현:
- RagDocument 모델을 사용한 검색 결과 정규화
- dataset 파라미터 지원 (도메인별 검색)
- 타임아웃 설정 지원
- 명확한 예외 처리 및 fallback 전략

사용 방법:
    from app.clients.ragflow_client import RagflowClient

    client = RagflowClient()

    # 헬스체크
    is_healthy = await client.health()

    # 문서 검색
    docs = await client.search(
        query="연차휴가 이월 규정",
        top_k=5,
        dataset="POLICY"
    )
"""

from typing import Any, Dict, List, Optional

import httpx

from app.clients.http_client import get_async_http_client
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.chat import ChatSource
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
    DEFAULT_TIMEOUT = 10.0

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """
        RagflowClient 초기화.

        Args:
            base_url: RAGFlow 서비스 URL. None이면 RAGFLOW_BASE_URL 환경변수 사용.
            timeout: HTTP 요청 타임아웃 (초). 기본값 10초.
            client: httpx.AsyncClient 인스턴스. None이면 공용 클라이언트 사용.
        """
        settings = get_settings()
        self._base_url = base_url if base_url is not None else settings.RAGFLOW_BASE_URL
        self._timeout = timeout
        self._client = client or get_async_http_client()

        if not self._base_url:
            logger.warning(
                "RAGFLOW_BASE_URL is not configured. "
                "RAGFlow API calls will be skipped and return empty results."
            )

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

        주어진 query에 대해 RAGFlow 검색을 수행하고,
        RagDocument 리스트를 반환합니다.

        Args:
            query: 검색 쿼리 텍스트
            top_k: 반환할 최대 문서 수 (기본값: 5)
            dataset: 검색할 데이터셋/컬렉션 이름 (예: "POLICY", "INCIDENT")
            domain: dataset의 별칭 (dataset이 None일 때 사용)
            user_role: ACL 필터용 사용자 역할
            department: ACL 필터용 부서

        Returns:
            List[RagDocument]: 검색된 문서 리스트

        Raises:
            RagflowSearchError: 검색 실패 시 (설정에 따라 빈 리스트 반환 가능)

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

        url = f"{self._base_url}/search"
        payload: Dict[str, Any] = {
            "query": query,
            "top_k": top_k,
        }
        if effective_dataset:
            payload["dataset"] = effective_dataset
        if user_role:
            payload["user_role"] = user_role
        if department:
            payload["department"] = department

        logger.info(
            f"Searching RAGFlow: query='{query[:50]}...', "
            f"dataset={effective_dataset}, top_k={top_k}"
        )

        try:
            response = await self._client.post(
                url,
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()

            # RAGFlow 응답 파싱
            # 예상 응답 구조:
            # {
            #     "results": [
            #         {"doc_id": "...", "title": "...", "page": 12, "score": 0.92, "snippet": "..."}
            #     ]
            # }
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
                    logger.warning(f"Failed to parse RAGFlow search result item: {e}")
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

        Args:
            doc: RagDocument 인스턴스

        Returns:
            ChatSource 인스턴스
        """
        return ChatSource(
            doc_id=doc.doc_id,
            title=doc.title,
            page=doc.page,
            score=doc.score,
            snippet=doc.snippet,
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
