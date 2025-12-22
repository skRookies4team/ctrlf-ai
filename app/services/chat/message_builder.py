"""
LLM 메시지 빌더 (LLM Message Builder)

ChatService에서 사용하는 LLM 프롬프트 구성 로직을 담당합니다.

Phase 2 리팩토링:
- 프롬프트 상수 정의 (SYSTEM_PROMPT_*)
- ChatService._build_llm_messages → MessageBuilder.build_rag_messages
- ChatService._build_mixed_llm_messages → MessageBuilder.build_mixed_messages
- ChatService._build_backend_api_llm_messages → MessageBuilder.build_backend_api_messages
- ChatService._format_sources_for_prompt → MessageBuilder.format_sources_for_prompt
"""

from typing import Dict, List, Optional, TYPE_CHECKING

from app.models.chat import ChatSource, ChatRequest
from app.services.backend_context_formatter import BackendContextFormatter
from app.services.guardrail_service import GuardrailService

if TYPE_CHECKING:
    from app.models.intent import IntentType, UserRole


# =============================================================================
# System Prompt 상수
# =============================================================================

# System prompt template for LLM (RAG context가 있는 경우)
# Phase 13: 조항 근거 명시 지침 추가
SYSTEM_PROMPT_WITH_RAG = """당신은 회사 내부 정보보호 및 사규를 안내하는 AI 어시스턴트입니다.
아래의 참고 문서 목록을 바탕으로 사용자의 질문에 한국어로 정확하고 친절하게 답변해 주세요.

답변 시 반드시 출처 문서와 해당 조항을 인용해 주세요.
가능하다면 답변 마지막에 "[참고 근거]" 섹션을 추가해서:
- 문서명
- 조문/항 번호 또는 위치 (예: 제10조 제2항, 제3장 > 제5조)
를 bullet으로 정리해 주세요.

예시:
[참고 근거]
- 연차휴가 관리 규정 제10조 (연차 이월) 제2항
- 인사관리 규정 제3장 근태관리 제5조 (지각/조퇴 처리 기준)

만약 참고 문서에서 답을 찾을 수 없다면, 솔직하게 "해당 내용은 참고 문서에서 찾을 수 없습니다"라고 말해 주세요.
추측이나 거짓 정보를 제공하지 마세요.
"""

# System prompt template for LLM (RAG context가 없는 경우)
SYSTEM_PROMPT_NO_RAG = """당신은 회사 내부 정보보호 및 사규를 안내하는 AI 어시스턴트입니다.
현재 관련 문서를 찾지 못했습니다. 일반적인 지식을 바탕으로 답변하되,
구체적인 사내 규정이나 정책에 대해서는 "관련 문서를 찾지 못했으므로, 담당 부서에 직접 문의해 주세요"라고 안내해 주세요.
추측이나 거짓 정보를 제공하지 마세요.
"""

# RAG 검색 결과가 없을 때 사용자에게 안내할 메시지
NO_RAG_RESULTS_NOTICE = (
    "\n\n※ 참고: 관련 문서를 찾지 못하여 일반적인 답변을 드립니다. "
    "정확한 정보는 담당 부서에 확인해 주세요."
)

# Phase 11: MIXED_BACKEND_RAG용 LLM 시스템 프롬프트
SYSTEM_PROMPT_MIXED_BACKEND_RAG = """당신은 회사 내부 정보보호 및 사규를 안내하는 AI 어시스턴트입니다.

아래에 두 가지 정보가 제공됩니다:
1. [정책/규정 근거]: 관련 사내 규정/정책 문서
2. [실제 현황/통계]: 백엔드 시스템에서 조회한 실제 데이터

두 정보를 모두 고려하여 질문에 답변해 주세요:
1) 정책상 어떻게 규정되어 있는지
2) 현재 조직/부서의 실제 상황이 어떠한지
3) 다음에 취할 수 있는 일반적인 조치나 권고

주의사항:
- 구체적인 인사/징계 조치를 단정적으로 말하지 마세요.
- 개인 식별이 가능한 정보(실명, 사번 등)는 언급하지 마세요.
- 추측이나 거짓 정보를 제공하지 마세요.
"""

