"""
Phase 17: 퀴즈 QC (Quality Check) 파이프라인 테스트

테스트 항목:
1. QC 모델/Enum 테스트
2. QuizQualityService 단위 테스트
3. SCHEMA 실패 케이스
4. SOURCE 실패 케이스
5. SELF_CHECK 실패 케이스
6. 모든 단계 통과 케이스
7. 세트 요약 결과 검증
8. QuizGenerateService 통합 테스트
"""

import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from app.models.quiz_generate import (
    GeneratedQuizOption,
    GeneratedQuizQuestion,
    QuizCandidateBlock,
)
from app.models.quiz_qc import (
    LLMSelfCheckResponse,
    QuizQcLogMeta,
    QuizQcReasonCode,
    QuizQcStage,
    QuizQuestionQcResult,
    QuizSetQcResult,
)
from app.services.quiz_quality_service import QuizQualityService


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


@pytest.fixture
def sample_source_blocks() -> List[QuizCandidateBlock]:
    """샘플 출처 블록들."""
    return [
        QuizCandidateBlock(
            block_id="BLOCK-001",
            chapter_id="CH1",
            learning_objective_id="LO-1",
            text="USB 메모리를 사외로 반출할 때에는 정보보호팀의 사전 승인을 받아야 한다.",
            tags=["USB", "반출", "승인"],
        ),
        QuizCandidateBlock(
            block_id="BLOCK-002",
            chapter_id="CH1",
            learning_objective_id="LO-2",
            text="회사 외부 클라우드 스토리지에 고객 개인정보를 업로드하는 것은 금지된다.",
            tags=["클라우드", "개인정보"],
        ),
        QuizCandidateBlock(
            block_id="BLOCK-003",
            chapter_id="CH2",
            learning_objective_id="LO-3",
            text="비밀번호는 8자리 이상, 영문/숫자/특수문자 조합으로 설정해야 한다.",
            tags=["비밀번호", "보안"],
        ),
    ]


@pytest.fixture
def valid_question() -> GeneratedQuizQuestion:
    """유효한 퀴즈 문항."""
    return GeneratedQuizQuestion(
        question_id="Q-TEST-001",
        stem="USB 메모리를 사외로 반출할 때 필요한 조치는 무엇인가요?",
        options=[
            GeneratedQuizOption(option_id="OPT-1", text="정보보호팀의 사전 승인", is_correct=True),
            GeneratedQuizOption(option_id="OPT-2", text="팀장에게 구두 보고", is_correct=False),
            GeneratedQuizOption(option_id="OPT-3", text="자유롭게 반출", is_correct=False),
            GeneratedQuizOption(option_id="OPT-4", text="사후 보고", is_correct=False),
        ],
        source_block_ids=["BLOCK-001"],
    )


# =============================================================================
# 1. QC 모델/Enum 테스트
# =============================================================================


