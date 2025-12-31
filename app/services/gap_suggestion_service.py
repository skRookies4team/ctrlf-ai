"""
Gap Suggestion Service (Phase 15)

RAG Gap 질문들을 분석하여 사규/교육 보완 제안을 생성하는 서비스.
LLM을 사용하여 질문 패턴을 분석하고 보완 제안을 생성합니다.
"""

import json
import re
import uuid
from typing import List, Optional

from app.clients.llm_client import LLMClient
from app.core.logging import get_logger
from app.models.gap_suggestion import (
    GapQuestion,
    GapSuggestionItem,
    GapSuggestionRequest,
    GapSuggestionResponse,
    LLMSuggestionResponse,
)

logger = get_logger(__name__)


# =============================================================================
# LLM 프롬프트 템플릿
# =============================================================================

SYSTEM_PROMPT = """당신은 기업 내부 정보보호/인사 사규를 설계하는 기획자를 돕는 AI입니다.

입력으로는 최근 일정 기간 동안 직원/관리자들이 자주 물어봤지만,
RAG 검색에서 관련 사규/교육 문서를 찾지 못한 질문 목록이 주어집니다.

당신의 역할:
1) 이 질문들을 보고, 기존 사규/교육에서 어떤 부분이 부족한지 요약합니다.
2) 새로 추가하거나 보완하면 좋을 "사규/교육 항목" 후보를 만듭니다.
3) 각 항목에 대해:
   - 제목 (title)
   - 왜 필요한지 설명 (description)
   - 어떤 질문들과 관련 있는지 (related_question_ids - question_id 목록)
   - 우선순위 (priority: HIGH/MEDIUM/LOW)
를 구조화해서 제안해 주세요.

중요:
- 반드시 아래 JSON 형식으로만 응답해 주세요.
- 다른 텍스트나 설명 없이 JSON만 출력하세요.
- 유사한 질문들은 하나의 제안으로 묶어주세요.
- 우선순위는 질문 횟수(asked_count)와 중요도를 고려하여 결정하세요.

응답 JSON 형식:
{
  "summary": "전체 분석 요약 (1-2문장)",
  "suggestions": [
    {
      "title": "제안 제목",
      "description": "왜 필요한지, 어떤 내용을 추가해야 하는지 설명",
      "related_question_ids": ["question_id1", "question_id2"],
      "priority": "HIGH"
    }
  ]
}
"""

USER_PROMPT_TEMPLATE = """다음은 RAG 검색에서 관련 문서를 찾지 못한 질문 목록입니다.
이 질문들을 분석하여 사규/교육 보완 제안을 만들어 주세요.

도메인: {domain}
분석 대상 질문 수: {question_count}개

질문 목록:
{questions_text}

위 질문들을 분석하여 JSON 형식으로 보완 제안을 만들어 주세요.
"""

EMPTY_QUESTIONS_RESPONSE = GapSuggestionResponse(
    summary="분석할 RAG Gap 질문이 없습니다. 현재 사규/교육 문서가 사용자 질문을 잘 커버하고 있는 것으로 보입니다.",
    suggestions=[],
)