# Phase 11: BACKEND_API용 LLM 시스템 프롬프트
SYSTEM_PROMPT_BACKEND_API = """당신은 회사 내부 정보보호 및 사규를 안내하는 AI 어시스턴트입니다.

아래에 백엔드 시스템에서 조회한 실제 데이터가 제공됩니다.
이 데이터를 바탕으로 사용자의 질문에 친절하고 정확하게 답변해 주세요.

주의사항:
- 제공된 데이터 범위 내에서만 답변하세요.
- 추측이나 거짓 정보를 제공하지 마세요.
- 개인 식별이 가능한 정보는 언급하지 마세요.
"""


class MessageBuilder:
    """
    LLM 메시지를 구성하는 빌더 클래스.

    역할별 가드레일과 프롬프트 상수를 사용하여 LLM 입력 메시지를 구성합니다.

    Attributes:
        _guardrail: GuardrailService 인스턴스
        _context_formatter: BackendContextFormatter 인스턴스
    """

    def __init__(
        self,
        guardrail_service: GuardrailService,
        context_formatter: Optional[BackendContextFormatter] = None,
    ) -> None:
        """
        MessageBuilder 초기화.

        Args:
            guardrail_service: GuardrailService 인스턴스
            context_formatter: BackendContextFormatter 인스턴스 (선택적)
        """
        self._guardrail = guardrail_service
        self._context_formatter = context_formatter or BackendContextFormatter()

    def build_rag_messages(
        self,
        user_query: str,
        sources: List[ChatSource],
        req: ChatRequest,
        rag_attempted: bool = False,
        user_role: Optional["UserRole"] = None,
        domain: Optional[str] = None,
        intent: Optional["IntentType"] = None,
    ) -> List[Dict[str, str]]:
        """
        RAG 기반 LLM 메시지를 구성합니다.

        Args:
            user_query: 사용자 질문
            sources: RAG 검색 결과
            req: 원본 요청
            rag_attempted: RAG 시도 여부
            user_role: 사용자 역할
            domain: 도메인
            intent: 의도

        Returns:
            List[Dict[str, str]]: LLM 메시지 목록
        """
        from app.models.intent import IntentType, UserRole

        messages: List[Dict[str, str]] = []

        # Phase 10: 역할별 가드레일을 system prompt 앞에 추가
        guardrail_prefix = ""
        if user_role and domain and intent:
            guardrail_prefix = self._guardrail.get_system_prompt_prefix(
                user_role=user_role,
                domain=domain,
                intent=intent,
            )

        # System message - RAG context 유무에 따라 다른 프롬프트 사용
        if sources:
            # RAG 결과가 있는 경우
            system_content = SYSTEM_PROMPT_WITH_RAG
            context_text = self.format_sources_for_prompt(sources)
            system_content += f"\n\n참고 문서:\n{context_text}"
        elif rag_attempted:
            # RAG 시도했지만 결과 없는 경우
            system_content = SYSTEM_PROMPT_NO_RAG
        else:
            # RAG 시도하지 않은 경우 (LLM_ONLY 등)
            system_content = SYSTEM_PROMPT_WITH_RAG
            system_content += "\n\n참고 문서: (검색 대상 아님)"

        # Combine guardrail prefix with system content
        if guardrail_prefix:
            system_content = guardrail_prefix + "\n\n" + system_content

        messages.append({
            "role": "system",
            "content": system_content,
        })

        messages.append({
            "role": "user",
            "content": user_query,
        })

        return messages

    def build_mixed_messages(
        self,
        user_query: str,
        sources: List[ChatSource],
        backend_context: str,
        domain: str,
        user_role: "UserRole",
        intent: "IntentType",
    ) -> List[Dict[str, str]]:
        """
        MIXED_BACKEND_RAG용 LLM 메시지를 구성합니다.

        Args:
            user_query: 사용자 질문
            sources: RAG 검색 결과
            backend_context: 백엔드 데이터 포맷팅 결과
            domain: 도메인
            user_role: 사용자 역할
            intent: 의도

        Returns:
            List[Dict[str, str]]: LLM 메시지 목록
        """
        messages: List[Dict[str, str]] = []

        # 가드레일 prefix
        guardrail_prefix = self._guardrail.get_system_prompt_prefix(
            user_role=user_role,
            domain=domain,
            intent=intent,
        )

        # RAG 컨텍스트 포맷팅
        rag_context = ""
        if sources:
            rag_context = self.format_sources_for_prompt(sources)

        # RAG + Backend 통합 컨텍스트
        mixed_context = self._context_formatter.format_mixed_context(
            rag_context=rag_context,
            backend_context=backend_context,
            domain=domain,
        )

        # 시스템 프롬프트 구성
        system_content = SYSTEM_PROMPT_MIXED_BACKEND_RAG
        system_content += f"\n\n{mixed_context}"

        if guardrail_prefix:
            system_content = guardrail_prefix + "\n\n" + system_content

        messages.append({
            "role": "system",
            "content": system_content,
        })

        messages.append({
            "role": "user",
            "content": user_query,
        })

        return messages

    def build_backend_api_messages(
        self,
        user_query: str,
        backend_context: str,
        user_role: "UserRole",
        domain: str,
        intent: "IntentType",
    ) -> List[Dict[str, str]]:
        """
        BACKEND_API용 LLM 메시지를 구성합니다.

        Args:
            user_query: 사용자 질문
            backend_context: 백엔드 데이터 포맷팅 결과
            user_role: 사용자 역할
            domain: 도메인
            intent: 의도

        Returns:
            List[Dict[str, str]]: LLM 메시지 목록
        """
        messages: List[Dict[str, str]] = []

        # 가드레일 prefix
        guardrail_prefix = self._guardrail.get_system_prompt_prefix(
            user_role=user_role,
            domain=domain,
            intent=intent,
        )

        # 시스템 프롬프트 구성
        system_content = SYSTEM_PROMPT_BACKEND_API

        if backend_context:
            system_content += f"\n\n[조회된 데이터]\n{backend_context}"
        else:
            system_content += "\n\n[조회된 데이터]\n(데이터를 조회하지 못했습니다)"

        if guardrail_prefix:
            system_content = guardrail_prefix + "\n\n" + system_content

        messages.append({
            "role": "system",
            "content": system_content,
        })

        messages.append({
            "role": "user",
            "content": user_query,
        })

        return messages

    def format_sources_for_prompt(self, sources: List[ChatSource]) -> str:
        """
        RAG 소스를 LLM 프롬프트용 텍스트로 포맷팅합니다.

        Phase 13: 조항 정보(article_label, article_path) 포함하도록 확장.

        Args:
            sources: RAG 검색 결과

        Returns:
            str: 포맷팅된 소스 텍스트
        """
        lines: List[str] = []

        for i, source in enumerate(sources, start=1):
            lines.append(f"[근거 {i}]")

            # 문서 제목
            doc_line = f"- 문서: {source.title}"
            if source.page:
                doc_line += f" (p.{source.page})"
            lines.append(doc_line)

            # Phase 13: 조항 위치 정보
            if source.article_label or source.article_path:
                location = source.article_path or source.article_label
                lines.append(f"- 위치: {location}")

            # 관련도 점수 (디버깅용, 선택적)
            if source.score:
                lines.append(f"- 관련도: {source.score:.2f}")

            # 발췌 내용
            if source.snippet:
                snippet = source.snippet[:400]
                if len(source.snippet) > 400:
                    snippet += "..."
                lines.append(f"- 내용: {snippet}")

            lines.append("")  # 빈 줄로 구분

        return "\n".join(lines).strip()
