"""
Phase 13: RAG 조항 정보 메타데이터 확장 테스트

테스트 항목:
1. RagDocument 조항 메타 필드 테스트 (section_label, section_path, article_id, clause_id)
2. ChatSource 조항 메타 필드 테스트 (article_label, article_path)
3. RagflowClient → RagDocument 매핑 테스트 (metadata에서 조항 정보 추출)
4. RagDocument → ChatSource 변환 테스트 (article_label 생성 로직)
5. ChatService 프롬프트 포맷팅 테스트 (조항 정보 포함)
6. HTTP 응답 JSON 구조 테스트 (articleLabel, articlePath 노출)
"""

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.clients.ragflow_client import RagflowClient
from app.models.chat import ChatMessage, ChatRequest, ChatResponse, ChatSource
from app.models.rag import RagDocument
from app.services.chat_service import ChatService
from app.services.guardrail_service import GuardrailService
from app.services.intent_service import IntentService
from app.services.pii_service import PiiService
from app.clients.llm_client import LLMClient


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


# =============================================================================
# 1. RagDocument 조항 메타 필드 테스트
# =============================================================================


def test_rag_document_with_article_metadata() -> None:
    """RagDocument에 조항 메타 필드가 있는지 테스트."""
    doc = RagDocument(
        doc_id="doc-001",
        title="연차휴가 관리 규정",
        page=5,
        score=0.92,
        snippet="연차는 다음 해 말일까지 최대 10일까지 이월할 수 있다.",
        section_label="제10조 (연차 이월)",
        section_path="제2장 근로시간 및 휴가 > 제10조 연차 이월 > 제2항",
        article_id="제10조",
        clause_id="제2항",
    )

    assert doc.section_label == "제10조 (연차 이월)"
    assert doc.section_path == "제2장 근로시간 및 휴가 > 제10조 연차 이월 > 제2항"
    assert doc.article_id == "제10조"
    assert doc.clause_id == "제2항"


def test_rag_document_without_article_metadata() -> None:
    """RagDocument 조항 메타 없이도 생성 가능한지 테스트."""
    doc = RagDocument(
        doc_id="doc-002",
        title="정보보안 정책",
        page=1,
        score=0.85,
        snippet="정보보안 기본 원칙...",
    )

    assert doc.section_label is None
    assert doc.section_path is None
    assert doc.article_id is None
    assert doc.clause_id is None


def test_rag_document_partial_article_metadata() -> None:
    """RagDocument 일부 조항 메타만 있는 경우 테스트."""
    doc = RagDocument(
        doc_id="doc-003",
        title="인사관리 규정",
        page=3,
        score=0.78,
        snippet="지각 처리 기준...",
        article_id="제5조",  # article_id만 있음
    )

    assert doc.section_label is None
    assert doc.article_id == "제5조"
    assert doc.clause_id is None


# =============================================================================
# 2. ChatSource 조항 메타 필드 테스트
# =============================================================================


def test_chat_source_with_article_metadata() -> None:
    """ChatSource에 조항 메타 필드가 있는지 테스트."""
    source = ChatSource(
        doc_id="doc-001",
        title="연차휴가 관리 규정",
        page=5,
        score=0.92,
        snippet="연차 이월 규정...",
        article_label="제10조 (연차 이월) 제2항",
        article_path="제2장 근로시간 및 휴가 > 제10조 연차 이월 > 제2항",
    )

    assert source.article_label == "제10조 (연차 이월) 제2항"
    assert source.article_path == "제2장 근로시간 및 휴가 > 제10조 연차 이월 > 제2항"


def test_chat_source_without_article_metadata() -> None:
    """ChatSource 조항 메타 없이도 생성 가능한지 테스트 (하위 호환)."""
    source = ChatSource(
        doc_id="doc-002",
        title="정보보안 정책",
        page=1,
    )

    assert source.article_label is None
    assert source.article_path is None


