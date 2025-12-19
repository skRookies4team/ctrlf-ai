"""
Guardrail Service Module

역할(UserRole) × 도메인(Domain) × 의도(IntentType)에 따른 가드레일을 적용합니다.

Phase 10에서 정의된 가드레일:
- 직원(EMPLOYEE): INCIDENT_REPORT 시 개인정보 주의 안내, EDU_STATUS 시 타인 정보 조회 제한
- 관리자(ADMIN): INCIDENT 답변에서 실명 일반화, 과도한 판단/추측 금지
- 신고관리자(INCIDENT_MANAGER): 사건 참여자 실명/사번 노출 금지, 징계 추천 금지

가드레일 적용 방법:
1. system_prompt에 역할별 지시사항 추가 (prepend)
2. 답변 앞에 필수 안내 문구 추가 (prefix)

Usage:
    guardrail = GuardrailService()
    system_prompt = guardrail.get_system_prompt_prefix(user_role, domain, intent)
    answer_prefix = guardrail.get_answer_prefix(user_role, domain, intent)
"""

from typing import Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.intent import Domain, IntentType, UserRole

logger = get_logger(__name__)


# =============================================================================
# 역할별 시스템 프롬프트 가드레일
# =============================================================================

# 직원(EMPLOYEE)용 시스템 프롬프트 가드레일
EMPLOYEE_GUARDRAILS = {
    # INCIDENT_REPORT: 개인정보 주의 안내
    IntentType.INCIDENT_REPORT: """
[가드레일 - 직원 신고]
- 사용자가 사고/위반을 신고하려고 합니다.
- 구체적인 개인정보(이름, 사번, 연락처, 주민번호 등)를 채팅창에 입력하지 않도록 안내하세요.
- 공식 신고 채널(신고 시스템, 익명 신고 페이지 등)을 통해 상세 내용을 제출하도록 유도하세요.
- 신고자 보호를 위해 신고 내용이 비밀 유지됨을 안내하세요.
""",
    # EDU_STATUS: 타인 정보 조회 제한
    IntentType.EDU_STATUS: """
[가드레일 - 직원 교육 현황]
- 사용자가 교육 현황을 문의하고 있습니다.
- 본인의 교육 이수 현황만 안내할 수 있습니다.
- 타인(동료, 부하직원)의 교육 현황이나 미이수자 명단은 직접 제공하지 마세요.
- 타인 정보가 필요한 경우 "담당 관리자 또는 교육 담당 부서에 문의하세요"라고 안내하세요.
""",
    # INCIDENT_QA: 사고 관련 일반 문의
    IntentType.INCIDENT_QA: """
[가드레일 - 직원 사고 문의]
- 사고/위반 관련 일반적인 질문에 답변합니다.
- 특정 사건의 관련자, 피해자, 신고자에 대한 정보는 제공하지 마세요.
- 처리 절차와 일반적인 규정만 안내하세요.
""",
}

# 관리자(ADMIN)용 시스템 프롬프트 가드레일
ADMIN_GUARDRAILS = {
    # INCIDENT 도메인 전체: 실명 일반화, 판단/추측 금지
    "INCIDENT_DOMAIN": """
[가드레일 - 관리자 사고 정보]
- 사고/위반 관련 정보를 제공할 때 다음 원칙을 준수하세요:
  1. 실제 이름/사번/부서명 대신 "관련자", "해당 직원", "A부서" 등 일반화된 표현을 사용하세요.
  2. "심각한 과실", "고의적 위반" 등 판단/추측성 표현을 피하세요.
  3. 사실 관계만 전달하고, 책임 소재나 징계 수준에 대한 의견을 제시하지 마세요.
  4. 통계나 현황 데이터는 개인을 특정할 수 없는 수준으로만 제공하세요.
""",
    # EDU 도메인: 통계 중심
    "EDU_DOMAIN": """
[가드레일 - 관리자 교육 정보]
- 교육 현황을 제공할 때 다음 원칙을 준수하세요:
  1. 부서/팀 단위 통계는 제공 가능하지만, 개인별 상세 이수 현황은 제한적으로 안내하세요.
  2. 미이수자 명단을 직접 나열하지 말고, "N명 미이수" 형태로 안내하세요.
  3. 개인별 상세 정보는 교육 관리 시스템에서 직접 확인하도록 유도하세요.
""",
}

# 신고관리자(INCIDENT_MANAGER)용 시스템 프롬프트 가드레일
INCIDENT_MANAGER_GUARDRAILS = {
    # INCIDENT 도메인 전체: 실명 노출 금지, 징계 추천 금지
    "INCIDENT_DOMAIN": """
[가드레일 - 신고관리자 사고 처리]
- 사건 처리 시 다음 원칙을 반드시 준수하세요:
  1. 사건 참여자(신고자, 피신고자, 목격자)의 실명/사번/연락처를 절대 생성하거나 노출하지 마세요.
  2. "OOO 직원", "신고자 A", "피신고자 B" 등 익명화된 표현만 사용하세요.
  3. 징계/인사 조치를 구체적으로 추천하지 마세요 (예: "경고 처분이 적절합니다" 금지).
  4. "내부 규정에 따라 담당 부서와 협의하세요"와 같이 절차 안내로 마무리하세요.
  5. 유사 사례 참조 시에도 관련자 정보는 완전히 익명화하세요.
""",
    # INCIDENT_REPORT: 신고 접수 시
    IntentType.INCIDENT_REPORT: """
[가드레일 - 신고관리자 신고 접수]
- 신고 내용을 접수/확인할 때:
  1. 신고자 보호가 최우선입니다. 신고자 정보를 절대 노출하지 마세요.
  2. 접수 확인 및 다음 절차 안내에 집중하세요.
  3. 초기 판단이나 결과 예측을 피하세요.
""",
}


