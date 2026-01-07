"""
Quiz Generate Service (Production-grade)
- N개 보장 루프
- 의미(embedding) 기반 중복 제거 (is_semantic_duplicate 연결)
- source_block 자동 매핑 (후보블록 기반)
- 질문 유형 강제 분기 (정의/절차/예외/법조항/사례)
- Phase 17 QC 파이프라인 통합
"""

import json
import re
import random
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
from app.models.quiz_qc import QuizSetQcResult

# ✅ 의미 중복 판단 유틸 (이미 너희 프로젝트에 존재)
#    - scripts/test_quiz_generation_by_domain.py 에서 쓰고 있으므로 여기서도 연결
from app.utils.embedding_utils import is_semantic_duplicate

logger = get_logger(__name__)

# =============================================================================
# 난이도 분배 상수 (고정 비율)
# =============================================================================
DIFFICULTY_RATIO = {
    "easy": 0.5,
    "normal": 0.3,
    "hard": 0.2,
}

# =============================================================================
# 질문 유형 강제 분기
# =============================================================================
QUESTION_TYPES = [
    "정의(개념)",
    "절차(처리/신고/조사/조치)",
    "예외(금지/허용/주의사항)",
    "법조항(근거/조항번호/제재)",
    "사례(상황판단)",
]

# =============================================================================
# LLM 프롬프트 템플릿
# =============================================================================

SYSTEM_PROMPT = """당신은 기업 교육/사규 문서를 기반으로 객관식 퀴즈를 설계하는 전문가입니다.

중요 원칙:
1) 문서에 명시된 사실만 사용하세요. 새 정책/추측/왜곡 금지.
2) 각 문항은 정답이 정확히 1개여야 합니다.
3) 오답은 그럴듯하지만 문서와 모순되게 작성하세요.
4) 같은 의미의 문항을 반복 생성하지 마세요.
5) 반드시 JSON만 출력하세요. (설명 텍스트/마크다운 금지)
"""

USER_PROMPT_TEMPLATE = """다음 교육/사규 문서 블록들을 참고하여 객관식 퀴즈 1문항만 생성하세요.

## 요청 정보
- 언어: {language}
- 보기 개수: {max_options}개 (정확히 {max_options}개)
- 난이도: {target_difficulty} (EASY/NORMAL/HARD 중 하나로)
- 문항 유형: {question_type}

## 문항 유형 가이드 (이번 문항은 반드시 '{question_type}'에 해당해야 함)
- 정의(개념): 용어/개념/의미를 정확히 묻기
- 절차: 신고/조사/조치/보고 등 단계나 순서를 묻기
- 예외: 금지/허용/주의사항/예외조건을 묻기
- 법조항: 조항번호/근거/제재(과태료 등) 같은 근거를 묻기 (문서에 있을 때만)
- 사례: 간단한 상황 제시 후 적절한 행동/판단을 묻기 (문서 내용으로만 판별 가능하게)

## 중복 방지 (매우 중요)
- 기존에 나온 질문과 의미가 비슷한 문항은 절대 만들지 마세요.
- 질문의 초점(개념/절차/예외/근거/사례)을 바꿔서 새로 구성하세요.

## 교육/사규 텍스트 블록
{blocks_text}

{exclude_instruction}

## 출력 형식 (JSON만)
{{
  "questions": [
    {{
      "stem": "문제 텍스트",
      "options": [
        {{"text": "보기1", "is_correct": true}},
        {{"text": "보기2", "is_correct": false}},
        {{"text": "보기3", "is_correct": false}},
        {{"text": "보기4", "is_correct": false}}
      ],
      "difficulty": "{target_difficulty}",
      "explanation": "정답 해설(간단명료)",
      "rationale": "문서 근거(어떤 문장/내용을 근거로 했는지 요약)",
      "source_block_ids": [],
      "tags": []
    }}
  ]
}}
"""

EXCLUDE_INSTRUCTION_TEMPLATE = """## 기존 문항 (의미 중복 금지)
아래 문항들과 의미상 동일/유사한 문항을 만들지 마세요:
{previous_stems}
"""


