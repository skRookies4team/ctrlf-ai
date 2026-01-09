"""
FAQ 모델 (Phase 18)

FAQ 초안 생성 API의 요청/응답 DTO를 정의합니다.

사용 예시:
    from app.models.faq import FaqDraftGenerateRequest, FaqDraftGenerateResponse

FAQ 도메인 목록 (백엔드/프론트와 동기화):
- ACCOUNT: 계정
- APPROVAL: 결재
- HR: 인사
- PAY: 급여
- WELFARE: 복지
- EDUCATION: 교육
- IT: IT
- SECURITY: 보안
- FACILITY: 시설
- ETC: 기타
"""

from datetime import datetime
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# FAQ 도메인 Enum (백엔드/프론트와 동기화)
# =============================================================================


class FaqDomain(str, Enum):
    """
    FAQ 도메인 목록
    
    백엔드 초기 데이터 마이그레이션 파일(V15__insert_initial_faq_data.sql) 기준으로 정의됨.
    """

    ACCOUNT = "ACCOUNT"  # 계정
    APPROVAL = "APPROVAL"  # 결재
    HR = "HR"  # 인사
    PAY = "PAY"  # 급여
    WELFARE = "WELFARE"  # 복지
    EDUCATION = "EDUCATION"  # 교육
    IT = "IT"  # IT
    SECURITY = "SECURITY"  # 보안
    FACILITY = "FACILITY"  # 시설
    ETC = "ETC"  # 기타


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
        domain: 도메인 (백엔드/프론트와 동기화된 도메인 목록 사용)
            - ACCOUNT: 계정
            - APPROVAL: 결재
            - HR: 인사
            - PAY: 급여
            - WELFARE: 복지
            - EDUCATION: 교육
            - IT: IT
            - SECURITY: 보안
            - FACILITY: 시설
            - ETC: 기타
        cluster_id: FAQ 후보 클러스터 ID
        canonical_question: 클러스터를 대표하는 질문
        sample_questions: 실제 직원 질문 예시들
        top_docs: 백엔드가 이미 RAG에서 뽑아온 후보 문서들 (선택)
        avg_intent_confidence: 평균 의도 신뢰도 (0.0~1.0, 선택)
        model: A/B 테스트 임베딩 모델 (선택, 기본값: openai)
    """

    domain: str = Field(
        ...,
        min_length=1,
        description="도메인 (ACCOUNT, APPROVAL, HR, PAY, WELFARE, EDUCATION, IT, SECURITY, FACILITY, ETC 중 하나)",
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
    avg_intent_confidence: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="평균 의도 신뢰도 (0.0~1.0, Chat-Service에서 전달되는 값, null이면 검증 스킵)",
    )
    # Phase AB: A/B 테스트 임베딩 모델 선택 (request_id 대체)
    model: Optional[Literal["openai", "sroberta"]] = Field(
        None,
        description="A/B 테스트 임베딩 모델: 'openai' 또는 'sroberta'. 기본값: openai",
    )
    # LLM 프로바이더 선택 (일반 채팅과 동일)
    llm_model: Optional[Literal["exaone", "openai"]] = Field(
        default=None,
        description="LLM provider: 'exaone' (내부 EXAONE) or 'openai' (GPT). 미지정시 서버 기본값(exaone)",
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
    answer_source: Literal["AI_RAG", "LOG_REUSE", "MIXED", "TOP_DOCS", "RAGFLOW", "MILVUS"] = Field(
        ..., description="답변 출처 (Phase 19-AI-3: TOP_DOCS/RAGFLOW, Option 3: MILVUS 추가)"
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


# =============================================================================
# Phase 20-AI-2: 배치 FAQ 생성 모델
# =============================================================================


class FaqDraftGenerateBatchRequest(BaseModel):
    """
    배치 FAQ 초안 생성 요청 (Phase 20-AI-2)

    다수의 FAQ 클러스터를 한 번에 생성합니다.

    Attributes:
        items: FAQ 초안 생성 요청 리스트
        concurrency: 동시 처리 수 (선택, 기본값: 서버 설정 FAQ_BATCH_CONCURRENCY)
    """

    items: List[FaqDraftGenerateRequest] = Field(
        ..., min_length=1, description="FAQ 초안 생성 요청 리스트"
    )
    concurrency: Optional[int] = Field(
        None, ge=1, le=10, description="동시 처리 수 (1-10, 기본값: 서버 설정)"
    )


class FaqDraftGenerateBatchResponse(BaseModel):
    """
    배치 FAQ 초안 생성 응답 (Phase 20-AI-2)

    요청 순서대로 각 항목의 결과를 반환합니다.
    각 항목은 독립적으로 처리되어 한 개 실패가 전체 실패로 번지지 않습니다.

    Attributes:
        items: FAQ 초안 생성 응답 리스트 (요청 순서 유지)
        total_count: 전체 요청 수
        success_count: 성공한 요청 수
        failed_count: 실패한 요청 수
    """

    items: List[FaqDraftGenerateResponse] = Field(
        ..., description="FAQ 초안 생성 응답 리스트 (요청 순서 유지)"
    )
    total_count: int = Field(..., ge=0, description="전체 요청 수")
    success_count: int = Field(..., ge=0, description="성공한 요청 수")
    failed_count: int = Field(..., ge=0, description="실패한 요청 수")


# =============================================================================
# 자동 FAQ 생성 모델 (Auto FAQ Generation)
# =============================================================================


class FaqCandidate(BaseModel):
    """
    FAQ 후보 정보

    후보 선정 단계에서 생성되는 후보 정보입니다.
    """

    candidate_id: str = Field(..., description="후보 ID (UUID)")
    cluster_id: str = Field(..., description="클러스터 ID")
    canonical_question: str = Field(..., description="표준 질문")
    frequency_score: float = Field(..., ge=0.0, le=1.0, description="빈도 점수")
    recency_score: float = Field(..., ge=0.0, le=1.0, description="최근성 점수")
    total_score: float = Field(..., ge=0.0, le=1.0, description="종합 점수")
    domain: Optional[str] = Field(None, description="도메인")
    sample_questions: List[str] = Field(
        default_factory=list, description="실제 직원 질문 예시들"
    )
    user_count: int = Field(..., ge=1, description="질문한 사용자 수")


class FaqAutoGenerateRequest(BaseModel):
    """
    자동 FAQ 생성 요청

    질문 로그를 분석하여 FAQ 후보를 선정하고, 필요시 FAQ 초안을 자동 생성합니다.

    Attributes:
        domain: 도메인 필터 (선택, null이면 모든 도메인)
        min_frequency: 최소 질문 빈도 (여러 사용자 간의 질문이 이 횟수 이상이어야 후보로 선정)
        days_back: 조회 기간 일수 (최근 N일간의 질문 로그 분석)
        max_candidates: 최대 후보 수 제한
        auto_generate_drafts: true이면 후보 선정 후 자동으로 FAQ 초안까지 생성
    """

    model_config = ConfigDict(
        populate_by_name=True,  # camelCase (daysBack)와 snake_case (days_back) 모두 허용
    )

    domain: Optional[str] = Field(
        None, description="도메인 필터 (ACCOUNT, APPROVAL, HR, PAY, WELFARE, EDUCATION, IT, SECURITY, FACILITY, ETC 중 하나, null이면 모든 도메인)"
    )
    min_frequency: int = Field(
        default=3, ge=1, alias="minFrequency", description="최소 질문 빈도 (여러 사용자 간의 질문 횟수)"
    )
    days_back: int = Field(
        default=30, ge=1, le=365, alias="daysBack", description="조회 기간 일수 (최근 N일간)"
    )
    max_candidates: int = Field(
        default=20, ge=1, le=100, alias="maxCandidates", description="최대 후보 수 제한"
    )
    auto_generate_drafts: Optional[bool] = Field(
        default=None, alias="autoGenerateDrafts", description="자동으로 FAQ 초안 생성 여부 (null이면 기본값 True, 명시적으로 False로 설정하면 초안 생성 안 함)"
    )
    # LLM 프로바이더 선택 (일반 채팅과 동일)
    llm_model: Optional[Literal["exaone", "openai"]] = Field(
        default=None, alias="llmModel",
        description="LLM provider: 'exaone' (내부 EXAONE) or 'openai' (GPT). 미지정시 서버 기본값(exaone)",
    )


class FaqAutoGenerateResponse(BaseModel):
    """
    자동 FAQ 생성 응답

    질문 로그 분석 결과와 생성된 FAQ 초안 목록을 반환합니다.

    Attributes:
        status: 처리 상태 (SUCCESS, PARTIAL, FAILED)
        candidates_found: 발견된 후보 수
        drafts_generated: 생성된 초안 수
        drafts_failed: 실패한 초안 수
        candidates: FAQ 후보 목록 (로깅/디버깅용, 선택적)
        drafts: 생성된 FAQ 초안 목록
        error_message: 에러 메시지 (실패 시)
    """

    status: Literal["SUCCESS", "PARTIAL", "FAILED"] = Field(
        ..., description="처리 상태"
    )
    candidates_found: int = Field(..., ge=0, description="발견된 후보 수")
    drafts_generated: int = Field(..., ge=0, description="생성된 초안 수")
    drafts_failed: int = Field(..., ge=0, description="실패한 초안 수")
    candidates: List[FaqCandidate] = Field(
        default_factory=list, description="FAQ 후보 목록 (로깅/디버깅용)"
    )
    drafts: List[FaqDraft] = Field(
        default_factory=list, description="생성된 FAQ 초안 목록"
    )
    error_message: Optional[str] = Field(None, description="에러 메시지 (실패 시)")