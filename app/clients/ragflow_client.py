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

from typing import Any, Dict, Optional

from app.clients.http_client import get_async_http_client
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class RagflowClient:
    """
    ctrlf-ragflow 서비스와 통신하는 클라이언트.

    실제 엔드포인트 경로는 팀의 ctrlf-ragflow API 명세에 맞게
    나중에 수정해야 합니다. (현재는 안전한 TODO 형태로 둡니다.)

    Attributes:
        _client: 공용 httpx.AsyncClient 인스턴스
        _base_url: RAGFlow 서비스 기본 URL
    """

    def __init__(self) -> None:
        """RagflowClient 초기화"""
        self._client = get_async_http_client()
        self._base_url = settings.RAGFLOW_BASE_URL

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
