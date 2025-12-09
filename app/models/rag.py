"""
RAG Models Module

Pydantic models for RAG (Retrieval-Augmented Generation) document processing.
These models define the contract between ctrlf-back (Spring backend)
and ctrlf-ai-gateway for document indexing and retrieval.

Usage:
    - RagProcessRequest: Backend sends document info for RAGFlow processing
    - RagProcessResponse: AI Gateway returns processing result
"""

from typing import List, Optional

from pydantic import BaseModel, Field, HttpUrl


class RagAcl(BaseModel):
    """
    Document access control information.

    Defines who can access the document based on roles and departments.

    Attributes:
        roles: List of roles that can access the document
        departments: List of departments that can access the document
    """

    roles: List[str] = Field(
        default_factory=list, description="List of roles that can access the document"
    )
    departments: List[str] = Field(
        default_factory=list,
        description="List of departments that can access the document",
    )


class RagProcessRequest(BaseModel):
    """
    RAG document processing request from backend to AI gateway.

    Contains document information for preprocessing, embedding, and indexing
    via RAGFlow service.

    Attributes:
        doc_id: Document ID managed by backend/RAGFlow
        file_url: URL where the original document file is located
        domain: Document domain (POLICY, INCIDENT, EDUCATION, etc.)
        acl: Document access control settings (optional)
    """

    doc_id: str = Field(description="Document ID managed by backend/RAGFlow")
    file_url: HttpUrl = Field(
        description="URL where the original document file is located"
    )
    domain: str = Field(
        description="Document domain (e.g., POLICY, INCIDENT, EDUCATION)"
    )
    acl: Optional[RagAcl] = Field(
        default=None, description="Document access control settings (optional)"
    )


class RagProcessResponse(BaseModel):
    """
    RAG document processing response from AI gateway to backend.

    Contains the result of document processing operation.

    Attributes:
        doc_id: Same document ID from request
        success: Whether processing was successful
        message: Additional description or error message
    """

    doc_id: str = Field(description="Same document ID from request")
    success: bool = Field(description="Whether processing was successful")
    message: Optional[str] = Field(
        default=None, description="Additional description or error message"
    )


class RagDocument(BaseModel):
    """
    RAG 검색 결과로 반환되는 문서 모델.

    RAGFlow 검색 API 응답을 정규화한 모델입니다.
    ChatSource로 변환하여 ChatResponse.sources에 포함됩니다.

    Attributes:
        doc_id: 문서 ID (백엔드/RAGFlow에서 관리)
        title: 문서 제목
        page: 문서 내 페이지 번호 (해당되는 경우)
        score: 검색 관련도 점수 (0.0 ~ 1.0)
        snippet: 문서에서 추출한 텍스트 발췌문
    """

    doc_id: str = Field(description="문서 ID (백엔드/RAGFlow에서 관리)")
    title: str = Field(description="문서 제목")
    page: Optional[int] = Field(default=None, description="문서 내 페이지 번호")
    score: float = Field(description="검색 관련도 점수 (0.0 ~ 1.0)")
    snippet: Optional[str] = Field(default=None, description="문서에서 추출한 텍스트 발췌문")