def test_chat_source_json_serialization() -> None:
    """ChatSource JSON 직렬화 시 조항 메타가 포함되는지 테스트."""
    source = ChatSource(
        doc_id="doc-001",
        title="연차휴가 관리 규정",
        page=5,
        score=0.92,
        article_label="제10조 (연차 이월) 제2항",
        article_path="제2장 > 제10조 > 제2항",
    )

    json_data = source.model_dump()

    assert "article_label" in json_data
    assert "article_path" in json_data
    assert json_data["article_label"] == "제10조 (연차 이월) 제2항"
    assert json_data["article_path"] == "제2장 > 제10조 > 제2항"


# =============================================================================
# 3. RagflowClient → RagDocument 매핑 테스트
# =============================================================================


@pytest.mark.anyio
async def test_ragflow_client_maps_metadata_fields() -> None:
    """RagflowClient가 RAGFlow 응답의 metadata에서 조항 정보를 추출하는지 테스트."""
    # RAGFlow 응답에 metadata가 포함된 경우
    mock_response = {
        "chunks": [
            {
                "chunk_id": "chunk-001",
                "doc_name": "연차휴가 관리 규정",
                "page_num": 5,
                "similarity": 0.92,
                "content": "연차는 다음 해 말일까지 최대 10일까지 이월할 수 있다.",
                "metadata": {
                    "section_title": "제10조 (연차 이월)",
                    "section_path": "제2장 > 제10조 > 제2항",
                    "article_number": "제10조",
                    "clause_number": "제2항",
                },
            }
        ]
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_response)

    mock_transport = httpx.MockTransport(mock_handler)
    mock_client = httpx.AsyncClient(transport=mock_transport)

    client = RagflowClient(base_url="http://test-ragflow:8000", client=mock_client)
    documents = await client.search(query="연차 이월", top_k=5, dataset="POLICY")

    assert len(documents) == 1
    doc = documents[0]
    assert doc.section_label == "제10조 (연차 이월)"
    assert doc.section_path == "제2장 > 제10조 > 제2항"
    assert doc.article_id == "제10조"
    assert doc.clause_id == "제2항"


@pytest.mark.anyio
async def test_ragflow_client_graceful_without_metadata() -> None:
    """RagflowClient가 metadata 없는 RAGFlow 응답을 처리하는지 테스트."""
    # RAGFlow 응답에 metadata가 없는 경우 (기존 형식)
    mock_response = {
        "chunks": [
            {
                "chunk_id": "chunk-002",
                "doc_name": "정보보안 정책",
                "page_num": 1,
                "similarity": 0.85,
                "content": "정보보안 기본 원칙...",
                # metadata 없음
            }
        ]
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_response)

    mock_transport = httpx.MockTransport(mock_handler)
    mock_client = httpx.AsyncClient(transport=mock_transport)

    client = RagflowClient(base_url="http://test-ragflow:8000", client=mock_client)
    documents = await client.search(query="정보보안", top_k=5)

    assert len(documents) == 1
    doc = documents[0]
    # 조항 메타가 None이어야 함
    assert doc.section_label is None
    assert doc.section_path is None
    assert doc.article_id is None
    assert doc.clause_id is None
    # 기존 필드는 정상적으로 매핑됨
    assert doc.doc_id == "chunk-002"
    assert doc.title == "정보보안 정책"


@pytest.mark.anyio
async def test_ragflow_client_maps_from_fields_or_extra() -> None:
    """RagflowClient가 fields 또는 extra에서도 조항 정보를 추출하는지 테스트."""
    # RAGFlow 응답에 fields에 조항 정보가 있는 경우
    mock_response = {
        "chunks": [
            {
                "chunk_id": "chunk-003",
                "doc_name": "인사관리 규정",
                "page_num": 3,
                "similarity": 0.78,
                "content": "지각 처리 기준...",
                "fields": {
                    "section_title": "제5조 (지각/조퇴 처리)",
                    "article_number": "제5조",
                },
            }
        ]
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_response)

    mock_transport = httpx.MockTransport(mock_handler)
    mock_client = httpx.AsyncClient(transport=mock_transport)

    client = RagflowClient(base_url="http://test-ragflow:8000", client=mock_client)
    documents = await client.search(query="지각 처리", top_k=5)

    assert len(documents) == 1
    doc = documents[0]
    assert doc.section_label == "제5조 (지각/조퇴 처리)"
    assert doc.article_id == "제5조"


