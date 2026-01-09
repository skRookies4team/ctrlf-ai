"""
Answer Generator Service (답변 생성 서비스)

백엔드에서 받은 facts 데이터를 기반으로 자연어 답변을 생성합니다.
LLM을 사용하여 facts에 있는 값만 사용해 답변을 구성합니다.

주요 규칙 (prompt.txt):
- 답변은 facts에 있는 값만 사용한다.
- facts에 없는 수치/목록/기간은 생성하지 않는다.
- period_start/end, updated_at이 있으면 답변에 자연스럽게 포함한다.
"""

import re
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
# 이메일/전화번호 부분 마스킹 유틸리티
# =============================================================================


def mask_email_partially(email: str) -> str:
    """이메일 주소를 부분 마스킹합니다.
    
    예시:
    - hong.gildong@company.com → hong****@company.com
    - abcd@example.com → abcd****@example.com
    - ab@example.com → a*@example.com
    
    Args:
        email: 원본 이메일 주소
    
    Returns:
        부분 마스킹된 이메일 주소
    """
    if not email or "@" not in email:
        return email
    
    try:
        local_part, domain = email.rsplit("@", 1)
        
        # 로컬 부분 마스킹
        # 예: hangyeon72112@gmail.com → han**********@gmail.com (앞 3자리 + 나머지 모두 *)
        if len(local_part) <= 1:
            masked_local = "*"
        elif len(local_part) <= 2:
            masked_local = f"{local_part[0]}*"
        elif len(local_part) <= 3:
            # 3글자 이하: 앞 2자리 + *
            masked_local = f"{local_part[:2]}*"
        elif len(local_part) <= 4:
            # 4글자: 앞 2자리 + **
            masked_local = f"{local_part[:2]}**"
        else:
            # 5글자 이상: 앞 3자리 + 나머지 모두 *로 마스킹
            remaining_length = len(local_part) - 3
            masked_local = f"{local_part[:3]}{'*' * remaining_length}"
        
        return f"{masked_local}@{domain}"
    except Exception:
        return email


def mask_phone_partially(phone: str) -> str:
    """전화번호를 부분 마스킹합니다.
    
    예시:
    - 010-1234-5678 → 010-****-5678
    - 01012345678 → 010-****-5678
    - 02-1234-5678 → 02-****-5678
    
    Args:
        phone: 원본 전화번호
    
    Returns:
        부분 마스킹된 전화번호 (앞 3자리와 뒤 4자리 표시, 가운데 4자리 마스킹)
    """
    if not phone:
        return phone
    
    # 숫자만 추출
    digits = re.sub(r'[^\d]', '', phone)
    
    if len(digits) < 8:
        # 너무 짧으면 완전 마스킹
        return "***-****-****"
    
    # 한국 전화번호 형식: 010-1234-5678 (3-4-4) 또는 02-1234-5678 (2-4-4)
    if len(digits) == 11:
        # 휴대폰: 010-1234-5678 → 010-****-5678
        return f"{digits[:3]}-****-{digits[7:]}"
    elif len(digits) == 10:
        # 지역번호: 02-1234-5678 → 02-****-5678
        if digits.startswith("02"):
            return f"{digits[:2]}-****-{digits[6:]}"
        else:
            return f"{digits[:3]}-****-{digits[6:]}"
    elif len(digits) == 8:
        # 8자리 전화번호 (예: 1588-1234)
        return f"{digits[:4]}-****"
    else:
        # 기타 형식: 앞 3자리와 뒤 4자리만 표시
        if len(digits) >= 7:
            return f"{digits[:3]}-****-{digits[-4:]}"
        else:
            return f"{digits[:3]}-****"


def mask_emails_in_text(text: str) -> str:
    """텍스트 내의 모든 이메일 주소를 부분 마스킹합니다."""
    if not text:
        return text
    
    email_pattern = r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b'
    
    def replace_email(match):
        return mask_email_partially(match.group(0))
    
    return re.sub(email_pattern, replace_email, text)


def mask_phones_in_text(text: str) -> str:
    """텍스트 내의 모든 전화번호를 부분 마스킹합니다."""
    if not text:
        return text
    
    # 한국 전화번호 패턴 (다양한 형식 지원)
    phone_patterns = [
        r'\b01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}\b',  # 휴대폰
        r'\b0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}\b',    # 지역번호
    ]
    
    result = text
    for pattern in phone_patterns:
        def replace_phone(match):
            return mask_phone_partially(match.group(0))
        result = re.sub(pattern, replace_phone, result)
    
    return result


def is_empty_value(value: any) -> bool:
    """값이 비어있거나 무의미한지 확인합니다."""
    if value is None:
        return True
    if isinstance(value, str):
        value_lower = value.lower().strip()
        return not value_lower or value_lower in ['없음', 'none', 'null', 'n/a', 'na', '']
    if isinstance(value, (int, float)):
        return value == 0
    return False


# =============================================================================
# Answer Generator 시스템 프롬프트
# =============================================================================

