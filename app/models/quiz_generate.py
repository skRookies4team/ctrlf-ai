"""
Quiz Generate Models (Phase 16)

교육/사규 문서에서 추출된 QUIZ_CANDIDATE 블록들을 입력으로 받아,
LLM이 객관식 퀴즈(문제/보기/정답/난이도/출처 메타)를 자동 생성하는 API용 모델.

주요 기능:
- 1차 응시: 새 퀴즈 세트 생성
- 2차 응시: 기존 문항 중복 방지
"""

from datetime import datetime
from enum import Enum
from typing import Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


# =============================================================================
# Enums
# =============================================================================


class QuestionType(str, Enum):
    """문제 유형 Enum."""

    MCQ_SINGLE = "MCQ_SINGLE"  # 단일 정답 객관식 (현재 지원)
    # MCQ_MULTIPLE = "MCQ_MULTIPLE"  # 다중 정답 객관식 (추후 지원 예정)
    # TRUE_FALSE = "TRUE_FALSE"  # O/X 문제 (추후 지원 예정)


class Difficulty(str, Enum):
    """난이도 Enum."""

    EASY = "EASY"
    NORMAL = "NORMAL"
    HARD = "HARD"


class QuestionStatus(str, Enum):
    """문제 상태 Enum."""

    DRAFT_AI_GENERATED = "DRAFT_AI_GENERATED"  # AI 생성 초안
    REVIEWED = "REVIEWED"  # 검토 완료
    APPROVED = "APPROVED"  # 승인됨
    REJECTED = "REJECTED"  # 반려됨


# =============================================================================
# Request DTOs
# =============================================================================


class QuizCandidateBlock(BaseModel):
    """퀴즈 생성에 사용할 텍스트 블록."""

    block_id: str = Field(
        ...,
        alias="blockId",
        description="블록 ID",
    )
    chapter_id: Optional[str] = Field(
        default=None,
        alias="chapterId",
        description="챕터/장 ID",
    )
    learning_objective_id: Optional[str] = Field(
        default=None,
        alias="learningObjectiveId",
        description="학습 목표 ID",
    )
    text: str = Field(
        ...,
        description="퀴즈 생성에 사용할 텍스트 내용",
        min_length=1,
    )
    tags: List[str] = Field(
        default_factory=list,
        description="관련 태그 목록",
    )
    article_path: Optional[str] = Field(
        default=None,
        alias="articlePath",
        description="조항 경로 (예: 제3장 > 제2조 > 제1항)",
    )

    model_config = {"populate_by_name": True}


class ExcludePreviousQuestion(BaseModel):
    """2차 응시 시 제외할 기존 문항 정보."""

    question_id: str = Field(
        ...,
        alias="questionId",
        description="1차 응시 때의 문항 ID",
    )
    stem: str = Field(
        ...,
        description="1차 응시 때의 문제 텍스트",
    )

    model_config = {"populate_by_name": True}


class DifficultyDistribution(BaseModel):
    """난이도 분배 설정."""

    easy: int = Field(default=0, ge=0, description="쉬움 난이도 문항 수")
    normal: int = Field(default=0, ge=0, description="보통 난이도 문항 수")
    hard: int = Field(default=0, ge=0, description="어려움 난이도 문항 수")

    @property
    def total(self) -> int:
        """총 문항 수."""
        return self.easy + self.normal + self.hard


class QuizGenerateRequest(BaseModel):
    """
    퀴즈 생성 요청 DTO.

    교육/사규 문서에서 추출된 QUIZ_CANDIDATE 블록들을 받아
    LLM으로 객관식 퀴즈를 생성합니다.
    """

    education_id: str = Field(
        ...,
        alias="educationId",
        description="교육/코스 식별자",
    )
    doc_id: str = Field(
        ...,
        alias="docId",
        description="사규/교육 문서 ID",
    )
    doc_version: str = Field(
        default="v1",
        alias="docVersion",
        description="문서 버전",
    )
    attempt_no: int = Field(
        default=1,
        alias="attemptNo",
        ge=1,
        le=10,
        description="응시 차수 (1차, 2차 등)",
    )
    language: str = Field(
        default="ko",
        description="퀴즈 언어 (ko, en 등)",
    )
    num_questions: int = Field(
        default=10,
        alias="numQuestions",
        ge=1,
        le=50,
        description="생성할 문항 수",
    )
    difficulty_distribution: Optional[DifficultyDistribution] = Field(
        default=None,
        alias="difficultyDistribution",
        description="난이도별 문항 수 분배 (미지정 시 균등 분배)",
    )
    question_type: QuestionType = Field(
        default=QuestionType.MCQ_SINGLE,
        alias="questionType",
        description="문제 유형",
    )
    max_options: int = Field(
        default=4,
        alias="maxOptions",
        ge=2,
        le=6,
        description="보기 개수",
    )
    quiz_candidate_blocks: List[QuizCandidateBlock] = Field(
        ...,
        alias="quizCandidateBlocks",
        min_length=1,
        description="퀴즈 생성에 사용할 텍스트 블록 목록",
    )
    exclude_previous_questions: List[ExcludePreviousQuestion] = Field(
        default_factory=list,
        alias="excludePreviousQuestions",
        description="2차 응시 시 제외할 기존 문항 목록",
    )

    model_config = {"populate_by_name": True}

    @field_validator("quiz_candidate_blocks")
    @classmethod
    def validate_blocks_not_empty(cls, v: List[QuizCandidateBlock]) -> List[QuizCandidateBlock]:
        """퀴즈 후보 블록이 최소 1개 이상인지 검증."""
        if not v:
            raise ValueError("quiz_candidate_blocks must have at least 1 block")
        return v