class QuizGenerateService:
    """
    Production-grade Quiz Generation Service
    - N개 보장 루프
    - 의미 기반 중복 제거 (embedding)
    - source_block 자동 매핑
    - QC 파이프라인 통합
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        qc_enabled: bool = True,
        # N개 보장 루프 파라미터
        max_attempts_per_question: int = 6,
        max_total_attempts: int = 200,
        # 중복 제거 임계/옵션
        enable_semantic_dedup: bool = True,
        semantic_dup_threshold: float = 0.86,  # is_semantic_duplicate 내부에서 쓰면 무시될 수 있음
        enable_exact_dedup: bool = True,
        # source mapping
        enable_source_mapping: bool = True,
    ) -> None:
        self._llm = llm_client or LLMClient()
        self._qc_enabled = qc_enabled
        self._qc_service = None  # lazy init
        self._last_qc_result: Optional[QuizSetQcResult] = None

        self._max_attempts_per_question = max_attempts_per_question
        self._max_total_attempts = max_total_attempts

        self._enable_semantic_dedup = enable_semantic_dedup
        self._semantic_dup_threshold = semantic_dup_threshold
        self._enable_exact_dedup = enable_exact_dedup

        self._enable_source_mapping = enable_source_mapping

    # =========================================================================
    # Public
    # =========================================================================

    async def generate_quiz(self, request: QuizGenerateRequest) -> QuizGenerateResponse:
        """
        목표: request.num_questions 개를 가능한 한 보장.
        - 파싱 실패/유효성 실패/QC 실패/중복이면 재시도
        - max_total_attempts로 무한루프 방지
        """
        logger.info(
            f"Generating quiz: target={request.num_questions}, blocks={len(request.quiz_candidate_blocks)}"
        )

        if request.num_questions <= 0:
            return QuizGenerateResponse(generated_count=0, questions=[])

        # 난이도 분배 계산
        difficulty_counts = self._calculate_difficulty_distribution(request.num_questions)
        difficulty_plan = self._build_difficulty_plan(difficulty_counts)

        final_questions: List[GeneratedQuizQuestion] = []

        # 중복 방지용 메모리
        seen_stems_norm: set[str] = set()
        seen_stems_raw: List[str] = []  # semantic duplicate 용(원문)

        if request.exclude_previous_questions:
            for q in request.exclude_previous_questions:
                norm = self._normalize_text(q.stem or "")
                if norm:
                    seen_stems_norm.add(norm)
                    seen_stems_raw.append(q.stem)

        total_attempts = 0

        # N개 보장 루프
        for target_idx in range(request.num_questions):
            if total_attempts >= self._max_total_attempts:
                logger.warning(
                    f"Reached max_total_attempts={self._max_total_attempts}. "
                    f"generated={len(final_questions)}/{request.num_questions}"
                )
                break

            target_difficulty = difficulty_plan[target_idx]
            question_type = QUESTION_TYPES[target_idx % len(QUESTION_TYPES)]

            success_for_this_slot = False

            for attempt in range(self._max_attempts_per_question):
                total_attempts += 1
                if total_attempts > self._max_total_attempts:
                    break

                # 다양성: 블록을 전부 넣지 말고 일부만 섞어서 주면 반복이 덜함
                blocks_for_prompt = self._pick_blocks_for_prompt(
                    request.quiz_candidate_blocks,
                    k=min(4, max(2, len(request.quiz_candidate_blocks))),
                )

                messages = self._build_llm_messages(
                    request=request,
                    blocks_for_prompt=blocks_for_prompt,
                    target_difficulty=target_difficulty,
                    question_type=question_type,
                    seen_stems_for_prompt=seen_stems_raw[-20:],  # 최근 것만 LLM에 보여줘도 충분
                )

                # 시도 횟수에 따라 temperature 살짝 상승 → 탈출 시도
                temperature = 0.55 + min(0.25, 0.05 * attempt)

                llm_response = await self._llm.generate_chat_completion(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=1400,
                )

                parsed = self._parse_llm_response(llm_response)
                if not parsed:
                    logger.debug("LLM parse produced 0 questions; retrying")
                    continue

                valid = self._validate_and_filter_questions(parsed, request.max_options)
                if not valid:
                    logger.debug("Validation produced 0 questions; retrying")
                    continue

                candidate_llm_q = valid[0]

                # (A) 중복 제거
                if self._is_duplicate(candidate_llm_q.stem, seen_stems_raw, seen_stems_norm):
                    logger.info("Duplicate detected; retrying")
                    continue

                # (B) source_block 자동 매핑
                if self._enable_source_mapping:
                    mapped_ids = self._map_source_blocks(
                        llm_question=candidate_llm_q,
                        candidate_blocks=request.quiz_candidate_blocks,
                    )
                    candidate_llm_q.source_block_ids = mapped_ids  # overwrite

                # (C) Assemble DTO
                assembled = self._assemble_questions([candidate_llm_q], request)
                if not assembled:
                    logger.debug("Assemble produced 0 questions; retrying")
                    continue

                new_q = assembled[0]

                # (D) QC (slot 단위로 돌리면 비용↑. 하지만 N 보장하려면 slot 단위가 현실적)
                if self._qc_enabled:
                    passed, qc_result = await self._apply_qc_single_question(
                        question=new_q,
                        source_blocks=request.quiz_candidate_blocks,
                    )
                    self._last_qc_result = qc_result  # 마지막 QC 결과 저장 (디버깅용)

                    if not passed:
                        logger.info("QC failed; retrying")
                        continue

                # (E) Accept
                final_questions.append(new_q)
                seen_stems_raw.append(new_q.stem)
                seen_stems_norm.add(self._normalize_text(new_q.stem))

                success_for_this_slot = True
                break  # 슬롯 채움

            if not success_for_this_slot:
                logger.warning(f"Failed to fill slot {target_idx+1}/{request.num_questions} after retries")

        # 최종 QC: set 단위 요약을 만들고 싶으면 여기서 validate_quiz_set을 한 번 더(옵션)
        # 현재는 slot 단위 QC로 충분.

        return QuizGenerateResponse(
            generated_count=len(final_questions),
            questions=final_questions,
        )

    def get_last_qc_result(self) -> Optional[QuizSetQcResult]:
        return self._last_qc_result

    # =========================================================================
    # Difficulty plan
    # =========================================================================

    def _calculate_difficulty_distribution(self, num_questions: int) -> Dict[str, int]:
        easy = round(num_questions * DIFFICULTY_RATIO["easy"])
        normal = round(num_questions * DIFFICULTY_RATIO["normal"])
        hard = num_questions - easy - normal
        return {"easy": max(0, easy), "normal": max(0, normal), "hard": max(0, hard)}

    def _build_difficulty_plan(self, counts: Dict[str, int]) -> List[str]:
        """
        예: EASY/EASY/... NORMAL/... HARD/... 순서로 깔고,
        QUESTION_TYPES 라운드로빈과 섞여도 다양성 유지되게 shuffle 약간.
        """
        plan = (["EASY"] * counts["easy"]) + (["NORMAL"] * counts["normal"]) + (["HARD"] * counts["hard"])
        if not plan:
            return ["NORMAL"]

        # 너무 한쪽으로 몰리면 다양성이 떨어져서 약간 섞음(재현성 필요하면 seed 고정)
        random.shuffle(plan)
        return plan

    # =========================================================================
    # Prompt
    # =========================================================================

    def _build_llm_messages(
        self,
        request: QuizGenerateRequest,
        blocks_for_prompt: List[QuizCandidateBlock],
        target_difficulty: str,
        question_type: str,
        seen_stems_for_prompt: List[str],
    ) -> List[dict]:
        blocks_text = self._format_blocks_for_prompt(blocks_for_prompt)

        exclude_instruction = ""
        previous_stems = []
        if request.exclude_previous_questions:
            previous_stems.extend([q.stem for q in request.exclude_previous_questions if q.stem])
        previous_stems.extend([s for s in seen_stems_for_prompt if s])

        if previous_stems:
            exclude_instruction = EXCLUDE_INSTRUCTION_TEMPLATE.format(
                previous_stems="\n".join(f"- {s}" for s in previous_stems[-30:])
            )

        user_message = USER_PROMPT_TEMPLATE.format(
            language=request.language,
            max_options=request.max_options,
            blocks_text=blocks_text,
            exclude_instruction=exclude_instruction,
            target_difficulty=target_difficulty,
            question_type=question_type,
        )

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

    def _format_blocks_for_prompt(self, blocks: List[QuizCandidateBlock]) -> str:
        lines = []
        for i, block in enumerate(blocks, start=1):
            tags_str = ", ".join(block.tags) if block.tags else "없음"
            chapter_info = f"챕터: {block.chapter_id}" if getattr(block, "chapter_id", None) else ""
            lo_info = f"학습목표: {block.learning_objective_id}" if getattr(block, "learning_objective_id", None) else ""
            article_info = f"조항: {block.article_path}" if getattr(block, "article_path", None) else ""

            meta_parts = [p for p in [chapter_info, lo_info, article_info] if p]
            meta_str = " | ".join(meta_parts) if meta_parts else "메타정보 없음"

            lines.append(
                f"### 블록 {i} (ID: {block.block_id})\n"
                f"- 메타: {meta_str}\n"
                f"- 태그: {tags_str}\n"
                f"- 내용: {block.text}\n"
            )
        return "\n".join(lines)

    def _pick_blocks_for_prompt(self, blocks: List[QuizCandidateBlock], k: int) -> List[QuizCandidateBlock]:
        if not blocks:
            return []
        if len(blocks) <= k:
            return blocks
        # tags/메타 다양성 고려하려면 여기서 그룹핑 가능. 일단 랜덤 샘플링.
        return random.sample(blocks, k=k)

    # =========================================================================
    # Parsing / Validation
    # =========================================================================

    def _parse_llm_response(self, llm_response: str) -> List[LLMQuizQuestion]:
        try:
            json_str = self._extract_json_from_response(llm_response)
            if not json_str:
                return []

            data = json.loads(json_str)

            if isinstance(data, dict) and "questions" in data:
                llm_result = LLMQuizResponse(**data)
                return llm_result.questions
            elif isinstance(data, list):
                return [LLMQuizQuestion(**q) for q in data]
        except Exception as e:
            logger.warning(f"Response parsing failed: {e}")
        return []

    def _extract_json_from_response(self, response: str) -> Optional[str]:
        response = response.strip()

        # 이미 JSON
        if (response.startswith("{") and response.endswith("}")) or (response.startswith("[") and response.endswith("]")):
            return response

        # ```json ... ```
        json_block_pattern = r"```(?:json)?\s*([\{\[].*?[\}\]])\s*```"
        match = re.search(json_block_pattern, response, re.DOTALL)
        if match:
            return match.group(1)

        # fallback: first balanced { } or [ ]
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start_idx = response.find(start_char)
            if start_idx == -1:
                continue
            depth = 0
            for i, ch in enumerate(response[start_idx:], start=start_idx):
                if ch == start_char:
                    depth += 1
                elif ch == end_char:
                    depth -= 1
                    if depth == 0:
                        return response[start_idx : i + 1]
        return None

    def _validate_and_filter_questions(
        self,
        questions: List[LLMQuizQuestion],
        max_options: int,
    ) -> List[LLMQuizQuestion]:
        valid = []
        for i, q in enumerate(questions):
            if not q.stem or not q.stem.strip():
                continue

            if not q.options or len(q.options) != max_options:
                # "정확히 N개 보기" 강제 (패딩/트렁케이트는 품질 깨짐)
                logger.warning(f"Question {i+1}: options={len(q.options) if q.options else 0} != {max_options}")
                continue

            correct_count = sum(1 for opt in q.options if opt.is_correct)
            if correct_count != 1:
                logger.warning(f"Question {i+1}: correct_count={correct_count} (expected 1)")
                continue

            valid.append(q)

        logger.info(f"Validated {len(valid)}/{len(questions)} questions")
        return valid

    # =========================================================================
    # Dedup (exact + semantic)
    # =========================================================================

    def _normalize_text(self, text: str) -> str:
        text = (text or "").strip().lower()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"[^\w\s가-힣]", "", text)  # 한글/영문/숫자 중심
        return text.strip()

    def _is_duplicate(self, stem: str, seen_raw: List[str], seen_norm: set[str]) -> bool:
        if not stem:
            return True

        norm = self._normalize_text(stem)

        # exact-ish
        if self._enable_exact_dedup:
            if norm in seen_norm:
                return True

        # semantic (embedding)
        if self._enable_semantic_dedup and seen_raw:
            try:
                # ✅ 프로젝트의 is_semantic_duplicate() 실제 연결
                #    보통 (text, candidates, threshold=...) 형태이거나 내부 threshold 고정일 수 있음
                #    signature가 다를 수 있어서 안전하게 호출
                try:
                    # 형태 1) is_semantic_duplicate(text, candidates, threshold=0.85)
                    if is_semantic_duplicate(stem, seen_raw, threshold=self._semantic_dup_threshold):
                        return True
                except TypeError:
                    # 형태 2) is_semantic_duplicate(text, candidates)
                    if is_semantic_duplicate(stem, seen_raw):
                        return True
            except Exception as e:
                logger.warning(f"Semantic dedup failed (fallback to exact only): {e}")

        return False

    # =========================================================================
    # Source block auto mapping
    # =========================================================================

    def _map_source_blocks(
        self,
        llm_question: LLMQuizQuestion,
        candidate_blocks: List[QuizCandidateBlock],
    ) -> List[str]:
        """
        source_block_ids를 '후보 블록' 기준으로 자동 매핑해서 최소 1개 보장하는 게 목표.
        - 1) LLM이 준 source_block_ids 중 candidate block_id와 매칭되는 게 있으면 그걸 사용
        - 2) 아니면 stem + (정답 보기) + rationale/explanation vs block.text 토큰 오버랩 점수로 best 1개 선택
        """
        if not candidate_blocks:
            return []

        # 1) LLM source_block_ids가 이미 block_id를 포함하고 있다면 우선 사용
        llm_ids = getattr(llm_question, "source_block_ids", None) or []
        if llm_ids:
            candidate_id_set = {b.block_id for b in candidate_blocks}
            matched = [sid for sid in llm_ids if sid in candidate_id_set]
            if matched:
                return matched[:3]  # 너무 많으면 상위 3개만

            # partial match (예: 문서명:페이지 같은데 공백/형식 차이)
            matched2 = []
            for sid in llm_ids:
                for bid in candidate_id_set:
                    if sid and bid and (sid in bid or bid in sid):
                        matched2.append(bid)
            if matched2:
                # 중복 제거
                uniq = []
                for x in matched2:
                    if x not in uniq:
                        uniq.append(x)
                return uniq[:3]

        # 2) 자동 매핑(토큰 오버랩)
        query_text = self._build_mapping_query_text(llm_question)

        best_block_id = None
        best_score = -1.0

        for b in candidate_blocks:
            score = self._token_overlap_score(query_text, b.text)
            if score > best_score:
                best_score = score
                best_block_id = b.block_id

        if best_block_id:
            return [best_block_id]

        return []

    def _build_mapping_query_text(self, llm_question: LLMQuizQuestion) -> str:
        parts = [llm_question.stem or ""]
        # 정답 보기 텍스트를 같이 넣으면 매핑이 훨씬 좋아짐
        try:
            for opt in llm_question.options or []:
                if getattr(opt, "is_correct", False):
                    parts.append(getattr(opt, "text", "") or "")
                    break
        except Exception:
            pass

        for k in ["explanation", "rationale"]:
            v = getattr(llm_question, k, None)
            if v:
                parts.append(v)

        return " ".join(p for p in parts if p).strip()

    def _token_overlap_score(self, a: str, b: str) -> float:
        """
        가벼운 매핑용 점수:
        - 형태소 분석 없이도 동작하도록 단순 토큰 교집합 기반 (Jaccard 변형)
        """
        a_norm = self._normalize_text(a)
        b_norm = self._normalize_text(b)
        if not a_norm or not b_norm:
            return 0.0

        a_tokens = set(a_norm.split())
        b_tokens = set(b_norm.split())

        if not a_tokens or not b_tokens:
            return 0.0

        inter = len(a_tokens & b_tokens)
        # query가 짧을 수 있으니 분모는 query 기준으로
        return inter / max(1, len(a_tokens))

    # =========================================================================
    # Assemble
    # =========================================================================

    def _assemble_questions(
        self,
        llm_questions: List[LLMQuizQuestion],
        request: QuizGenerateRequest,
    ) -> List[GeneratedQuizQuestion]:
        block_map = {b.block_id: b for b in request.quiz_candidate_blocks}

        questions: List[GeneratedQuizQuestion] = []
        for llm_q in llm_questions:
            question_id = generate_question_id()

            options: List[GeneratedQuizOption] = []
            for j, opt in enumerate(llm_q.options):
                options.append(
                    GeneratedQuizOption(
                        option_id=generate_option_id(j),
                        text=opt.text,
                        is_correct=opt.is_correct,
                    )
                )

            difficulty = self._parse_difficulty(getattr(llm_q, "difficulty", None))

            # source는 source_block_ids[0] 기준으로 메타 매핑
            src_ids = getattr(llm_q, "source_block_ids", None) or []
            primary_source_id = src_ids[0] if src_ids else None
            source_block = block_map.get(primary_source_id) if primary_source_id else None

            question = GeneratedQuizQuestion(
                question_id=question_id,
                status=QuestionStatus.DRAFT_AI_GENERATED,
                question_type=QuestionType.MCQ_SINGLE,
                stem=llm_q.stem,
                options=options,
                difficulty=difficulty,
                learning_objective_id=source_block.learning_objective_id if source_block else None,
                chapter_id=source_block.chapter_id if source_block else None,
                source_block_ids=src_ids,
                source_doc_id=source_block.doc_id if source_block else None,
                source_doc_version=source_block.doc_version if source_block else None,
                source_article_path=source_block.article_path if source_block else None,
                tags=(llm_q.tags or (source_block.tags if source_block else [])),
                explanation=getattr(llm_q, "explanation", None),
                rationale=getattr(llm_q, "rationale", None),
            )
            questions.append(question)

        return questions

    def _parse_difficulty(self, difficulty: Optional[str]) -> Difficulty:
        if not difficulty:
            return Difficulty.NORMAL
        d = difficulty.upper().strip()
        if d in ("EASY", "쉬움", "E"):
            return Difficulty.EASY
        if d in ("NORMAL", "보통", "N", "MEDIUM"):
            return Difficulty.NORMAL
        if d in ("HARD", "어려움", "H", "DIFFICULT"):
            return Difficulty.HARD
        return Difficulty.NORMAL

    # =========================================================================
    # Phase 17 QC (single-question wrapper)
    # =========================================================================

    async def _apply_qc_single_question(
        self,
        question: GeneratedQuizQuestion,
        source_blocks: List[QuizCandidateBlock],
    ) -> Tuple[bool, QuizSetQcResult]:
        """
        단일 문항에 대해 QC 파이프라인을 적용합니다.

        반환:
            passed: QC 통과 여부
            qc_result: QuizSetQcResult (단일 문항 기준)

        핵심 원칙:
        - QC 결과에서 question 객체를 꺼내지 않는다
        - validate_quiz_set()의 첫 번째 반환값(filtered_questions)가 정답
        - QC 실패 시 question은 수정/대체하지 않고 그대로 폐기
        """

        # QC 서비스 lazy init
        if self._qc_service is None:
            from app.services.quiz_quality_service import QuizQualityService
            self._qc_service = QuizQualityService(llm_client=self._llm)

        # QC는 "세트" 기준 API이므로 단일 문항도 리스트로 감싼다
        filtered_questions, qc_result = await self._qc_service.validate_quiz_set(
            questions=[question],
            source_blocks=source_blocks,
        )

        # QC 통과 여부 판단
        passed = bool(filtered_questions)

        if passed:
            logger.debug(
                f"[QC][PASS] question_id={question.question_id}"
            )
        else:
            # 실패 로그 (사유는 qc_result에 모두 들어 있음)
            if qc_result.question_results:
                r = qc_result.question_results[0]
                logger.debug(
                    f"[QC][FAIL] question_id={question.question_id} "
                    f"stage={r.qc_stage_failed} "
                    f"reason={r.qc_reason_code}"
                )
            else:
                logger.debug(
                    f"[QC][FAIL] question_id={question.question_id} (no qc_result)"
                )

        return passed, qc_result

