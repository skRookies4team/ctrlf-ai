"""
Answer Generator Service (답변 생성 서비스)

백엔드에서 받은 facts 데이터를 기반으로 자연어 답변을 생성합니다.
LLM을 사용하여 facts에 있는 값만 사용해 답변을 구성합니다.

주요 규칙 (prompt.txt):
- 답변은 facts에 있는 값만 사용한다.
- facts에 없는 수치/목록/기간은 생성하지 않는다.
- period_start/end, updated_at이 있으면 답변에 자연스럽게 포함한다.
"""

from typing import Optional

from app.clients.llm_client import LLMClient
from app.core.logging import get_logger
from app.models.personalization import (
    AnswerGeneratorContext,
    ERROR_RESPONSE_TEMPLATES,
    PersonalizationFacts,
    SUB_INTENT_METADATA,
)

logger = get_logger(__name__)


# =============================================================================
# Answer Generator 시스템 프롬프트
# =============================================================================

ANSWER_GENERATOR_SYSTEM_PROMPT = """당신은 기업 내부 정보보호 AI 어시스턴트입니다.
주어진 facts 데이터를 바탕으로 사용자에게 친절하고 자연스러운 답변을 작성하세요.

## 중요 규칙

1. **facts에 있는 값만 사용**: 답변에는 facts에 있는 수치, 목록, 날짜만 포함합니다.
2. **추측 금지**: facts에 없는 정보는 절대 추측하거나 생성하지 않습니다.
3. **기간 포함**: period_start/end가 있으면 "~기준" 형태로 자연스럽게 포함합니다.
4. **업데이트 시점**: updated_at이 있으면 필요시 "마지막 업데이트: ~" 형태로 언급합니다.
5. **간결함**: 불필요한 인사나 부가 설명 없이 핵심 정보만 전달합니다.
6. **한국어 사용**: 모든 답변은 한국어로 작성합니다.

## 출력 형식

- 수치가 있으면 명확히 표시 (예: "남은 연차: 7일")
- 목록이 있으면 번호나 글머리로 정리
- 기간이 있으면 자연스럽게 포함 (예: "2025년 1월 기준으로...")

## 예시

facts: {"metrics": {"remaining_days": 7}, "period_start": "2025-01-01"}
답변: "2025년 1월 기준, 남은 연차는 7일입니다."

facts: {"items": [{"title": "개인정보보호 교육", "deadline": "2025-01-31"}]}
답변: "이번 달 마감되는 필수 교육이 1건 있어요.
- 개인정보보호 교육 (마감: 1/31)"

사용자의 질문과 facts 데이터를 받으면 위 규칙에 따라 답변만 출력하세요."""


# =============================================================================
# AnswerGenerator 클래스
# =============================================================================


class AnswerGenerator:
    """Facts 기반 답변 생성기.

    백엔드에서 받은 facts를 LLM에게 전달하여
    자연어 답변을 생성합니다.

    Usage:
        generator = AnswerGenerator()
        answer = await generator.generate(context)
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        """AnswerGenerator 초기화.

        Args:
            llm_client: LLM 클라이언트. None이면 새로 생성.
        """
        self._llm = llm_client or LLMClient()

    async def generate(
        self,
        context: AnswerGeneratorContext,
    ) -> str:
        """facts 기반으로 자연어 답변을 생성합니다.

        Args:
            context: 답변 생성 컨텍스트 (sub_intent_id, user_question, facts)

        Returns:
            str: 생성된 자연어 답변
        """
        facts = context.facts

        # 에러가 있으면 에러 템플릿 반환
        if facts.error:
            error_type = facts.error.type
            return ERROR_RESPONSE_TEMPLATES.get(
                error_type,
                "조회 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요.",
            )

        # facts가 비어있으면 기본 메시지
        if not facts.metrics and not facts.items:
            return "조회된 데이터가 없어요."

        # LLM으로 답변 생성
        try:
            answer = await self._generate_with_llm(context)
            return answer
        except Exception as e:
            logger.warning(f"Answer generation failed, using fallback: {e}")
            return self._generate_fallback(context)

    async def _generate_with_llm(
        self,
        context: AnswerGeneratorContext,
    ) -> str:
        """LLM을 사용하여 답변을 생성합니다.

        Args:
            context: 답변 생성 컨텍스트

        Returns:
            str: LLM이 생성한 답변
        """
        # 메타데이터 가져오기
        metadata = SUB_INTENT_METADATA.get(context.sub_intent_id)
        intent_desc = metadata.description if metadata else context.sub_intent_id

        # Facts를 JSON 문자열로 변환
        facts_json = context.facts.model_dump_json(exclude_none=True, indent=2)

        # 사용자 프롬프트 구성
        user_prompt = f"""## 사용자 질문
{context.user_question}

## 조회 유형
{intent_desc}

## Facts 데이터
{facts_json}

