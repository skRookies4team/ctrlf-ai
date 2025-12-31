"""
Answer Guard Service - Phase 39: 답변 품질 가드레일

[A] Answerability Gate: RAG 근거 없으면 답변 생성 금지
[B] Citation Hallucination Guard: 가짜 조항 인용 차단
[C] Template Routing Fix: 템플릿 매핑 버그 제거 (request_id 스코프)
[D] Korean-only Output Enforcement: 언어 가드레일 + 후처리
[E] Complaint Fast Path: 불만/욕설 빠른 경로
[F] Debug Logging: 디버그 가시성

Usage:
    guard = AnswerGuardService()

    # [E] 불만 빠른 경로 체크 (intent 분류 전)
    complaint_result = guard.check_complaint_fast_path(user_query, last_error_reason)
    if complaint_result:
        return complaint_result  # 즉시 응답

    # [A] 답변 가능 여부 체크 (RAG 검색 후)
    is_answerable, template_response = guard.check_answerability(
        intent=Tier0Intent.POLICY_QA,
        sources=rag_sources,
        route_type=RouteType.RAG_INTERNAL,
    )
    if not is_answerable:
        return template_response  # 고정 템플릿으로 종료

    # [B] 가짜 조항 인용 검증 (답변 생성 후)
    is_valid, validated_answer = guard.validate_citation(answer, rag_sources)
    if not is_valid:
        return guard.get_no_evidence_template()

    # [D] 한국어 출력 검증 (최종 출력 전)
    is_korean, final_answer = await guard.enforce_korean_output(answer, llm_client)
    if not is_korean:
        return guard.get_language_error_template()
"""

import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.chat import ChatSource
from app.models.router_types import RouterRouteType, Tier0Intent

logger = get_logger(__name__)


# =============================================================================
# Constants & Configuration
# =============================================================================


class AnswerGuardError(str, Enum):
    """답변 가드 에러 유형."""

    NO_RAG_EVIDENCE = "NO_RAG_EVIDENCE"  # RAG 근거 없음
    CITATION_HALLUCINATION = "CITATION_HALLUCINATION"  # 가짜 조항 인용
    LANGUAGE_ERROR = "LANGUAGE_ERROR"  # 언어 오류 (영어 혼입)
    REQUEST_ID_MISMATCH = "REQUEST_ID_MISMATCH"  # request_id 불일치


# [E] 불만/욕설 키워드 리스트
# Phase 44: 회귀 방지 - 모든 키워드는 최소 2자 이상이어야 함
_RAW_COMPLAINT_KEYWORDS: Set[str] = {
    "그지", "왜몰라", "뭐하", "답답", "짜증", "개같", "멍청", "병신", "꺼져",
    "미친", "지랄", "시발", "씨발", "ㅅㅂ", "ㅂㅅ", "아씨", "에휴", "아오",
    "대체왜", "못알아", "모르냐", "이게뭐야", "쓸모없", "뭐냐",
    # Phase 43: "하" 제거 - 한국어 동사 기본형이므로 너무 광범위함
    # Phase 44: 단일 음절 키워드 금지 (회귀 방지)
}

# Phase 44: 회귀 방지 - 2자 미만 키워드 필터링 (단일 음절 금지)
COMPLAINT_KEYWORDS: Set[str] = {
    kw for kw in _RAW_COMPLAINT_KEYWORDS if len(kw) >= 2
}

# Phase 44: 런타임 검증 - 1자 키워드가 있으면 로그 경고
_short_keywords = [kw for kw in _RAW_COMPLAINT_KEYWORDS if len(kw) < 2]
if _short_keywords:
    import logging
    logging.getLogger(__name__).warning(
        f"COMPLAINT_KEYWORDS에 2자 미만 키워드 발견 (무시됨): {_short_keywords}"
    )

# [A] 내부 규정/사규/정책 관련 intent (RAG 필수)
POLICY_INTENTS: Set[Tier0Intent] = {
    Tier0Intent.POLICY_QA,
}

# Phase 45: 소프트 가드레일 대상 intent
# 이 intent들은 sources=0일 때 "확정 답변" 대신 "KB 근거 없음" 안내로 모드 변경
SOFT_GUARDRAIL_INTENTS: Set[Tier0Intent] = {
    Tier0Intent.POLICY_QA,
    Tier0Intent.EDUCATION_QA,
}