class TestQuizQcModels:
    """QC 모델 테스트."""

    def test_quiz_qc_stage_enum(self) -> None:
        """QuizQcStage Enum 테스트."""
        assert QuizQcStage.NONE == "NONE"
        assert QuizQcStage.SCHEMA == "SCHEMA"
        assert QuizQcStage.SOURCE == "SOURCE"
        assert QuizQcStage.SELF_CHECK == "SELF_CHECK"

    def test_quiz_qc_reason_code_enum(self) -> None:
        """QuizQcReasonCode Enum 테스트."""
        assert QuizQcReasonCode.NONE == "NONE"
        assert QuizQcReasonCode.INVALID_STRUCTURE == "INVALID_STRUCTURE"
        assert QuizQcReasonCode.MULTIPLE_CORRECT == "MULTIPLE_CORRECT"
        assert QuizQcReasonCode.NO_CORRECT_OPTION == "NO_CORRECT_OPTION"
        assert QuizQcReasonCode.SOURCE_MISMATCH == "SOURCE_MISMATCH"

    def test_quiz_question_qc_result_creation(self) -> None:
        """QuizQuestionQcResult 모델 생성 테스트."""
        result = QuizQuestionQcResult(
            question_id="Q-001",
            qc_pass=False,
            qc_stage_failed=QuizQcStage.SCHEMA,
            qc_reason_code=QuizQcReasonCode.INVALID_STRUCTURE,
            qc_reason_detail="옵션 개수 부족",
        )

        assert result.question_id == "Q-001"
        assert result.qc_pass is False
        assert result.qc_stage_failed == QuizQcStage.SCHEMA
        assert result.qc_reason_code == QuizQcReasonCode.INVALID_STRUCTURE

    def test_quiz_set_qc_result_creation(self) -> None:
        """QuizSetQcResult 모델 생성 테스트."""
        result = QuizSetQcResult(
            total_questions=10,
            passed_questions=8,
            failed_questions=2,
            question_results=[],
        )

        assert result.total_questions == 10
        assert result.passed_questions == 8
        assert result.failed_questions == 2

    def test_llm_selfcheck_response_parsing(self) -> None:
        """LLMSelfCheckResponse 파싱 테스트."""
        data = {
            "verdict": "FAIL",
            "reason_code": "MULTIPLE_CORRECT",
            "reason_detail": "보기 1과 2가 모두 정답이 될 수 있습니다.",
        }
        response = LLMSelfCheckResponse(**data)

        assert response.verdict == "FAIL"
        assert response.reason_code == "MULTIPLE_CORRECT"
        assert "보기 1" in response.reason_detail

    def test_quiz_qc_result_json_serialization(self) -> None:
        """QuizQuestionQcResult JSON 직렬화 테스트."""
        result = QuizQuestionQcResult(
            question_id="Q-001",
            qc_pass=False,
            qc_stage_failed=QuizQcStage.SELF_CHECK,
            qc_reason_code=QuizQcReasonCode.AMBIGUOUS_QUESTION,
            qc_reason_detail="질문이 모호함",
        )

        json_data = result.model_dump(by_alias=True)

        assert json_data["questionId"] == "Q-001"
        assert json_data["qcPass"] is False
        assert json_data["qcStageFailed"] == "SELF_CHECK"
        assert json_data["qcReasonCode"] == "AMBIGUOUS_QUESTION"

    def test_quiz_qc_log_meta_creation(self) -> None:
        """QuizQcLogMeta 모델 생성 테스트."""
        meta = QuizQcLogMeta(
            education_id="EDU-001",
            doc_id="DOC-001",
            attempt_no=1,
            quiz_qc_total_questions=10,
            quiz_qc_passed_questions=8,
            quiz_qc_failed_questions=2,
        )

        assert meta.education_id == "EDU-001"
        assert meta.quiz_qc_total_questions == 10


# =============================================================================
# 2. QuizQualityService 단위 테스트 - SCHEMA 실패
# =============================================================================


