"""
Quiz Quality Check (QC) Models (Phase 17)

LLM이 생성한 퀴즈 문항을 여러 단계로 검증하는 QC 파이프라인용 모델.
"""

from enum import StrEnum
from typing import List, Optional

from pydantic import BaseModel, Field


# =============================================================================
# QC 단계 Enum
# =============================================================================


class QuizQcStage(StrEnum):
    NONE = "NONE"
    SCHEMA = "SCHEMA"
    SOURCE = "SOURCE"
    SELF_CHECK = "SELF_CHECK"


# =============================================================================
# QC 사유 Enum
# =============================================================================


class QuizQcReasonCode(StrEnum):
    NONE = "NONE"
    INVALID_STRUCTURE = "INVALID_STRUCTURE"
    MULTIPLE_CORRECT = "MULTIPLE_CORRECT"
    NO_CORRECT_OPTION = "NO_CORRECT_OPTION"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    LOW_QUALITY_TEXT = "LOW_QUALITY_TEXT"
    AMBIGUOUS_QUESTION = "AMBIGUOUS_QUESTION"
    OTHER = "OTHER"


# =============================================================================
# 문항별 QC 결과 모델 (🔥 핵심 수정 지점)
# =============================================================================


class QuizQuestionQcResult(BaseModel):
    """
    개별 퀴즈 문항의 QC 결과.
    """

    question_id: Optional[str] = Field(
        default=None,
        alias="questionId",
        description="문항 ID",
    )

    # 🔥 추가: 문항의 근거 블록 ID (복수 허용)
    source_block_ids: List[str] = Field(
        default_factory=list,
        alias="sourceBlockIds",
        description="문항 생성에 사용된 원문 블록 ID 목록",
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
    """

    total_questions: int = Field(
        default=0,
        alias="totalQuestions",
    )

    passed_questions: int = Field(
        default=0,
        alias="passedQuestions",
    )

    failed_questions: int = Field(
        default=0,
        alias="failedQuestions",
    )

    question_results: List[QuizQuestionQcResult] = Field(
        default_factory=list,
        alias="questionResults",
    )

    model_config = {"populate_by_name": True}


# =============================================================================
# LLM Self-check 응답 파싱용 모델
# =============================================================================


class LLMSelfCheckResponse(BaseModel):
    verdict: str = Field(default="FAIL")
    reason_code: Optional[str] = Field(default=None, alias="reasonCode")
    reason_detail: Optional[str] = Field(default=None, alias="reasonDetail")

    model_config = {"populate_by_name": True}


# =============================================================================
# AI 로그용 메타 모델
# =============================================================================


class QuizQcLogMeta(BaseModel):
    education_id: Optional[str] = Field(default=None, alias="educationId")
    doc_id: Optional[str] = Field(default=None, alias="docId")
    attempt_no: int = Field(default=1, alias="attemptNo")

    quiz_qc_total_questions: int = Field(
        default=0,
        alias="quizQcTotalQuestions",
    )
    quiz_qc_passed_questions: int = Field(
        default=0,
        alias="quizQcPassedQuestions",
    )
    quiz_qc_failed_questions: int = Field(
        default=0,
        alias="quizQcFailedQuestions",
    )

    llm_prompt_version: str = Field(default="v1", alias="llmPromptVersion")
    llm_selfcheck_prompt_version: str = Field(
        default="v1",
        alias="llmSelfcheckPromptVersion",
    )

    model_config = {"populate_by_name": True}