# Phase 45: 자연 답변 허용 intent (sources 없어도 자유롭게 답변)
FREE_ANSWER_INTENTS: Set[Tier0Intent] = {
    Tier0Intent.GENERAL_CHAT,
    Tier0Intent.SYSTEM_HELP,
}

# [B] 조항/규정 패턴 정규식
CITATION_PATTERN = re.compile(
    r"(제\s*\d+\s*조|제\s*\d+\s*항|제\s*\d+\s*호|"
    r"\d+조\s*\d*항?|\d+항|\d+호|"
    r"조항|별표|부칙|시행령|시행규칙)",
    re.IGNORECASE
)

# [D] 영어 문자 범위 (ASCII Alphabets)
ENGLISH_CHAR_PATTERN = re.compile(r"[a-zA-Z]")
ENGLISH_THRESHOLD = 10  # 영어 문자가 이 개수 이상이면 실패로 간주 (전문용어 허용)


# =============================================================================
# Templates (고정 문구, 한국어)
# =============================================================================


class AnswerTemplates:
    """고정 응답 템플릿."""

    # [A] RAG 근거 없음 템플릿
    NO_EVIDENCE = (
        "승인/인덱싱된 사내 문서에서 관련 내용을 찾지 못했어요.\n\n"
        "**가능한 원인:**\n"
        "• 문서 미업로드\n"
        "• 문서 미승인\n"
        "• 인덱싱 제외\n"
        "• 검색 설정 문제\n\n"
        "**조치:** 문서 업로드 → 승인 → 인덱싱 후 다시 질문해 주세요."
    )

    # [A] 디버그 모드용 추가 정보 템플릿
    NO_EVIDENCE_DEBUG = (
        "\n\n---\n"
        "**[디버그 정보]**\n"
        "• topK: {top_k}\n"
        "• 검색된 문서: {doc_count}개\n"
        "• 최고 점수: {max_score:.3f}"
    )

    # [B] 가짜 조항 인용 차단 템플릿
    CITATION_BLOCKED = (
        "답변에 문서 근거를 확인할 수 없는 조항/규정이 포함되어 있어 표시할 수 없습니다.\n\n"
        "정확한 정보가 필요하시면 해당 규정 문서를 업로드해 주세요."
    )

    # [D] 언어 오류 템플릿
    LANGUAGE_ERROR = (
        "언어 오류가 감지되어 답변을 중단합니다.\n"
        "다시 질문해 주세요."
    )

    # [E] 불만 빠른 경로 템플릿
    COMPLAINT_APOLOGY = "방금 답변이 도움 안 됐죠. 미안해요."

    COMPLAINT_REASON_NO_DOC = "지금은 관련 문서 근거를 못 찾아서 정확히 답할 수 없었어요."
    COMPLAINT_REASON_ROUTING_ERROR = "요청을 처리하는 과정에서 오류가 발생했어요."
    COMPLAINT_REASON_GENERAL = "관련 정보가 충분하지 않아 정확한 답변이 어려웠어요."

    COMPLAINT_NEXT_STEP = "문서를 인덱싱하면 그 기준으로만 답하게 만들게요. 다시 질문해 주세요."

    # Phase 45: 소프트 가드레일 안내 템플릿
    # sources=0일 때 "확정 답변" 대신 이 안내를 앞에 붙임
    SOFT_GUARDRAIL_PREFIX = (
        "⚠️ **현재 승인된 사내 문서에서 관련 근거를 찾지 못했습니다.**\n\n"
        "아래 답변은 일반적인 지식을 바탕으로 한 참고 정보이며, "
        "**회사 기준으로 확정된 답변이 아닙니다.**\n\n"
        "정확한 정보가 필요하시면 담당 부서에 문의해 주세요:\n"
    )

    # Phase 47: 도메인별 담당 부서 안내 (정규화된 구조)
    # - 시스템 도메인(POLICY, EDUCATION, INCIDENT, GENERAL)
    # - 교육 주제 카테고리(PIP, SHP, BHP, DEP, JOB 등)는 TOPIC_CONTACT_INFO에 별도 정의
    # - Phase 47.1: 정규화된 키만 유지 (EDU는 normalize_domain_key()에서 EDUCATION으로 매핑)
    DOMAIN_CONTACT_INFO = {
        # 시스템 도메인 (라우팅용) - 정규화된 키만 유지
        "POLICY": "• 인사팀 / 총무팀 (사내 규정 관련)",
        "EDUCATION": "• 교육팀 / HR팀 (교육 관련)",
        "INCIDENT": "• 보안팀 / 감사팀 (사건/사고 관련)",
        "GENERAL": "• 담당 부서에 문의해 주세요.",
        "DEFAULT": "• 담당 부서에 문의해 주세요.",
    }

    # Phase 47: 교육 주제 카테고리별 담당부서 (dataset/topic용)
    # 더 구체적인 안내가 필요한 경우 사용
    TOPIC_CONTACT_INFO = {
        "PIP": "• 개인정보보호팀 (개인정보 관련)",
        "SHP": "• 인사팀 / 고충처리위원회 (성희롱 예방)",
        "BHP": "• 인사팀 / 고충처리위원회 (직장내 괴롭힘)",
        "DEP": "• 인사팀 (장애인 인식개선)",
        "JOB": "• 교육팀 / 해당 부서 (직무교육)",
    }


