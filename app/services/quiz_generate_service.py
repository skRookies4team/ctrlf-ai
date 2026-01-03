"""
Quiz Generate Service (Phase 16 + Phase 17 QC 통합)

교육/사규 문서의 QUIZ_CANDIDATE 블록들을 입력받아
LLM을 통해 객관식 퀴즈를 자동 생성하는 서비스.

주요 기능:
- 난이도 분배 계산
- LLM 프롬프트 구성 및 호출
- 응답 파싱 및 정합성 검증
- 2차 응시 시 중복 문항 방지
- [Phase 17] QC 파이프라인 통합 (SCHEMA/SOURCE/SELF_CHECK)
"""

import json
import re
from typing import Dict, List, Optional, Tuple

from app.clients.llm_client import LLMClient
from app.core.logging import get_logger
from app.models.quiz_generate import (
    Difficulty,
    ExcludePreviousQuestion,
    GeneratedQuizOption,
    GeneratedQuizQuestion,
    LLMQuizQuestion,
    LLMQuizResponse,
    QuestionStatus,
    QuestionType,
    QuizCandidateBlock,
    QuizGenerateRequest,
    QuizGenerateResponse,
    generate_option_id,
    generate_question_id,
)

# =============================================================================
# 난이도 분배 상수 (고정 비율)
# =============================================================================

# 난이도 분배: 쉬움 50%, 보통 30%, 어려움 20%
DIFFICULTY_RATIO = {
    "easy": 0.5,
    "normal": 0.3,
    "hard": 0.2,
}
from app.models.quiz_qc import QuizSetQcResult

logger = get_logger(__name__)


# =============================================================================
# LLM 프롬프트 템플릿
# =============================================================================

SYSTEM_PROMPT = """당신은 기업 정보보안/개인정보/사규 교육용 객관식 퀴즈를 설계하는 전문가입니다.

아래 교육 텍스트 블록을 참고하여 객관식 문제를 생성하세요.

중요 원칙:
1. 정책을 새로 만들거나 왜곡하지 말고, 문서에 명시된 사실만 사용하세요.
2. 각 문항은 오직 1개의 정답만 가져야 합니다.
3. 오답 보기는 그럴듯하지만 틀린 내용이어야 합니다.
4. 문제는 명확하고 이해하기 쉬워야 합니다.
5. 반드시 지정된 JSON 포맷으로만 응답하세요.

응답 JSON 형식:
{
  "questions": [
    {
      "stem": "문제 텍스트",
      "options": [
        {"text": "보기1 텍스트", "is_correct": true},
        {"text": "보기2 텍스트", "is_correct": false},
        {"text": "보기3 텍스트", "is_correct": false},
        {"text": "보기4 텍스트", "is_correct": false}
      ],
      "difficulty": "EASY",
      "explanation": "정답 해설",
      "rationale": "출처 근거 설명",
      "source_block_id": "BLOCK-001",
      "tags": ["태그1", "태그2"]
    }
  ]
}

난이도 기준:
- EASY: 문서에서 직접 찾을 수 있는 기본 사실 확인
- NORMAL: 여러 사실을 조합하거나 적용해야 하는 문제
- HARD: 상황 판단, 예외 케이스, 복합적 이해가 필요한 문제
"""

USER_PROMPT_TEMPLATE = """다음 교육/사규 문서 블록들을 참고하여 객관식 퀴즈를 생성해 주세요.

## 요청 정보
- 언어: {language}
- 생성할 문항 수: {num_questions}개
- 난이도 분배: 쉬움 {easy}개, 보통 {normal}개, 어려움 {hard}개
- 보기 개수: {max_options}개

## 교육/사규 텍스트 블록
{blocks_text}

{exclude_instruction}

위 텍스트 블록을 참고하여 {num_questions}개의 객관식 문항을 JSON 형식으로 생성해 주세요.
각 문항에는 정확히 {max_options}개의 보기를 포함하고, 정답은 반드시 1개만 있어야 합니다.
"""

EXCLUDE_INSTRUCTION_TEMPLATE = """## 중복 방지 (2차 응시)
아래 나열된 기존 문항과 의미상 동일하거나 매우 비슷한 문항은 만들지 마세요:
{previous_stems}
"""


