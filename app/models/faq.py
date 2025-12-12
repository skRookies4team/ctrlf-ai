"""
FAQ 모델 (Phase 18)

FAQ 초안 생성 API의 요청/응답 DTO를 정의합니다.

사용 예시:
    from app.models.faq import FaqDraftGenerateRequest, FaqDraftGenerateResponse
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class FaqSourceDoc(BaseModel):
    """
    FAQ 답변 근거 후보 문서

    백엔드가 이미 RAG에서 뽑아온 후보 문서들을 전달할 때 사용합니다.
    """

    doc_id: str = Field(..., description="문서 ID")
    doc_version: Optional[str] = Field(None, description="문서 버전")
    title: Optional[str] = Field(None, description="문서 제목")
    snippet: Optional[str] = Field(None, description="문서 발췌 내용")
    article_label: Optional[str] = Field(
        None, description="조항 라벨 (예: '제3장 제2조 제1항')"
    )
    article_path: Optional[str] = Field(
        None, description="조항 경로 (예: '제3장 > 제2조 > 제1항')"
    )


class FaqDraftGenerateRequest(BaseModel):
    """
    FAQ 초안 생성 요청

    Attributes:
        domain: 도메인 (예: SEC_POLICY, PII_PRIVACY, TRAINING_QUIZ 등)
        cluster_id: FAQ 후보 클러스터 ID
        canonical_question: 클러스터를 대표하는 질문
        sample_questions: 실제 직원 질문 예시들
        top_docs: 백엔드가 이미 RAG에서 뽑아온 후보 문서들 (선택)
        answer_source_hint: 답변 생성 힌트 (AI_RAG, LOG_REUSE 등)
        meta: 추후 확장을 위한 메타데이터
    """

    domain: str = Field(
        ...,
        min_length=1,
        description="도메인 (예: SEC_POLICY, PII_PRIVACY)",
    )
    cluster_id: str = Field(..., min_length=1, description="FAQ 후보 클러스터 ID")
    canonical_question: str = Field(
        ..., min_length=1, description="클러스터를 대표하는 질문"
    )
    sample_questions: List[str] = Field(
        default_factory=list, description="실제 직원 질문 예시들"
    )
    top_docs: List[FaqSourceDoc] = Field(
        default_factory=list, description="RAG에서 뽑아온 후보 문서들"
    )
    answer_source_hint: Optional[str] = Field(
        None, description="답변 생성 힌트 (AI_RAG, LOG_REUSE)"
    )
    meta: Optional[Dict[str, Any]] = Field(
        None, description="추후 확장을 위한 메타데이터"
    )


class FaqDraft(BaseModel):
    """
    FAQ 초안

    LLM이 생성한 FAQ 초안 정보입니다.
    """

    faq_draft_id: str = Field(..., description="FAQ 초안 ID")
    domain: str = Field(..., description="도메인")
    cluster_id: str = Field(..., description="클러스터 ID")
    question: str = Field(..., description="최종 FAQ 질문 문구")
    answer_markdown: str = Field(..., description="FAQ 답변 (마크다운)")
    summary: Optional[str] = Field(None, description="FAQ 한 줄 요약")
    source_doc_id: Optional[str] = Field(None, description="근거 문서 ID")
    source_doc_version: Optional[str] = Field(None, description="근거 문서 버전")
    source_article_label: Optional[str] = Field(None, description="근거 조항 라벨")
    source_article_path: Optional[str] = Field(None, description="근거 조항 경로")
    answer_source: Literal["AI_RAG", "LOG_REUSE", "MIXED"] = Field(
        ..., description="답변 출처"
    )
    ai_confidence: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="AI 신뢰도 (0~1)"
    )
    created_at: datetime = Field(..., description="생성 시각 (UTC)")


class FaqDraftGenerateResponse(BaseModel):
    """
    FAQ 초안 생성 응답

    Attributes:
        status: 처리 상태 (SUCCESS, FAILED)
        faq_draft: 생성된 FAQ 초안 (성공 시)
        error_message: 에러 메시지 (실패 시)
    """

    status: Literal["SUCCESS", "FAILED"] = Field(..., description="처리 상태")
    faq_draft: Optional[FaqDraft] = Field(None, description="생성된 FAQ 초안")
    error_message: Optional[str] = Field(None, description="에러 메시지")