# =============================================================================
# 4. RagDocument → ChatSource 변환 테스트
# =============================================================================


def test_to_chat_source_with_section_label() -> None:
    """RagDocument → ChatSource 변환: section_label이 article_label로 변환."""
    doc = RagDocument(
        doc_id="doc-001",
        title="연차휴가 관리 규정",
        page=5,
        score=0.92,
        snippet="연차 이월 규정...",
        section_label="제10조 (연차 이월) 제2항",
        section_path="제2장 > 제10조 > 제2항",
    )

    source = RagflowClient._to_chat_source(doc)

    assert source.article_label == "제10조 (연차 이월) 제2항"
    assert source.article_path == "제2장 > 제10조 > 제2항"


def test_to_chat_source_without_section_label_with_ids() -> None:
    """RagDocument → ChatSource 변환: section_label 없고 article_id/clause_id만 있는 경우."""
    doc = RagDocument(
        doc_id="doc-002",
        title="인사관리 규정",
        page=3,
        score=0.78,
        snippet="지각 처리...",
        article_id="제5조",
        clause_id="제3항",
    )

    source = RagflowClient._to_chat_source(doc)

    # article_id + clause_id 조합으로 article_label 생성
    assert source.article_label == "제5조 제3항"
    assert source.article_path is None


def test_to_chat_source_with_only_article_id() -> None:
    """RagDocument → ChatSource 변환: article_id만 있는 경우."""
    doc = RagDocument(
        doc_id="doc-003",
        title="정보보안 정책",
        page=1,
        score=0.85,
        snippet="보안 원칙...",
        article_id="제3조",
    )

    source = RagflowClient._to_chat_source(doc)

    assert source.article_label == "제3조"


def test_to_chat_source_no_article_metadata() -> None:
    """RagDocument → ChatSource 변환: 조항 메타 없는 경우."""
    doc = RagDocument(
        doc_id="doc-004",
        title="일반 문서",
        page=1,
        score=0.5,
        snippet="일반 내용...",
    )

    source = RagflowClient._to_chat_source(doc)

    assert source.article_label is None
    assert source.article_path is None
    # 기존 필드는 정상
    assert source.doc_id == "doc-004"
    assert source.title == "일반 문서"


# =============================================================================
# 5. ChatService 프롬프트 포맷팅 테스트
# =============================================================================


def test_format_sources_for_prompt_with_article_info() -> None:
    """ChatService._format_sources_for_prompt: 조항 정보 포함 포맷팅."""
    service = ChatService(
        ragflow_client=RagflowClient(base_url=""),
        llm_client=LLMClient(base_url=""),
        pii_service=PiiService(base_url="", enabled=False),
    )

    sources = [
        ChatSource(
            doc_id="doc-001",
            title="연차휴가 관리 규정 (2025.01 개정)",
            page=4,
            score=0.92,
            snippet="연차는 다음 해 말일까지 최대 10일까지 이월할 수 있다.",
            article_label="제10조 (연차 이월) 제2항",
            article_path="제2장 근로시간 및 휴가 > 제10조 연차 이월 > 제2항",
        ),
        ChatSource(
            doc_id="doc-002",
            title="인사관리 규정",
            page=3,
            score=0.78,
            snippet="지각 3회 시 결근 1일로 처리한다.",
            article_label="제5조 (지각/조퇴 처리 기준)",
        ),
    ]

    result = service._format_sources_for_prompt(sources)

    # 근거 1 확인
    assert "[근거 1]" in result
    assert "연차휴가 관리 규정 (2025.01 개정)" in result
    assert "위치:" in result
    assert "제2장 근로시간 및 휴가 > 제10조 연차 이월 > 제2항" in result

    # 근거 2 확인
    assert "[근거 2]" in result
    assert "인사관리 규정" in result
    assert "제5조 (지각/조퇴 처리 기준)" in result