class TestSchemaValidation:
    """SCHEMA 검증 테스트."""

    @pytest.mark.anyio
    async def test_fail_empty_stem(
        self,
        sample_source_blocks: List[QuizCandidateBlock],
    ) -> None:
        """빈 stem 실패 테스트."""
        question = GeneratedQuizQuestion(
            question_id="Q-001",
            stem="",  # 빈 stem
            options=[
                GeneratedQuizOption(option_id="OPT-1", text="정답", is_correct=True),
                GeneratedQuizOption(option_id="OPT-2", text="오답", is_correct=False),
            ],
        )

        service = QuizQualityService(selfcheck_enabled=False)
        valid, qc_result = await service.validate_quiz_set(
            questions=[question],
            source_blocks=sample_source_blocks,
        )

        assert len(valid) == 0
        assert qc_result.failed_questions == 1
        assert qc_result.question_results[0].qc_pass is False
        assert qc_result.question_results[0].qc_stage_failed == QuizQcStage.SCHEMA
        assert qc_result.question_results[0].qc_reason_code == QuizQcReasonCode.LOW_QUALITY_TEXT

    @pytest.mark.anyio
    async def test_fail_single_option(
        self,
        sample_source_blocks: List[QuizCandidateBlock],
    ) -> None:
        """보기가 1개인 경우 실패 테스트.

        Note: Pydantic 모델에서 이미 min_length=2 검증이 있으므로,
        직접 서비스 내부 메서드를 테스트합니다.
        """
        # Pydantic 검증을 우회하여 직접 _validate_schema 테스트
        service = QuizQualityService(selfcheck_enabled=False)

        # 정상 문항 생성 후 options를 수동으로 변경하여 테스트
        # (실제로는 Pydantic이 막으므로, 서비스 내부 검증 로직만 테스트)
        question = GeneratedQuizQuestion(
            question_id="Q-001",
            stem="테스트 문제입니다",
            options=[
                GeneratedQuizOption(option_id="OPT-1", text="보기1", is_correct=True),
                GeneratedQuizOption(option_id="OPT-2", text="보기2", is_correct=False),
            ],
        )

        # options를 강제로 1개로 변경 (테스트용)
        question.options = [
            GeneratedQuizOption(option_id="OPT-1", text="유일한 보기", is_correct=True),
        ]

        valid, qc_result = await service.validate_quiz_set(
            questions=[question],
            source_blocks=sample_source_blocks,
        )

        assert len(valid) == 0
        assert qc_result.question_results[0].qc_stage_failed == QuizQcStage.SCHEMA
        assert qc_result.question_results[0].qc_reason_code == QuizQcReasonCode.INVALID_STRUCTURE

    @pytest.mark.anyio
    async def test_fail_no_correct_option(
        self,
        sample_source_blocks: List[QuizCandidateBlock],
    ) -> None:
        """정답이 없는 경우 실패 테스트."""
        question = GeneratedQuizQuestion(
            question_id="Q-001",
            stem="정답이 없는 문제입니다",
            options=[
                GeneratedQuizOption(option_id="OPT-1", text="오답1", is_correct=False),
                GeneratedQuizOption(option_id="OPT-2", text="오답2", is_correct=False),
                GeneratedQuizOption(option_id="OPT-3", text="오답3", is_correct=False),
            ],
        )

        service = QuizQualityService(selfcheck_enabled=False)
        valid, qc_result = await service.validate_quiz_set(
            questions=[question],
            source_blocks=sample_source_blocks,
        )

        assert len(valid) == 0
        assert qc_result.question_results[0].qc_stage_failed == QuizQcStage.SCHEMA
        assert qc_result.question_results[0].qc_reason_code == QuizQcReasonCode.NO_CORRECT_OPTION

    @pytest.mark.anyio
    async def test_fail_multiple_correct_options(
        self,
        sample_source_blocks: List[QuizCandidateBlock],
    ) -> None:
        """정답이 2개 이상인 경우 실패 테스트."""
        question = GeneratedQuizQuestion(
            question_id="Q-001",
            stem="정답이 여러 개인 문제입니다",
            options=[
                GeneratedQuizOption(option_id="OPT-1", text="정답1", is_correct=True),
                GeneratedQuizOption(option_id="OPT-2", text="정답2", is_correct=True),
                GeneratedQuizOption(option_id="OPT-3", text="오답", is_correct=False),
            ],
        )

        service = QuizQualityService(selfcheck_enabled=False)
        valid, qc_result = await service.validate_quiz_set(
            questions=[question],
            source_blocks=sample_source_blocks,
        )

        assert len(valid) == 0
        assert qc_result.question_results[0].qc_stage_failed == QuizQcStage.SCHEMA
        assert qc_result.question_results[0].qc_reason_code == QuizQcReasonCode.MULTIPLE_CORRECT


# =============================================================================
# 3. SOURCE 검증 실패 테스트
# =============================================================================


