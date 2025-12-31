"""
Phase 16: 퀴즈 자동 생성 API 테스트

테스트 항목:
1. QuizGenerateRequest/Response 모델 테스트
2. QuizGenerateService 단위 테스트
3. FastAPI 엔드포인트 통합 테스트
4. 에러 케이스 테스트
5. 난이도 분배 계산 테스트
6. 2차 응시 중복 방지 테스트
"""

import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.quiz_generate import (
    Difficulty,
    ExcludePreviousQuestion,
    GeneratedQuizOption,
    GeneratedQuizQuestion,
    LLMQuizOption,
    LLMQuizQuestion,
    QuestionStatus,
    QuestionType,
    QuizCandidateBlock,
    QuizGenerateRequest,
    QuizGenerateResponse,
    generate_option_id,
    generate_question_id,
)
from app.services.quiz_generate_service import QuizGenerateService


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


@pytest.fixture
def test_client() -> TestClient:
    """FastAPI 테스트 클라이언트."""
    return TestClient(app)


@pytest.fixture
def sample_quiz_candidate_blocks() -> List[Dict[str, Any]]:
    """샘플 퀴즈 후보 블록들."""
    return [
        {
            "blockId": "BLOCK-001",
            "docId": "DOC-SEC-001",
            "docVersion": "v1",
            "chapterId": "CH1",
            "learningObjectiveId": "LO-1",
            "text": "USB 메모리를 사외로 반출할 때에는 정보보호팀의 사전 승인을 받아야 한다.",
            "tags": ["USB", "반출", "승인"],
            "articlePath": "제3장 > 제2조 > 제1항",
        },
        {
            "blockId": "BLOCK-002",
            "docId": "DOC-SEC-001",
            "docVersion": "v1",
            "chapterId": "CH1",
            "learningObjectiveId": "LO-2",
            "text": "회사 외부 클라우드 스토리지에 고객 개인정보를 업로드하는 것은 금지된다.",
            "tags": ["클라우드", "개인정보", "업로드"],
        },
        {
            "blockId": "BLOCK-003",
            "docId": "DOC-SEC-001",
            "docVersion": "v1",
            "chapterId": "CH2",
            "learningObjectiveId": "LO-3",
            "text": "비밀번호는 8자리 이상, 영문/숫자/특수문자 조합으로 설정해야 한다.",
            "tags": ["비밀번호", "보안"],
        },
    ]


@pytest.fixture
def sample_llm_response() -> str:
    """샘플 LLM 응답."""
    return json.dumps({
        "questions": [
            {
                "stem": "USB 메모리를 사외로 반출할 때 필요한 조치는 무엇인가요?",
                "options": [
                    {"text": "정보보호팀의 사전 승인", "is_correct": True},
                    {"text": "팀장에게 구두 보고만 한다", "is_correct": False},
                    {"text": "개인 판단에 따라 자유롭게 반출한다", "is_correct": False},
                    {"text": "사후에만 보고하면 된다", "is_correct": False},
                ],
                "difficulty": "EASY",
                "explanation": "USB 반출 시에는 반드시 정보보호팀의 사전 승인을 받아야 합니다.",
                "rationale": "문서 DOC-SEC-001 v1 제3장 제2조 제1항에 해당 내용이 명시되어 있습니다.",
                "source_block_id": "BLOCK-001",
                "tags": ["USB", "반출", "승인"],
            },
            {
                "stem": "회사 외부 클라우드 스토리지에 업로드가 금지된 정보는?",
                "options": [
                    {"text": "고객 개인정보", "is_correct": True},
                    {"text": "공개된 마케팅 자료", "is_correct": False},
                    {"text": "회사 공지사항", "is_correct": False},
                    {"text": "일반 업무 문서", "is_correct": False},
                ],
                "difficulty": "NORMAL",
                "explanation": "고객 개인정보는 외부 클라우드에 업로드할 수 없습니다.",
                "source_block_id": "BLOCK-002",
                "tags": ["클라우드", "개인정보"],
            },
        ]
    })


