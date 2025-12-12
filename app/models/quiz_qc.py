"""
Quiz Quality Check (QC) Models (Phase 17)

LLM이 생성한 퀴즈 문항을 여러 단계로 검증하는 QC 파이프라인용 모델.

검증 단계:
1. SCHEMA: 스키마/구조 검증 (필수 필드, 옵션 수, 정답 개수)
2. SOURCE: 원문 일치 검증 (정답이 출처 블록과 일치하는지)
3. SELF_CHECK: LLM Self-check (복수 정답, 모호성 등)

사용처:
- QuizQualityService에서 검증 결과 반환
- AI 로그에 QC 결과 저장 (프롬프트 튜닝/품질 분석용)
"""

from enum import StrEnum
from typing import List, Optional

from pydantic import BaseModel, Field


# =============================================================================
# QC 단계 Enum
# =============================================================================


class QuizQcStage(StrEnum):
    """퀴즈 QC 실패 단계."""

    NONE = "NONE"  # 모든 검증 통과
    SCHEMA = "SCHEMA"  # 스키마/구조 검증에서 실패
    SOURCE = "SOURCE"  # 원문 일치 검증에서 실패
    SELF_CHECK = "SELF_CHECK"  # LLM Self-check에서 실패


# =============================================================================
# QC 사유 Enum
# =============================================================================


class QuizQcReasonCode(StrEnum):
    """퀴즈 QC 실패 사유 코드."""

    NONE = "NONE"  # 실패 없음 (통과)
    INVALID_STRUCTURE = "INVALID_STRUCTURE"  # 필수 필드 누락, 옵션 부족 등
    MULTIPLE_CORRECT = "MULTIPLE_CORRECT"  # 정답 후보가 2개 이상
    NO_CORRECT_OPTION = "NO_CORRECT_OPTION"  # 정답이 없음
    SOURCE_MISMATCH = "SOURCE_MISMATCH"  # 문서와 상충 / 원문에 근거 없음
    LOW_QUALITY_TEXT = "LOW_QUALITY_TEXT"  # 너무 짧거나 의미 불명
    AMBIGUOUS_QUESTION = "AMBIGUOUS_QUESTION"  # 질문이 모호하거나 답변 불가
    OTHER = "OTHER"  # 기타 사유


# =============================================================================
# 문항별 QC 결과 모델
# =============================================================================


class QuizQuestionQcResult(BaseModel):
    """
    개별 퀴즈 문항의 QC 결과.

    Attributes:
        question_id: QuizGenerateService에서 부여된 문항 ID
        qc_pass: QC 통과 여부
        qc_stage_failed: 실패한 QC 단계 (통과 시 NONE)
        qc_reason_code: 실패 사유 코드
        qc_reason_detail: 상세 사유 설명
    """

    question_id: Optional[str] = Field(
        default=None,
        alias="questionId",
        description="문항 ID",
    )
    qc_pass: bool = Field(
        default=True,
        alias="qcPass",
        description="QC 통과 여부",
    )
    qc_stage_failed: QuizQcStage = Field(
        default=QuizQcStage.NONE,
        alias="qcStageFailed",
        description="실패한 QC 단계",
    )
    qc_reason_code: QuizQcReasonCode = Field(
        default=QuizQcReasonCode.NONE,
        alias="qcReasonCode",
        description="실패 사유 코드",
    )
    qc_reason_detail: Optional[str] = Field(
        default=None,
        alias="qcReasonDetail",
        description="상세 사유 설명",
    )

    model_config = {"populate_by_name": True}


# =============================================================================
# 세트 단위 QC 결과 모델
# =============================================================================


class QuizSetQcResult(BaseModel):
    """
    퀴즈 세트 전체의 QC 결과 요약.

    AI 로그/대시보드 저장용으로 설계됨.

    Attributes:
        total_questions: 검증 대상 전체 문항 수
        passed_questions: QC 통과 문항 수
        failed_questions: QC 실패 문항 수
        question_results: 문항별 QC 결과 목록
    """

    total_questions: int = Field(
        default=0,
        alias="totalQuestions",
        description="검증 대상 전체 문항 수",
    )
    passed_questions: int = Field(
        default=0,
        alias="passedQuestions",
        description="QC 통과 문항 수",
    )
    failed_questions: int = Field(
        default=0,
        alias="failedQuestions",
        description="QC 실패 문항 수",
    )
    question_results: List[QuizQuestionQcResult] = Field(
        default_factory=list,
        alias="questionResults",
        description="문항별 QC 결과 목록",
    )

    model_config = {"populate_by_name": True}


# =============================================================================
# LLM Self-check 응답 파싱용 모델
# =============================================================================


class LLMSelfCheckResponse(BaseModel):
    """
    LLM Self-check 응답 파싱용 모델.

    LLM이 반환하는 JSON 형식:
    {"verdict": "PASS" | "FAIL", "reason_code": "...", "reason_detail": "..."}
    """

    verdict: str = Field(
        default="FAIL",
        description="검증 결과 (PASS/FAIL)",
    )
    reason_code: Optional[str] = Field(
        default=None,
        alias="reasonCode",
        description="실패 사유 코드",
    )
    reason_detail: Optional[str] = Field(
        default=None,
        alias="reasonDetail",
        description="상세 사유 설명",
    )

    model_config = {"populate_by_name": True}


# =============================================================================
# AI 로그용 메타 모델 (향후 확장용)
# =============================================================================


class QuizQcLogMeta(BaseModel):
    """
    퀴즈 QC 결과 로그용 메타데이터.

    프롬프트 튜닝/품질 분석에 활용.

    Note:
        실제 DB 저장은 백엔드 팀에서 구현 예정.
        이 모델은 AI Gateway에서 계산한 메타를 전달하는 용도.
    """

    education_id: Optional[str] = Field(
        default=None,
        alias="educationId",
        description="교육/코스 ID",
    )
    doc_id: Optional[str] = Field(
        default=None,
        alias="docId",
        description="문서 ID",
    )
    attempt_no: int = Field(
        default=1,
        alias="attemptNo",
        description="응시 차수",
    )
    quiz_qc_total_questions: int = Field(
        default=0,
        alias="quizQcTotalQuestions",
        description="QC 대상 전체 문항 수",
    )
    quiz_qc_passed_questions: int = Field(
        default=0,
        alias="quizQcPassedQuestions",
        description="QC 통과 문항 수",
    )
    quiz_qc_failed_questions: int = Field(
        default=0,
        alias="quizQcFailedQuestions",
        description="QC 실패 문항 수",
    )
    llm_prompt_version: str = Field(
        default="v1",
        alias="llmPromptVersion",
        description="퀴즈 생성 프롬프트 버전",
    )
    llm_selfcheck_prompt_version: str = Field(
        default="v1",
        alias="llmSelfcheckPromptVersion",
        description="Self-check 프롬프트 버전",
    )

    model_config = {"populate_by_name": True}
