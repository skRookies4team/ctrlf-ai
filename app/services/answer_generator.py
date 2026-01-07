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
            "Q2": self._format_q2_fallback,
            "Q3": self._format_q3_fallback,
            "Q5": self._format_q5_fallback,
            "Q6": self._format_q6_fallback,
            "Q7": self._format_q7_fallback,
            "Q8": self._format_q8_fallback,
            "Q9": self._format_q9_fallback,
            "Q10": self._format_q10_fallback,
            "Q11": self._format_q11_fallback,
            "Q12": self._format_q12_fallback,
            "Q14": self._format_q14_fallback,
            "Q15": self._format_q15_fallback,
            "Q18": self._format_q18_fallback,
            "Q19": self._format_q19_fallback,
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

    def _format_q2_fallback(self, facts: PersonalizationFacts) -> str:
        """Q2 (특정 토픽 교육 이수 여부) 폴백."""
        topic_label = facts.metrics.get("topic_label", "해당 토픽")
        education_count = facts.metrics.get("education_count", 0)
        completed_count = facts.metrics.get("completed_count", 0)
        is_completed = facts.metrics.get("is_completed", False)

        if education_count == 0:
            return f"{topic_label} 관련 교육이 없어요."

        if is_completed:
            return f"{topic_label} 교육을 모두 이수했어요! ({completed_count}/{education_count}건 완료)"

        items = facts.items
        if items:
            lines = [f"{topic_label} 교육 이수 현황: {completed_count}/{education_count}건 완료"]
            for item in items[:5]:
                title = item.get("title", "")
                is_done = item.get("is_completed", False)
                status = "완료" if is_done else "미완료"
                progress = item.get("progress_percent", 0)
                if not is_done:
                    lines.append(f"- {title} ({status}, {progress}%)")
                else:
                    lines.append(f"- {title} ({status})")
            return "\n".join(lines)

        return f"{topic_label} 교육: {completed_count}/{education_count}건 이수"

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

    def _format_q5_fallback(self, facts: PersonalizationFacts) -> str:
        """Q5 (내 평균 vs 부서/전사 평균) 폴백."""
        my_avg = facts.metrics.get("my_average", 0)
        dept_avg = facts.metrics.get("dept_average", 0)
        company_avg = facts.metrics.get("company_average", 0)

        lines = [f"내 평균 점수: {my_avg}점"]
        if dept_avg:
            diff = my_avg - dept_avg
            diff_text = f"+{diff:.1f}" if diff > 0 else f"{diff:.1f}"
            lines.append(f"- 부서 평균: {dept_avg}점 (나와 {diff_text}점 차이)")
        if company_avg:
            diff = my_avg - company_avg
            diff_text = f"+{diff:.1f}" if diff > 0 else f"{diff:.1f}"
            lines.append(f"- 전사 평균: {company_avg}점 (나와 {diff_text}점 차이)")

        return "\n".join(lines)

    def _format_q6_fallback(self, facts: PersonalizationFacts) -> str:
        """Q6 (가장 낮은/높은 점수 교육 TOP3) 폴백."""
        items = facts.items
        if not items:
            return "퀴즈 응시 기록이 없어요."

        lines = ["취약 과목 TOP3:"]
        for item in items[:3]:
            rank = item.get("rank", "")
            topic = item.get("topic", "")
            wrong_rate = item.get("wrong_rate", 0)
            lines.append(f"{rank}. {topic} (오답률 {wrong_rate}%)")

        return "\n".join(lines)

    def _format_q7_fallback(self, facts: PersonalizationFacts) -> str:
        """Q7 (특정 토픽 퀴즈 점수 조회) 폴백."""
        topic_label = facts.metrics.get("topic_label", "해당 토픽")
        has_attempt = facts.metrics.get("has_attempt", False)
        average_score = facts.metrics.get("average_score", 0)
        passed_count = facts.metrics.get("passed_count", 0)
        education_count = facts.metrics.get("education_count", 0)

        if not has_attempt:
            return f"{topic_label} 퀴즈를 아직 응시하지 않았어요."

        items = facts.items
        if items:
            lines = [f"{topic_label} 퀴즈 현황: 평균 {average_score}점 ({passed_count}/{education_count}건 통과)"]
            for item in items[:5]:
                title = item.get("title", "")
                best_score = item.get("best_score", 0)
                passed = item.get("passed", False)
                status = "통과" if passed else "미통과"
                if item.get("has_attempt", False):
                    lines.append(f"- {title}: {best_score}점 ({status})")
                else:
                    lines.append(f"- {title}: 미응시")
            return "\n".join(lines)

        return f"{topic_label} 퀴즈 평균 점수: {average_score}점"

    def _format_q8_fallback(self, facts: PersonalizationFacts) -> str:
        """Q8 (특정 토픽 교육 시청 완료 여부) 폴백."""
        topic_label = facts.metrics.get("topic_label", "해당 토픽")
        education_count = facts.metrics.get("education_count", 0)
        completed_count = facts.metrics.get("completed_count", 0)
        is_all_completed = facts.metrics.get("is_all_completed", False)

        if education_count == 0:
            return f"{topic_label} 관련 교육이 없어요."

        if is_all_completed:
            return f"{topic_label} 교육 영상을 모두 시청 완료했어요! ({completed_count}건)"

        items = facts.items
        if items:
            lines = [f"{topic_label} 교육 시청 현황: {completed_count}/{education_count}건 완료"]
            for item in items[:5]:
                title = item.get("title", "")
                is_done = item.get("is_completed", False)
                progress = item.get("progress_percent", 0)
                if is_done:
                    lines.append(f"- {title}: 시청 완료")
                else:
                    lines.append(f"- {title}: {progress}% 시청")
            return "\n".join(lines)

        return f"{topic_label} 교육: {completed_count}/{education_count}건 시청 완료"

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

    def _format_q10_fallback(self, facts: PersonalizationFacts) -> str:
        """Q10 (내 근태 현황) 폴백."""
        work_days = facts.metrics.get("work_days", 0)
        actual_work_days = facts.metrics.get("actual_work_days", 0)
        late_count = facts.metrics.get("late_count", 0)
        early_leave_count = facts.metrics.get("early_leave_count", 0)
        absent_count = facts.metrics.get("absent_count", 0)
        remote_days = facts.metrics.get("remote_days", 0)
        overtime_hours = facts.metrics.get("overtime_hours", 0)

        lines = [f"이번 달 근태 현황 (총 근무일 {work_days}일 중 {actual_work_days}일 출근):"]

        # 요약 정보
        summary_parts = []
        if late_count > 0:
            summary_parts.append(f"지각 {late_count}회")
        if early_leave_count > 0:
            summary_parts.append(f"조퇴 {early_leave_count}회")
        if absent_count > 0:
            summary_parts.append(f"결근 {absent_count}회")
        if remote_days > 0:
            summary_parts.append(f"재택 {remote_days}일")
        if overtime_hours > 0:
            summary_parts.append(f"초과근무 {overtime_hours}시간")

        if summary_parts:
            lines.append("- " + ", ".join(summary_parts))
        else:
            lines.append("- 지각/조퇴/결근 없음")

        # 최근 출퇴근 기록
        items = facts.items
        if items:
            lines.append("")
            lines.append("[최근 출퇴근 기록]")
            for item in items[:7]:  # 최대 7일 (일주일)
                date = item.get("date", "")
                day_of_week = item.get("day_of_week", "")
                check_in = item.get("check_in", "-")
                check_out = item.get("check_out", "-")
                status = item.get("status", "")
                work_type = item.get("work_type", "")

                # 날짜 포맷팅 (YYYY-MM-DD -> MM/DD)
                try:
                    date_str = date[5:10].replace("-", "/") if date else ""
                except Exception:
                    date_str = date

                # 상태 표시
                status_str = ""
                if status == "지각":
                    status_str = " [지각]"
                elif status == "조퇴":
                    status_str = " [조퇴]"
                elif work_type == "재택":
                    status_str = " [재택]"

                lines.append(f"- {date_str}({day_of_week}) {check_in}~{check_out}{status_str}")

        return "\n".join(lines)

    def _format_q11_fallback(self, facts: PersonalizationFacts) -> str:
        """Q11 (남은 연차) 폴백."""
        remaining = facts.metrics.get("remaining_days", 0)
        total = facts.metrics.get("total_days", 0)
        used = facts.metrics.get("used_days", 0)

        if total:
            return f"남은 연차: {remaining}일 (총 {total}일 중 {used}일 사용)"
        return f"남은 연차: {remaining}일"

    def _format_q12_fallback(self, facts: PersonalizationFacts) -> str:
        """Q12 (연차 사용 이력) 폴백."""
        total = facts.metrics.get("total_days", 0)
        used = facts.metrics.get("used_days", 0)
        remaining = facts.metrics.get("remaining_days", 0)
        usage_count = facts.metrics.get("usage_count", 0)

        items = facts.items
        if not items:
            if used == 0:
                return "올해 사용한 연차가 없어요."
            return f"올해 연차 {used}일을 사용했어요. (잔여: {remaining}일)"

        # 사용 이력이 있는 경우
        lines = [f"올해 연차 사용 이력 ({usage_count}건, 총 {used}일 사용):"]

        for item in items[:10]:  # 최대 10개 표시
            leave_type = item.get("leave_type", "연차")
            start_date = item.get("start_date", "")
            end_date = item.get("end_date", "")
            days = item.get("days", 0)
            reason = item.get("reason", "")

            # 날짜 포맷팅 (YYYY-MM-DD -> MM/DD)
            try:
                start_str = start_date[5:10].replace("-", "/") if start_date else ""
                end_str = end_date[5:10].replace("-", "/") if end_date else ""
            except Exception:
                start_str = start_date
                end_str = end_date

            # 기간 표시
            if start_str == end_str or not end_str:
                date_str = start_str
            else:
                date_str = f"{start_str}~{end_str}"

            # 일수 표시
            if days == 0.5:
                days_str = "반차"
            elif days == int(days):
                days_str = f"{int(days)}일"
            else:
                days_str = f"{days}일"

            # 사유가 있으면 표시
            if reason:
                lines.append(f"- [{leave_type}] {date_str} ({days_str}) - {reason}")
            else:
                lines.append(f"- [{leave_type}] {date_str} ({days_str})")

        lines.append(f"\n잔여 연차: {remaining}일")
        return "\n".join(lines)

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

    def _format_q15_fallback(self, facts: PersonalizationFacts) -> str:
        """Q15 (복지 포인트 사용 내역) 폴백."""
        total_granted = facts.metrics.get("total_granted", 0)
        total_used = facts.metrics.get("total_used", 0)
        remaining = facts.metrics.get("remaining", 0)
        usage_count = facts.metrics.get("usage_count", 0)

        items = facts.items
        if not items:
            if total_used == 0:
                return f"올해 사용한 복지 포인트가 없어요. (잔액: {remaining:,}원)"
            return f"올해 복지 포인트 {total_used:,}원을 사용했어요. (잔액: {remaining:,}원)"

        # 사용 내역이 있는 경우
        lines = [f"복지 포인트 사용 내역 ({usage_count}건, 총 {total_used:,}원 사용):"]

        # 카테고리별 합계 계산
        category_totals: dict = {}
        for item in items:
            cat = item.get("category", "기타")
            amt = item.get("amount", 0)
            category_totals[cat] = category_totals.get(cat, 0) + amt

        # 카테고리별 요약
        if category_totals:
            lines.append("")
            lines.append("[카테고리별 사용 현황]")
            for cat, amt in sorted(category_totals.items(), key=lambda x: -x[1]):
                lines.append(f"- {cat}: {amt:,}원")

        # 최근 사용 내역 (최대 10개)
        lines.append("")
        lines.append("[최근 사용 내역]")
        for item in items[:10]:
            date = item.get("date", "")
            merchant = item.get("merchant", "")
            amount = item.get("amount", 0)
            description = item.get("description", "")

            # 날짜 포맷팅 (YYYY-MM-DD -> MM/DD)
            try:
                date_str = date[5:10].replace("-", "/") if date else ""
            except Exception:
                date_str = date

            if description:
                lines.append(f"- {date_str} {merchant}: {amount:,}원 ({description})")
            else:
                lines.append(f"- {date_str} {merchant}: {amount:,}원")

        lines.append(f"\n잔여 포인트: {remaining:,}원")
        return "\n".join(lines)

    def _format_q18_fallback(self, facts: PersonalizationFacts) -> str:
        """Q18 (보안교육/특정 토픽 완료 여부) 폴백."""
        topic_label = facts.metrics.get("topic_label", "보안 교육")
        education_count = facts.metrics.get("education_count", 0)
        video_completed_count = facts.metrics.get("video_completed_count", 0)
        quiz_passed_count = facts.metrics.get("quiz_passed_count", 0)
        is_fully_completed = facts.metrics.get("is_fully_completed", False)

        if education_count == 0:
            return f"{topic_label} 관련 교육이 없어요."

        if is_fully_completed:
            return f"{topic_label}을 모두 완료했어요! (영상 {video_completed_count}건, 퀴즈 {quiz_passed_count}건 통과)"

        items = facts.items
        if items:
            lines = [f"{topic_label} 이수 현황:"]
            lines.append(f"- 영상 시청: {video_completed_count}/{education_count}건 완료")
            lines.append(f"- 퀴즈 통과: {quiz_passed_count}/{education_count}건 통과")

            incomplete_items = [item for item in items if not item.get("video_completed", False) or not item.get("quiz_passed", False)]
            if incomplete_items:
                lines.append("")
                lines.append("미완료 항목:")
                for item in incomplete_items[:3]:
                    title = item.get("title", "")
                    video_done = "완료" if item.get("video_completed", False) else "미완료"
                    quiz_done = "통과" if item.get("quiz_passed", False) else "미통과"
                    lines.append(f"- {title} (영상: {video_done}, 퀴즈: {quiz_done})")
            return "\n".join(lines)

        return f"{topic_label}: 영상 {video_completed_count}/{education_count}건, 퀴즈 {quiz_passed_count}/{education_count}건 완료"

    def _format_q19_fallback(self, facts: PersonalizationFacts) -> str:
        """Q19 (특정 토픽 교육 마감일 조회) 폴백."""
        topic_label = facts.metrics.get("topic_label", "해당 토픽")
        has_deadline = facts.metrics.get("has_deadline", False)
        nearest_deadline = facts.metrics.get("nearest_deadline", "")

        if not has_deadline:
            return f"{topic_label} 교육에는 설정된 마감일이 없어요."

        items = facts.items
        if items:
            lines = [f"{topic_label} 교육 마감일:"]

            # 미완료 항목만 먼저 표시
            incomplete_items = [item for item in items if not item.get("is_completed", False)]
            for item in incomplete_items[:5]:
                title = item.get("title", "")
                deadline = item.get("deadline", "")
                if deadline:
                    # ISO 형식 날짜를 읽기 쉽게 변환
                    try:
                        deadline_str = deadline[:10]  # YYYY-MM-DD 형식
                    except Exception:
                        deadline_str = deadline
                    lines.append(f"- {title}: {deadline_str}까지")

            if not incomplete_items:
                lines.append("모든 교육을 이미 완료했어요!")

            return "\n".join(lines)

        if nearest_deadline:
            try:
                deadline_str = nearest_deadline[:10]
            except Exception:
                deadline_str = nearest_deadline
            return f"{topic_label} 교육 마감일: {deadline_str}"

        return f"{topic_label} 교육 마감일 정보를 확인해주세요."

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