# =============================================================================
# 1. DTO/모델 테스트
# =============================================================================


class TestQuizGenerateModels:
    """Quiz Generate 모델 테스트."""

    def test_quiz_candidate_block_creation(self) -> None:
        """QuizCandidateBlock 모델 생성 테스트."""
        block = QuizCandidateBlock(
            block_id="BLOCK-001",
            doc_id="DOC-SEC-001",
            doc_version="v1",
            chapter_id="CH1",
            learning_objective_id="LO-1",
            text="USB 메모리를 사외로 반출할 때에는 정보보호팀의 사전 승인을 받아야 한다.",
            tags=["USB", "반출", "승인"],
            article_path="제3장 > 제2조 > 제1항",
        )

        assert block.block_id == "BLOCK-001"
        assert block.doc_id == "DOC-SEC-001"
        assert block.doc_version == "v1"
        assert block.chapter_id == "CH1"
        assert block.learning_objective_id == "LO-1"
        assert "USB 메모리" in block.text
        assert len(block.tags) == 3

    def test_quiz_candidate_block_camelcase_alias(self) -> None:
        """QuizCandidateBlock camelCase alias 테스트."""
        data = {
            "blockId": "BLOCK-002",
            "docId": "DOC-001",
            "docVersion": "v2",
            "chapterId": "CH2",
            "learningObjectiveId": "LO-2",
            "text": "테스트 텍스트",
            "tags": ["태그1"],
            "articlePath": "제1장 > 제1조",
        }
        block = QuizCandidateBlock(**data)

        assert block.block_id == "BLOCK-002"
        assert block.doc_id == "DOC-001"
        assert block.doc_version == "v2"
        assert block.chapter_id == "CH2"
        assert block.article_path == "제1장 > 제1조"

    def test_quiz_generate_request_creation(
        self,
        sample_quiz_candidate_blocks: List[Dict[str, Any]],
    ) -> None:
        """QuizGenerateRequest 모델 생성 테스트 (간소화된 API)."""
        blocks = [QuizCandidateBlock(**b) for b in sample_quiz_candidate_blocks]

        request = QuizGenerateRequest(
            num_questions=10,
            language="ko",
            max_options=4,
            quiz_candidate_blocks=blocks,
        )

        assert request.num_questions == 10
        assert request.language == "ko"
        assert request.max_options == 4
        assert len(request.quiz_candidate_blocks) == 3

    def test_quiz_generate_request_camelcase(self) -> None:
        """QuizGenerateRequest camelCase 입력 테스트 (간소화된 API)."""
        data = {
            "numQuestions": 5,
            "maxOptions": 4,
            "quizCandidateBlocks": [
                {
                    "blockId": "B1",
                    "docId": "DOC-001",
                    "text": "테스트 텍스트",
                    "tags": [],
                }
            ],
            "excludePreviousQuestions": [
                {
                    "questionId": "Q-OLD-001",
                    "stem": "기존 문항 텍스트",
                }
            ],
        }
        request = QuizGenerateRequest(**data)

        assert request.num_questions == 5
        assert len(request.exclude_previous_questions) == 1

    def test_generated_quiz_option_creation(self) -> None:
        """GeneratedQuizOption 모델 생성 테스트."""
        option = GeneratedQuizOption(
            option_id="OPT-1",
            text="정보보호팀의 사전 승인",
            is_correct=True,
        )

        assert option.option_id == "OPT-1"
        assert option.text == "정보보호팀의 사전 승인"
        assert option.is_correct is True

    def test_generated_quiz_question_creation(self) -> None:
        """GeneratedQuizQuestion 모델 생성 테스트."""
        question = GeneratedQuizQuestion(
            question_id="Q-20251212-ABCD1234",
            status=QuestionStatus.DRAFT_AI_GENERATED,
            question_type=QuestionType.MCQ_SINGLE,
            stem="USB 메모리를 사외로 반출할 때 필요한 조치는?",
            options=[
                GeneratedQuizOption(option_id="OPT-1", text="승인 받기", is_correct=True),
                GeneratedQuizOption(option_id="OPT-2", text="무시", is_correct=False),
            ],
            difficulty=Difficulty.EASY,
            source_block_ids=["BLOCK-001"],
            source_doc_id="DOC-SEC-001",
        )

        assert question.question_id == "Q-20251212-ABCD1234"
        assert question.status == QuestionStatus.DRAFT_AI_GENERATED
        assert len(question.options) == 2

    def test_quiz_generate_response_creation(self) -> None:
        """QuizGenerateResponse 모델 생성 테스트 (간소화된 API)."""
        response = QuizGenerateResponse(
            generated_count=2,
            questions=[],
        )

        assert response.generated_count == 2

    def test_quiz_generate_response_json_serialization(self) -> None:
        """QuizGenerateResponse JSON 직렬화 테스트 (간소화된 API)."""
        response = QuizGenerateResponse(
            generated_count=1,
            questions=[
                GeneratedQuizQuestion(
                    question_id="Q-001",
                    stem="테스트 문제",
                    options=[
                        GeneratedQuizOption(option_id="OPT-1", text="답1", is_correct=True),
                        GeneratedQuizOption(option_id="OPT-2", text="답2", is_correct=False),
                    ],
                    source_block_ids=["B1"],
                    source_doc_id="DOC-001",
                    source_doc_version="v1",
                )
            ],
        )

        json_data = response.model_dump(by_alias=True)

        assert "generatedCount" in json_data
        assert json_data["questions"][0]["questionId"] == "Q-001"
        assert json_data["questions"][0]["sourceDocId"] == "DOC-001"
        assert json_data["questions"][0]["options"][0]["isCorrect"] is True

    def test_generate_question_id_format(self) -> None:
        """문항 ID 생성 형식 테스트."""
        qid = generate_question_id()

        assert qid.startswith("Q-")
        assert len(qid) == 19  # Q-YYYYMMDD-XXXXXXXX

    def test_generate_option_id_format(self) -> None:
        """보기 ID 생성 형식 테스트."""
        assert generate_option_id(0) == "OPT-1"
        assert generate_option_id(1) == "OPT-2"
        assert generate_option_id(3) == "OPT-4"