def test_format_sources_for_prompt_without_article_info() -> None:
    """ChatService._format_sources_for_prompt: 조항 정보 없는 경우."""
    service = ChatService(
        ragflow_client=RagflowClient(base_url=""),
        llm_client=LLMClient(base_url=""),
        pii_service=PiiService(base_url="", enabled=False),
    )

    sources = [
        ChatSource(
            doc_id="doc-003",
            title="일반 문서",
            page=1,
            score=0.5,
            snippet="일반 내용입니다.",
            # article_label, article_path 없음
        ),
    ]

    result = service._format_sources_for_prompt(sources)

    # 기본 정보는 포함
    assert "[근거 1]" in result
    assert "일반 문서" in result
    # 위치: 라인이 없어야 함
    assert "위치:" not in result


# =============================================================================
# 6. HTTP 응답 JSON 구조 테스트
# =============================================================================


def test_chat_response_sources_include_article_fields() -> None:
    """ChatResponse의 sources에 article_label, article_path가 포함되는지 테스트."""
    response = ChatResponse(
        answer="연차는 최대 10일까지 이월할 수 있습니다.",
        sources=[
            ChatSource(
                doc_id="doc-001",
                title="연차휴가 관리 규정",
                page=4,
                score=0.92,
                snippet="연차 이월 규정...",
                article_label="제10조 (연차 이월) 제2항",
                article_path="제2장 > 제10조 > 제2항",
            )
        ],
    )

    # JSON 직렬화
    json_data = response.model_dump()

    # sources[0]에 article_label, article_path 포함 확인
    source_data = json_data["sources"][0]
    assert "article_label" in source_data
    assert "article_path" in source_data
    assert source_data["article_label"] == "제10조 (연차 이월) 제2항"
    assert source_data["article_path"] == "제2장 > 제10조 > 제2항"

    # 기존 필드도 유지 확인
    assert source_data["doc_id"] == "doc-001"
    assert source_data["title"] == "연차휴가 관리 규정"
    assert source_data["page"] == 4


def test_chat_response_backward_compatibility() -> None:
    """ChatResponse가 기존 스키마와 하위 호환되는지 테스트."""
    # 조항 메타 없이 응답 생성
    response = ChatResponse(
        answer="테스트 응답",
        sources=[
            ChatSource(
                doc_id="doc-001",
                title="테스트 문서",
                page=1,
                snippet="테스트 내용",
            )
        ],
    )

    json_data = response.model_dump()

    # 기존 필드 유지
    assert json_data["answer"] == "테스트 응답"
    assert len(json_data["sources"]) == 1
    assert json_data["sources"][0]["doc_id"] == "doc-001"
    # 조항 메타는 None으로 포함됨 (하위 호환)
    assert json_data["sources"][0]["article_label"] is None
    assert json_data["sources"][0]["article_path"] is None


# =============================================================================
# 7. 시스템 프롬프트 테스트
# =============================================================================


def test_system_prompt_includes_citation_guide() -> None:
    """시스템 프롬프트에 근거 인용 지침이 포함되어 있는지 테스트."""
    from app.services.chat_service import SYSTEM_PROMPT_WITH_RAG

    # 근거 인용 지침 확인
    assert "[참고 근거]" in SYSTEM_PROMPT_WITH_RAG
    assert "조문/항 번호" in SYSTEM_PROMPT_WITH_RAG
    assert "bullet" in SYSTEM_PROMPT_WITH_RAG or "정리" in SYSTEM_PROMPT_WITH_RAG