# =============================================================================
# Request Context (request_id 스코프 관리)
# =============================================================================


@dataclass
class RequestContext:
    """요청 컨텍스트 (request_id 스코프 관리).

    [C] Template Routing Fix: request_id를 생성해서 tool 호출~응답까지 동일하게 묶고,
    불일치 시 출력 금지.
    """

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    intent: Optional[Tier0Intent] = None
    route_type: Optional[RouterRouteType] = None
    tool_name: Optional[str] = None

    def validate_response_context(
        self,
        response_request_id: Optional[str] = None,
        response_tool_name: Optional[str] = None,
    ) -> bool:
        """응답 컨텍스트가 요청과 일치하는지 검증.

        Args:
            response_request_id: 응답에 포함된 request_id
            response_tool_name: 응답에 포함된 tool_name

        Returns:
            True if valid, False if mismatch
        """
        if response_request_id and response_request_id != self.request_id:
            logger.warning(
                f"Request ID mismatch: expected={self.request_id}, "
                f"got={response_request_id}"
            )
            return False
        return True


# =============================================================================
# Debug Info (디버그 가시성)
# =============================================================================


@dataclass
class DebugInfo:
    """[F] 디버그 로그 정보.

    CLI 출력에 표시할 디버그 정보를 담습니다.
    기본 off, env/flag로 on.
    """

    # Route 결정 정보
    intent: Optional[str] = None
    domain: Optional[str] = None
    route_type: Optional[str] = None
    route_reason: str = ""

    # Retrieval 정보
    retrieval_top_k: int = 0
    retrieval_results: List[Dict[str, Any]] = field(default_factory=list)

    # Answerability 정보
    answerable: bool = True
    answerable_reason: str = ""

    # Guard 정보
    citation_valid: bool = True
    citation_blocked_patterns: List[str] = field(default_factory=list)
    language_valid: bool = True
    english_char_count: int = 0

    def to_log_dict(self) -> Dict[str, Any]:
        """로그용 딕셔너리로 변환 (개인정보 제외)."""
        return {
            "route": {
                "intent": self.intent,
                "domain": self.domain,
                "route_type": self.route_type,
                "reason": self.route_reason,
            },
            "retrieval": {
                "top_k": self.retrieval_top_k,
                "results": [
                    {
                        "doc_title": r.get("doc_title", ""),
                        "score": r.get("score", 0),
                        "chunk_id": r.get("chunk_id", ""),
                    }
                    for r in self.retrieval_results[:5]  # 상위 5개만
                ],
            },
            "answerable": {
                "result": self.answerable,
                "reason": self.answerable_reason,
            },
            "guards": {
                "citation_valid": self.citation_valid,
                "blocked_patterns": self.citation_blocked_patterns,
                "language_valid": self.language_valid,
                "english_chars": self.english_char_count,
            },
        }


# =============================================================================
# AnswerGuardService
# =============================================================================


