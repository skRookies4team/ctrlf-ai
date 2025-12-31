"""
Phase 21: LLM 기반 라우터 (LLM Router)

LLM을 호출하여 의도를 분류하고 JSON 응답을 파싱합니다.
rule_router에서 낮은 신뢰도(confidence < 0.9)로 분류된 경우 사용됩니다.

주요 기능:
1. LLM 프롬프트 구성 (few-shot examples 포함)
2. JSON 응답 파싱 및 유효성 검증
3. 실패 시 ROUTE_UNKNOWN으로 폴백
4. 애매한 경계 감지 및 되묻기 설정
"""

import json
import re
from typing import Any, Dict, List, Optional

from app.clients.llm_client import LLMClient
from app.core.logging import get_logger
from app.models.router_types import (
    ClarifyTemplates,
    ConfirmationTemplates,
    CRITICAL_ACTION_SUB_INTENTS,
    RouterDebugInfo,
    RouterDomain,
    RouterResult,
    RouterRouteType,
    SubIntentId,
    Tier0Intent,
)

logger = get_logger(__name__)


# =============================================================================
# LLM Router 프롬프트
# =============================================================================

LLM_ROUTER_SYSTEM_PROMPT = """당신은 기업 내부 정보보호 AI 어시스턴트의 의도 분류기입니다.
사용자의 질문을 분석하여 아래 JSON 스키마에 맞는 응답만 출력하세요.

## Tier-0 Intent (6개만 사용)
- POLICY_QA: 사규/규정/정책 관련 질문 (정책의 기준/원칙/절차 설명)
- EDUCATION_QA: 교육 내용/규정 관련 질문 (교육이 무엇인지, 무슨 내용인지)
- BACKEND_STATUS: 개인화 조회 (내 연차/근태/복지/교육현황 등 "내" 정보 조회)
- GENERAL_CHAT: 일반 잡담, 인사
- SYSTEM_HELP: 시스템 사용법, 메뉴 설명
- UNKNOWN: 분류 불가

## Domain (5개)
- POLICY: 사규/보안 정책
- EDU: 4대 교육/직무 교육
- HR: 인사/근태/복지/연차/급여
- QUIZ: 퀴즈/시험 관련
- GENERAL: 일반

## RouteType (5개)
- RAG_INTERNAL: POLICY_QA, EDUCATION_QA → 내부 RAG + LLM
- BACKEND_API: BACKEND_STATUS → 백엔드 API 호출
- LLM_ONLY: GENERAL_CHAT → RAG 없이 LLM만
- ROUTE_SYSTEM_HELP: SYSTEM_HELP → 시스템 도움말
- ROUTE_UNKNOWN: UNKNOWN → 분류 불가

## Sub Intent ID (BACKEND_STATUS일 때 세부 분류)
- QUIZ_START: 퀴즈 시작/시험 시작
- QUIZ_SUBMIT: 답안 제출/채점
- QUIZ_GENERATION: 퀴즈 생성/문제 출제
- EDU_STATUS_CHECK: 교육 이수현황/진도 조회
- HR_LEAVE_CHECK: 연차/휴가 잔여 조회
- HR_ATTENDANCE_CHECK: 근태 현황 조회
- HR_WELFARE_CHECK: 복지 포인트/혜택 조회

## 중요 규칙

1. **애매한 경계 A (교육 내용 vs 이수현황)**: "교육 알려줘", "교육 확인해줘" 같이 교육 내용인지 내 이수현황인지 불분명하면:
   - needs_clarify=true
   - clarify_question="교육 내용 설명이 필요하신가요, 아니면 내 이수현황/진도 조회가 필요하신가요?"

2. **애매한 경계 B (규정 vs 개인화)**: "연차 알려줘", "휴가 확인해줘" 같이 규정인지 내 잔여인지 불분명하면:
   - needs_clarify=true
   - clarify_question="회사 규정(정책) 설명을 원하시나요, 아니면 내 HR 정보(연차/근태/복지) 조회를 원하시나요?"

3. **치명 액션 확인 게이트**: QUIZ_START, QUIZ_SUBMIT, QUIZ_GENERATION이면:
   - requires_confirmation=true
   - confirmation_prompt="퀴즈를 지금 시작할까요? (예/아니오)" 등

4. **BACKEND_STATUS인데 sub_intent_id가 비어있으면**: needs_clarify=true로 설정

## 출력 JSON 스키마 (이 형식만 출력하세요)
```json
{
  "tier0_intent": "POLICY_QA | EDUCATION_QA | BACKEND_STATUS | GENERAL_CHAT | SYSTEM_HELP | UNKNOWN",
  "domain": "POLICY | EDU | HR | QUIZ | GENERAL",
  "route_type": "RAG_INTERNAL | BACKEND_API | LLM_ONLY | ROUTE_SYSTEM_HELP | ROUTE_UNKNOWN",
  "sub_intent_id": "",
  "confidence": 0.0,
  "needs_clarify": false,
  "clarify_question": "",
  "requires_confirmation": false,
  "confirmation_prompt": "",
  "debug": {
    "rule_hits": [],
    "keywords": []
  }
}
```

JSON 이외의 출력은 하지 마세요. 설명이나 마크다운 코드블록 없이 순수 JSON만 출력하세요."""