# =============================================================================
# 역할별 답변 앞에 붙는 필수 안내 문구 (Prefix)
# =============================================================================

# 직원 INCIDENT_REPORT 시 필수 안내
EMPLOYEE_INCIDENT_REPORT_PREFIX = """⚠️ **신고 시 주의사항**
구체적인 개인정보(이름, 사번, 연락처 등)나 회사 기밀은 여기 채팅창에 적지 말고, **공식 신고 채널**에서만 입력해 주세요.

"""

# 직원 EDU_STATUS 시 안내
EMPLOYEE_EDU_STATUS_PREFIX = """📋 **교육 현황 안내**
본인의 교육 이수 현황을 안내해 드립니다. 타인의 현황이나 부서별 미이수자 명단은 담당 관리자에게 문의해 주세요.

"""


class GuardrailService:
    """
    역할 × 도메인 × 의도에 따른 가드레일을 적용하는 서비스.

    가드레일은 두 가지 방식으로 적용됩니다:
    1. System Prompt Prefix: LLM 호출 시 system prompt에 추가되는 지시사항
    2. Answer Prefix: 최종 답변 앞에 붙는 필수 안내 문구

    Attributes:
        settings: 애플리케이션 설정 (INCIDENT_REPORT_URL 등)
    """

    def __init__(self) -> None:
        """GuardrailService 초기화."""
        self.settings = get_settings()

    def get_system_prompt_prefix(
        self,
        user_role: UserRole,
        domain: str,
        intent: IntentType,
    ) -> str:
        """
        역할/도메인/의도에 맞는 system prompt 가드레일을 반환합니다.

        Args:
            user_role: 사용자 역할 (EMPLOYEE, ADMIN, INCIDENT_MANAGER)
            domain: 도메인 (POLICY, INCIDENT, EDU)
            intent: 분류된 의도

        Returns:
            System prompt에 prepend할 가드레일 텍스트.
            해당하는 가드레일이 없으면 빈 문자열.
        """
        prefix_parts: list[str] = []

        if user_role == UserRole.EMPLOYEE:
            # 직원용 가드레일
            if intent in EMPLOYEE_GUARDRAILS:
                prefix_parts.append(EMPLOYEE_GUARDRAILS[intent])

        elif user_role == UserRole.ADMIN:
            # 관리자용 가드레일
            if domain == Domain.INCIDENT.value or domain == "INCIDENT":
                prefix_parts.append(ADMIN_GUARDRAILS["INCIDENT_DOMAIN"])
            elif domain == Domain.EDU.value or domain == "EDU":
                prefix_parts.append(ADMIN_GUARDRAILS["EDU_DOMAIN"])

        elif user_role == UserRole.INCIDENT_MANAGER:
            # 신고관리자용 가드레일
            if domain == Domain.INCIDENT.value or domain == "INCIDENT":
                prefix_parts.append(INCIDENT_MANAGER_GUARDRAILS["INCIDENT_DOMAIN"])
            if intent == IntentType.INCIDENT_REPORT:
                prefix_parts.append(INCIDENT_MANAGER_GUARDRAILS[IntentType.INCIDENT_REPORT])

        if prefix_parts:
            result = "\n".join(prefix_parts)
            logger.debug(
                f"Guardrail applied: role={user_role.value}, domain={domain}, "
                f"intent={intent.value}, length={len(result)}"
            )
            return result

        return ""

    def get_answer_prefix(
        self,
        user_role: UserRole,
        domain: str,
        intent: IntentType,
    ) -> str:
        """
        역할/도메인/의도에 맞는 답변 앞 안내 문구를 반환합니다.

        Args:
            user_role: 사용자 역할
            domain: 도메인
            intent: 분류된 의도

        Returns:
            답변 앞에 붙일 안내 문구.
            해당하는 안내가 없으면 빈 문자열.
        """
        if user_role == UserRole.EMPLOYEE:
            if intent == IntentType.INCIDENT_REPORT:
                # 신고 채널 URL이 설정되어 있으면 링크 추가
                prefix = EMPLOYEE_INCIDENT_REPORT_PREFIX
                incident_url = getattr(self.settings, 'INCIDENT_REPORT_URL', None)
                if incident_url:
                    prefix = prefix.rstrip() + f"\n👉 신고 바로가기: {incident_url}\n\n"
                return prefix

            elif intent == IntentType.EDU_STATUS:
                return EMPLOYEE_EDU_STATUS_PREFIX

        # 관리자/신고관리자는 답변 prefix 없이 system prompt 가드레일만 적용
        return ""

    def apply_to_answer(
        self,
        answer: str,
        user_role: UserRole,
        domain: str,
        intent: IntentType,
    ) -> str:
        """
        답변에 가드레일 prefix를 적용합니다.

        Args:
            answer: 원본 답변
            user_role: 사용자 역할
            domain: 도메인
            intent: 분류된 의도

        Returns:
            가드레일 prefix가 적용된 답변
        """
        prefix = self.get_answer_prefix(user_role, domain, intent)
        if prefix:
            return prefix + answer
        return answer