class TestSourceValidation:
    """SOURCE 검증 테스트."""

    @pytest.mark.anyio
    async def test_fail_source_mismatch(
        self,
        sample_source_blocks: List[QuizCandidateBlock],
    ) -> None:
        """정답이 출처에 없는 경우 실패 테스트."""
        question = GeneratedQuizQuestion(
            question_id="Q-001",
            stem="USB 관련 질문입니다",
            options=[
                # 출처 블록에 "사전 승인" 키워드가 있지만, "즉시 폐기"는 없음
                GeneratedQuizOption(option_id="OPT-1", text="즉시 폐기 처리", is_correct=True),
                GeneratedQuizOption(option_id="OPT-2", text="사용 금지", is_correct=False),
            ],
            source_block_ids=["BLOCK-001"],
        )

        service = QuizQualityService(selfcheck_enabled=False)
        valid, qc_result = await service.validate_quiz_set(
            questions=[question],
            source_blocks=sample_source_blocks,
        )

        assert len(valid) == 0
        assert qc_result.question_results[0].qc_stage_failed == QuizQcStage.SOURCE
        assert qc_result.question_results[0].qc_reason_code == QuizQcReasonCode.SOURCE_MISMATCH

    @pytest.mark.anyio
    async def test_pass_source_match(
        self,
        sample_source_blocks: List[QuizCandidateBlock],
    ) -> None:
        """정답이 출처에 있는 경우 통과 테스트."""
        question = GeneratedQuizQuestion(
            question_id="Q-001",
            stem="USB 메모리를 사외로 반출할 때 필요한 조치는?",
            options=[
                # "승인" 키워드가 출처 블록에 있음
                GeneratedQuizOption(option_id="OPT-1", text="정보보호팀의 사전 승인", is_correct=True),
                GeneratedQuizOption(option_id="OPT-2", text="구두 보고", is_correct=False),
            ],
            source_block_ids=["BLOCK-001"],
        )

        service = QuizQualityService(selfcheck_enabled=False)
        valid, qc_result = await service.validate_quiz_set(
            questions=[question],
            source_blocks=sample_source_blocks,
        )

        assert len(valid) == 1
        assert qc_result.passed_questions == 1


# =============================================================================
# 4. SELF_CHECK 검증 테스트
# =============================================================================


class TestSelfCheckValidation:
    """SELF_CHECK 검증 테스트."""

    @pytest.mark.anyio
    async def test_fail_selfcheck_multiple_correct(
        self,
        sample_source_blocks: List[QuizCandidateBlock],
        valid_question: GeneratedQuizQuestion,
    ) -> None:
        """Self-check에서 복수 정답 발견 시 실패 테스트."""
        mock_llm_response = json.dumps({
            "verdict": "FAIL",
            "reason_code": "MULTIPLE_CORRECT",
            "reason_detail": "보기 1과 2 모두 문서에서 정답이 될 수 있습니다.",
        })

        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.return_value = mock_llm_response

        service = QuizQualityService(llm_client=mock_llm, selfcheck_enabled=True)
        valid, qc_result = await service.validate_quiz_set(
            questions=[valid_question],
            source_blocks=sample_source_blocks,
        )

        assert len(valid) == 0
        assert qc_result.question_results[0].qc_stage_failed == QuizQcStage.SELF_CHECK
        assert qc_result.question_results[0].qc_reason_code == QuizQcReasonCode.MULTIPLE_CORRECT

    @pytest.mark.anyio
    async def test_fail_selfcheck_ambiguous(
        self,
        sample_source_blocks: List[QuizCandidateBlock],
        valid_question: GeneratedQuizQuestion,
    ) -> None:
        """Self-check에서 모호한 질문 발견 시 실패 테스트."""
        mock_llm_response = json.dumps({
            "verdict": "FAIL",
            "reason_code": "AMBIGUOUS_QUESTION",
            "reason_detail": "질문이 너무 모호하여 정답을 특정하기 어렵습니다.",
        })

        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.return_value = mock_llm_response

        service = QuizQualityService(llm_client=mock_llm, selfcheck_enabled=True)
        valid, qc_result = await service.validate_quiz_set(
            questions=[valid_question],
            source_blocks=sample_source_blocks,
        )

        assert len(valid) == 0
        assert qc_result.question_results[0].qc_reason_code == QuizQcReasonCode.AMBIGUOUS_QUESTION

    @pytest.mark.anyio
    async def test_pass_selfcheck(
        self,
        sample_source_blocks: List[QuizCandidateBlock],
        valid_question: GeneratedQuizQuestion,
    ) -> None:
        """Self-check 통과 테스트."""
        mock_llm_response = json.dumps({
            "verdict": "PASS",
            "reason_code": None,
            "reason_detail": None,
        })

        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.return_value = mock_llm_response

        service = QuizQualityService(llm_client=mock_llm, selfcheck_enabled=True)
        valid, qc_result = await service.validate_quiz_set(
            questions=[valid_question],
            source_blocks=sample_source_blocks,
        )

        assert len(valid) == 1
        assert qc_result.passed_questions == 1
        assert qc_result.question_results[0].qc_pass is True
        assert qc_result.question_results[0].qc_stage_failed == QuizQcStage.NONE

    @pytest.mark.anyio
    async def test_selfcheck_llm_error_fails(
        self,
        sample_source_blocks: List[QuizCandidateBlock],
        valid_question: GeneratedQuizQuestion,
    ) -> None:
        """Self-check LLM 호출 실패 시 FAIL 처리 테스트."""
        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.side_effect = Exception("LLM Error")

        service = QuizQualityService(llm_client=mock_llm, selfcheck_enabled=True)
        valid, qc_result = await service.validate_quiz_set(
            questions=[valid_question],
            source_blocks=sample_source_blocks,
        )

        # 보수적 접근: LLM 에러 시 FAIL 처리
        assert len(valid) == 0
        assert qc_result.question_results[0].qc_stage_failed == QuizQcStage.SELF_CHECK
        assert qc_result.question_results[0].qc_reason_code == QuizQcReasonCode.OTHER


