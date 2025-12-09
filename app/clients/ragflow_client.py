"""
RAGFlow 클라이언트 모듈 (RAGFlow Client Module)

ctrlf-ragflow 서비스와 통신하는 클라이언트입니다.

실제 엔드포인트 경로는 팀의 ctrlf-ragflow API 명세에 맞게
나중에 수정해야 합니다. (현재는 안전한 TODO 형태로 둡니다.)

사용 방법:
    from app.clients.ragflow_client import RagflowClient

    client = RagflowClient()

    # 헬스체크
    is_healthy = await client.health_check()

    # 문서 처리
    result = await client.process_document(
        doc_id="HR-001",
        file_url="https://example.com/doc.pdf",
        domain="POLICY"
    )
"""

from typing import Any, Dict, List, Optional

import httpx

from app.clients.http_client import get_async_http_client
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.chat import ChatSource
from app.models.rag import RagProcessRequest, RagProcessResponse

logger = get_logger(__name__)


class RagflowClient:
    """
    ctrlf-ragflow 서비스와 통신하는 클라이언트.

    실제 엔드포인트 경로는 팀의 ctrlf-ragflow API 명세에 맞게
    나중에 수정해야 합니다. (현재는 안전한 TODO 형태로 둡니다.)

    Attributes:
        _client: 공용 httpx.AsyncClient 인스턴스
        _base_url: RAGFlow 서비스 기본 URL
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """
        RagflowClient 초기화.

        Args:
            base_url: RAGFlow 서비스 URL. None이면 설정에서 가져옴.
            client: httpx.AsyncClient 인스턴스. None이면 공용 클라이언트 사용.
        """
        settings = get_settings()
        self._base_url = base_url if base_url is not None else settings.RAGFLOW_BASE_URL
        self._client = client or get_async_http_client()

        if not self._base_url:
            logger.warning(
                "RAGFLOW_BASE_URL is not configured. "
                "RAGFlow API calls will be skipped and return fallback responses."
            )

    def _ensure_base_url(self) -> None:
        """
        BASE_URL이 설정되어 있는지 확인합니다.

        Raises:
            RuntimeError: RAGFLOW_BASE_URL이 설정되지 않은 경우
        """
        if not self._base_url:
            raise RuntimeError("RAGFLOW_BASE_URL is not configured")

    async def health_check(self) -> bool:
        """
        RAGFlow 헬스체크를 수행합니다.

        BASE_URL이 설정되지 않은 경우 False를 반환합니다.
        상위 레벨에서 '점검 대상 아님'으로 판단할 수 있습니다.

        Returns:
            bool: RAGFlow 서비스가 정상이면 True, 그렇지 않으면 False
        """
        if not self._base_url:
            logger.warning("RAGFLOW_BASE_URL is not set, skipping health check")
            return False

        try:
            # TODO: 팀의 실제 RAGFlow health 엔드포인트로 수정할 것
            url = f"{self._base_url}/health"
            resp = await self._client.get(url)
            ok = resp.status_code == 200
            if not ok:
                logger.error(
                    "RAGFlow health check failed: status=%s", resp.status_code
                )
            return ok
        except Exception as e:
            logger.exception("RAGFlow health check error: %s", e)
            return False

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

        실제 path/payload는 ctrlf-ragflow API에 맞춰 반드시 수정해야 합니다.
        현재는 게이트웨이의 /ai/rag/process 스펙과 비슷한 형태의 payload를
        예시로 둡니다.

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
        self._ensure_base_url()
        payload: Dict[str, Any] = {
            "doc_id": doc_id,
            "file_url": file_url,
            "domain": domain,
            "acl": acl or {},
        }
        # TODO: 실제 RAGFlow 문서 처리 엔드포인트로 수정
        url = f"{self._base_url}/api/rag/process"
        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    async def search_documents(
        self,
        *,
        query: str,
        domain: Optional[str] = None,
        user_role: Optional[str] = None,
        department: Optional[str] = None,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """
        RAG 문서 검색을 요청합니다.

        TODO: 실제 RAGFlow 검색 API 스펙에 맞게 수정 필요

        Args:
            query: 검색 쿼리
            domain: 검색 도메인 필터
            user_role: 사용자 역할 (ACL 필터용)
            department: 부서 (ACL 필터용)
            top_k: 반환할 문서 수

        Returns:
            Dict[str, Any]: RAGFlow 검색 결과 JSON

        Raises:
            RuntimeError: RAGFLOW_BASE_URL이 설정되지 않은 경우
            httpx.HTTPStatusError: HTTP 요청 실패 시
        """
        self._ensure_base_url()
        payload: Dict[str, Any] = {
            "query": query,
            "top_k": top_k,
        }
        if domain:
            payload["domain"] = domain
        if user_role:
            payload["user_role"] = user_role
        if department:
            payload["department"] = department

        # TODO: 실제 RAGFlow 검색 엔드포인트로 수정
        url = f"{self._base_url}/api/rag/search"
        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    async def search(
        self,
        query: str,
        domain: Optional[str],
        user_role: str,
        department: Optional[str],
        top_k: int = 5,
    ) -> List[ChatSource]:
        """
        RAGFlow에서 관련 문서를 검색합니다.

        ChatService에서 사용하는 통합 검색 메서드입니다.
        base_url이 설정되지 않은 경우 빈 리스트를 반환합니다.

        Args:
            query: 검색 쿼리 텍스트
            domain: 문서 도메인 필터 (e.g., POLICY, INCIDENT)
            user_role: ACL 필터용 사용자 역할
            department: ACL 필터용 부서
            top_k: 반환할 최대 문서 수

        Returns:
            ChatSource 객체 리스트
        """
        if not self._base_url:
            logger.warning("RAGFlow search skipped: base_url not configured")
            return []

        url = f"{self._base_url}/api/rag/search"
        payload: Dict[str, Any] = {
            "query": query,
            "user_role": user_role,
            "top_k": top_k,
        }
        if domain:
            payload["domain"] = domain
        if department:
            payload["department"] = department

        logger.info(
            f"Searching RAGFlow: query={query[:50]}..., domain={domain}, top_k={top_k}"
        )

        try:
            response = await self._client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

            items = data.get("results", [])
            sources: List[ChatSource] = []

            for item in items:
                try:
                    source = ChatSource(
                        doc_id=item.get("doc_id", "unknown"),
                        title=item.get("title", "Untitled"),
                        page=item.get("page"),
                        score=item.get("score"),
                        snippet=item.get("snippet"),
                    )
                    sources.append(source)
                except Exception as e:
                    logger.warning(f"Failed to parse RAGFlow search result item: {e}")
                    continue

            logger.info(f"RAGFlow search returned {len(sources)} results")
            return sources

        except httpx.HTTPStatusError as e:
            logger.error(
                f"RAGFlow search HTTP error: status={e.response.status_code}, "
                f"detail={e.response.text[:200]}"
            )
            return []

        except httpx.RequestError as e:
            logger.error(f"RAGFlow search request error: {e}")
            return []

        except Exception as e:
            logger.exception("RAGFlow search unexpected error")
            return []

    async def process_document_request(
        self, req: RagProcessRequest
    ) -> RagProcessResponse:
        """
        RagProcessRequest를 받아 문서 처리를 요청합니다.

        RagService에서 사용하는 통합 처리 메서드입니다.

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
            response = await self._client.post(url, json=payload)
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
                f"status={e.response.status_code}, detail={e.response.text[:200]}"
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