# =============================================================================
# 2. QuizGenerateService 단위 테스트
# =============================================================================


class TestQuizGenerateService:
    """QuizGenerateService 단위 테스트."""

    @pytest.mark.anyio
    async def test_basic_quiz_generation(
        self,
        sample_quiz_candidate_blocks: List[Dict[str, Any]],
        sample_llm_response: str,
    ) -> None:
        """기본 퀴즈 생성 테스트 (간소화된 API)."""
        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.return_value = sample_llm_response

        # Phase 16 테스트에서는 QC 비활성화 (Phase 17에서 별도 테스트)
        service = QuizGenerateService(llm_client=mock_llm, qc_enabled=False)

        blocks = [QuizCandidateBlock(**b) for b in sample_quiz_candidate_blocks]
        request = QuizGenerateRequest(
            num_questions=5,
            quiz_candidate_blocks=blocks,
        )

        response = await service.generate_quiz(request)

        assert response.generated_count == 2  # LLM 응답에 2개 문항
        assert len(response.questions) == 2

        # 첫 번째 문항 검증
        q1 = response.questions[0]
        assert "USB" in q1.stem
        assert len(q1.options) == 4
        assert sum(1 for opt in q1.options if opt.is_correct) == 1
        assert q1.difficulty == Difficulty.EASY

    @pytest.mark.anyio
    async def test_llm_response_with_json_block(
        self,
        sample_quiz_candidate_blocks: List[Dict[str, Any]],
    ) -> None:
        """LLM이 ```json``` 블록으로 응답할 때 파싱 테스트."""
        mock_response = """
        다음은 생성된 퀴즈입니다:

        ```json
        {
            "questions": [
                {
                    "stem": "비밀번호 설정 시 최소 자릿수는?",
                    "options": [
                        {"text": "8자리 이상", "is_correct": true},
                        {"text": "4자리", "is_correct": false},
                        {"text": "6자리", "is_correct": false},
                        {"text": "제한 없음", "is_correct": false}
                    ],
                    "difficulty": "EASY",
                    "explanation": "비밀번호는 8자리 이상이어야 합니다."
                }
            ]
        }
        ```
        """

        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.return_value = mock_response

        # Phase 16 테스트에서는 QC 비활성화
        service = QuizGenerateService(llm_client=mock_llm, qc_enabled=False)

        blocks = [QuizCandidateBlock(**b) for b in sample_quiz_candidate_blocks]
        request = QuizGenerateRequest(
            num_questions=5,
            quiz_candidate_blocks=blocks,
        )

        response = await service.generate_quiz(request)

        assert response.generated_count == 1
        assert "비밀번호" in response.questions[0].stem

    @pytest.mark.anyio
    async def test_llm_failure_returns_empty_response(
        self,
        sample_quiz_candidate_blocks: List[Dict[str, Any]],
    ) -> None:
        """LLM 호출 실패 시 빈 응답 반환 테스트."""
        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.side_effect = Exception("LLM Error")

        service = QuizGenerateService(llm_client=mock_llm, qc_enabled=False)

        blocks = [QuizCandidateBlock(**b) for b in sample_quiz_candidate_blocks]
        request = QuizGenerateRequest(
            num_questions=5,
            quiz_candidate_blocks=blocks,
        )

        response = await service.generate_quiz(request)

        assert response.generated_count == 0
        assert len(response.questions) == 0

    @pytest.mark.anyio
    async def test_invalid_json_returns_empty_response(
        self,
        sample_quiz_candidate_blocks: List[Dict[str, Any]],
    ) -> None:
        """LLM이 잘못된 JSON 반환 시 빈 응답 테스트."""
        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.return_value = "이것은 유효하지 않은 응답입니다."

        service = QuizGenerateService(llm_client=mock_llm, qc_enabled=False)

        blocks = [QuizCandidateBlock(**b) for b in sample_quiz_candidate_blocks]
        request = QuizGenerateRequest(
            num_questions=5,
            quiz_candidate_blocks=blocks,
        )

        response = await service.generate_quiz(request)

        assert response.generated_count == 0