# =============================================================================
# 5. 모든 단계 통과 케이스
# =============================================================================


class TestAllStagesPass:
    """모든 QC 단계 통과 테스트."""

    @pytest.mark.anyio
    async def test_all_stages_pass(
        self,
        sample_source_blocks: List[QuizCandidateBlock],
        valid_question: GeneratedQuizQuestion,
    ) -> None:
        """모든 검증 단계 통과 테스트."""
        mock_llm_response = json.dumps({
            "verdict": "PASS",
            "reason_code": None,
            "reason_detail": None,
        })

        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.return_value = mock_llm_response

        service = QuizQualityService(llm_client=mock_llm, selfcheck_enabled=True)
        valid, qc_result = await service.validate_quiz_set(
            questions=[valid_question],
            source_blocks=sample_source_blocks,
        )

        assert len(valid) == 1
        assert qc_result.total_questions == 1
        assert qc_result.passed_questions == 1
        assert qc_result.failed_questions == 0

        qr = qc_result.question_results[0]
        assert qr.qc_pass is True
        assert qr.qc_stage_failed == QuizQcStage.NONE
        assert qr.qc_reason_code == QuizQcReasonCode.NONE
        assert qr.qc_reason_detail is None


# =============================================================================
# 6. 세트 요약 결과 검증
# =============================================================================


class TestSetSummary:
    """세트 요약 결과 테스트."""

    @pytest.mark.anyio
    async def test_mixed_pass_fail_summary(
        self,
        sample_source_blocks: List[QuizCandidateBlock],
    ) -> None:
        """PASS/FAIL이 섞인 경우 요약 테스트."""
        questions = [
            # 통과할 문항
            GeneratedQuizQuestion(
                question_id="Q-001",
                stem="USB 메모리를 사외로 반출할 때 필요한 조치는?",
                options=[
                    GeneratedQuizOption(option_id="OPT-1", text="정보보호팀 승인", is_correct=True),
                    GeneratedQuizOption(option_id="OPT-2", text="자유 반출", is_correct=False),
                ],
                source_block_ids=["BLOCK-001"],
            ),
            # SCHEMA 실패 - 정답 없음
            GeneratedQuizQuestion(
                question_id="Q-002",
                stem="실패할 문항",
                options=[
                    GeneratedQuizOption(option_id="OPT-1", text="오답1", is_correct=False),
                    GeneratedQuizOption(option_id="OPT-2", text="오답2", is_correct=False),
                ],
            ),
            # 통과할 문항
            GeneratedQuizQuestion(
                question_id="Q-003",
                stem="비밀번호 설정 시 최소 자릿수는?",
                options=[
                    GeneratedQuizOption(option_id="OPT-1", text="8자리 이상", is_correct=True),
                    GeneratedQuizOption(option_id="OPT-2", text="4자리", is_correct=False),
                ],
                source_block_ids=["BLOCK-003"],
            ),
        ]

        service = QuizQualityService(selfcheck_enabled=False)
        valid, qc_result = await service.validate_quiz_set(
            questions=questions,
            source_blocks=sample_source_blocks,
        )

        assert len(valid) == 2
        assert qc_result.total_questions == 3
        assert qc_result.passed_questions == 2
        assert qc_result.failed_questions == 1

        # 개별 결과 확인
        assert qc_result.question_results[0].qc_pass is True
        assert qc_result.question_results[1].qc_pass is False
        assert qc_result.question_results[2].qc_pass is True


