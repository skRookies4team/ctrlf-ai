"""
Quiz Quality Service (Phase 17)

LLM이 생성한 퀴즈 문항을 여러 단계로 검증하는 QC 파이프라인.

검증 단계:
1. SCHEMA: 스키마/구조 검증 (필수 필드, 옵션 수, 정답 개수)
2. SOURCE: 원문 일치 검증 (정답이 출처 블록과 일치하는지)
3. SELF_CHECK: LLM Self-check (복수 정답, 모호성 등)

사용법:
    service = QuizQualityService()
    valid_questions, qc_result = await service.validate_quiz_set(
        questions=generated_questions,
        source_blocks=quiz_candidate_blocks,
    )
"""

import json
import re
from typing import Dict, List, Optional, Tuple

from app.clients.llm_client import LLMClient
from app.core.logging import get_logger
from app.models.quiz_generate import (
    GeneratedQuizQuestion,
    QuizCandidateBlock,
)
from app.models.quiz_qc import (
    LLMSelfCheckResponse,
    QuizQcReasonCode,
    QuizQcStage,
    QuizQuestionQcResult,
    QuizSetQcResult,
)

logger = get_logger(__name__)


# =============================================================================
# LLM Self-check 프롬프트 템플릿
# =============================================================================

SELF_CHECK_SYSTEM_PROMPT = """당신은 기업 교육/사규 퀴즈의 품질을 검증하는 전문 검수자입니다.

주어진 문서 텍스트(source)와 퀴즈 문항(문제 + 보기)을 검토하여,
해당 문항이 품질 기준을 충족하는지 판단해 주세요.

품질 기준:
1. 정답이 정확히 1개여야 합니다 (복수 정답 불가)
2. 정답이 문서 내용과 일치해야 합니다
3. 오답 보기들이 명백히 틀린 내용이어야 합니다
4. 질문이 명확하고 모호하지 않아야 합니다
5. 문서만으로 답을 판단할 수 있어야 합니다

반드시 아래 JSON 형식으로만 응답하세요:
{
  "verdict": "PASS" 또는 "FAIL",
  "reason_code": "실패 시 사유 코드",
  "reason_detail": "상세 설명"
}

사유 코드 종류:
- MULTIPLE_CORRECT: 보기 중 정답이 될 수 있는 선택지가 2개 이상
- NO_CORRECT_OPTION: 모든 보기가 틀림 (정답 없음)
- SOURCE_MISMATCH: 정답이 문서 내용과 맞지 않음
- AMBIGUOUS_QUESTION: 질문이 모호하거나 답변 불가
- LOW_QUALITY_TEXT: 질문/보기가 너무 짧거나 의미 불명
- OTHER: 기타 품질 문제
"""

SELF_CHECK_USER_PROMPT_TEMPLATE = """## 문서 텍스트 (Source)
{source_text}

## 퀴즈 문항
**문제:** {stem}

**보기:**
{options_text}

**지정된 정답:** {correct_option}

위 문항을 검토하여 품질 기준을 충족하는지 JSON 형식으로 판단해 주세요.
"""