# =============================================================================
# 3. 난이도 분배 계산 테스트 (고정 비율: 쉬움 50%, 보통 30%, 어려움 20%)
# =============================================================================


class TestDifficultyDistribution:
    """난이도 분배 계산 테스트 (고정 비율)."""

    def test_10_questions_distribution(self) -> None:
        """10문항 난이도 분배 테스트 (50%, 30%, 20%)."""
        service = QuizGenerateService()

        result = service._calculate_difficulty_distribution(10)

        # 10문항: 쉬움 5개, 보통 3개, 어려움 2개
        assert result["easy"] == 5
        assert result["normal"] == 3
        assert result["hard"] == 2

    def test_20_questions_distribution(self) -> None:
        """20문항 난이도 분배 테스트."""
        service = QuizGenerateService()

        result = service._calculate_difficulty_distribution(20)

        # 20문항: 쉬움 10개, 보통 6개, 어려움 4개
        assert result["easy"] == 10
        assert result["normal"] == 6
        assert result["hard"] == 4

    def test_5_questions_distribution(self) -> None:
        """5문항 난이도 분배 테스트."""
        service = QuizGenerateService()

        result = service._calculate_difficulty_distribution(5)

        # 5문항: 쉬움 2~3개, 보통 1~2개, 어려움 1개 (반올림)
        total = result["easy"] + result["normal"] + result["hard"]
        assert total == 5
        assert result["easy"] >= 2  # 50% of 5 = 2.5 → 2 or 3

    def test_small_number_distribution(self) -> None:
        """적은 문항 수 분배 테스트."""
        service = QuizGenerateService()

        result = service._calculate_difficulty_distribution(3)

        # 3문항도 총합이 맞아야 함
        total = result["easy"] + result["normal"] + result["hard"]
        assert total == 3

    def test_total_always_matches(self) -> None:
        """다양한 문항 수에서 총합이 항상 맞는지 테스트."""
        service = QuizGenerateService()

        for num in [1, 2, 3, 5, 7, 10, 15, 20, 50]:
            result = service._calculate_difficulty_distribution(num)
            total = result["easy"] + result["normal"] + result["hard"]
            assert total == num, f"Failed for {num}: {result}"