# =============================================================================
# Response DTOs
# =============================================================================


class GeneratedQuizOption(BaseModel):
    """생성된 퀴즈 보기."""

    option_id: str = Field(
        ...,
        alias="optionId",
        description="보기 ID",
    )
    text: str = Field(
        ...,
        description="보기 텍스트",
    )
    is_correct: bool = Field(
        default=False,
        alias="isCorrect",
        description="정답 여부",
    )

    model_config = {"populate_by_name": True}


class GeneratedQuizQuestion(BaseModel):
    """생성된 퀴즈 문항."""

    question_id: str = Field(
        ...,
        alias="questionId",
        description="문항 ID",
    )
    status: QuestionStatus = Field(
        default=QuestionStatus.DRAFT_AI_GENERATED,
        description="문항 상태",
    )
    question_type: QuestionType = Field(
        default=QuestionType.MCQ_SINGLE,
        alias="questionType",
        description="문제 유형",
    )
    stem: str = Field(
        ...,
        description="문제 텍스트",
    )
    options: List[GeneratedQuizOption] = Field(
        ...,
        description="보기 목록",
        min_length=2,
    )
    difficulty: Difficulty = Field(
        default=Difficulty.NORMAL,
        description="난이도",
    )
    learning_objective_id: Optional[str] = Field(
        default=None,
        alias="learningObjectiveId",
        description="학습 목표 ID",
    )
    chapter_id: Optional[str] = Field(
        default=None,
        alias="chapterId",
        description="챕터/장 ID",
    )
    source_block_ids: List[str] = Field(
        default_factory=list,
        alias="sourceBlockIds",
        description="출처 블록 ID 목록",
    )
    source_doc_id: Optional[str] = Field(
        default=None,
        alias="sourceDocId",
        description="출처 문서 ID",
    )
    source_doc_version: Optional[str] = Field(
        default=None,
        alias="sourceDocVersion",
        description="출처 문서 버전",
    )
    source_article_path: Optional[str] = Field(
        default=None,
        alias="sourceArticlePath",
        description="출처 조항 경로",
    )
    tags: List[str] = Field(
        default_factory=list,
        description="관련 태그 목록",
    )
    explanation: Optional[str] = Field(
        default=None,
        description="정답 해설",
    )
    rationale: Optional[str] = Field(
        default=None,
        description="출처 근거 설명",
    )

    model_config = {"populate_by_name": True}


class QuizGenerateResponse(BaseModel):
    """
    퀴즈 생성 응답 DTO.

    LLM이 생성한 퀴즈 문항들을 구조화하여 반환합니다.
    """

    education_id: str = Field(
        ...,
        alias="educationId",
        description="교육/코스 식별자",
    )
    doc_id: str = Field(
        ...,
        alias="docId",
        description="문서 ID",
    )
    doc_version: str = Field(
        ...,
        alias="docVersion",
        description="문서 버전",
    )
    attempt_no: int = Field(
        ...,
        alias="attemptNo",
        description="응시 차수",
    )
    generated_count: int = Field(
        ...,
        alias="generatedCount",
        description="생성된 문항 수",
    )
    questions: List[GeneratedQuizQuestion] = Field(
        default_factory=list,
        description="생성된 문항 목록",
    )

    model_config = {"populate_by_name": True}


# =============================================================================
# LLM 응답 파싱용 내부 모델
# =============================================================================


class LLMQuizOption(BaseModel):
    """LLM이 반환하는 보기 (파싱용)."""

    text: str
    is_correct: bool = False


class LLMQuizQuestion(BaseModel):
    """LLM이 반환하는 문항 (파싱용)."""

    stem: str
    options: List[LLMQuizOption]
    difficulty: Optional[str] = None
    explanation: Optional[str] = None
    rationale: Optional[str] = None
    source_block_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class LLMQuizResponse(BaseModel):
    """LLM이 반환하는 전체 응답 (파싱용)."""

    questions: List[LLMQuizQuestion] = Field(default_factory=list)


# =============================================================================
# 헬퍼 함수
# =============================================================================


def generate_question_id(prefix: str = "Q") -> str:
    """고유한 문항 ID 생성."""
    date_str = datetime.now().strftime("%Y%m%d")
    short_uuid = uuid4().hex[:8].upper()
    return f"{prefix}-{date_str}-{short_uuid}"


def generate_option_id(index: int) -> str:
    """보기 ID 생성."""
    return f"OPT-{index + 1}"