# Few-shot 예시
LLM_ROUTER_EXAMPLES = [
    {
        "user": "연차 이월 규정 알려줘",
        "assistant": '{"tier0_intent":"POLICY_QA","domain":"POLICY","route_type":"RAG_INTERNAL","sub_intent_id":"","confidence":0.92,"needs_clarify":false,"clarify_question":"","requires_confirmation":false,"confirmation_prompt":"","debug":{"rule_hits":["POLICY_KEYWORD"],"keywords":["연차","이월","규정"]}}'
    },
    {
        "user": "내 연차 며칠 남았어?",
        "assistant": '{"tier0_intent":"BACKEND_STATUS","domain":"HR","route_type":"BACKEND_API","sub_intent_id":"HR_LEAVE_CHECK","confidence":0.95,"needs_clarify":false,"clarify_question":"","requires_confirmation":false,"confirmation_prompt":"","debug":{"rule_hits":["HR_PERSONAL"],"keywords":["내","연차","남았어"]}}'
    },
    {
        "user": "교육 알려줘",
        "assistant": '{"tier0_intent":"UNKNOWN","domain":"EDU","route_type":"ROUTE_UNKNOWN","sub_intent_id":"","confidence":0.3,"needs_clarify":true,"clarify_question":"교육 내용 설명이 필요하신가요, 아니면 내 이수현황/진도 조회가 필요하신가요?","requires_confirmation":false,"confirmation_prompt":"","debug":{"rule_hits":["BOUNDARY_A_AMBIGUOUS"],"keywords":["교육"]}}'
    },
    {
        "user": "퀴즈 시작해줘",
        "assistant": '{"tier0_intent":"BACKEND_STATUS","domain":"QUIZ","route_type":"BACKEND_API","sub_intent_id":"QUIZ_START","confidence":0.95,"needs_clarify":false,"clarify_question":"","requires_confirmation":true,"confirmation_prompt":"퀴즈를 지금 시작할까요? (예/아니오)","debug":{"rule_hits":["QUIZ_START"],"keywords":["퀴즈","시작"]}}'
    },
    {
        "user": "안녕하세요",
        "assistant": '{"tier0_intent":"GENERAL_CHAT","domain":"GENERAL","route_type":"LLM_ONLY","sub_intent_id":"","confidence":0.9,"needs_clarify":false,"clarify_question":"","requires_confirmation":false,"confirmation_prompt":"","debug":{"rule_hits":["GENERAL_CHAT"],"keywords":["안녕"]}}'
    },
]


# =============================================================================
# LLMRouter 클래스
# =============================================================================