위 facts 데이터를 바탕으로 사용자에게 답변해주세요."""

        # LLM 호출
        messages = [
            {"role": "system", "content": ANSWER_GENERATOR_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        response = await self._llm.generate_chat_completion(
            messages=messages,
            temperature=0.3,  # 일관된 답변을 위해 낮은 temperature
            max_tokens=512,
        )

        return response.strip()

    def _generate_fallback(
        self,
        context: AnswerGeneratorContext,
    ) -> str:
        """LLM 실패 시 폴백 답변을 생성합니다.

        Args:
            context: 답변 생성 컨텍스트

        Returns:
            str: 폴백 답변
        """
        facts = context.facts
        sub_intent_id = context.sub_intent_id

        # 인텐트별 기본 폴백 메시지
        fallback_templates = {
            "Q1": self._format_q1_fallback,
            "Q3": self._format_q3_fallback,
            "Q9": self._format_q9_fallback,
            "Q11": self._format_q11_fallback,
            "Q14": self._format_q14_fallback,
            "Q20": self._format_q20_fallback,
        }

        formatter = fallback_templates.get(sub_intent_id)
        if formatter:
            return formatter(facts)

        # 기본 폴백
        return "조회가 완료되었어요."

    def _format_q1_fallback(self, facts: PersonalizationFacts) -> str:
        """Q1 (미이수 필수 교육) 폴백."""
        remaining = facts.metrics.get("remaining", 0)
        if remaining == 0:
            return "미이수 필수 교육이 없어요. 모두 완료하셨네요!"

        items = facts.items
        if items:
            lines = [f"미이수 필수 교육이 {remaining}건 있어요."]
            for item in items[:5]:  # 최대 5개
                title = item.get("title", "")
                deadline = item.get("deadline", "")
                if deadline:
                    lines.append(f"- {title} (마감: {deadline})")
                else:
                    lines.append(f"- {title}")
            return "\n".join(lines)

        return f"미이수 필수 교육이 {remaining}건 있어요."

    def _format_q3_fallback(self, facts: PersonalizationFacts) -> str:
        """Q3 (이번 달 데드라인 필수 교육) 폴백."""
        count = facts.metrics.get("deadline_count", 0)
        if count == 0:
            return "이번 달 마감되는 필수 교육은 없어요."

        items = facts.items
        if items:
            lines = [f"이번 달 마감되는 필수 교육이 {count}건 있어요."]
            for item in items[:5]:
                title = item.get("title", "")
                days_left = item.get("days_left", "")
                if days_left:
                    lines.append(f"- {title} (D-{days_left})")
                else:
                    lines.append(f"- {title}")
            return "\n".join(lines)

        return f"이번 달 마감되는 필수 교육이 {count}건 있어요."

    def _format_q9_fallback(self, facts: PersonalizationFacts) -> str:
        """Q9 (이번 주 할 일) 폴백."""
        count = facts.metrics.get("todo_count", 0)
        if count == 0:
            return "이번 주 해야 할 교육/퀴즈가 없어요."

        items = facts.items
        if items:
            lines = [f"이번 주 할 일이 {count}건 있어요."]
            for item in items[:5]:
                item_type = item.get("type", "")
                title = item.get("title", "")
                deadline = item.get("deadline", "")
                type_label = "교육" if item_type == "education" else "퀴즈"
                if deadline:
                    lines.append(f"- [{type_label}] {title} (~{deadline})")
                else:
                    lines.append(f"- [{type_label}] {title}")
            return "\n".join(lines)

        return f"이번 주 할 일이 {count}건 있어요."

    def _format_q11_fallback(self, facts: PersonalizationFacts) -> str:
        """Q11 (남은 연차) 폴백."""
        remaining = facts.metrics.get("remaining_days", 0)
        total = facts.metrics.get("total_days", 0)
        used = facts.metrics.get("used_days", 0)

        if total:
            return f"남은 연차: {remaining}일 (총 {total}일 중 {used}일 사용)"
        return f"남은 연차: {remaining}일"

    def _format_q14_fallback(self, facts: PersonalizationFacts) -> str:
        """Q14 (복지/식대 포인트) 폴백."""
        welfare = facts.metrics.get("welfare_points", 0)
        meal = facts.metrics.get("meal_allowance", 0)

        lines = ["포인트 잔액:"]
        if welfare:
            lines.append(f"- 복지 포인트: {welfare:,}원")
        if meal:
            lines.append(f"- 식대: {meal:,}원")

        if len(lines) > 1:
            return "\n".join(lines)
        return "포인트 잔액을 조회할 수 없어요."

    def _format_q20_fallback(self, facts: PersonalizationFacts) -> str:
        """Q20 (올해 HR 할 일) 폴백."""
        count = facts.metrics.get("todo_count", 0)
        if count == 0:
            return "올해 HR 할 일이 모두 완료되었어요!"

        items = facts.items
        if items:
            lines = [f"올해 미완료 HR 항목이 {count}건 있어요."]
            for item in items[:5]:
                item_type = item.get("type", "")
                title = item.get("title", "")
                deadline = item.get("deadline", "")
                if deadline:
                    lines.append(f"- [{item_type}] {title} (마감: {deadline})")
                else:
                    lines.append(f"- [{item_type}] {title}")
            return "\n".join(lines)

        return f"올해 미완료 HR 항목이 {count}건 있어요."