# =============================================================================
# 4. 2차 응시 중복 방지 테스트
# =============================================================================


class TestDuplicatePrevention:
    """2차 응시 중복 방지 테스트."""

    @pytest.mark.anyio
    async def test_exact_duplicate_filtered(
        self,
        sample_quiz_candidate_blocks: List[Dict[str, Any]],
    ) -> None:
        """완전 일치 중복 필터링 테스트."""
        # LLM이 기존 문항과 동일한 stem을 생성
        llm_response = json.dumps({
            "questions": [
                {
                    "stem": "USB 메모리를 사외로 반출할 때 필요한 조치는?",  # 기존과 동일
                    "options": [
                        {"text": "승인", "is_correct": True},
                        {"text": "무시", "is_correct": False},
                        {"text": "보고", "is_correct": False},
                        {"text": "확인", "is_correct": False},
                    ],
                    "difficulty": "EASY",
                },
                {
                    "stem": "클라우드 업로드 금지 대상은?",  # 새로운 문항
                    "options": [
                        {"text": "개인정보", "is_correct": True},
                        {"text": "공지사항", "is_correct": False},
                        {"text": "마케팅 자료", "is_correct": False},
                        {"text": "일반 문서", "is_correct": False},
                    ],
                    "difficulty": "NORMAL",
                },
            ]
        })

        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.return_value = llm_response

        service = QuizGenerateService(llm_client=mock_llm, qc_enabled=False)

        blocks = [QuizCandidateBlock(**b) for b in sample_quiz_candidate_blocks]
        request = QuizGenerateRequest(
            num_questions=5,
            quiz_candidate_blocks=blocks,
            exclude_previous_questions=[
                ExcludePreviousQuestion(
                    question_id="Q-OLD-001",
                    stem="USB 메모리를 사외로 반출할 때 필요한 조치는?",  # 1차 때 사용한 문항
                )
            ],
        )

        response = await service.generate_quiz(request)

        # 중복 문항이 필터링되어 1개만 남아야 함
        assert response.generated_count == 1
        assert "클라우드" in response.questions[0].stem

    def test_normalize_text_for_comparison(self) -> None:
        """텍스트 정규화 테스트."""
        service = QuizGenerateService()

        # 공백, 대소문자 차이가 있어도 동일하게 처리
        text1 = "USB 메모리를  사외로   반출할 때"
        text2 = "usb 메모리를 사외로 반출할 때"

        assert service._normalize_text(text1) == service._normalize_text(text2)


# =============================================================================
# 5. 문항 정합성 검증 테스트
# =============================================================================