class QuizQualityService:
    """
    퀴즈 품질 검증(QC) 서비스.

    LLM이 생성한 퀴즈 문항 리스트를 입력받아:
    - 여러 단계의 검증을 수행하고
    - 통과한 문항만 반환하며
    - 검증 결과 메타를 로그용으로 제공합니다.

    Attributes:
        _llm: LLM 클라이언트 (Self-check용)
        _selfcheck_enabled: Self-check 활성화 여부

    Example:
        service = QuizQualityService()
        valid_questions, qc_result = await service.validate_quiz_set(
            questions=generated_questions,
            source_blocks=quiz_candidate_blocks,
        )
    """

    # Self-check 프롬프트 버전 (튜닝 시 버전 업)
    SELFCHECK_PROMPT_VERSION = "v1"

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        selfcheck_enabled: bool = True,
    ) -> None:
        """
        QuizQualityService 초기화.

        Args:
            llm_client: LLM 클라이언트. None이면 새로 생성.
            selfcheck_enabled: LLM Self-check 활성화 여부 (비용 고려)
        """
        self._llm = llm_client or LLMClient()
        self._selfcheck_enabled = selfcheck_enabled

    async def validate_quiz_set(
        self,
        questions: List[GeneratedQuizQuestion],
        source_blocks: List[QuizCandidateBlock],
    ) -> Tuple[List[GeneratedQuizQuestion], QuizSetQcResult]:
        """
        퀴즈 세트를 검증합니다.

        Args:
            questions: LLM이 생성한 퀴즈 문항 리스트
            source_blocks: 출처가 되는 퀴즈 후보 블록 리스트

        Returns:
            Tuple[List[GeneratedQuizQuestion], QuizSetQcResult]:
                - QC를 통과한 문항 리스트
                - 문항별 QC 결과 요약
        """
        logger.info(f"Starting QC validation for {len(questions)} questions")

        # 블록 ID → 블록 매핑
        block_map = {b.block_id: b for b in source_blocks}

        valid_questions: List[GeneratedQuizQuestion] = []
        question_results: List[QuizQuestionQcResult] = []

        for question in questions:
            qc_result = await self._validate_single_question(
                question=question,
                block_map=block_map,
            )
            question_results.append(qc_result)

            if qc_result.qc_pass:
                valid_questions.append(question)

        # 세트 요약 생성
        passed_count = len(valid_questions)
        failed_count = len(questions) - passed_count

        qc_summary = QuizSetQcResult(
            total_questions=len(questions),
            passed_questions=passed_count,
            failed_questions=failed_count,
            question_results=question_results,
        )

        logger.info(
            f"QC validation complete: {passed_count}/{len(questions)} passed"
        )

        return valid_questions, qc_summary

    async def _validate_single_question(
        self,
        question: GeneratedQuizQuestion,
        block_map: Dict[str, QuizCandidateBlock],
    ) -> QuizQuestionQcResult:
        """
        단일 문항을 검증합니다.

        검증 순서:
        1. SCHEMA (구조 검증)
        2. SOURCE (원문 일치 검증)
        3. SELF_CHECK (LLM Self-check)

        Args:
            question: 검증할 퀴즈 문항
            block_map: 블록 ID → 블록 매핑

        Returns:
            QuizQuestionQcResult: 문항별 QC 결과
        """
        question_id = question.question_id

        # 1. SCHEMA 검증
        schema_result = self._validate_schema(question)
        if not schema_result.qc_pass:
            schema_result.question_id = question_id
            logger.debug(
                f"Question {question_id} failed SCHEMA: {schema_result.qc_reason_code}"
            )
            return schema_result

        # 2. SOURCE 검증
        source_result = self._validate_source(question, block_map)
        if not source_result.qc_pass:
            source_result.question_id = question_id
            logger.debug(
                f"Question {question_id} failed SOURCE: {source_result.qc_reason_code}"
            )
            return source_result

        # 3. SELF_CHECK (활성화된 경우)
        if self._selfcheck_enabled:
            selfcheck_result = await self._validate_selfcheck(question, block_map)
            if not selfcheck_result.qc_pass:
                selfcheck_result.question_id = question_id
                logger.debug(
                    f"Question {question_id} failed SELF_CHECK: {selfcheck_result.qc_reason_code}"
                )
                return selfcheck_result

        # 모든 검증 통과
        return QuizQuestionQcResult(
            question_id=question_id,
            qc_pass=True,
            qc_stage_failed=QuizQcStage.NONE,
            qc_reason_code=QuizQcReasonCode.NONE,
            qc_reason_detail=None,
        )

    def _validate_schema(
        self,
        question: GeneratedQuizQuestion,
    ) -> QuizQuestionQcResult:
        """
        스키마/구조 검증.

        검증 항목:
        - 필수 필드 존재 (stem, options)
        - 옵션 개수 >= 2
        - 정답 개수 정확히 1개
        - stem 길이가 충분한지

        Args:
            question: 검증할 문항

        Returns:
            QuizQuestionQcResult: 검증 결과
        """
        # stem 존재 및 길이 검사
        if not question.stem or len(question.stem.strip()) < 5:
            return QuizQuestionQcResult(
                qc_pass=False,
                qc_stage_failed=QuizQcStage.SCHEMA,
                qc_reason_code=QuizQcReasonCode.LOW_QUALITY_TEXT,
                qc_reason_detail="문제 텍스트(stem)가 너무 짧거나 비어 있습니다",
            )

        # 옵션 존재 및 개수 검사
        if not question.options or len(question.options) < 2:
            return QuizQuestionQcResult(
                qc_pass=False,
                qc_stage_failed=QuizQcStage.SCHEMA,
                qc_reason_code=QuizQcReasonCode.INVALID_STRUCTURE,
                qc_reason_detail=f"보기 개수가 부족합니다 (현재: {len(question.options) if question.options else 0}개, 최소: 2개)",
            )

        # 정답 개수 검사
        correct_count = sum(1 for opt in question.options if opt.is_correct)

        if correct_count == 0:
            return QuizQuestionQcResult(
                qc_pass=False,
                qc_stage_failed=QuizQcStage.SCHEMA,
                qc_reason_code=QuizQcReasonCode.NO_CORRECT_OPTION,
                qc_reason_detail="정답으로 지정된 보기가 없습니다",
            )

        if correct_count > 1:
            return QuizQuestionQcResult(
                qc_pass=False,
                qc_stage_failed=QuizQcStage.SCHEMA,
                qc_reason_code=QuizQcReasonCode.MULTIPLE_CORRECT,
                qc_reason_detail=f"정답이 {correct_count}개 지정되어 있습니다 (1개만 허용)",
            )

        # 각 옵션 텍스트 검사
        for i, opt in enumerate(question.options):
            if not opt.text or len(opt.text.strip()) < 1:
                return QuizQuestionQcResult(
                    qc_pass=False,
                    qc_stage_failed=QuizQcStage.SCHEMA,
                    qc_reason_code=QuizQcReasonCode.LOW_QUALITY_TEXT,
                    qc_reason_detail=f"보기 {i + 1}의 텍스트가 비어 있습니다",
                )

        # 통과
        return QuizQuestionQcResult(qc_pass=True)

    def _validate_source(
        self,
        question: GeneratedQuizQuestion,
        block_map: Dict[str, QuizCandidateBlock],
    ) -> QuizQuestionQcResult:
        """
        원문 일치 검증.

        정답 텍스트의 핵심 키워드가 출처 블록에 포함되어 있는지 확인.

        Args:
            question: 검증할 문항
            block_map: 블록 ID → 블록 매핑

        Returns:
            QuizQuestionQcResult: 검증 결과

        Note:
            이 Phase에서는 간단한 문자열 기반 검사만 구현.
            TODO: RAG/Embedding 기반 검증은 향후 추가 가능.
        """
        # 출처 블록 텍스트 수집
        source_texts: List[str] = []

        for block_id in question.source_block_ids:
            if block_id in block_map:
                source_texts.append(block_map[block_id].text)

        # 출처 블록이 없으면 전체 블록에서 검색
        if not source_texts:
            source_texts = [b.text for b in block_map.values()]

        # 출처 텍스트가 아예 없으면 통과 (검증 불가)
        if not source_texts:
            logger.warning("No source blocks available for SOURCE validation")
            return QuizQuestionQcResult(qc_pass=True)

        # 전체 출처 텍스트 결합
        combined_source = " ".join(source_texts).lower()

        # 정답 옵션 찾기
        correct_option = None
        for opt in question.options:
            if opt.is_correct:
                correct_option = opt
                break

        if not correct_option:
            # SCHEMA에서 이미 검사했으므로 여기까지 오면 안됨
            return QuizQuestionQcResult(qc_pass=True)

        # 정답 텍스트에서 핵심 키워드 추출 (간단한 방식)
        correct_text = correct_option.text.lower()
        keywords = self._extract_keywords(correct_text)

        # 키워드가 출처에 포함되어 있는지 확인
        # 최소 하나의 키워드가 출처에 있어야 함
        keyword_found = False
        for keyword in keywords:
            if keyword in combined_source:
                keyword_found = True
                break

        if not keyword_found and keywords:
            return QuizQuestionQcResult(
                qc_pass=False,
                qc_stage_failed=QuizQcStage.SOURCE,
                qc_reason_code=QuizQcReasonCode.SOURCE_MISMATCH,
                qc_reason_detail=f"정답 '{correct_option.text}'의 핵심 키워드가 출처 블록에서 발견되지 않습니다",
            )

        return QuizQuestionQcResult(qc_pass=True)

    def _extract_keywords(self, text: str) -> List[str]:
        """
        텍스트에서 핵심 키워드를 추출합니다.

        Args:
            text: 원본 텍스트

        Returns:
            키워드 목록
        """
        # 한국어/영어 명사/키워드 추출 (간단한 방식)
        # 조사, 어미 등 제거
        # 2글자 이상 단어만 추출

        # 불용어 (간단한 목록)
        stopwords = {
            "이", "그", "저", "것", "수", "등", "및", "를", "을", "에", "의",
            "가", "는", "은", "로", "으로", "와", "과", "도", "만", "에서",
            "한다", "된다", "하는", "되는", "있는", "없는", "하여", "되어",
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "have", "has", "had", "do", "does", "did", "will", "would",
            "can", "could", "may", "might", "must", "shall", "should",
        }

        # 단어 분리
        words = re.findall(r"[\w]+", text.lower())

        # 필터링
        keywords = [
            w for w in words
            if len(w) >= 2 and w not in stopwords
        ]

        return keywords

    async def _validate_selfcheck(
        self,
        question: GeneratedQuizQuestion,
        block_map: Dict[str, QuizCandidateBlock],
    ) -> QuizQuestionQcResult:
        """
        LLM Self-check 검증.

        별도의 LLM 호출을 통해:
        - 복수 정답 가능성
        - 모두 틀린 보기
        - 문서와 상충되는 내용
        - 지나치게 모호한 표현
        을 점검합니다.

        Args:
            question: 검증할 문항
            block_map: 블록 ID → 블록 매핑

        Returns:
            QuizQuestionQcResult: 검증 결과

        Note:
            Self-check 에러 시 FAIL 처리 (보수적 접근).
            향후 정책에 따라 Skip으로 변경 가능.
        """
        # 출처 텍스트 수집
        source_texts: List[str] = []
        for block_id in question.source_block_ids:
            if block_id in block_map:
                source_texts.append(block_map[block_id].text)

        if not source_texts:
            source_texts = [b.text for b in block_map.values()]

        source_text = "\n".join(source_texts) if source_texts else "(출처 없음)"

        # 보기 텍스트 포맷
        options_text = ""
        correct_option = ""
        for i, opt in enumerate(question.options):
            marker = " (정답)" if opt.is_correct else ""
            options_text += f"{i + 1}. {opt.text}{marker}\n"
            if opt.is_correct:
                correct_option = opt.text

        # LLM 메시지 구성
        user_message = SELF_CHECK_USER_PROMPT_TEMPLATE.format(
            source_text=source_text[:2000],  # 토큰 절약
            stem=question.stem,
            options_text=options_text,
            correct_option=correct_option,
        )

        messages = [
            {"role": "system", "content": SELF_CHECK_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        try:
            llm_response = await self._llm.generate_chat_completion(
                messages=messages,
                model=None,  # 기본 모델
                temperature=0.1,  # 일관된 판단을 위해 낮은 temperature
                max_tokens=512,
            )

            # 응답 파싱
            return self._parse_selfcheck_response(llm_response)

        except Exception as e:
            logger.warning(f"Self-check LLM call failed: {e}")
            # 에러 시 FAIL 처리 (보수적 접근)
            return QuizQuestionQcResult(
                qc_pass=False,
                qc_stage_failed=QuizQcStage.SELF_CHECK,
                qc_reason_code=QuizQcReasonCode.OTHER,
                qc_reason_detail=f"Self-check LLM 호출 실패: {str(e)}",
            )

    def _parse_selfcheck_response(
        self,
        llm_response: str,
    ) -> QuizQuestionQcResult:
        """
        LLM Self-check 응답을 파싱합니다.

        Args:
            llm_response: LLM 응답 텍스트

        Returns:
            QuizQuestionQcResult: 파싱된 결과
        """
        try:
            # JSON 추출
            json_str = self._extract_json_from_response(llm_response)

            if json_str:
                data = json.loads(json_str)
                selfcheck = LLMSelfCheckResponse(**data)

                if selfcheck.verdict.upper() == "PASS":
                    return QuizQuestionQcResult(qc_pass=True)
                else:
                    # 실패 사유 매핑
                    reason_code = self._map_reason_code(selfcheck.reason_code)
                    return QuizQuestionQcResult(
                        qc_pass=False,
                        qc_stage_failed=QuizQcStage.SELF_CHECK,
                        qc_reason_code=reason_code,
                        qc_reason_detail=selfcheck.reason_detail,
                    )

        except json.JSONDecodeError as e:
            logger.warning(f"Self-check JSON parsing failed: {e}")
        except Exception as e:
            logger.warning(f"Self-check response parsing failed: {e}")

        # 파싱 실패 시 FAIL 처리
        return QuizQuestionQcResult(
            qc_pass=False,
            qc_stage_failed=QuizQcStage.SELF_CHECK,
            qc_reason_code=QuizQcReasonCode.OTHER,
            qc_reason_detail="Self-check 응답 파싱 실패",
        )

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
        if response.startswith("{") and response.endswith("}"):
            return response

        # ```json ... ``` 블록 추출
        json_block_pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
        match = re.search(json_block_pattern, response, re.DOTALL)
        if match:
            return match.group(1)

        # { ... } 패턴 추출
        brace_start = response.find("{")
        if brace_start != -1:
            depth = 0
            for i, char in enumerate(response[brace_start:], start=brace_start):
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        return response[brace_start:i + 1]

        return None

    def _map_reason_code(
        self,
        llm_reason_code: Optional[str],
    ) -> QuizQcReasonCode:
        """
        LLM 응답의 reason_code를 Enum으로 매핑합니다.

        Args:
            llm_reason_code: LLM이 반환한 사유 코드

        Returns:
            QuizQcReasonCode Enum
        """
        if not llm_reason_code:
            return QuizQcReasonCode.OTHER

        code_upper = llm_reason_code.upper().strip()

        mapping = {
            "MULTIPLE_CORRECT": QuizQcReasonCode.MULTIPLE_CORRECT,
            "NO_CORRECT_OPTION": QuizQcReasonCode.NO_CORRECT_OPTION,
            "NO_CORRECT": QuizQcReasonCode.NO_CORRECT_OPTION,
            "SOURCE_MISMATCH": QuizQcReasonCode.SOURCE_MISMATCH,
            "AMBIGUOUS_QUESTION": QuizQcReasonCode.AMBIGUOUS_QUESTION,
            "AMBIGUOUS": QuizQcReasonCode.AMBIGUOUS_QUESTION,
            "LOW_QUALITY_TEXT": QuizQcReasonCode.LOW_QUALITY_TEXT,
            "LOW_QUALITY": QuizQcReasonCode.LOW_QUALITY_TEXT,
            "INVALID_STRUCTURE": QuizQcReasonCode.INVALID_STRUCTURE,
        }

        return mapping.get(code_upper, QuizQcReasonCode.OTHER)