# =============================================================================
# 7. QuizGenerateService 통합 테스트
# =============================================================================


class TestQuizGenerateServiceIntegration:
    """QuizGenerateService와 QC 통합 테스트."""

    @pytest.mark.anyio
    async def test_qc_integrated_in_generate_quiz(self) -> None:
        """generate_quiz에 QC가 통합되어 있는지 테스트."""
        from app.models.quiz_generate import QuizGenerateRequest
        from app.services.quiz_generate_service import QuizGenerateService

        # LLM mock - 정상 퀴즈 응답
        mock_llm_response = json.dumps({
            "questions": [
                {
                    "stem": "USB 메모리를 사외로 반출할 때 필요한 조치는?",
                    "options": [
                        {"text": "정보보호팀의 사전 승인", "is_correct": True},
                        {"text": "팀장 보고", "is_correct": False},
                        {"text": "자유 반출", "is_correct": False},
                        {"text": "사후 보고", "is_correct": False},
                    ],
                    "difficulty": "EASY",
                    "source_block_id": "BLOCK-001",
                }
            ]
        })

        # Self-check mock - PASS 응답
        selfcheck_response = json.dumps({
            "verdict": "PASS",
            "reason_code": None,
            "reason_detail": None,
        })

        mock_llm = AsyncMock()
        # 첫 번째 호출: 퀴즈 생성, 두 번째 호출: Self-check
        mock_llm.generate_chat_completion.side_effect = [
            mock_llm_response,
            selfcheck_response,
        ]

        service = QuizGenerateService(llm_client=mock_llm, qc_enabled=True)

        request = QuizGenerateRequest(
            education_id="EDU-001",
            doc_id="DOC-001",
            num_questions=1,
            quiz_candidate_blocks=[
                QuizCandidateBlock(
                    block_id="BLOCK-001",
                    text="USB 메모리를 사외로 반출할 때에는 정보보호팀의 사전 승인을 받아야 한다.",
                    tags=["USB", "반출"],
                )
            ],
        )

        response = await service.generate_quiz(request)

        assert response.generated_count == 1
        assert len(response.questions) == 1

        # QC 결과 확인
        qc_result = service.get_last_qc_result()
        assert qc_result is not None
        assert qc_result.passed_questions == 1

    @pytest.mark.anyio
    async def test_qc_filters_invalid_questions(self) -> None:
        """QC가 유효하지 않은 문항을 필터링하는지 테스트."""
        from app.models.quiz_generate import QuizGenerateRequest
        from app.services.quiz_generate_service import QuizGenerateService

        # LLM mock - 정답이 2개인 잘못된 퀴즈 응답
        mock_llm_response = json.dumps({
            "questions": [
                {
                    "stem": "정답이 2개인 문항",
                    "options": [
                        {"text": "정답1", "is_correct": True},
                        {"text": "정답2", "is_correct": True},  # 두 번째 정답
                        {"text": "오답", "is_correct": False},
                    ],
                    "difficulty": "EASY",
                }
            ]
        })

        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.return_value = mock_llm_response

        service = QuizGenerateService(llm_client=mock_llm, qc_enabled=True)

        request = QuizGenerateRequest(
            education_id="EDU-001",
            doc_id="DOC-001",
            num_questions=1,
            quiz_candidate_blocks=[
                QuizCandidateBlock(
                    block_id="BLOCK-001",
                    text="테스트 텍스트",
                    tags=[],
                )
            ],
        )

        response = await service.generate_quiz(request)

        # Phase 16 기본 검증에서 이미 필터링되므로 QC까지 안 감
        # 또는 QC에서 필터링됨
        assert response.generated_count == 0

    @pytest.mark.anyio
    async def test_qc_disabled(self) -> None:
        """QC 비활성화 테스트."""
        from app.models.quiz_generate import QuizGenerateRequest
        from app.services.quiz_generate_service import QuizGenerateService

        mock_llm_response = json.dumps({
            "questions": [
                {
                    "stem": "USB 메모리를 사외로 반출할 때 필요한 조치는?",
                    "options": [
                        {"text": "정보보호팀의 사전 승인", "is_correct": True},
                        {"text": "자유 반출", "is_correct": False},
                    ],
                    "difficulty": "EASY",
                }
            ]
        })

        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.return_value = mock_llm_response

        # QC 비활성화
        service = QuizGenerateService(llm_client=mock_llm, qc_enabled=False)

        request = QuizGenerateRequest(
            education_id="EDU-001",
            doc_id="DOC-001",
            num_questions=1,
            quiz_candidate_blocks=[
                QuizCandidateBlock(
                    block_id="BLOCK-001",
                    text="USB 메모리를 사외로 반출할 때에는 정보보호팀의 사전 승인을 받아야 한다.",
                    tags=[],
                )
            ],
        )

        response = await service.generate_quiz(request)

        assert response.generated_count == 1
        # QC 결과가 없어야 함
        assert service.get_last_qc_result() is None


