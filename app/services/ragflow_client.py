"""
RAGFlow Client Module

HTTP client for communicating with ctrlf-ragflow service.
Handles document processing and search operations.

This module provides a client layer for RAGFlow integration:
- process_document: Forward document processing requests to RAGFlow
- search: Search for relevant documents based on query

NOTE: Actual RAGFlow API endpoints are placeholders (TODO).
      Update endpoint paths and payload structures when RAGFlow API spec is finalized.
"""

from typing import List, Optional

import httpx

from app.clients.http_client import get_async_http_client
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.chat import ChatSource
from app.models.rag import RagProcessRequest, RagProcessResponse

logger = get_logger(__name__)


class RagflowClient:
    """
    Client for communicating with ctrlf-ragflow service.

    Handles HTTP communication with RAGFlow for document processing and search.
    Uses shared httpx.AsyncClient singleton for connection pooling.

    Attributes:
        _base_url: RAGFlow service base URL from settings
        _client: Shared httpx.AsyncClient instance

    Example:
        client = RagflowClient()
        result = await client.process_document(request)
        sources = await client.search(query="연차 정책", domain="POLICY", user_role="EMPLOYEE")
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """
        Initialize RagflowClient.

        Args:
            base_url: RAGFlow service URL. If None, uses RAGFLOW_BASE_URL from settings.
            client: httpx.AsyncClient instance. If None, uses shared singleton.
        """
        settings = get_settings()
        self._base_url = base_url or settings.RAGFLOW_BASE_URL
        self._client = client or get_async_http_client()

        if not self._base_url:
            logger.warning(
                "RAGFLOW_BASE_URL is not configured. "
                "RAGFlow API calls will be skipped and return fallback responses."
            )

    async def process_document(self, req: RagProcessRequest) -> RagProcessResponse:
        """
        Forward document processing request to RAGFlow service.

        Sends the document info to RAGFlow for preprocessing, chunking,
        and embedding generation.

        Args:
            req: RagProcessRequest containing document info and ACL

        Returns:
            RagProcessResponse with processing result

        Note:
            - If RAGFLOW_BASE_URL is not configured, returns success=False
            - On HTTP error, returns success=False with error message
        """
        if not self._base_url:
            logger.warning("RAGFlow process_document skipped: base_url not configured")
            return RagProcessResponse(
                doc_id=req.doc_id,
                success=False,
                message="RAGFlow service not configured (RAGFLOW_BASE_URL is empty)",
            )

        # TODO: Update endpoint path when RAGFlow API spec is finalized
        url = f"{self._base_url}/rag/process"

        # Build request payload
        # TODO: Adjust payload structure to match actual RAGFlow API spec
        payload = {
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

            # TODO: Parse actual RAGFlow response structure
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

    async def search(
        self,
        query: str,
        domain: Optional[str],
        user_role: str,
        department: Optional[str],
        top_k: int = 5,
    ) -> List[ChatSource]:
        """
        Search for relevant documents in RAGFlow.

        Queries RAGFlow to find documents matching the search query,
        filtered by domain, user role, and department for ACL compliance.

        Args:
            query: Search query text
            domain: Document domain filter (e.g., POLICY, INCIDENT)
            user_role: User's role for ACL filtering
            department: User's department for ACL filtering
            top_k: Maximum number of results to return

        Returns:
            List of ChatSource objects with document info and snippets

        Note:
            - If RAGFLOW_BASE_URL is not configured, returns empty list
            - On HTTP error, returns empty list (logs error)
        """
        if not self._base_url:
            logger.warning("RAGFlow search skipped: base_url not configured")
            return []

        # TODO: Update endpoint path when RAGFlow API spec is finalized
        url = f"{self._base_url}/rag/search"

        # Build request payload
        # TODO: Adjust payload structure to match actual RAGFlow API spec
        payload = {
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

            # TODO: Adjust response parsing to match actual RAGFlow response structure
            # Expected structure (placeholder):
            # {
            #     "results": [
            #         {"doc_id": "...", "title": "...", "page": 1, "score": 0.95, "snippet": "..."},
            #         ...
            #     ]
            # }
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