class AnswerGuardService:
    """답변 품질 가드레일 서비스.

    [A]~[F] 요구사항을 통합 관리합니다.
    """

    # Phase 47: 교육 주제 카테고리 → 시스템 도메인 매핑
    # 교육 관련 카테고리는 모두 EDUCATION 계열로 통합
    _TOPIC_TO_DOMAIN_MAP: Dict[str, str] = {
        "PIP": "EDUCATION",
        "SHP": "EDUCATION",
        "BHP": "EDUCATION",
        "DEP": "EDUCATION",
        "JOB": "EDUCATION",
    }

    def __init__(self) -> None:
        """AnswerGuardService 초기화."""
        settings = get_settings()
        self._debug_enabled = getattr(settings, "ANSWER_GUARD_DEBUG", False)

        # 환경변수로 디버그 모드 활성화
        if os.environ.get("ANSWER_GUARD_DEBUG", "").lower() in ("1", "true", "on"):
            self._debug_enabled = True

    # -------------------------------------------------------------------------
    # Phase 47: 도메인/토픽 정규화
    # -------------------------------------------------------------------------

    def normalize_domain_key(self, domain: Optional[str]) -> str:
        """도메인 키를 정규화하여 담당부서 안내에 사용합니다.

        Phase 47: 시스템 도메인과 교육 주제 카테고리를 통합하여 일관된 키 반환.

        정규화 규칙:
        1. EDUCATION/EDU → EDUCATION
        2. PIP/SHP/BHP/DEP/JOB → EDUCATION (교육 주제 카테고리)
        3. POLICY/INCIDENT/GENERAL → 그대로 유지
        4. None 또는 알 수 없는 값 → DEFAULT

        Args:
            domain: 입력 도메인 또는 토픽 키

        Returns:
            정규화된 도메인 키
        """
        if not domain:
            return "DEFAULT"

        domain_upper = domain.upper()

        # EDU → EDUCATION 정규화
        if domain_upper == "EDU":
            return "EDUCATION"

        # 교육 주제 카테고리 → EDUCATION 매핑
        if domain_upper in self._TOPIC_TO_DOMAIN_MAP:
            return self._TOPIC_TO_DOMAIN_MAP[domain_upper]

        # 알려진 시스템 도메인
        if domain_upper in ("POLICY", "EDUCATION", "INCIDENT", "GENERAL"):
            return domain_upper

        # 알 수 없는 값 → DEFAULT
        return "DEFAULT"

    def get_contact_info(self, domain: Optional[str], topic: Optional[str] = None) -> str:
        """도메인/토픽에 맞는 담당부서 안내를 반환합니다.

        Phase 47: 토픽이 있으면 토픽 기준, 없으면 도메인 기준으로 안내.

        Args:
            domain: 시스템 도메인 (POLICY, EDUCATION, INCIDENT 등)
            topic: 교육 주제 카테고리 (PIP, SHP, BHP 등, 선택적)

        Returns:
            담당부서 안내 문자열
        """
        # 토픽이 있고 TOPIC_CONTACT_INFO에 있으면 더 구체적인 안내 사용
        if topic:
            topic_upper = topic.upper()
            if topic_upper in AnswerTemplates.TOPIC_CONTACT_INFO:
                return AnswerTemplates.TOPIC_CONTACT_INFO[topic_upper]

        # 도메인 기준 안내
        normalized_domain = self.normalize_domain_key(domain)
        return AnswerTemplates.DOMAIN_CONTACT_INFO.get(
            normalized_domain,
            AnswerTemplates.DOMAIN_CONTACT_INFO["DEFAULT"]
        )

    # -------------------------------------------------------------------------
    # Phase 45: Soft Guardrail (소프트 가드레일)
    # -------------------------------------------------------------------------

    def check_soft_guardrail(
        self,
        intent: Tier0Intent,
        sources: List[ChatSource],
        domain: Optional[str] = None,
        topic: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """소프트 가드레일 적용 여부를 판정합니다.

        Phase 45: POLICY_QA/EDUCATION_QA에서 sources=0이면:
        - "확정 답변" 대신 "KB 근거 없음 + 담당부서 안내" prefix 반환
        - GENERAL_CHAT/SYSTEM_HELP는 자유 답변 허용

        Phase 47: 도메인/토픽 정규화 적용
        - 토픽(PIP, SHP 등)이 있으면 더 구체적인 담당부서 안내
        - 도메인(EDUCATION, POLICY 등) 정규화로 일관된 매핑

        Args:
            intent: Tier0 의도
            sources: RAG 검색 결과
            domain: 도메인 (담당부서 안내용)
            topic: 교육 주제 카테고리 (선택적, 더 구체적인 안내용)

        Returns:
            (needs_soft_guardrail, prefix) 튜플
            - needs_soft_guardrail=True: 소프트 가드레일 필요, prefix 문자열 반환
            - needs_soft_guardrail=False: 일반 답변 가능, None 반환
        """
        has_sources = len(sources) > 0

        # 자연 답변 허용 intent는 바로 통과
        if intent in FREE_ANSWER_INTENTS:
            return (False, None)

        # 소프트 가드레일 대상 intent이고 sources가 없는 경우
        if intent in SOFT_GUARDRAIL_INTENTS and not has_sources:
            # Phase 47: 정규화된 담당부서 안내 구성
            contact_info = self.get_contact_info(domain=domain, topic=topic)

            prefix = AnswerTemplates.SOFT_GUARDRAIL_PREFIX + contact_info + "\n\n---\n\n"

            logger.info(
                f"Soft guardrail ACTIVE: intent={intent.value}, "
                f"sources=0, domain={domain}, topic={topic}"
            )
            return (True, prefix)

        # 기타 경우: 일반 답변
        return (False, None)

    def get_soft_guardrail_system_instruction(self) -> str:
        """소프트 가드레일용 시스템 프롬프트 추가 지침을 반환합니다.

        Phase 45: sources=0일 때 LLM이 "확정" 표현을 쓰지 않도록 지시.
        Phase 46: '확정 표현 금지' 규칙 강화 + 답변 형태 제한
        Phase 47: '~입니다' 종결어미 금지 제거 (한국어 기본 서술 종결어미)
                  → '회사 기준 확정/근거 주장'만 금지하도록 변경
        """
        return (
            "\n\n[중요 지침 - 회사 기준 확정 표현 금지]\n"
            "현재 참고할 사내 문서 근거가 없습니다.\n"
            "따라서 답변 시 다음 규칙을 반드시 따르세요:\n\n"
            "【금지 표현 - 회사 기준 확정/근거 주장】\n"
            "• '회사 규정상', '사규에 따르면', '정책에 따라', '회사 방침으로'\n"
            "• '의무적으로', '반드시', '무조건'\n"
            "• 제N조, 제N항, 제N호 등 구체적 조항 번호 단정 인용\n\n"
            "【허용 표현 - 일반 지식/조건부 표현】\n"
            "• '일반적으로는 ~로 운영되는 경우가 많습니다'\n"
            "• '회사마다 다를 수 있습니다', '~일 수 있습니다'\n"
            "• '통상적으로 ~합니다', '대부분의 경우 ~합니다'\n"
            "• 일반적인 서술형 종결('~입니다', '~합니다')은 사용 가능\n\n"
            "【답변 형식】\n"
            "답변은 반드시 다음 구조를 따르세요:\n"
            "1. 일반적인 안내 (회사 기준 확정 표현 없이)\n"
            "2. 확인 방법 안내 (어떤 문서/담당부서/키워드로 찾을 수 있는지)\n"
            "3. '정확한 정보는 담당 부서에 확인해 주세요' 문구로 마무리\n\n"
            "반드시 한국어로만 답변하세요.\n"
        )

    # -------------------------------------------------------------------------
    # [E] Complaint Fast Path (불만/욕설 빠른 경로)
    # -------------------------------------------------------------------------

    def check_complaint_fast_path(
        self,
        user_query: str,
        last_error_reason: Optional[str] = None,
    ) -> Optional[str]:
        """불만/욕설 키워드를 감지하고 빠른 경로로 응답합니다.

        [E] intent 분류 전에 먼저 실행 (전처리).
        RAG/툴 호출 없이 즉시 응답.

        Args:
            user_query: 사용자 입력
            last_error_reason: 직전 오류 사유 (있으면)

        Returns:
            빠른 응답 문자열, 또는 None (불만 아닌 경우)
        """
        query_lower = user_query.lower().strip()

        # 키워드 매칭
        is_complaint = any(keyword in query_lower for keyword in COMPLAINT_KEYWORDS)

        if not is_complaint:
            return None

        logger.info(f"Complaint detected: '{user_query[:30]}...'")

        # 응답 구성
        apology = AnswerTemplates.COMPLAINT_APOLOGY

        # 원인 결정
        if last_error_reason == "NO_RAG_EVIDENCE":
            reason = AnswerTemplates.COMPLAINT_REASON_NO_DOC
        elif last_error_reason in ("ROUTING_ERROR", "TOOL_ERROR"):
            reason = AnswerTemplates.COMPLAINT_REASON_ROUTING_ERROR
        else:
            reason = AnswerTemplates.COMPLAINT_REASON_GENERAL

        next_step = AnswerTemplates.COMPLAINT_NEXT_STEP

        return f"{apology}\n\n{reason}\n\n{next_step}"

    # -------------------------------------------------------------------------
    # [A] Answerability Gate (답변 가능 여부 게이트)
    # -------------------------------------------------------------------------

    def check_answerability(
        self,
        intent: Tier0Intent,
        sources: List[ChatSource],
        route_type: RouterRouteType,
        top_k: int = 5,
        debug_info: Optional[DebugInfo] = None,
    ) -> Tuple[bool, Optional[str]]:
        """RAG 근거 기반 답변 가능 여부를 판정합니다.

        [A] 내부 규정/사규/정책류 intent AND retrieval_result가 empty → answerable=false

        Args:
            intent: Tier0 의도
            sources: RAG 검색 결과
            route_type: 라우팅 타입
            top_k: 검색 설정 topK
            debug_info: 디버그 정보 객체 (수정됨)

        Returns:
            (answerable, template_response) 튜플
            - answerable=True: (True, None)
            - answerable=False: (False, 고정 템플릿 문자열)
        """
        # LLM_ONLY 경로는 RAG 체크 스킵
        if route_type in (RouterRouteType.LLM_ONLY, RouterRouteType.ROUTE_SYSTEM_HELP):
            if debug_info:
                debug_info.answerable = True
                debug_info.answerable_reason = "LLM_ONLY route - RAG check skipped"
            return (True, None)

        # 정책/사규 질문인지 확인
        is_policy_intent = intent in POLICY_INTENTS
        has_sources = len(sources) > 0

        # 디버그 정보 업데이트
        if debug_info:
            debug_info.retrieval_top_k = top_k
            debug_info.retrieval_results = [
                {
                    "doc_title": s.title or "",
                    "score": s.score,
                    "chunk_id": getattr(s, "chunk_id", "") or "",
                }
                for s in sources
            ]

        # Phase 44: Answerability 정책 완화
        # 정책/사규 intent인데 RAG 결과가 없어도 답변 허용 (경고만)
        # LLM의 일반 지식으로 답변하고, 사용자에게 근거 부족 알림
        if is_policy_intent and not has_sources:
            logger.info(
                f"Answerability INFO: intent={intent.value}, "
                f"sources={len(sources)} - allowing LLM general knowledge"
            )

            if debug_info:
                debug_info.answerable = True  # 답변 허용
                debug_info.answerable_reason = (
                    f"Policy intent ({intent.value}) - no RAG but allowing LLM"
                )

            # Phase 44: 차단하지 않고 답변 허용
            return (True, None)

            # [이전 로직 - 비활성화]
            # 고정 템플릿 반환
            # template = AnswerTemplates.NO_EVIDENCE

            # 디버그 모드면 추가 정보 포함
            _ = None  # placeholder for removed code
            if False and self._debug_enabled and sources:
                max_score = max((s.score or 0) for s in sources)
                _ = AnswerTemplates.NO_EVIDENCE_DEBUG.format(
                    top_k=top_k,
                    doc_count=len(sources),
                    max_score=max_score,
                )
            elif False and self._debug_enabled:
                template += AnswerTemplates.NO_EVIDENCE_DEBUG.format(
                    top_k=top_k,
                    doc_count=0,
                    max_score=0.0,
                )

            return (False, template)

        if debug_info:
            debug_info.answerable = True
            debug_info.answerable_reason = (
                f"sources={len(sources)}, is_policy={is_policy_intent}"
            )

        return (True, None)

    # -------------------------------------------------------------------------
    # [B] Citation Hallucination Guard (가짜 조항 인용 차단)
    # -------------------------------------------------------------------------

    def validate_citation(
        self,
        answer: str,
        sources: List[ChatSource],
        debug_info: Optional[DebugInfo] = None,
    ) -> Tuple[bool, str]:
        """답변의 조항/규정 인용이 RAG 소스에 근거하는지 검증합니다.

        [B] 답변에 "제N조/조항/항" 패턴이 있으면 RAG sources에도 있는지 확인.

        Phase 44: Citation 검증 완화
        - RAG sources가 없어도 LLM의 일반 법률 지식 기반 답변 허용
        - 차단 대신 경고 로그만 남김
        - 명백한 hallucination만 차단 (예: 실존하지 않는 조항 직접 인용)

        Args:
            answer: LLM 생성 답변
            sources: RAG 검색 결과
            debug_info: 디버그 정보 객체

        Returns:
            (is_valid, validated_answer) 튜플
            - 유효: (True, 원본 answer)
            - 무효: (False, 차단 템플릿)
        """
        # 답변에서 조항 패턴 추출
        answer_citations = CITATION_PATTERN.findall(answer)

        if not answer_citations:
            # 조항 패턴 없음 - 검증 불필요
            if debug_info:
                debug_info.citation_valid = True
            return (True, answer)

        # Phase 44: RAG sources가 없어도 답변 허용 (경고만 로그)
        # LLM이 일반적인 법률 지식으로 조항을 언급하는 것은 허용
        if not sources:
            logger.info(
                f"Citation INFO: found {len(answer_citations)} citations "
                f"without RAG sources - allowing LLM general knowledge"
            )
            if debug_info:
                debug_info.citation_valid = True  # 유효로 처리
            return (True, answer)  # 차단하지 않고 허용

        # RAG sources 텍스트 결합
        source_text = " ".join(
            (s.snippet or "") + " " + (s.article_label or "")
            for s in sources
        ).lower()

        # 각 인용 패턴이 sources에 있는지 확인
        blocked_citations = []
        for citation in set(answer_citations):
            citation_normalized = re.sub(r"\s+", "", citation.lower())
            source_normalized = re.sub(r"\s+", "", source_text)

            if citation_normalized not in source_normalized:
                # 숫자만 추출해서 한번 더 체크
                citation_nums = re.findall(r"\d+", citation)
                found_in_source = False

                for num in citation_nums:
                    # "제N조" 형태로 sources에서 찾기
                    if f"제{num}조" in source_text or f"{num}조" in source_text:
                        found_in_source = True
                        break

                if not found_in_source:
                    blocked_citations.append(citation)

        # Phase 44: Citation 검증 완화
        # RAG sources가 있어도 일치하지 않는 조항은 경고만 (차단하지 않음)
        # LLM이 관련 지식으로 추가 조항을 언급하는 것은 허용
        if blocked_citations:
            logger.info(
                f"Citation INFO: {len(blocked_citations)} patterns not in sources "
                f"({blocked_citations}) - allowing as supplementary info"
            )
            # 차단하지 않고 허용 (경고만)
            if debug_info:
                debug_info.citation_valid = True
                debug_info.citation_blocked_patterns = blocked_citations  # 로그용으로 기록

        if debug_info:
            debug_info.citation_valid = True

        return (True, answer)

    # -------------------------------------------------------------------------
    # [D] Korean-only Output Enforcement (언어 가드레일)
    # -------------------------------------------------------------------------

    def check_language(
        self,
        text: str,
        debug_info: Optional[DebugInfo] = None,
    ) -> Tuple[bool, int]:
        """텍스트에 영어가 혼입되었는지 검사합니다.

        Args:
            text: 검사할 텍스트
            debug_info: 디버그 정보 객체

        Returns:
            (is_valid, english_char_count) 튜플
        """
        english_chars = ENGLISH_CHAR_PATTERN.findall(text)
        count = len(english_chars)

        is_valid = count < ENGLISH_THRESHOLD

        if debug_info:
            debug_info.language_valid = is_valid
            debug_info.english_char_count = count

        if not is_valid:
            logger.warning(
                f"Language check FAILED: {count} English characters detected"
            )

        return (is_valid, count)

    async def enforce_korean_output(
        self,
        answer: str,
        llm_regenerate_fn: Optional[Any] = None,
        original_query: str = "",
        debug_info: Optional[DebugInfo] = None,
    ) -> Tuple[bool, str]:
        """한국어 출력을 강제합니다.

        [D] 영어 혼입 탐지 시:
        1) "한국어로만 다시 작성" 강제 프롬프트로 1회 재생성
        2) 재생성도 실패하면 "언어 오류" 템플릿

        Args:
            answer: LLM 생성 답변
            llm_regenerate_fn: LLM 재생성 함수 (async)
            original_query: 원본 질문
            debug_info: 디버그 정보 객체

        Returns:
            (success, final_answer) 튜플
        """
        # 1차 검사
        is_valid, count = self.check_language(answer, debug_info)

        if is_valid:
            return (True, answer)

        # 재생성 함수 없으면 바로 실패
        if not llm_regenerate_fn:
            logger.warning("Korean enforcement failed: no regenerate function")
            return (False, AnswerTemplates.LANGUAGE_ERROR)

        # 재생성 시도
        logger.info("Attempting Korean-only regeneration...")

        try:
            regenerate_prompt = (
                "이전 답변에 영어가 섞여 있었습니다. "
                "반드시 한국어로만 다시 답변해 주세요.\n\n"
                f"원래 질문: {original_query}"
            )

            regenerated = await llm_regenerate_fn(regenerate_prompt)

            # 재생성 결과 검사
            is_valid_2, count_2 = self.check_language(regenerated)

            if is_valid_2:
                logger.info("Korean-only regeneration succeeded")
                return (True, regenerated)
            else:
                logger.warning(
                    f"Korean-only regeneration failed: "
                    f"still {count_2} English chars"
                )
                return (False, AnswerTemplates.LANGUAGE_ERROR)

        except Exception as e:
            logger.error(f"Korean regeneration error: {e}")
            return (False, AnswerTemplates.LANGUAGE_ERROR)

    # -------------------------------------------------------------------------
    # [F] Debug Logging (디버그 가시성)
    # -------------------------------------------------------------------------

    def create_debug_info(
        self,
        intent: Optional[Tier0Intent] = None,
        domain: Optional[str] = None,
        route_type: Optional[RouterRouteType] = None,
        route_reason: str = "",
    ) -> DebugInfo:
        """디버그 정보 객체를 생성합니다.

        Args:
            intent: Tier0 의도
            domain: 도메인
            route_type: 라우팅 타입
            route_reason: 라우팅 결정 사유

        Returns:
            DebugInfo 객체
        """
        return DebugInfo(
            intent=intent.value if intent else None,
            domain=domain,
            route_type=route_type.value if route_type else None,
            route_reason=route_reason,
        )

    def log_debug_info(self, debug_info: DebugInfo, request_id: str = "") -> None:
        """디버그 정보를 로그로 출력합니다.

        [F] 기본 off, env/flag로 on.
        개인정보/민감정보는 로그에 남기지 않음.

        Args:
            debug_info: 디버그 정보 객체
            request_id: 요청 ID
        """
        if not self._debug_enabled:
            return

        log_dict = debug_info.to_log_dict()

        logger.info(
            f"[ANSWER_GUARD_DEBUG] request_id={request_id}\n"
            f"  route: intent={log_dict['route']['intent']}, "
            f"route_type={log_dict['route']['route_type']}, "
            f"reason={log_dict['route']['reason']}\n"
            f"  retrieval: topK={log_dict['retrieval']['top_k']}, "
            f"results={len(log_dict['retrieval']['results'])}\n"
            f"  answerable: {log_dict['answerable']['result']} "
            f"({log_dict['answerable']['reason']})\n"
            f"  guards: citation_valid={log_dict['guards']['citation_valid']}, "
            f"language_valid={log_dict['guards']['language_valid']}"
        )

    # -------------------------------------------------------------------------
    # [C] Request Context Management
    # -------------------------------------------------------------------------

    def create_request_context(
        self,
        intent: Optional[Tier0Intent] = None,
        route_type: Optional[RouterRouteType] = None,
        tool_name: Optional[str] = None,
    ) -> RequestContext:
        """요청 컨텍스트를 생성합니다.

        [C] request_id를 생성해서 tool 호출~응답까지 동일하게 묶습니다.

        Args:
            intent: Tier0 의도
            route_type: 라우팅 타입
            tool_name: 사용할 tool 이름

        Returns:
            RequestContext 객체
        """
        return RequestContext(
            intent=intent,
            route_type=route_type,
            tool_name=tool_name,
        )

    # -------------------------------------------------------------------------
    # Template Getters
    # -------------------------------------------------------------------------

    def get_no_evidence_template(self, debug_mode: bool = False) -> str:
        """RAG 근거 없음 템플릿을 반환합니다."""
        return AnswerTemplates.NO_EVIDENCE

    def get_citation_blocked_template(self) -> str:
        """가짜 조항 인용 차단 템플릿을 반환합니다."""
        return AnswerTemplates.CITATION_BLOCKED

    def get_language_error_template(self) -> str:
        """언어 오류 템플릿을 반환합니다."""
        return AnswerTemplates.LANGUAGE_ERROR


# =============================================================================
# Module-level functions
# =============================================================================


_answer_guard_service: Optional[AnswerGuardService] = None


def get_answer_guard_service() -> AnswerGuardService:
    """AnswerGuardService 싱글톤 인스턴스를 반환합니다."""
    global _answer_guard_service
    if _answer_guard_service is None:
        _answer_guard_service = AnswerGuardService()
    return _answer_guard_service


def reset_answer_guard_service() -> None:
    """AnswerGuardService 인스턴스를 리셋합니다 (테스트용)."""
    global _answer_guard_service
    _answer_guard_service = None