# =============================================================================
# 8. 헬퍼 함수 테스트
# =============================================================================


class TestHelperFunctions:
    """헬퍼 함수 테스트."""

    def test_extract_keywords(self) -> None:
        """키워드 추출 테스트."""
        service = QuizQualityService(selfcheck_enabled=False)

        keywords = service._extract_keywords("정보보호팀의 사전 승인을 받아야 한다")

        # 한글 형태소 분석 없이 단어 단위로 추출되므로 조사가 붙어있을 수 있음
        # 적어도 일부 키워드가 추출되는지 확인
        assert len(keywords) > 0
        # "사전", "승인" 등이 포함되어야 함
        keyword_str = " ".join(keywords)
        assert "사전" in keyword_str or "승인" in keyword_str
        # 불용어는 제외
        assert "의" not in keywords
        assert "을" not in keywords

    def test_map_reason_code(self) -> None:
        """reason_code 매핑 테스트."""
        service = QuizQualityService(selfcheck_enabled=False)

        assert service._map_reason_code("MULTIPLE_CORRECT") == QuizQcReasonCode.MULTIPLE_CORRECT
        assert service._map_reason_code("NO_CORRECT_OPTION") == QuizQcReasonCode.NO_CORRECT_OPTION
        assert service._map_reason_code("NO_CORRECT") == QuizQcReasonCode.NO_CORRECT_OPTION
        assert service._map_reason_code("AMBIGUOUS") == QuizQcReasonCode.AMBIGUOUS_QUESTION
        assert service._map_reason_code("UNKNOWN") == QuizQcReasonCode.OTHER
        assert service._map_reason_code(None) == QuizQcReasonCode.OTHER

    def test_extract_json_from_response(self) -> None:
        """JSON 추출 테스트."""
        service = QuizQualityService(selfcheck_enabled=False)

        # 순수 JSON
        json1 = '{"verdict": "PASS"}'
        assert service._extract_json_from_response(json1) == json1

        # ```json 블록
        json2 = '```json\n{"verdict": "FAIL"}\n```'
        result2 = service._extract_json_from_response(json2)
        assert '"verdict": "FAIL"' in result2

        # 텍스트 + JSON
        json3 = '분석 결과입니다:\n{"verdict": "PASS", "reason_code": null}'
        result3 = service._extract_json_from_response(json3)
        assert '"verdict": "PASS"' in result3