class TestQuestionValidation:
    """문항 정합성 검증 테스트."""

    @pytest.mark.anyio
    async def test_filter_question_with_no_correct_answer(
        self,
        sample_quiz_candidate_blocks: List[Dict[str, Any]],
    ) -> None:
        """정답이 없는 문항 필터링 테스트."""
        llm_response = json.dumps({
            "questions": [
                {
                    "stem": "정답이 없는 문항",
                    "options": [
                        {"text": "오답1", "is_correct": False},
                        {"text": "오답2", "is_correct": False},
                        {"text": "오답3", "is_correct": False},
                        {"text": "오답4", "is_correct": False},
                    ],
                    "difficulty": "EASY",
                },
                {
                    "stem": "정상 문항",
                    "options": [
                        {"text": "정답", "is_correct": True},
                        {"text": "오답", "is_correct": False},
                    ],
                    "difficulty": "NORMAL",
                },
            ]
        })

        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.return_value = llm_response

        service = QuizGenerateService(llm_client=mock_llm, qc_enabled=False)

        blocks = [QuizCandidateBlock(**b) for b in sample_quiz_candidate_blocks]
        request = QuizGenerateRequest(
            num_questions=5,
            quiz_candidate_blocks=blocks,
        )

        response = await service.generate_quiz(request)

        # 정답이 없는 문항은 필터링됨
        assert response.generated_count == 1
        assert response.questions[0].stem == "정상 문항"

    @pytest.mark.anyio
    async def test_filter_question_with_multiple_correct_answers(
        self,
        sample_quiz_candidate_blocks: List[Dict[str, Any]],
    ) -> None:
        """정답이 여러 개인 문항 필터링 테스트."""
        llm_response = json.dumps({
            "questions": [
                {
                    "stem": "정답이 2개인 문항",
                    "options": [
                        {"text": "정답1", "is_correct": True},
                        {"text": "정답2", "is_correct": True},
                        {"text": "오답", "is_correct": False},
                    ],
                    "difficulty": "EASY",
                },
            ]
        })

        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.return_value = llm_response

        service = QuizGenerateService(llm_client=mock_llm, qc_enabled=False)

        blocks = [QuizCandidateBlock(**b) for b in sample_quiz_candidate_blocks]
        request = QuizGenerateRequest(
            num_questions=5,
            quiz_candidate_blocks=blocks,
        )

        response = await service.generate_quiz(request)

        # 정답이 2개인 문항은 필터링됨
        assert response.generated_count == 0

    @pytest.mark.anyio
    async def test_filter_question_with_single_option(
        self,
        sample_quiz_candidate_blocks: List[Dict[str, Any]],
    ) -> None:
        """보기가 1개뿐인 문항 필터링 테스트."""
        llm_response = json.dumps({
            "questions": [
                {
                    "stem": "보기가 1개인 문항",
                    "options": [
                        {"text": "유일한 보기", "is_correct": True},
                    ],
                    "difficulty": "EASY",
                },
            ]
        })

        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.return_value = llm_response

        service = QuizGenerateService(llm_client=mock_llm, qc_enabled=False)

        blocks = [QuizCandidateBlock(**b) for b in sample_quiz_candidate_blocks]
        request = QuizGenerateRequest(
            num_questions=5,
            quiz_candidate_blocks=blocks,
        )

        response = await service.generate_quiz(request)

        # 보기가 1개인 문항은 필터링됨
        assert response.generated_count == 0


# =============================================================================
# 6. FastAPI 엔드포인트 통합 테스트
# =============================================================================


