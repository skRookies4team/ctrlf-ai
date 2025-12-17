"""
Gap Suggestion Models (Phase 15)

RAG Gap 질문들을 분석하여 사규/교육 보완 제안을 생성하는 API용 모델.
백엔드/관리자 대시보드에서 호출하여 사용.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class GapQuestion(BaseModel):
    """RAG Gap으로 식별된 질문."""

    question_id: str = Field(
        ...,
        alias="questionId",
        description="질문 로그 ID",
    )
    text: str = Field(
        ...,
        description="질문 원문 텍스트",
    )
    user_role: str = Field(
        ...,
        alias="userRole",
        description="질문자 역할 (EMPLOYEE, ADMIN 등)",
    )
    intent: str = Field(
        ...,
        description="분류된 의도 (POLICY_QA, EDU_QA 등)",
    )
    domain: str = Field(
        ...,
        description="도메인 (POLICY, EDU 등)",
    )
    asked_count: Optional[int] = Field(
        default=None,
        alias="askedCount",
        description="동일/유사 질문 횟수 (집계된 경우)",
    )

    model_config = {"populate_by_name": True}


class GapSuggestionRequest(BaseModel):
    """
    RAG Gap 보완 제안 요청.

    백엔드에서 수집한 RAG Gap 질문 목록을 보내면,
    AI가 사규/교육 보완 제안을 생성합니다.
    """

    domain: Optional[str] = Field(
        default=None,
        description="대상 도메인 필터 (POLICY, EDU 등)",
    )
    questions: List[GapQuestion] = Field(
        ...,
        description="RAG Gap 질문 목록",
        min_length=0,
    )

    model_config = {"populate_by_name": True}


class GapSuggestionItem(BaseModel):
    """개별 보완 제안 항목."""

    id: str = Field(
        ...,
        description="제안 ID (SUG-001 등)",
    )
    title: str = Field(
        ...,
        description="제안 제목",
    )
    description: str = Field(
        ...,
        description="제안 설명 (왜 필요한지, 어떤 내용을 추가해야 하는지)",
    )
    related_question_ids: List[str] = Field(
        default_factory=list,
        alias="relatedQuestionIds",
        description="관련된 질문 ID 목록",
    )
    priority: Optional[Literal["HIGH", "MEDIUM", "LOW"]] = Field(
        default=None,
        description="우선순위 (HIGH, MEDIUM, LOW)",
    )

    model_config = {"populate_by_name": True}


class GapSuggestionResponse(BaseModel):
    """
    RAG Gap 보완 제안 응답.

    AI가 분석한 요약과 구체적인 보완 제안 항목들을 반환합니다.
    """

    summary: str = Field(
        ...,
        description="전체 분석 요약",
    )
    suggestions: List[GapSuggestionItem] = Field(
        default_factory=list,
        description="보완 제안 항목 목록",
    )

    model_config = {"populate_by_name": True}


# =============================================================================
# LLM 응답 파싱용 내부 모델
# =============================================================================


class LLMSuggestionItem(BaseModel):
    """LLM이 반환하는 제안 항목 (파싱용)."""

    title: str
    description: str
    related_question_ids: List[str] = Field(default_factory=list)
    priority: Optional[str] = None


class LLMSuggestionResponse(BaseModel):
    """LLM이 반환하는 전체 응답 (파싱용)."""

    summary: str
    suggestions: List[LLMSuggestionItem] = Field(default_factory=list)