ANSWER_GENERATOR_SYSTEM_PROMPT = """당신은 기업 내부 정보보호 AI 어시스턴트입니다.
주어진 facts 데이터를 바탕으로 사용자에게 친절하고 자연스러운 답변을 작성하세요.

## 중요 규칙

1. **facts에 있는 값만 사용**: 답변에는 facts에 있는 수치, 목록, 날짜만 포함합니다.
2. **추측 금지**: facts에 없는 정보는 절대 추측하거나 생성하지 않습니다.
3. **사용자 이름 포함**: extra.employee_name이 있으면 답변에 "OOO님" 형태로 자연스럽게 포함합니다.
4. **기간 포함**: period_start/end가 있으면 "~기준" 형태로 자연스럽게 포함합니다.
5. **간결함**: 불필요한 인사나 부가 설명 없이 핵심 정보만 전달합니다.
6. **한국어 사용**: 모든 답변은 한국어로 작성합니다.
7. **자연스러운 문장**: 딱딱한 나열보다는 자연스러운 문장으로 답변합니다.

## 출력 형식

- 사용자 이름이 있으면 "OOO님은..." 형태로 시작
- 수치는 문장 속에 자연스럽게 포함 (예: "총 15일 중 8일 사용하셔서 남은 연차는 7일입니다")
- 목록이 있으면 번호나 글머리로 정리

## 예시

facts: {"metrics": {"total_days": 15, "used_days": 8, "remaining_days": 7}, "extra": {"employee_name": "홍길동"}}
답변: "홍길동님은 총 15일 중 8일 사용하셔서 남은 연차는 7일입니다."

facts: {"metrics": {"remaining": 2}, "items": [{"title": "개인정보보호 교육"}, {"title": "정보보안 교육"}], "extra": {"employee_name": "김철수"}}
답변: "김철수님은 현재 미이수 필수 교육이 2건 있습니다.
- 개인정보보호 교육
- 정보보안 교육"

facts: {"metrics": {"welfare_points": 150000, "meal_allowance": 280000}, "extra": {"employee_name": "이영희"}}
답변: "이영희님의 복지 포인트 잔액은 150,000원이고, 식대 잔액은 280,000원입니다."

사용자의 질문과 facts 데이터를 받으면 위 규칙에 따라 자연스러운 답변만 출력하세요."""


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
            error_message = facts.error.message or ""
            logger.warning(
                f"Personalization facts has error: sub_intent_id={context.sub_intent_id}, "
                f"error_type={error_type}, error_message={error_message}"
            )
            user_message = ERROR_RESPONSE_TEMPLATES.get(
                error_type,
                "조회 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요.",
            )
            logger.info(
                f"Returning error response: sub_intent_id={context.sub_intent_id}, "
                f"error_type={error_type}, user_message_length={len(user_message)}"
            )
            return user_message

        # facts가 비어있으면 기본 메시지
        if not facts.metrics and not facts.items:
            logger.warning(
                f"Personalization facts is empty: sub_intent_id={context.sub_intent_id}, "
                f"metrics_keys={list(facts.metrics.keys()) if facts.metrics else []}, "
                f"items_count={len(facts.items) if facts.items else 0}"
            )
            return "조회된 데이터가 없어요."

        # Q16 (인사 정보 조회)는 LLM을 건너뛰고 바로 fallback 사용
        # 이유: LLM이 개인정보 제공을 거부하는 일반 답변을 생성할 수 있음
        # 백엔드에서 이미 포맷된 데이터를 받으므로 fallback으로 충분
        if context.sub_intent_id == "Q16":
            logger.debug("Q16: Using template-based fallback (skipping LLM)")
            return self._generate_fallback(context)

        # LLM으로 답변 생성
        try:
            answer = await self._generate_with_llm(context)
            # LLM이 차단되어 FALLBACK_MESSAGE가 반환된 경우 감지
            if answer == "LLM service is not configured or unavailable. This is a fallback response. Please configure LLM_BASE_URL or check the LLM service status.":
                logger.warning("LLM blocked or unavailable, using template-based fallback")
                return self._generate_fallback(context)
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
        user_question = (context.user_question or "").lower()

        # Q16: 민감한 개인정보 질문 체크
        if sub_intent_id == "Q16":
            # 이메일/전화번호는 부분 마스킹하여 제공 가능
            email_phone_keywords = ['이메일', 'email', '전화번호', '휴대폰', '연락처', 'phone']
            # 주민번호, 나이 등은 여전히 차단
            blocked_keywords = ['주민번호', '주민등록번호', 'ssn', '나이', '생년월일']
            
            if any(keyword in user_question for keyword in blocked_keywords):
                return (
                    "개인정보나 민감정보(주민번호/나이 등)는 보안상의 이유로 "
                    "제공할 수 없어요. 본인 정보는 사내 HR 포털이나 인사팀에 문의해 주세요."
                )
            # 이메일/전화번호 질문은 백엔드 응답 확인 후 부분 마스킹하여 제공 (차단하지 않음)

        # 인텐트별 기본 폴백 메시지
        fallback_templates = {
            "Q1": self._format_q1_fallback,
            "Q2": self._format_q2_fallback,
            "Q3": self._format_q3_fallback,
            "Q4": self._format_q4_fallback,
            "Q5": self._format_q5_fallback,
            "Q6": self._format_q6_fallback,
            "Q7": self._format_q7_fallback,
            "Q8": self._format_q8_fallback,
            "Q9": self._format_q9_fallback,
            "Q10": self._format_q10_fallback,
            "Q11": self._format_q11_fallback,
            "Q12": self._format_q12_fallback,
            "Q13": self._format_q13_fallback,
            "Q14": self._format_q14_fallback,
            "Q15": self._format_q15_fallback,
            "Q16": self._format_q16_fallback,
            "Q17": self._format_q17_fallback,
            "Q18": self._format_q18_fallback,
            "Q19": self._format_q19_fallback,
            "Q20": self._format_q20_fallback,
        }

        formatter = fallback_templates.get(sub_intent_id)
        if formatter:
            # Q16은 user_question을 전달하여 질문에 맞는 답변만 반환
            if sub_intent_id == "Q16":
                return formatter(facts, user_question)
            return formatter(facts)

        # 기본 폴백
        return "조회가 완료되었어요."

    def _format_q1_fallback(self, facts: PersonalizationFacts) -> str:
        """Q1 (미이수 필수 교육) 폴백."""
        remaining = facts.metrics.get("remaining", 0)
        employee_name = facts.extra.get("employee_name")
        name_prefix = f"{employee_name}님은 " if employee_name else ""

        if remaining == 0:
            if employee_name:
                return f"{employee_name}님은 필수 교육을 모두 완료하셨습니다."
            return "필수 교육을 모두 완료하셨습니다."

        items = facts.items
        if items:
            lines = [f"{name_prefix}현재 미이수 필수 교육이 {remaining}건 있습니다."]
            for item in items[:5]:  # 최대 5개
                title = item.get("title", "")
                deadline = item.get("deadline", "")
                if deadline:
                    lines.append(f"- {title} (마감: {deadline})")
                else:
                    lines.append(f"- {title}")
            return "\n".join(lines)

        return f"{name_prefix}현재 미이수 필수 교육이 {remaining}건 있습니다."

    def _format_q2_fallback(self, facts: PersonalizationFacts) -> str:
        """Q2 (내 교육 수료 현황) 폴백."""
        employee_name = facts.extra.get("employee_name")
        total_count = facts.metrics.get("total_count", 0)
        completed_count = facts.metrics.get("completed_count", 0)
        in_progress_count = facts.metrics.get("in_progress_count", 0)

        if total_count == 0:
            if employee_name:
                return f"{employee_name}님은 수강 중인 교육이 없습니다."
            return "수강 중인 교육이 없어요."

        items = facts.items
        if items:
            if employee_name:
                lines = [f"{employee_name}님의 교육 수료 현황입니다. (완료 {completed_count}건 / 진행중 {in_progress_count}건)"]
            else:
                lines = [f"교육 수료 현황입니다. (완료 {completed_count}건 / 진행중 {in_progress_count}건)"]
            for item in items[:5]:
                title = item.get("title", "")
                is_done = item.get("is_completed", False)
                status = "완료" if is_done else "진행중"
                progress = item.get("progress_percent", 0)
                if not is_done:
                    lines.append(f"- {title} ({status}, {progress}%)")
                else:
                    lines.append(f"- {title} ({status})")
            return "\n".join(lines)

        if employee_name:
            return f"{employee_name}님은 총 {total_count}건의 교육 중 {completed_count}건을 수료하셨습니다."
        return f"총 {total_count}건의 교육 중 {completed_count}건을 수료했어요."

    def _format_q3_fallback(self, facts: PersonalizationFacts) -> str:
        """Q3 (이번 달 데드라인 필수 교육) 폴백."""
        employee_name = facts.extra.get("employee_name")
        count = facts.metrics.get("deadline_count", 0)

        if count == 0:
            if employee_name:
                return f"{employee_name}님은 이번 달 마감되는 필수 교육이 없습니다."
            return "이번 달 마감되는 필수 교육은 없어요."

        items = facts.items
        if items:
            if employee_name:
                lines = [f"{employee_name}님은 이번 달 마감되는 필수 교육이 {count}건 있습니다."]
            else:
                lines = [f"이번 달 마감되는 필수 교육이 {count}건 있어요."]
            for item in items[:5]:
                title = item.get("title", "")
                days_left = item.get("days_left", "")
                if days_left:
                    lines.append(f"- {title} (D-{days_left})")
                else:
                    lines.append(f"- {title}")
            return "\n".join(lines)

        if employee_name:
            return f"{employee_name}님은 이번 달 마감되는 필수 교육이 {count}건 있습니다."
        return f"이번 달 마감되는 필수 교육이 {count}건 있어요."

    def _format_q5_fallback(self, facts: PersonalizationFacts) -> str:
        """Q5 (내 평균 vs 부서/전사 평균) 폴백."""
        my_avg = facts.metrics.get("my_average", 0)
        dept_avg = facts.metrics.get("dept_average", 0)
        company_avg = facts.metrics.get("company_average", 0)
        employee_name = facts.extra.get("employee_name")

        if employee_name:
            lines = [f"{employee_name}님의 퀴즈 평균 점수는 {my_avg}점입니다."]
        else:
            lines = [f"퀴즈 평균 점수는 {my_avg}점입니다."]
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
        employee_name = facts.extra.get("employee_name")
        items = facts.items

        if not items:
            if employee_name:
                return f"{employee_name}님의 퀴즈 응시 기록이 없습니다."
            return "퀴즈 응시 기록이 없어요."

        if employee_name:
            lines = [f"{employee_name}님의 취약 과목 TOP3입니다."]
        else:
            lines = ["취약 과목 TOP3:"]
        for item in items[:3]:
            rank = item.get("rank", "")
            topic = item.get("topic", "")
            wrong_rate = item.get("wrong_rate", 0)
            lines.append(f"{rank}. {topic} (오답률 {wrong_rate}%)")

        return "\n".join(lines)

    def _format_q7_fallback(self, facts: PersonalizationFacts) -> str:
        """Q7 (특정 교육 퀴즈 결과 조회) 폴백."""
        employee_name = facts.extra.get("employee_name")
        topic_label = facts.metrics.get("topic_label", "해당 토픽")
        has_attempt = facts.metrics.get("has_attempt", False)
        average_score = facts.metrics.get("average_score", 0)
        passed_count = facts.metrics.get("passed_count", 0)
        education_count = facts.metrics.get("education_count", 0)

        if not has_attempt:
            if employee_name:
                return f"{employee_name}님은 {topic_label} 퀴즈를 아직 응시하지 않으셨습니다."
            return f"{topic_label} 퀴즈를 아직 응시하지 않았어요."

        items = facts.items
        if items:
            if employee_name:
                lines = [f"{employee_name}님의 {topic_label} 퀴즈 현황입니다. (평균 {average_score}점, {passed_count}/{education_count}건 통과)"]
            else:
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

        if employee_name:
            return f"{employee_name}님의 {topic_label} 퀴즈 평균 점수는 {average_score}점입니다."
        return f"{topic_label} 퀴즈 평균 점수: {average_score}점"

    def _format_q8_fallback(self, facts: PersonalizationFacts) -> str:
        """Q8 (내 퀴즈 점수 이력 조회) 폴백."""
        employee_name = facts.extra.get("employee_name")
        total_count = facts.metrics.get("total_count", 0)
        average_score = facts.metrics.get("average_score", 0)

        if total_count == 0:
            if employee_name:
                return f"{employee_name}님은 아직 퀴즈를 응시한 기록이 없습니다."
            return "아직 퀴즈를 응시한 기록이 없어요."

        items = facts.items
        if items:
            if employee_name:
                lines = [f"{employee_name}님의 퀴즈 점수 이력입니다. (총 {total_count}건, 평균 {average_score}점)"]
            else:
                lines = [f"퀴즈 점수 이력입니다. (총 {total_count}건, 평균 {average_score}점)"]
            for item in items[:5]:
                title = item.get("title", "")
                score = item.get("score", 0)
                date = item.get("date", "")
                passed = item.get("passed", False)
                status = "통과" if passed else "미통과"
                if date:
                    try:
                        date_str = date[5:10].replace("-", "/") if date else ""
                    except Exception:
                        date_str = date
                    lines.append(f"- {title}: {score}점 ({status}) - {date_str}")
                else:
                    lines.append(f"- {title}: {score}점 ({status})")
            return "\n".join(lines)

        if employee_name:
            return f"{employee_name}님의 퀴즈 평균 점수는 {average_score}점입니다. (총 {total_count}건 응시)"
        return f"퀴즈 평균 점수: {average_score}점 (총 {total_count}건 응시)"

    def _format_q9_fallback(self, facts: PersonalizationFacts) -> str:
        """Q9 (이번 주 할 일) 폴백."""
        count = facts.metrics.get("todo_count", 0)
        employee_name = facts.extra.get("employee_name")
        name_prefix = f"{employee_name}님은 " if employee_name else ""

        if count == 0:
            if employee_name:
                return f"{employee_name}님은 이번 주 해야 할 교육/퀴즈가 없습니다."
            return "이번 주 해야 할 교육/퀴즈가 없습니다."

        items = facts.items
        if items:
            lines = [f"{name_prefix}이번 주 할 일이 {count}건 있습니다."]
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

        if employee_name:
            return f"{employee_name}님은 이번 주 할 일이 {count}건 있습니다."
        return f"이번 주 할 일이 {count}건 있어요."

    def _format_q10_fallback(self, facts: PersonalizationFacts) -> str:
        """Q10 (내 근태 현황) 폴백."""
        employee_name = facts.extra.get("employee_name")
        work_days = facts.metrics.get("work_days", 0)
        actual_work_days = facts.metrics.get("actual_work_days", 0)
        late_count = facts.metrics.get("late_count", 0)
        early_leave_count = facts.metrics.get("early_leave_count", 0)
        absent_count = facts.metrics.get("absent_count", 0)
        remote_days = facts.metrics.get("remote_days", 0)
        overtime_hours = facts.metrics.get("overtime_hours", 0)

        if employee_name:
            lines = [f"{employee_name}님의 이번 달 근태 현황입니다. (총 근무일 {work_days}일 중 {actual_work_days}일 출근)"]
        else:
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
        employee_name = facts.extra.get("employee_name")

        if employee_name:
            if total:
                return f"{employee_name}님은 총 {total}일 중 {used}일 사용하셔서 남은 연차는 {remaining}일입니다."
            return f"{employee_name}님의 남은 연차는 {remaining}일입니다."
        else:
            if total:
                return f"총 {total}일 중 {used}일 사용하셔서 남은 연차는 {remaining}일입니다."
            return f"남은 연차는 {remaining}일입니다."

    def _format_q12_fallback(self, facts: PersonalizationFacts) -> str:
        """Q12 (연차 사용 이력) 폴백."""
        total = facts.metrics.get("total_days", 0)
        used = facts.metrics.get("used_days", 0)
        remaining = facts.metrics.get("remaining_days", 0)
        usage_count = facts.metrics.get("usage_count", 0)
        employee_name = facts.extra.get("employee_name")

        items = facts.items
        if not items:
            if used == 0:
                if employee_name:
                    return f"{employee_name}님은 올해 사용한 연차가 없습니다."
                return "올해 사용한 연차가 없습니다."
            if employee_name:
                return f"{employee_name}님은 올해 연차 {used}일을 사용하셨습니다. (잔여: {remaining}일)"
            return f"올해 연차 {used}일을 사용하셨습니다. (잔여: {remaining}일)"

        # 사용 이력이 있는 경우
        if employee_name:
            lines = [f"{employee_name}님의 올해 연차 사용 이력입니다. ({usage_count}건, 총 {used}일 사용)"]
        else:
            lines = [f"올해 연차 사용 이력입니다. ({usage_count}건, 총 {used}일 사용)"]

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

        if employee_name:
            lines.append(f"\n{employee_name}님의 잔여 연차: {remaining}일")
        else:
            lines.append(f"\n잔여 연차: {remaining}일")
        return "\n".join(lines)

    def _format_q14_fallback(self, facts: PersonalizationFacts) -> str:
        """Q14 (복지/식대 포인트) 폴백."""
        welfare = facts.metrics.get("welfare_points", 0)
        meal = facts.metrics.get("meal_allowance", 0)
        employee_name = facts.extra.get("employee_name")

        # 자연스러운 문장 형태로 응답
        if employee_name:
            if welfare and meal:
                return f"{employee_name}님의 복지 포인트 잔액은 {welfare:,}원이고, 식대 잔액은 {meal:,}원입니다."
            elif welfare:
                return f"{employee_name}님의 복지 포인트 잔액은 {welfare:,}원입니다."
            elif meal:
                return f"{employee_name}님의 식대 잔액은 {meal:,}원입니다."
        else:
            if welfare and meal:
                return f"복지 포인트 잔액은 {welfare:,}원이고, 식대 잔액은 {meal:,}원입니다."
            elif welfare:
                return f"복지 포인트 잔액은 {welfare:,}원입니다."
            elif meal:
                return f"식대 잔액은 {meal:,}원입니다."

        return "포인트 잔액을 조회할 수 없습니다."

    def _format_q15_fallback(self, facts: PersonalizationFacts) -> str:
        """Q15 (복지 포인트 사용 내역) 폴백."""
        employee_name = facts.extra.get("employee_name")
        total_granted = facts.metrics.get("total_granted", 0)
        total_used = facts.metrics.get("total_used", 0)
        remaining = facts.metrics.get("remaining", 0)
        usage_count = facts.metrics.get("usage_count", 0)

        items = facts.items
        if not items:
            if total_used == 0:
                if employee_name:
                    return f"{employee_name}님은 올해 사용한 복지 포인트가 없습니다. (잔액: {remaining:,}원)"
                return f"올해 사용한 복지 포인트가 없어요. (잔액: {remaining:,}원)"
            if employee_name:
                return f"{employee_name}님은 올해 복지 포인트 {total_used:,}원을 사용하셨습니다. (잔액: {remaining:,}원)"
            return f"올해 복지 포인트 {total_used:,}원을 사용했어요. (잔액: {remaining:,}원)"

        # 사용 내역이 있는 경우
        if employee_name:
            lines = [f"{employee_name}님의 복지 포인트 사용 내역입니다. ({usage_count}건, 총 {total_used:,}원 사용)"]
        else:
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

        if employee_name:
            lines.append(f"\n{employee_name}님의 잔여 포인트: {remaining:,}원")
        else:
            lines.append(f"\n잔여 포인트: {remaining:,}원")
        return "\n".join(lines)

    def _format_q16_fallback(self, facts: PersonalizationFacts, user_question: str = "") -> str:
        """Q16 (내 인사 정보 조회) 폴백.
        
        주의: 이메일, 전화번호, 주민번호 같은 민감한 개인정보는 제공하지 않습니다.
        민감 정보 체크는 _generate_fallback에서 이미 수행됩니다.
        
        백엔드에서 extra 필드에 요약 정보를 제공하는 경우 우선 사용합니다.
        사용자 질문을 분석하여 요청된 정보만 반환합니다.
        """
        # 사용자 질문 분석: 어떤 정보를 요청했는지 확인
        user_question_lower = user_question.lower() if user_question else ""
        requested_fields = set()
        
        # 질문에서 요청된 필드 감지
        if any(kw in user_question_lower for kw in ['직급', '직책', 'position', 'job_title']):
            requested_fields.add('position')
        if any(kw in user_question_lower for kw in ['부서', 'department', 'dept']):
            requested_fields.add('department')
        if any(kw in user_question_lower for kw in ['이메일', 'email', '메일']):
            requested_fields.add('email')
        if any(kw in user_question_lower for kw in ['전화번호', '전화', '휴대폰', '연락처', 'phone', 'mobile']):
            requested_fields.add('phone')
        if any(kw in user_question_lower for kw in ['입사일', '입사', 'hire_date', 'hire']):
            requested_fields.add('hire_date')
        if any(kw in user_question_lower for kw in ['근속', 'years_of_service', 'service']):
            requested_fields.add('years_of_service')
        
        # 요청된 필드가 없으면 모든 정보 반환 (기존 동작)
        show_all = len(requested_fields) == 0
        
        # 1) extra 필드에서 요약 정보 확인 (백엔드가 제공한 자연어 요약 우선 사용)
        extra = facts.extra or {}
        summary = extra.get("summary") or extra.get("formatted_answer") or extra.get("answer")
        
        if summary and isinstance(summary, str) and summary.strip():
            # 백엔드가 제공한 요약 정보가 있으면 우선 사용
            # 이메일/전화번호 부분 마스킹 적용
            masked_summary = mask_emails_in_text(summary.strip())
            masked_summary = mask_phones_in_text(masked_summary)
            
            # 요청된 필드만 필터링 (요청이 있는 경우)
            if not show_all:
                filtered_lines = []
                summary_lines = masked_summary.split('\n')
                for line in summary_lines:
                    line_lower = line.lower()
                    if any(
                        (field == 'position' and ('직급' in line_lower or '직책' in line_lower)) or
                        (field == 'department' and '부서' in line_lower) or
                        (field == 'email' and ('이메일' in line_lower or 'email' in line_lower)) or
                        (field == 'phone' and ('전화' in line_lower or 'phone' in line_lower or '연락처' in line_lower)) or
                        (field == 'hire_date' and '입사일' in line_lower) or
                        (field == 'years_of_service' and '근속' in line_lower)
                        for field in requested_fields
                    ):
                        filtered_lines.append(line)
                
                if filtered_lines:
                    return '\n'.join(filtered_lines)
                # 필터링 결과가 없으면 원본 반환 (백엔드 요약이 우선)
            
            return masked_summary
        
        # 2) extra 필드에 요약이 없으면 기존 방식으로 포맷팅
        items = facts.items
        metrics = facts.metrics
        
        # metrics에서 정보 추출
        employee_id = metrics.get("employee_id", "")
        name = metrics.get("name", "")
        department = metrics.get("department", "")
        position = metrics.get("position", "")  # 직급
        job_title = metrics.get("job_title", "")  # 직책
        hire_date = metrics.get("hire_date", "")
        years_of_service = metrics.get("years_of_service", 0)
        months_of_service = metrics.get("months_of_service", 0)
        
        # 사용자 이름 가져오기
        employee_name = extra.get("employee_name") or metrics.get("name", "")
        
        lines = []
        
        # 질문에 맞는 답변만 반환 (이름은 항상 포함)
        # 직급/직책 정보 (요청되었거나 모든 정보 표시 시)
        if (show_all or 'position' in requested_fields):
            if position and not is_empty_value(position):
                if employee_name:
                    lines.append(f"{employee_name}님의 직급은 {position}입니다.")
                else:
                    lines.append(f"직급: {position}")
            elif job_title and not is_empty_value(job_title):
                if employee_name:
                    lines.append(f"{employee_name}님의 직책은 {job_title}입니다.")
                else:
                    lines.append(f"직책: {job_title}")
        
        # 부서 정보 (요청되었거나 모든 정보 표시 시)
        if (show_all or 'department' in requested_fields):
            if department and not is_empty_value(department):
                if employee_name:
                    lines.append(f"{employee_name}님의 부서는 {department}입니다.")
                else:
                    lines.append(f"부서: {department}")
        
        # 입사일 정보 (요청되었거나 모든 정보 표시 시)
        if (show_all or 'hire_date' in requested_fields):
            if hire_date and not is_empty_value(hire_date):
                try:
                    # YYYY-MM-DD 형식을 YYYY년 MM월 DD일로 변환
                    parts = hire_date.split("-")
                    if len(parts) == 3:
                        formatted_date = f"{parts[0]}년 {int(parts[1])}월 {int(parts[2])}일"
                        lines.append(f"입사일: {formatted_date}")
                except Exception:
                    lines.append(f"입사일: {hire_date}")
        
        # 근속연수 (요청되었거나 모든 정보 표시 시)
        if (show_all or 'years_of_service' in requested_fields):
            if years_of_service > 0 or months_of_service > 0:
                service_years = years_of_service or 0
                service_months = months_of_service or 0
                if service_years > 0 and service_months > 0:
                    lines.append(f"근속연수: {service_years}년 {service_months}개월")
                elif service_years > 0:
                    lines.append(f"근속연수: {service_years}년")
                elif service_months > 0:
                    lines.append(f"근속연수: {service_months}개월")
        
        # metrics에서 이메일/전화번호 확인 (요청되었거나 모든 정보 표시 시, 부분 마스킹하여 제공)
        email_found = False
        phone_found = False
        
        if (show_all or 'email' in requested_fields):
            # 우선순위: metrics.email → extra.email → items에서 찾기
            email = metrics.get("email", "")
            if not email or is_empty_value(email):
                # metrics에 없으면 extra.email 확인
                email = extra.get("email", "")
            
            if email and not is_empty_value(email):
                masked_email = mask_email_partially(str(email))
                if employee_name:
                    lines.append(f"{employee_name}님의 이메일은 {masked_email}입니다.")
                else:
                    lines.append(f"이메일: {masked_email}")
                email_found = True
            elif 'email' in requested_fields:
                # metrics와 extra에 없으면 items에서 찾기
                if items:
                    for item in items:
                        label = item.get("label", "")
                        value = item.get("value", "")
                        if ('이메일' in label.lower() or 'email' in label.lower()) and value and not is_empty_value(value):
                            masked_email = mask_email_partially(str(value))
                            if employee_name:
                                lines.append(f"{employee_name}님의 이메일은 {masked_email}입니다.")
                            else:
                                lines.append(f"이메일: {masked_email}")
                            email_found = True
                            break
                if not email_found:
                    # items에서도 찾지 못했으면 안내 메시지
                    lines.append("이메일 정보를 조회할 수 없어요.")
        
        if (show_all or 'phone' in requested_fields):
            # 우선순위: metrics.phone → extra.phone → items에서 찾기
            phone = metrics.get("phone", "") or metrics.get("phone_number", "") or metrics.get("mobile", "")
            if not phone or is_empty_value(phone):
                # metrics에 없으면 extra.phone 확인
                phone = extra.get("phone", "") or extra.get("phone_number", "") or extra.get("mobile", "")
            
            if phone and not is_empty_value(phone):
                masked_phone = mask_phone_partially(str(phone))
                if employee_name:
                    lines.append(f"{employee_name}님의 전화번호는 {masked_phone}입니다.")
                else:
                    lines.append(f"전화번호: {masked_phone}")
                phone_found = True
            elif 'phone' in requested_fields:
                # metrics와 extra에 없으면 items에서 찾기
                if items:
                    for item in items:
                        label = item.get("label", "")
                        value = item.get("value", "")
                        if ('전화' in label.lower() or 'phone' in label.lower() or '연락처' in label.lower() or '휴대폰' in label.lower()) and value and not is_empty_value(value):
                            masked_phone = mask_phone_partially(str(value))
                            if employee_name:
                                lines.append(f"{employee_name}님의 전화번호는 {masked_phone}입니다.")
                            else:
                                lines.append(f"전화번호: {masked_phone}")
                            phone_found = True
                            break
                if not phone_found:
                    # items에서도 찾지 못했으면 안내 메시지
                    lines.append("전화번호 정보를 조회할 수 없어요.")
        
        # items에서 추가 정보 추출 (요청된 필드만 표시)
        if items:
            for item in items:
                label = item.get("label", "")
                value = item.get("value", "")
                
                # 빈 값 제외
                if is_empty_value(value):
                    continue
                
                # 주민번호 등은 제외
                if any(sensitive in label.lower() for sensitive in ['주민']):
                    continue
                
                # 이름은 제외 (요청하지 않은 경우)
                if label in ['이름', '성명', 'name'] and 'name' not in requested_fields and not show_all:
                    continue
                
                # 이미 표시한 정보 제외
                if label in ['직급', '직책', '부서', '입사일', '근속연수', '이메일', '전화번호']:
                    continue
                
                # 이메일/전화번호는 위에서 이미 처리했으므로 제외
                if ('이메일' in label.lower() or 'email' in label.lower()) and email_found:
                    continue
                if ('전화' in label.lower() or 'phone' in label.lower() or '연락처' in label.lower() or '휴대폰' in label.lower()) and phone_found:
                    continue
                
                # 요청된 필드만 표시 (show_all이 아닌 경우)
                if not show_all:
                    # items의 label이 요청된 필드와 매칭되는지 확인
                    label_lower = label.lower()
                    should_include = False
                    
                    if 'position' in requested_fields and ('직급' in label_lower or '직책' in label_lower):
                        should_include = True
                    elif 'department' in requested_fields and '부서' in label_lower:
                        should_include = True
                    elif 'email' in requested_fields and ('이메일' in label_lower or 'email' in label_lower):
                        should_include = True
                    elif 'phone' in requested_fields and ('전화' in label_lower or 'phone' in label_lower or '연락처' in label_lower):
                        should_include = True
                    elif 'hire_date' in requested_fields and '입사일' in label_lower:
                        should_include = True
                    elif 'years_of_service' in requested_fields and '근속' in label_lower:
                        should_include = True
                    
                    if not should_include:
                        continue
                
                # 이메일/전화번호인 경우 부분 마스킹
                if '이메일' in label.lower() or 'email' in label.lower():
                    masked_value = mask_email_partially(str(value))
                    if employee_name:
                        lines.append(f"{employee_name}님의 이메일은 {masked_value}입니다.")
                    else:
                        lines.append(f"{label}: {masked_value}")
                elif '전화' in label.lower() or 'phone' in label.lower() or '연락처' in label.lower() or '휴대폰' in label.lower():
                    masked_value = mask_phone_partially(str(value))
                    if employee_name:
                        lines.append(f"{employee_name}님의 전화번호는 {masked_value}입니다.")
                    else:
                        lines.append(f"{label}: {masked_value}")
                elif label and value:
                    lines.append(f"{label}: {value}")
        
        if lines:
            return "\n".join(lines)
        
        # 정보가 없으면 기본 메시지
        return "인사 정보를 조회할 수 없어요. 사내 HR 포털이나 인사팀에 문의해 주세요."

    def _format_q17_fallback(self, facts: PersonalizationFacts) -> str:
        """Q17 (내 팀/부서 정보 조회) 폴백."""
        metrics = facts.metrics
        items = facts.items
        
        team_name = metrics.get("team_name", "")
        department = metrics.get("department", "")
        team_leader = metrics.get("team_leader", "")
        team_size = metrics.get("team_size", 0)
        department_size = metrics.get("department_size", 0)
        
        lines = []
        
        if team_name:
            lines.append(f"팀명: {team_name}")
        if department:
            lines.append(f"부서: {department}")
        if team_leader:
            lines.append(f"팀장: {team_leader}")
        if team_size > 0:
            lines.append(f"팀 인원: {team_size}명")
        if department_size > 0:
            lines.append(f"부서 인원: {department_size}명")
        
        # items에서 추가 정보 추출
        if items:
            for item in items:
                label = item.get("label", "")
                value = item.get("value", "")
                if label and value:
                    lines.append(f"{label}: {value}")
        
        if lines:
            return "\n".join(lines)
        
        return "팀/부서 정보를 조회할 수 없어요."

    def _format_q18_fallback(self, facts: PersonalizationFacts) -> str:
        """Q18 (보안 교육 이수 현황) 폴백."""
        employee_name = facts.extra.get("employee_name")
        topic_label = facts.metrics.get("topic_label", "보안 교육")
        education_count = facts.metrics.get("education_count", 0)
        video_completed_count = facts.metrics.get("video_completed_count", 0)
        quiz_passed_count = facts.metrics.get("quiz_passed_count", 0)
        is_fully_completed = facts.metrics.get("is_fully_completed", False)

        if education_count == 0:
            if employee_name:
                return f"{employee_name}님에게 해당하는 {topic_label} 관련 교육이 없습니다."
            return f"{topic_label} 관련 교육이 없어요."

        if is_fully_completed:
            if employee_name:
                return f"{employee_name}님은 {topic_label}을 모두 완료하셨습니다! (영상 {video_completed_count}건, 퀴즈 {quiz_passed_count}건 통과)"
            return f"{topic_label}을 모두 완료했어요! (영상 {video_completed_count}건, 퀴즈 {quiz_passed_count}건 통과)"

        items = facts.items
        if items:
            if employee_name:
                lines = [f"{employee_name}님의 {topic_label} 이수 현황입니다."]
            else:
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

        if employee_name:
            return f"{employee_name}님의 {topic_label} 현황: 영상 {video_completed_count}/{education_count}건, 퀴즈 {quiz_passed_count}/{education_count}건 완료"
        return f"{topic_label}: 영상 {video_completed_count}/{education_count}건, 퀴즈 {quiz_passed_count}/{education_count}건 완료"

    def _format_q19_fallback(self, facts: PersonalizationFacts) -> str:
        """Q19 (필수 교육 전체 요약) 폴백."""
        employee_name = facts.extra.get("employee_name")
        total_count = facts.metrics.get("total_count", 0)
        completed_count = facts.metrics.get("completed_count", 0)
        in_progress_count = facts.metrics.get("in_progress_count", 0)
        not_started_count = facts.metrics.get("not_started_count", 0)

        if total_count == 0:
            if employee_name:
                return f"{employee_name}님에게 해당하는 필수 교육이 없습니다."
            return "현재 필수 교육이 없어요."

        items = facts.items
        if items:
            if employee_name:
                lines = [f"{employee_name}님의 올해 필수 교육 현황입니다. (총 {total_count}건)"]
            else:
                lines = [f"올해 필수 교육 현황입니다. (총 {total_count}건)"]
            lines.append(f"- 완료: {completed_count}건")
            lines.append(f"- 진행중: {in_progress_count}건")
            lines.append(f"- 미시작: {not_started_count}건")

            # 미완료 항목 표시
            incomplete_items = [item for item in items if not item.get("is_completed", False)]
            if incomplete_items:
                lines.append("")
                lines.append("[미완료 교육]")
                for item in incomplete_items[:5]:
                    title = item.get("title", "")
                    deadline = item.get("deadline", "")
                    progress = item.get("progress_percent", 0)
                    if deadline:
                        lines.append(f"- {title} ({progress}%) - 마감: {deadline}")
                    else:
                        lines.append(f"- {title} ({progress}%)")
            return "\n".join(lines)

        if employee_name:
            return f"{employee_name}님은 총 {total_count}건의 필수 교육 중 {completed_count}건을 완료하셨습니다."
        return f"총 {total_count}건의 필수 교육 중 {completed_count}건 완료"

    def _format_q20_fallback(self, facts: PersonalizationFacts) -> str:
        """Q20 (올해 HR 할 일) 폴백."""
        count = facts.metrics.get("todo_count", 0)
        employee_name = facts.extra.get("employee_name")
        name_prefix = f"{employee_name}님은 " if employee_name else ""

        if count == 0:
            if employee_name:
                return f"{employee_name}님은 올해 HR 할 일을 모두 완료하셨습니다."
            return "올해 HR 할 일을 모두 완료하셨습니다."

        items = facts.items
        if items:
            lines = [f"{name_prefix}올해 미완료 HR 항목이 {count}건 있습니다."]
            for item in items[:5]:
                item_type = item.get("type", "")
                title = item.get("title", "")
                deadline = item.get("deadline", "")
                if deadline:
                    lines.append(f"- [{item_type}] {title} (마감: {deadline})")
                else:
                    lines.append(f"- [{item_type}] {title}")
            return "\n".join(lines)

        return f"{name_prefix}올해 미완료 HR 항목이 {count}건 있습니다."

    def _format_q4_fallback(self, facts: PersonalizationFacts) -> str:
        """Q4 (특정 교육 진도율/시청률 조회) 폴백."""
        employee_name = facts.extra.get("employee_name")
        education_title = facts.metrics.get("education_title", "해당 교육")
        progress_percent = facts.metrics.get("progress_percent", 0)
        is_completed = facts.metrics.get("is_completed", False)
        video_duration = facts.metrics.get("video_duration", 0)
        watched_duration = facts.metrics.get("watched_duration", 0)

        if is_completed:
            if employee_name:
                return f"{employee_name}님은 '{education_title}' 교육을 이미 완료하셨습니다. (100%)"
            return f"'{education_title}' 교육을 이미 완료했어요. (100%)"

        if employee_name:
            if video_duration and watched_duration:
                return f"{employee_name}님은 '{education_title}' 교육을 {progress_percent}% 시청하셨습니다. ({watched_duration}분/{video_duration}분)"
            return f"{employee_name}님은 '{education_title}' 교육을 {progress_percent}% 시청하셨습니다."
        else:
            if video_duration and watched_duration:
                return f"'{education_title}' 교육 진도율: {progress_percent}% ({watched_duration}분/{video_duration}분)"
            return f"'{education_title}' 교육 진도율: {progress_percent}%"

    def _format_q13_fallback(self, facts: PersonalizationFacts) -> str:
        """Q13 (급여 명세서 요약) 폴백."""
        employee_name = facts.extra.get("employee_name")
        year_month = facts.metrics.get("year_month", "")
        total_pay = facts.metrics.get("total_pay", 0)
        base_salary = facts.metrics.get("base_salary", 0)
        deductions = facts.metrics.get("deductions", 0)
        net_pay = facts.metrics.get("net_pay", 0)

        if not total_pay and not net_pay:
            if employee_name:
                return f"{employee_name}님의 급여 명세서를 조회할 수 없습니다."
            return "급여 명세서를 조회할 수 없어요."

        if employee_name:
            lines = [f"{employee_name}님의 {year_month} 급여 명세서입니다."]
        else:
            lines = [f"{year_month} 급여 명세서:"]

        if base_salary:
            lines.append(f"- 기본급: {base_salary:,}원")
        if total_pay:
            lines.append(f"- 총 지급액: {total_pay:,}원")
        if deductions:
            lines.append(f"- 공제액: {deductions:,}원")
        if net_pay:
            lines.append(f"- 실수령액: {net_pay:,}원")

        return "\n".join(lines)


    def _format_q17_fallback(self, facts: PersonalizationFacts) -> str:
        """Q17 (내 팀/부서 정보 조회) 폴백."""
        employee_name = facts.extra.get("employee_name")
        department = facts.metrics.get("department", "")
        team_size = facts.metrics.get("team_size", 0)
        team_leader = facts.metrics.get("team_leader", "")

        if not department:
            if employee_name:
                return f"{employee_name}님의 팀/부서 정보를 조회할 수 없습니다."
            return "팀/부서 정보를 조회할 수 없어요."

        if employee_name:
            lines = [f"{employee_name}님의 팀/부서 정보입니다."]
        else:
            lines = ["팀/부서 정보:"]

        lines.append(f"- 부서명: {department}")
        if team_size:
            lines.append(f"- 팀 인원: {team_size}명")
        if team_leader:
            lines.append(f"- 팀장: {team_leader}")

        # 팀원 목록이 있는 경우
        items = facts.items
        if items:
            lines.append("")
            lines.append("[팀원 목록]")
            for item in items[:10]:
                name = item.get("name", "")
                position = item.get("position", "")
                if position:
                    lines.append(f"- {name} ({position})")
                else:
                    lines.append(f"- {name}")

        return "\n".join(lines)