class QuizGenerateService:
    """
    퀴즈 자동 생성 서비스.

    QUIZ_CANDIDATE 블록들을 LLM에게 전달하여
    객관식 퀴즈 문항 세트를 생성합니다.

    Phase 17에서 QC 파이프라인이 통합되어,
    생성된 문항에 대해 SCHEMA/SOURCE/SELF_CHECK 검증을 수행합니다.

    Attributes:
        _llm: LLM 클라이언트
        _qc_enabled: QC 파이프라인 활성화 여부
        _qc_service: QuizQualityService 인스턴스 (lazy init)

    Example:
        service = QuizGenerateService()
        response = await service.generate_quiz(request)
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        qc_enabled: bool = True,
    ) -> None:
        """
        QuizGenerateService 초기화.

        Args:
            llm_client: LLM 클라이언트. None이면 새로 생성.
            qc_enabled: QC 파이프라인 활성화 여부. 기본 True.
        """
        self._llm = llm_client or LLMClient()
        self._qc_enabled = qc_enabled
        self._qc_service = None  # Lazy initialization
        self._last_qc_result: Optional[QuizSetQcResult] = None  # 마지막 QC 결과 저장

    async def generate_quiz(
        self,
        request: QuizGenerateRequest,
    ) -> QuizGenerateResponse:
        """
        퀴즈를 자동 생성합니다.

        Args:
            request: 퀴즈 생성 요청

        Returns:
            QuizGenerateResponse: 생성된 퀴즈 문항들
        """
        logger.info(
            f"Generating quiz: num_questions={request.num_questions}, "
            f"blocks_count={len(request.quiz_candidate_blocks)}"
        )

        # 1. 난이도 분배 계산 (고정 비율: 쉬움 50%, 보통 30%, 어려움 20%)
        difficulty_counts = self._calculate_difficulty_distribution(request.num_questions)
        logger.debug(f"Difficulty distribution: {difficulty_counts}")

        # 2. LLM 메시지 구성
        messages = self._build_llm_messages(request, difficulty_counts)

        # 3. LLM 호출
        try:
            llm_response = await self._llm.generate_chat_completion(
                messages=messages,
                model=None,  # 기본 모델 사용
                temperature=0.7,  # 다양한 문항 생성을 위해
                max_tokens=4096,  # 충분한 토큰
            )

            logger.debug(f"LLM response length: {len(llm_response)}")

            # 4. 응답 파싱
            parsed_questions = self._parse_llm_response(llm_response)

            # 5. 정합성 검증 및 필터링
            valid_questions = self._validate_and_filter_questions(
                parsed_questions,
                request.max_options,
            )

            # 6. 중복 제거 (2차 응시)
            if request.exclude_previous_questions:
                valid_questions = self._filter_duplicate_questions(
                    valid_questions,
                    request.exclude_previous_questions,
                )

            # 7. 결과 조립
            final_questions = self._assemble_questions(
                valid_questions,
                request,
            )

            # 8. [Phase 17] QC 파이프라인 적용
            if self._qc_enabled and final_questions:
                final_questions, qc_result = await self._apply_qc_pipeline(
                    questions=final_questions,
                    source_blocks=request.quiz_candidate_blocks,
                )
                self._last_qc_result = qc_result
                logger.info(
                    f"QC result: {qc_result.passed_questions}/{qc_result.total_questions} passed"
                )

            # Note: 문항이 0개여도 빈 리스트 반환 (정책 A)
            # 백엔드/프론트에서 "이번에는 문제가 생성되지 않았다" 처리

            return QuizGenerateResponse(
                generated_count=len(final_questions),
                questions=final_questions,
            )

        except Exception as e:
            logger.exception(f"Failed to generate quiz: {e}")
            # 실패 시 빈 응답 반환
            return QuizGenerateResponse(
                generated_count=0,
                questions=[],
            )

    def _calculate_difficulty_distribution(
        self,
        num_questions: int,
    ) -> Dict[str, int]:
        """
        난이도별 문항 수를 계산합니다.

        고정 비율: 쉬움 50%, 보통 30%, 어려움 20%

        Args:
            num_questions: 총 문항 수

        Returns:
            난이도별 문항 수 딕셔너리
        """
        easy = round(num_questions * DIFFICULTY_RATIO["easy"])
        normal = round(num_questions * DIFFICULTY_RATIO["normal"])
        hard = num_questions - easy - normal  # 나머지는 hard에 할당 (반올림 오차 보정)

        return {
            "easy": max(0, easy),
            "normal": max(0, normal),
            "hard": max(0, hard),
        }

    def _build_llm_messages(
        self,
        request: QuizGenerateRequest,
        difficulty_counts: Dict[str, int],
    ) -> List[dict]:
        """
        LLM 호출용 메시지를 구성합니다.

        Args:
            request: 요청 정보
            difficulty_counts: 난이도별 문항 수

        Returns:
            LLM 메시지 목록
        """
        # 블록 텍스트 포맷
        blocks_text = self._format_blocks_for_prompt(request.quiz_candidate_blocks)

        # 2차 응시 중복 방지 지시
        exclude_instruction = ""
        if request.exclude_previous_questions:
            previous_stems = "\n".join(
                f"- {q.stem}" for q in request.exclude_previous_questions
            )
            exclude_instruction = EXCLUDE_INSTRUCTION_TEMPLATE.format(
                previous_stems=previous_stems
            )

        # User 메시지 생성
        user_message = USER_PROMPT_TEMPLATE.format(
            language=request.language,
            num_questions=request.num_questions,
            easy=difficulty_counts["easy"],
            normal=difficulty_counts["normal"],
            hard=difficulty_counts["hard"],
            max_options=request.max_options,
            blocks_text=blocks_text,
            exclude_instruction=exclude_instruction,
        )

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

    def _format_blocks_for_prompt(
        self,
        blocks: List[QuizCandidateBlock],
    ) -> str:
        """
        퀴즈 후보 블록들을 LLM 프롬프트용 텍스트로 포맷합니다.

        Args:
            blocks: 퀴즈 후보 블록 목록

        Returns:
            포맷된 텍스트
        """
        lines = []
        for i, block in enumerate(blocks, start=1):
            tags_str = ", ".join(block.tags) if block.tags else "없음"
            chapter_info = f"챕터: {block.chapter_id}" if block.chapter_id else ""
            lo_info = f"학습목표: {block.learning_objective_id}" if block.learning_objective_id else ""
            article_info = f"조항: {block.article_path}" if block.article_path else ""

            meta_parts = [p for p in [chapter_info, lo_info, article_info] if p]
            meta_str = " | ".join(meta_parts) if meta_parts else "메타정보 없음"

            lines.append(
                f"### 블록 {i} (ID: {block.block_id})\n"
                f"- 메타: {meta_str}\n"
                f"- 태그: {tags_str}\n"
                f"- 내용: {block.text}\n"
            )

        return "\n".join(lines)

    def _parse_llm_response(
        self,
        llm_response: str,
    ) -> List[LLMQuizQuestion]:
        """
        LLM 응답을 파싱하여 퀴즈 문항 목록을 반환합니다.

        Args:
            llm_response: LLM 응답 텍스트

        Returns:
            파싱된 퀴즈 문항 목록
        """
        try:
            # JSON 추출
            json_str = self._extract_json_from_response(llm_response)

            if json_str:
                data = json.loads(json_str)

                # LLMQuizResponse로 파싱
                if isinstance(data, dict) and "questions" in data:
                    llm_result = LLMQuizResponse(**data)
                    return llm_result.questions
                elif isinstance(data, list):
                    # questions 키 없이 배열만 반환한 경우
                    return [LLMQuizQuestion(**q) for q in data]

        except json.JSONDecodeError as e:
            logger.warning(f"JSON parsing failed: {e}")
        except Exception as e:
            logger.warning(f"Response parsing failed: {e}")

        return []

    def _extract_json_from_response(self, response: str) -> Optional[str]:
        """
        LLM 응답에서 JSON 부분을 추출합니다.

        Args:
            response: LLM 응답 텍스트

        Returns:
            JSON 문자열 또는 None
        """
        response = response.strip()

        # 이미 JSON인 경우
        if response.startswith("{") or response.startswith("["):
            # 끝까지 JSON인지 확인
            if response.endswith("}") or response.endswith("]"):
                return response

        # ```json ... ``` 블록 추출
        json_block_pattern = r"```(?:json)?\s*([\{\[].*?[\}\]])\s*```"
        match = re.search(json_block_pattern, response, re.DOTALL)
        if match:
            return match.group(1)

        # { ... } 또는 [ ... ] 패턴 추출
        # 가장 바깥쪽 중괄호/대괄호 찾기
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            start_idx = response.find(start_char)
            if start_idx != -1:
                depth = 0
                for i, char in enumerate(response[start_idx:], start=start_idx):
                    if char == start_char:
                        depth += 1
                    elif char == end_char:
                        depth -= 1
                        if depth == 0:
                            return response[start_idx:i + 1]

        return None

    def _validate_and_filter_questions(
        self,
        questions: List[LLMQuizQuestion],
        max_options: int,
    ) -> List[LLMQuizQuestion]:
        """
        퀴즈 문항의 정합성을 검증하고 유효한 문항만 필터링합니다.

        Args:
            questions: 파싱된 퀴즈 문항 목록
            max_options: 최대 보기 개수

        Returns:
            유효한 퀴즈 문항 목록

        Note:
            Phase 16에서는 기본 구조 검증만 수행합니다.
            LLM Self-check, RAG 재검증 등 고급 QC는 Phase 17에서 구현 예정.
        """
        valid_questions = []

        for i, q in enumerate(questions):
            # 옵션 수 검사
            if len(q.options) < 2:
                logger.warning(
                    f"Question {i + 1} has less than 2 options, skipping"
                )
                continue

            # 정답 개수 검사
            correct_count = sum(1 for opt in q.options if opt.is_correct)
            if correct_count != 1:
                logger.warning(
                    f"Question {i + 1} has {correct_count} correct answers "
                    f"(expected 1), skipping"
                )
                continue

            # stem 비어있는지 검사
            if not q.stem or not q.stem.strip():
                logger.warning(f"Question {i + 1} has empty stem, skipping")
                continue

            valid_questions.append(q)

        logger.info(
            f"Validated {len(valid_questions)}/{len(questions)} questions"
        )
        return valid_questions

    def _filter_duplicate_questions(
        self,
        questions: List[LLMQuizQuestion],
        exclude_list: List[ExcludePreviousQuestion],
    ) -> List[LLMQuizQuestion]:
        """
        2차 응시 시 기존 문항과 중복되는 문항을 필터링합니다.

        Args:
            questions: 생성된 퀴즈 문항 목록
            exclude_list: 제외할 기존 문항 목록

        Returns:
            중복 제거된 퀴즈 문항 목록

        Note:
            Phase 16에서는 완전 일치 기반 중복 제거만 수행합니다.
            문장 유사도(embedding) 기반 중복 제거는 Phase 17 이후 구현 예정.
        """
        # 제외할 stem 목록 (정규화)
        exclude_stems = {
            self._normalize_text(q.stem) for q in exclude_list
        }

        filtered = []
        for q in questions:
            normalized_stem = self._normalize_text(q.stem)
            if normalized_stem in exclude_stems:
                logger.info(f"Filtering duplicate question: stem_len={len(q.stem)}")
            else:
                filtered.append(q)

        if len(filtered) < len(questions):
            logger.info(
                f"Filtered {len(questions) - len(filtered)} duplicate questions"
            )

        return filtered

    def _normalize_text(self, text: str) -> str:
        """
        텍스트를 정규화합니다 (비교용).

        Args:
            text: 원본 텍스트

        Returns:
            정규화된 텍스트
        """
        # 공백 정규화, 소문자 변환
        return " ".join(text.lower().split())

    def _assemble_questions(
        self,
        llm_questions: List[LLMQuizQuestion],
        request: QuizGenerateRequest,
    ) -> List[GeneratedQuizQuestion]:
        """
        LLM 응답을 최종 응답 DTO로 조립합니다.

        Args:
            llm_questions: 파싱된 LLM 응답
            request: 원본 요청

        Returns:
            GeneratedQuizQuestion 목록
        """
        # 블록 ID → 블록 매핑
        block_map = {b.block_id: b for b in request.quiz_candidate_blocks}

        questions = []
        for llm_q in llm_questions:
            # ID 생성
            question_id = generate_question_id()

            # 옵션 조립
            options = []
            for j, opt in enumerate(llm_q.options):
                options.append(
                    GeneratedQuizOption(
                        option_id=generate_option_id(j),
                        text=opt.text,
                        is_correct=opt.is_correct,
                    )
                )

            # 난이도 파싱
            difficulty = self._parse_difficulty(llm_q.difficulty)

            # 소스 블록 정보 조회
            source_block = block_map.get(llm_q.source_block_id) if llm_q.source_block_id else None

            # 문항 조립 (출처 정보는 블록에서 가져옴)
            question = GeneratedQuizQuestion(
                question_id=question_id,
                status=QuestionStatus.DRAFT_AI_GENERATED,
                question_type=QuestionType.MCQ_SINGLE,
                stem=llm_q.stem,
                options=options,
                difficulty=difficulty,
                learning_objective_id=source_block.learning_objective_id if source_block else None,
                chapter_id=source_block.chapter_id if source_block else None,
                source_block_ids=[llm_q.source_block_id] if llm_q.source_block_id else [],
                source_doc_id=source_block.doc_id if source_block else None,
                source_doc_version=source_block.doc_version if source_block else None,
                source_article_path=source_block.article_path if source_block else None,
                tags=llm_q.tags or (source_block.tags if source_block else []),
                explanation=llm_q.explanation,
                rationale=llm_q.rationale,
            )
            questions.append(question)

        return questions

    def _parse_difficulty(self, difficulty: Optional[str]) -> Difficulty:
        """
        난이도 문자열을 Enum으로 파싱합니다.

        Args:
            difficulty: LLM이 반환한 난이도

        Returns:
            Difficulty Enum
        """
        if not difficulty:
            return Difficulty.NORMAL

        difficulty_upper = difficulty.upper().strip()

        if difficulty_upper in ("EASY", "쉬움", "E"):
            return Difficulty.EASY
        elif difficulty_upper in ("NORMAL", "보통", "N", "MEDIUM"):
            return Difficulty.NORMAL
        elif difficulty_upper in ("HARD", "어려움", "H", "DIFFICULT"):
            return Difficulty.HARD

        return Difficulty.NORMAL

    # =========================================================================
    # Phase 17: QC 파이프라인 통합
    # =========================================================================

    async def _apply_qc_pipeline(
        self,
        questions: List[GeneratedQuizQuestion],
        source_blocks: List[QuizCandidateBlock],
    ) -> Tuple[List[GeneratedQuizQuestion], QuizSetQcResult]:
        """
        QC 파이프라인을 적용합니다.

        검증 단계:
        1. SCHEMA: 스키마/구조 검증
        2. SOURCE: 원문 일치 검증
        3. SELF_CHECK: LLM Self-check

        Args:
            questions: 검증할 퀴즈 문항 목록
            source_blocks: 출처 퀴즈 후보 블록 목록

        Returns:
            Tuple[List[GeneratedQuizQuestion], QuizSetQcResult]:
                - QC 통과한 문항 목록
                - QC 결과 요약
        """
        # Lazy initialization of QC service
        if self._qc_service is None:
            from app.services.quiz_quality_service import QuizQualityService
            self._qc_service = QuizQualityService(llm_client=self._llm)

        return await self._qc_service.validate_quiz_set(
            questions=questions,
            source_blocks=source_blocks,
        )

    def get_last_qc_result(self) -> Optional[QuizSetQcResult]:
        """
        마지막 퀴즈 생성 시 QC 결과를 반환합니다.

        AI 로그 저장이나 분석용으로 활용할 수 있습니다.

        Returns:
            마지막 QC 결과 또는 None
        """
        return self._last_qc_result