class LLMRouter:
    """LLM 기반 라우터.

    LLM을 호출하여 의도를 분류하고 JSON 응답을 파싱합니다.

    Usage:
        router = LLMRouter(llm_client=llm_client)
        result = await router.route(user_query="정보보호교육 어떻게 신청해?")
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        """LLMRouter 초기화.

        Args:
            llm_client: LLM 클라이언트. None이면 새로 생성.
        """
        self._llm = llm_client or LLMClient()

    async def route(
        self,
        user_query: str,
        rule_router_result: Optional[RouterResult] = None,
    ) -> RouterResult:
        """사용자 질문을 LLM 기반으로 분류합니다.

        Args:
            user_query: 사용자 질문 텍스트
            rule_router_result: rule_router의 사전 분류 결과 (컨텍스트 제공용)

        Returns:
            RouterResult: 분류 결과

        Note:
            - JSON 파싱 실패 시 ROUTE_UNKNOWN으로 폴백
            - LLM 호출 실패 시 ROUTE_UNKNOWN으로 폴백
        """
        try:
            # LLM 메시지 구성
            messages = self._build_messages(user_query, rule_router_result)

            # LLM 호출
            response = await self._llm.generate_chat_completion(
                messages=messages,
                temperature=0.1,  # 일관된 분류를 위해 낮은 temperature
                max_tokens=512,
            )

            # JSON 파싱
            result = self._parse_response(response)

            # 유효성 검증 및 보정
            result = self._validate_and_fix(result)

            logger.info(
                f"LLMRouter: intent={result.tier0_intent.value}, "
                f"domain={result.domain.value}, "
                f"confidence={result.confidence}, "
                f"needs_clarify={result.needs_clarify}, "
                f"query={user_query[:50]}..."
            )

            return result

        except Exception as e:
            logger.warning(f"LLMRouter failed, falling back to UNKNOWN: {e}")
            return RouterResult(
                tier0_intent=Tier0Intent.UNKNOWN,
                domain=RouterDomain.GENERAL,
                route_type=RouterRouteType.ROUTE_UNKNOWN,
                confidence=0.1,
                needs_clarify=True,
                clarify_question="질문을 이해하지 못했습니다. 좀 더 구체적으로 말씀해 주시겠어요?",
                debug=RouterDebugInfo(rule_hits=["LLM_FALLBACK"]),
            )

    def _build_messages(
        self,
        user_query: str,
        rule_router_result: Optional[RouterResult] = None,
    ) -> List[Dict[str, str]]:
        """LLM 호출용 메시지를 구성합니다.

        Args:
            user_query: 사용자 질문
            rule_router_result: rule_router 결과 (컨텍스트 제공용)

        Returns:
            List[Dict[str, str]]: LLM 메시지 목록
        """
        messages: List[Dict[str, str]] = []

        # System prompt
        messages.append({
            "role": "system",
            "content": LLM_ROUTER_SYSTEM_PROMPT,
        })

        # Few-shot examples
        for example in LLM_ROUTER_EXAMPLES:
            messages.append({"role": "user", "content": example["user"]})
            messages.append({"role": "assistant", "content": example["assistant"]})

        # 실제 사용자 질문
        user_content = user_query

        # rule_router 결과가 있으면 힌트로 제공
        if rule_router_result and rule_router_result.debug.keywords:
            hint = f"\n\n[참고: 키워드 감지됨: {', '.join(rule_router_result.debug.keywords)}]"
            user_content += hint

        messages.append({"role": "user", "content": user_content})

        return messages

    def _parse_response(self, response: str) -> RouterResult:
        """LLM 응답을 파싱하여 RouterResult를 반환합니다.

        Args:
            response: LLM 응답 텍스트

        Returns:
            RouterResult: 파싱된 결과

        Raises:
            ValueError: JSON 파싱 실패 시
        """
        # 마크다운 코드블록 제거
        response = response.strip()
        if response.startswith("```"):
            # ```json ... ``` 형태 처리
            response = re.sub(r"^```(?:json)?\s*", "", response)
            response = re.sub(r"\s*```$", "", response)

        # JSON 파싱
        try:
            data = json.loads(response)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse LLM response as JSON: {e}")
            logger.debug(f"Raw response: {response[:500]}")
            raise ValueError(f"Invalid JSON response: {e}")

        # RouterResult로 변환
        return self._dict_to_router_result(data)

    def _dict_to_router_result(self, data: Dict[str, Any]) -> RouterResult:
        """딕셔너리를 RouterResult로 변환합니다.

        Args:
            data: 파싱된 JSON 딕셔너리

        Returns:
            RouterResult: 변환된 결과
        """
        # Enum 변환 (안전하게)
        tier0_intent = self._safe_enum_convert(
            data.get("tier0_intent", "UNKNOWN"),
            Tier0Intent,
            Tier0Intent.UNKNOWN,
        )
        domain = self._safe_enum_convert(
            data.get("domain", "GENERAL"),
            RouterDomain,
            RouterDomain.GENERAL,
        )
        route_type = self._safe_enum_convert(
            data.get("route_type", "ROUTE_UNKNOWN"),
            RouterRouteType,
            RouterRouteType.ROUTE_UNKNOWN,
        )

        # Debug info
        debug_data = data.get("debug", {})
        debug_info = RouterDebugInfo(
            rule_hits=debug_data.get("rule_hits", []),
            keywords=debug_data.get("keywords", []),
        )

        return RouterResult(
            tier0_intent=tier0_intent,
            domain=domain,
            route_type=route_type,
            sub_intent_id=str(data.get("sub_intent_id", "")),
            confidence=float(data.get("confidence", 0.0)),
            needs_clarify=bool(data.get("needs_clarify", False)),
            clarify_question=str(data.get("clarify_question", "")),
            requires_confirmation=bool(data.get("requires_confirmation", False)),
            confirmation_prompt=str(data.get("confirmation_prompt", "")),
            debug=debug_info,
        )

    def _safe_enum_convert(
        self,
        value: str,
        enum_class: type,
        default: Any,
    ) -> Any:
        """안전하게 문자열을 Enum으로 변환합니다.

        Args:
            value: 변환할 문자열
            enum_class: 대상 Enum 클래스
            default: 변환 실패 시 기본값

        Returns:
            Enum 값 또는 기본값
        """
        try:
            return enum_class(value)
        except (ValueError, KeyError):
            logger.warning(
                f"Invalid enum value '{value}' for {enum_class.__name__}, "
                f"using default: {default}"
            )
            return default

    def _validate_and_fix(self, result: RouterResult) -> RouterResult:
        """분류 결과를 검증하고 필요시 보정합니다.

        Args:
            result: 원본 분류 결과

        Returns:
            RouterResult: 검증/보정된 결과

        규칙:
        1. BACKEND_STATUS인데 sub_intent_id가 비어있으면 needs_clarify=true
        2. 치명 액션이면 requires_confirmation=true 강제
        3. route_type과 tier0_intent 일관성 검증
        """
        # 규칙 1: BACKEND_STATUS + 빈 sub_intent_id → needs_clarify
        if (
            result.tier0_intent == Tier0Intent.BACKEND_STATUS
            and not result.sub_intent_id
            and not result.needs_clarify
        ):
            logger.debug("BACKEND_STATUS without sub_intent_id, setting needs_clarify")
            result.needs_clarify = True
            result.clarify_question = (
                "어떤 정보를 조회하시겠어요? "
                "(연차 잔여, 교육 이수현황, 근태 현황 등)"
            )

        # 규칙 2: 치명 액션 → requires_confirmation 강제
        if result.sub_intent_id in CRITICAL_ACTION_SUB_INTENTS:
            if not result.requires_confirmation:
                logger.debug(
                    f"Critical action {result.sub_intent_id} detected, "
                    "forcing requires_confirmation=true"
                )
                result.requires_confirmation = True

                # 확인 프롬프트 설정
                if not result.confirmation_prompt:
                    if result.sub_intent_id == SubIntentId.QUIZ_START.value:
                        result.confirmation_prompt = ConfirmationTemplates.QUIZ_START
                    elif result.sub_intent_id == SubIntentId.QUIZ_SUBMIT.value:
                        result.confirmation_prompt = ConfirmationTemplates.QUIZ_SUBMIT
                    elif result.sub_intent_id == SubIntentId.QUIZ_GENERATION.value:
                        result.confirmation_prompt = ConfirmationTemplates.QUIZ_GENERATION

        # 규칙 3: route_type과 tier0_intent 일관성
        expected_routes = {
            Tier0Intent.POLICY_QA: RouterRouteType.RAG_INTERNAL,
            Tier0Intent.EDUCATION_QA: RouterRouteType.RAG_INTERNAL,
            Tier0Intent.BACKEND_STATUS: RouterRouteType.BACKEND_API,
            Tier0Intent.GENERAL_CHAT: RouterRouteType.LLM_ONLY,
            Tier0Intent.SYSTEM_HELP: RouterRouteType.ROUTE_SYSTEM_HELP,
            Tier0Intent.UNKNOWN: RouterRouteType.ROUTE_UNKNOWN,
        }
        expected_route = expected_routes.get(result.tier0_intent)
        if expected_route and result.route_type != expected_route:
            logger.debug(
                f"Route type mismatch: {result.route_type} -> {expected_route} "
                f"for intent {result.tier0_intent}"
            )
            result.route_type = expected_route

        return result