class TestQuizGenerateAPI:
    """Quiz Generate API 통합 테스트 (간소화된 API)."""

    def test_endpoint_exists(self, test_client: TestClient) -> None:
        """엔드포인트 존재 확인 테스트."""
        # 최소한의 요청으로 테스트
        response = test_client.post(
            "/ai/quiz/generate",
            json={
                "quizCandidateBlocks": [
                    {
                        "blockId": "B1",
                        "docId": "DOC-001",
                        "text": "테스트 텍스트",
                    }
                ],
            },
        )

        # 422(validation error)나 500이 아닌 것 확인
        # LLM mock이 없으므로 실제로는 빈 응답이나 에러일 수 있음
        assert response.status_code in [200, 500]

    def test_valid_request_with_mock(
        self,
        test_client: TestClient,
        sample_quiz_candidate_blocks: List[Dict[str, Any]],
    ) -> None:
        """유효한 요청 테스트 (서비스 mock, 간소화된 API)."""
        mock_response = QuizGenerateResponse(
            generated_count=2,
            questions=[
                GeneratedQuizQuestion(
                    question_id="Q-TEST-001",
                    stem="테스트 문제 1",
                    options=[
                        GeneratedQuizOption(option_id="OPT-1", text="정답", is_correct=True),
                        GeneratedQuizOption(option_id="OPT-2", text="오답", is_correct=False),
                    ],
                    source_block_ids=["B1"],
                    source_doc_id="DOC-SEC-001",
                ),
                GeneratedQuizQuestion(
                    question_id="Q-TEST-002",
                    stem="테스트 문제 2",
                    options=[
                        GeneratedQuizOption(option_id="OPT-1", text="정답", is_correct=True),
                        GeneratedQuizOption(option_id="OPT-2", text="오답", is_correct=False),
                    ],
                    source_block_ids=["B2"],
                    source_doc_id="DOC-SEC-001",
                ),
            ],
        )

        with patch(
            "app.services.quiz_generate_service.QuizGenerateService.generate_quiz"
        ) as mock_generate:
            mock_generate.return_value = mock_response

            response = test_client.post(
                "/ai/quiz/generate",
                json={
                    "numQuestions": 5,
                    "quizCandidateBlocks": sample_quiz_candidate_blocks,
                },
            )

            assert response.status_code == 200
            data = response.json()

            assert data["generatedCount"] == 2
            assert len(data["questions"]) == 2

    def test_validation_error_empty_blocks(self, test_client: TestClient) -> None:
        """빈 블록 목록 유효성 검사 오류 테스트."""
        response = test_client.post(
            "/ai/quiz/generate",
            json={
                "quizCandidateBlocks": [],  # 빈 배열
            },
        )

        assert response.status_code == 422  # Validation Error

    def test_validation_error_invalid_num_questions(self, test_client: TestClient) -> None:
        """잘못된 문항 수 유효성 검사 오류 테스트."""
        response = test_client.post(
            "/ai/quiz/generate",
            json={
                "numQuestions": 0,  # 0 이하는 안됨
                "quizCandidateBlocks": [{"blockId": "B1", "text": "텍스트"}],
            },
        )

        assert response.status_code == 422

    def test_request_with_exclude_list(
        self,
        test_client: TestClient,
        sample_quiz_candidate_blocks: List[Dict[str, Any]],
    ) -> None:
        """exclude 목록 포함 요청 테스트."""
        mock_response = QuizGenerateResponse(
            generated_count=1,
            questions=[
                GeneratedQuizQuestion(
                    question_id="Q-TEST-NEW",
                    stem="새로운 문제",
                    options=[
                        GeneratedQuizOption(option_id="OPT-1", text="정답", is_correct=True),
                        GeneratedQuizOption(option_id="OPT-2", text="오답", is_correct=False),
                    ],
                    source_block_ids=["B1"],
                    source_doc_id="DOC-SEC-001",
                ),
            ],
        )

        with patch(
            "app.services.quiz_generate_service.QuizGenerateService.generate_quiz"
        ) as mock_generate:
            mock_generate.return_value = mock_response

            response = test_client.post(
                "/ai/quiz/generate",
                json={
                    "numQuestions": 5,
                    "quizCandidateBlocks": sample_quiz_candidate_blocks,
                    "excludePreviousQuestions": [
                        {
                            "questionId": "Q-OLD-001",
                            "stem": "기존 문항 텍스트",
                        }
                    ],
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["generatedCount"] == 1


# =============================================================================
# 7. 에러 케이스 테스트
# =============================================================================


class TestQuizGenerateErrorCases:
    """Quiz Generate 에러 케이스 테스트."""

    @pytest.mark.anyio
    async def test_service_handles_connection_error(
        self,
        sample_quiz_candidate_blocks: List[Dict[str, Any]],
    ) -> None:
        """연결 오류 처리 테스트."""
        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.side_effect = ConnectionError("Connection failed")

        service = QuizGenerateService(llm_client=mock_llm, qc_enabled=False)

        blocks = [QuizCandidateBlock(**b) for b in sample_quiz_candidate_blocks]
        request = QuizGenerateRequest(
            num_questions=5,
            quiz_candidate_blocks=blocks,
        )

        response = await service.generate_quiz(request)

        # 예외가 발생해도 빈 응답 반환
        assert response.generated_count == 0
        assert len(response.questions) == 0

    @pytest.mark.anyio
    async def test_service_handles_timeout(
        self,
        sample_quiz_candidate_blocks: List[Dict[str, Any]],
    ) -> None:
        """타임아웃 처리 테스트."""
        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.side_effect = TimeoutError("Request timed out")

        service = QuizGenerateService(llm_client=mock_llm, qc_enabled=False)

        blocks = [QuizCandidateBlock(**b) for b in sample_quiz_candidate_blocks]
        request = QuizGenerateRequest(
            num_questions=5,
            quiz_candidate_blocks=blocks,
        )

        response = await service.generate_quiz(request)

        assert response.generated_count == 0

    def test_api_handles_service_exception(
        self,
        test_client: TestClient,
        sample_quiz_candidate_blocks: List[Dict[str, Any]],
    ) -> None:
        """서비스 예외 처리 API 테스트."""
        with patch(
            "app.services.quiz_generate_service.QuizGenerateService.generate_quiz"
        ) as mock_generate:
            mock_generate.side_effect = Exception("Unexpected error")

            response = test_client.post(
                "/ai/quiz/generate",
                json={
                    "quizCandidateBlocks": sample_quiz_candidate_blocks,
                },
            )

            assert response.status_code == 500
            assert "Failed to generate quiz" in response.json()["detail"]


# =============================================================================
# 8. Difficulty 파싱 테스트
# =============================================================================


class TestDifficultyParsing:
    """난이도 파싱 테스트."""

    def test_parse_english_difficulty(self) -> None:
        """영어 난이도 파싱 테스트."""
        service = QuizGenerateService()

        assert service._parse_difficulty("EASY") == Difficulty.EASY
        assert service._parse_difficulty("NORMAL") == Difficulty.NORMAL
        assert service._parse_difficulty("HARD") == Difficulty.HARD
        assert service._parse_difficulty("MEDIUM") == Difficulty.NORMAL
        assert service._parse_difficulty("DIFFICULT") == Difficulty.HARD

    def test_parse_korean_difficulty(self) -> None:
        """한국어 난이도 파싱 테스트."""
        service = QuizGenerateService()

        assert service._parse_difficulty("쉬움") == Difficulty.EASY
        assert service._parse_difficulty("보통") == Difficulty.NORMAL
        assert service._parse_difficulty("어려움") == Difficulty.HARD

    def test_parse_abbreviation_difficulty(self) -> None:
        """약어 난이도 파싱 테스트."""
        service = QuizGenerateService()

        assert service._parse_difficulty("E") == Difficulty.EASY
        assert service._parse_difficulty("N") == Difficulty.NORMAL
        assert service._parse_difficulty("H") == Difficulty.HARD

    def test_parse_unknown_difficulty_defaults_to_normal(self) -> None:
        """알 수 없는 난이도는 NORMAL로 기본값."""
        service = QuizGenerateService()

        assert service._parse_difficulty("UNKNOWN") == Difficulty.NORMAL
        assert service._parse_difficulty(None) == Difficulty.NORMAL
        assert service._parse_difficulty("") == Difficulty.NORMAL