class GapSuggestionService:
    """
    RAG Gap 보완 제안 서비스.

    백엔드에서 수집한 RAG Gap 질문들을 LLM에게 전달하여
    사규/교육 보완 제안을 생성합니다.

    Attributes:
        _llm: LLM 클라이언트

    Example:
        service = GapSuggestionService()
        response = await service.generate_suggestions(request)
    """

    def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
        """
        GapSuggestionService 초기화.

        Args:
            llm_client: LLM 클라이언트. None이면 새로 생성.
        """
        self._llm = llm_client or LLMClient()

    async def generate_suggestions(
        self,
        request: GapSuggestionRequest,
    ) -> GapSuggestionResponse:
        """
        RAG Gap 질문들을 분석하여 보완 제안을 생성합니다.

        Args:
            request: RAG Gap 보완 제안 요청

        Returns:
            GapSuggestionResponse: 분석 요약 및 보완 제안 목록
        """
        # 질문이 없으면 빈 응답
        if not request.questions:
            logger.info("No questions provided, returning empty response")
            return EMPTY_QUESTIONS_RESPONSE

        logger.info(
            f"Generating gap suggestions for {len(request.questions)} questions, "
            f"domain={request.domain}"
        )

        # LLM 메시지 구성
        messages = self._build_llm_messages(request)

        # LLM 호출
        try:
            llm_response = await self._llm.generate_chat_completion(
                messages=messages,
                model=None,  # 기본 모델 사용
                temperature=0.3,  # 일관된 응답을 위해 낮은 temperature
                max_tokens=2048,
            )

            logger.debug(f"LLM response received: len={len(llm_response)}")

            # 응답 파싱
            return self._parse_llm_response(llm_response, request.questions)

        except Exception as e:
            logger.exception(f"Failed to generate suggestions: {e}")
            return self._create_fallback_response(request.questions, str(e))

    def _build_llm_messages(
        self,
        request: GapSuggestionRequest,
    ) -> List[dict]:
        """
        LLM 호출용 메시지를 구성합니다.

        Args:
            request: 요청 정보

        Returns:
            LLM 메시지 목록
        """
        # 질문 목록을 텍스트로 변환
        questions_text = self._format_questions_for_prompt(request.questions)

        # 도메인 정보
        domain = request.domain or "전체"

        # User 메시지 생성
        user_message = USER_PROMPT_TEMPLATE.format(
            domain=domain,
            question_count=len(request.questions),
            questions_text=questions_text,
        )

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

    def _format_questions_for_prompt(
        self,
        questions: List[GapQuestion],
    ) -> str:
        """
        질문 목록을 LLM 프롬프트용 텍스트로 포맷합니다.

        Args:
            questions: 질문 목록

        Returns:
            포맷된 텍스트
        """
        lines = []
        for i, q in enumerate(questions, start=1):
            asked_info = f" (질문 횟수: {q.asked_count}회)" if q.asked_count else ""
            lines.append(
                f"{i}. [ID: {q.question_id}] {q.text}\n"
                f"   - 역할: {q.user_role}, 의도: {q.intent}, 도메인: {q.domain}{asked_info}"
            )
        return "\n".join(lines)

    def _parse_llm_response(
        self,
        llm_response: str,
        questions: List[GapQuestion],
    ) -> GapSuggestionResponse:
        """
        LLM 응답을 GapSuggestionResponse로 파싱합니다.

        Args:
            llm_response: LLM 응답 텍스트
            questions: 원본 질문 목록 (fallback용)

        Returns:
            GapSuggestionResponse
        """
        try:
            # JSON 추출 시도
            json_str = self._extract_json_from_response(llm_response)

            if json_str:
                data = json.loads(json_str)

                # LLMSuggestionResponse로 파싱
                llm_result = LLMSuggestionResponse(**data)

                # GapSuggestionResponse로 변환
                suggestions = []
                for i, item in enumerate(llm_result.suggestions):
                    suggestions.append(
                        GapSuggestionItem(
                            id=f"SUG-{i+1:03d}",
                            title=item.title,
                            description=item.description,
                            related_question_ids=item.related_question_ids,
                            priority=self._normalize_priority(item.priority),
                        )
                    )

                return GapSuggestionResponse(
                    summary=llm_result.summary,
                    suggestions=suggestions,
                )

        except json.JSONDecodeError as e:
            logger.warning(f"JSON parsing failed: {e}")
        except Exception as e:
            logger.warning(f"Response parsing failed: {e}")

        # 파싱 실패 시 fallback
        return self._create_fallback_response(questions, "LLM 응답 파싱 실패")

    def _extract_json_from_response(self, response: str) -> Optional[str]:
        """
        LLM 응답에서 JSON 부분을 추출합니다.

        Args:
            response: LLM 응답 텍스트

        Returns:
            JSON 문자열 또는 None
        """
        # 이미 JSON인 경우
        response = response.strip()
        if response.startswith("{") and response.endswith("}"):
            return response

        # ```json ... ``` 블록 추출
        json_block_pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
        match = re.search(json_block_pattern, response, re.DOTALL)
        if match:
            return match.group(1)

        # { ... } 패턴 추출
        brace_pattern = r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"
        matches = re.findall(brace_pattern, response, re.DOTALL)
        if matches:
            # 가장 긴 JSON 선택
            return max(matches, key=len)

        return None

    def _normalize_priority(self, priority: Optional[str]) -> Optional[str]:
        """
        우선순위 값을 정규화합니다.

        Args:
            priority: LLM이 반환한 우선순위

        Returns:
            정규화된 우선순위 (HIGH, MEDIUM, LOW 중 하나 또는 None)
        """
        if not priority:
            return None

        priority_upper = priority.upper().strip()

        if priority_upper in ("HIGH", "높음", "H"):
            return "HIGH"
        elif priority_upper in ("MEDIUM", "중간", "M", "MED"):
            return "MEDIUM"
        elif priority_upper in ("LOW", "낮음", "L"):
            return "LOW"

        return None

    def _create_fallback_response(
        self,
        questions: List[GapQuestion],
        error_reason: str,
    ) -> GapSuggestionResponse:
        """
        LLM 호출/파싱 실패 시 fallback 응답을 생성합니다.

        Args:
            questions: 원본 질문 목록
            error_reason: 실패 이유

        Returns:
            기본 GapSuggestionResponse
        """
        logger.warning(f"Creating fallback response due to: {error_reason}")

        # 도메인별로 질문 그룹화
        domain_questions: dict = {}
        for q in questions:
            domain = q.domain or "UNKNOWN"
            if domain not in domain_questions:
                domain_questions[domain] = []
            domain_questions[domain].append(q)

        # 기본 제안 생성
        suggestions = []
        for domain, qs in domain_questions.items():
            question_ids = [q.question_id for q in qs]
            suggestions.append(
                GapSuggestionItem(
                    id=f"SUG-FALLBACK-{domain}",
                    title=f"{domain} 영역 문서 보완 필요",
                    description=f"{domain} 도메인에서 {len(qs)}개의 질문에 대한 문서가 부족합니다. "
                    f"관련 사규/교육 문서 추가를 검토해 주세요.",
                    related_question_ids=question_ids,
                    priority="MEDIUM",
                )
            )

        return GapSuggestionResponse(
            summary=f"총 {len(questions)}개의 RAG Gap 질문이 발견되었습니다. "
            f"자동 분석에 문제가 있어 기본 제안을 드립니다.",
            suggestions=suggestions,
        )
