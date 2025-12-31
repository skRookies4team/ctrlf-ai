"""
Phase 39: Answer Guard Service 테스트

[A] Answerability Gate: RAG 근거 없으면 답변 생성 금지
[B] Citation Hallucination Guard: 가짜 조항 인용 차단
[C] Template Routing Fix: request_id 스코프 관리
[D] Korean-only Output Enforcement: 언어 가드레일
[E] Complaint Fast Path: 불만/욕설 빠른 경로
[F] Debug Logging: 디버그 가시성
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.models.chat import ChatSource
from app.models.router_types import RouterRouteType, Tier0Intent
from app.services.answer_guard_service import (
    AnswerGuardService,
    AnswerTemplates,
    COMPLAINT_KEYWORDS,
    CITATION_PATTERN,
    DebugInfo,
    RequestContext,
    get_answer_guard_service,
    reset_answer_guard_service,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def answer_guard():
    """AnswerGuardService 인스턴스."""
    reset_answer_guard_service()
    return AnswerGuardService()


@pytest.fixture
def sample_sources():
    """샘플 RAG 소스 리스트."""
    return [
        ChatSource(
            doc_id="doc1",
            title="사규 규정집",
            snippet="제10조 연차휴가는 1년 이상 근무 시 발생한다.",
            article_label="제10조",
            score=0.85,
        ),
        ChatSource(
            doc_id="doc2",
            title="복리후생 안내",
            snippet="제5조 경조사 지원금은 본인 결혼 시 100만원이다.",
            article_label="제5조",
            score=0.72,
        ),
    ]


@pytest.fixture
def empty_sources():
    """빈 RAG 소스 리스트."""
    return []


# =============================================================================
# [E] Complaint Fast Path Tests (불만/욕설 빠른 경로)
# =============================================================================


class TestComplaintFastPath:
    """불만/욕설 빠른 경로 테스트."""

    def test_complaint_keyword_detection_basic(self, answer_guard):
        """기본 불만 키워드 감지 테스트."""
        # 불만 키워드 포함
        result = answer_guard.check_complaint_fast_path("왜몰라이씨", None)
        assert result is not None
        assert "미안해요" in result
        assert "문서" in result or "다시" in result

    def test_complaint_keyword_variations(self, answer_guard):
        """다양한 불만 키워드 테스트."""
        complaint_inputs = [
            "그지같네",
            "뭐하냐",
            "답답하네",
            "짜증나",
            "개같은 답변",
            "멍청한 AI",
        ]
        for query in complaint_inputs:
            result = answer_guard.check_complaint_fast_path(query, None)
            assert result is not None, f"Should detect complaint: {query}"
            assert "미안해요" in result

    def test_normal_query_not_flagged(self, answer_guard):
        """일반 질문은 불만으로 감지되지 않음."""
        normal_queries = [
            "연차 규정 알려줘",
            "퇴직금 계산 방법은?",
            "교육 이수 현황 조회",
            "정보보안 규정 뭐야",
        ]
        for query in normal_queries:
            result = answer_guard.check_complaint_fast_path(query, None)
            assert result is None, f"Should not detect complaint: {query}"

    def test_complaint_with_last_error_no_rag(self, answer_guard):
        """이전 에러 사유가 NO_RAG_EVIDENCE인 경우."""
        result = answer_guard.check_complaint_fast_path(
            "왜몰라", last_error_reason="NO_RAG_EVIDENCE"
        )
        assert result is not None
        assert "문서 근거를 못 찾아서" in result

    def test_complaint_with_routing_error(self, answer_guard):
        """이전 에러 사유가 ROUTING_ERROR인 경우."""
        result = answer_guard.check_complaint_fast_path(
            "짜증나", last_error_reason="ROUTING_ERROR"
        )
        assert result is not None
        assert "오류가 발생" in result

    def test_complaint_no_rag_tool_call(self, answer_guard):
        """불만 키워드 시 RAG/툴 호출 없이 즉시 응답 (시간 체크)."""
        import time

        start = time.perf_counter()
        result = answer_guard.check_complaint_fast_path("답답해", None)
        elapsed = time.perf_counter() - start

        assert result is not None
        # 빠른 경로는 100ms 미만이어야 함
        assert elapsed < 0.1


# =============================================================================
# [A] Answerability Gate Tests (답변 가능 여부 게이트)
# =============================================================================


class TestAnswerabilityGate:
    """답변 가능 여부 게이트 테스트."""

    def test_policy_intent_with_sources_answerable(self, answer_guard, sample_sources):
        """정책 질문 + RAG 소스 있음 → 답변 가능."""
        is_answerable, template = answer_guard.check_answerability(
            intent=Tier0Intent.POLICY_QA,
            sources=sample_sources,
            route_type=RouterRouteType.RAG_INTERNAL,
        )
        assert is_answerable is True
        assert template is None

    def test_policy_intent_without_sources_allowed_with_warning(self, answer_guard, empty_sources):
        """정책 질문 + RAG 소스 없음 → 답변 허용 (Phase 44 정책 완화).

        Phase 44: 차단 대신 경고만 로그하고 LLM 일반 지식으로 답변 허용.
        """
        is_answerable, template = answer_guard.check_answerability(
            intent=Tier0Intent.POLICY_QA,
            sources=empty_sources,
            route_type=RouterRouteType.RAG_INTERNAL,
        )
        # Phase 44: 답변 허용 (차단하지 않음)
        assert is_answerable is True
        assert template is None

    def test_llm_only_route_skips_check(self, answer_guard, empty_sources):
        """LLM_ONLY 경로는 RAG 체크 스킵."""
        is_answerable, template = answer_guard.check_answerability(
            intent=Tier0Intent.POLICY_QA,
            sources=empty_sources,
            route_type=RouterRouteType.LLM_ONLY,
        )
        assert is_answerable is True
        assert template is None

    def test_general_chat_without_sources_allowed(self, answer_guard, empty_sources):
        """일반 채팅은 RAG 소스 없어도 허용."""
        is_answerable, template = answer_guard.check_answerability(
            intent=Tier0Intent.GENERAL_CHAT,
            sources=empty_sources,
            route_type=RouterRouteType.RAG_INTERNAL,
        )
        assert is_answerable is True
        assert template is None

    def test_debug_info_updated(self, answer_guard, empty_sources):
        """디버그 정보가 업데이트됨.

        Phase 44: 정책 완화로 answerable=True, 경고 사유만 기록.
        """
        debug_info = DebugInfo()
        answer_guard.check_answerability(
            intent=Tier0Intent.POLICY_QA,
            sources=empty_sources,
            route_type=RouterRouteType.RAG_INTERNAL,
            debug_info=debug_info,
        )
        # Phase 44: 답변 허용
        assert debug_info.answerable is True
        assert "allowing LLM" in debug_info.answerable_reason


# =============================================================================
# [B] Citation Hallucination Guard Tests (가짜 조항 인용 차단)
# =============================================================================


class TestCitationHallucinationGuard:
    """가짜 조항 인용 차단 테스트."""

    def test_valid_citation_passes(self, answer_guard, sample_sources):
        """RAG 소스에 있는 조항 인용은 통과."""
        answer = "제10조에 따르면 연차휴가는 1년 이상 근무 시 발생합니다."
        is_valid, result = answer_guard.validate_citation(answer, sample_sources)
        assert is_valid is True
        assert result == answer

    def test_hallucinated_citation_allowed_with_warning(self, answer_guard, sample_sources):
        """RAG 소스에 없는 조항 인용 → 허용 (Phase 44 정책 완화).

        Phase 44: RAG sources가 있어도 일치하지 않는 조항은 경고만 로그.
        LLM이 관련 지식으로 추가 조항을 언급하는 것은 허용.
        """
        answer = "제99조 제5항에 따르면 특별휴가를 사용할 수 있습니다."
        is_valid, result = answer_guard.validate_citation(answer, sample_sources)
        # Phase 44: 차단하지 않고 허용
        assert is_valid is True
        assert result == answer

    def test_no_citation_in_answer_passes(self, answer_guard, sample_sources):
        """조항 인용이 없는 답변은 통과."""
        answer = "연차휴가에 대해 설명드리겠습니다. 1년 이상 근무하면 발생합니다."
        is_valid, result = answer_guard.validate_citation(answer, sample_sources)
        assert is_valid is True
        assert result == answer

    def test_citation_without_sources_allowed_with_warning(self, answer_guard, empty_sources):
        """RAG 소스 없이 조항 인용 시 → 허용 (Phase 44 정책 완화).

        Phase 44: RAG sources가 없어도 LLM의 일반적인 법률 지식으로
        조항을 언급하는 것은 허용.
        """
        answer = "제10조에 의하면 연차가 발생합니다."
        is_valid, result = answer_guard.validate_citation(answer, empty_sources)
        # Phase 44: 차단하지 않고 허용
        assert is_valid is True
        assert result == answer

    def test_llm_only_answer_no_citation_section(self, answer_guard, empty_sources):
        """LLM_ONLY 답변에 조항 없으면 통과."""
        answer = "일반적으로 연차휴가는 근로기준법에 따라 부여됩니다."
        is_valid, result = answer_guard.validate_citation(answer, empty_sources)
        assert is_valid is True

    def test_citation_pattern_matching(self):
        """조항 패턴 정규식 테스트."""
        test_cases = [
            ("제10조", True),
            ("제 10 조", True),
            ("제5항", True),
            ("제3호", True),
            ("10조 2항", True),
            ("조항에 따르면", True),
            ("별표 1", True),
            ("연차휴가", False),
            ("일반적인 규정", False),
        ]
        for text, should_match in test_cases:
            matches = CITATION_PATTERN.findall(text)
            has_match = len(matches) > 0
            assert has_match == should_match, f"'{text}' should {'match' if should_match else 'not match'}"


# =============================================================================
# [C] Request Context Tests (request_id 스코프 관리)
# =============================================================================


class TestRequestContext:
    """요청 컨텍스트 테스트."""

    def test_context_creation(self, answer_guard):
        """컨텍스트 생성."""
        ctx = answer_guard.create_request_context(
            intent=Tier0Intent.POLICY_QA,
            route_type=RouterRouteType.RAG_INTERNAL,
            tool_name="rag_search",
        )
        assert ctx.request_id is not None
        assert len(ctx.request_id) == 36  # UUID 형식
        assert ctx.intent == Tier0Intent.POLICY_QA

    def test_context_validation_match(self):
        """컨텍스트 검증 - 일치."""
        ctx = RequestContext(intent=Tier0Intent.POLICY_QA)
        is_valid = ctx.validate_response_context(
            response_request_id=ctx.request_id
        )
        assert is_valid is True

    def test_context_validation_mismatch(self):
        """컨텍스트 검증 - 불일치."""
        ctx = RequestContext(intent=Tier0Intent.POLICY_QA)
        is_valid = ctx.validate_response_context(
            response_request_id="wrong-request-id"
        )
        assert is_valid is False

    def test_context_validation_no_response_id(self):
        """컨텍스트 검증 - 응답 ID 없음 (허용)."""
        ctx = RequestContext(intent=Tier0Intent.POLICY_QA)
        is_valid = ctx.validate_response_context(
            response_request_id=None
        )
        assert is_valid is True


# =============================================================================
# [F] Debug Logging Tests (디버그 가시성)
# =============================================================================


class TestDebugLogging:
    """디버그 로깅 테스트."""

    def test_debug_info_creation(self, answer_guard):
        """디버그 정보 생성."""
        debug_info = answer_guard.create_debug_info(
            intent=Tier0Intent.POLICY_QA,
            domain="POLICY",
            route_type=RouterRouteType.RAG_INTERNAL,
            route_reason="keyword match",
        )
        assert debug_info.intent == "POLICY_QA"
        assert debug_info.domain == "POLICY"
        assert debug_info.route_type == "RAG_INTERNAL"
        assert debug_info.route_reason == "keyword match"

    def test_debug_info_to_log_dict(self, answer_guard, sample_sources):
        """디버그 정보 → 로그 딕셔너리 변환."""
        debug_info = answer_guard.create_debug_info(
            intent=Tier0Intent.POLICY_QA,
            domain="POLICY",
            route_type=RouterRouteType.RAG_INTERNAL,
        )
        debug_info.retrieval_results = [
            {"doc_title": "사규", "score": 0.85, "chunk_id": "c1"}
        ]
        debug_info.answerable = True
        debug_info.answerable_reason = "has sources"

        log_dict = debug_info.to_log_dict()

        assert "route" in log_dict
        assert "retrieval" in log_dict
        assert "answerable" in log_dict
        assert "guards" in log_dict
        assert log_dict["route"]["intent"] == "POLICY_QA"
        assert log_dict["answerable"]["result"] is True

    def test_debug_log_no_pii(self, answer_guard):
        """디버그 로그에 PII 없음 확인."""
        debug_info = DebugInfo()
        debug_info.retrieval_results = [
            {
                "doc_title": "직원 정보",
                "score": 0.9,
                "chunk_id": "c1",
                # 실제로는 여기에 snippet 같은 민감정보가 있을 수 있지만
                # to_log_dict()는 doc_title, score, chunk_id만 포함
            }
        ]
        log_dict = debug_info.to_log_dict()
        result_str = str(log_dict)
        # snippet이나 개인정보 패턴이 없어야 함
        assert "snippet" not in result_str


# =============================================================================
# Template Tests
# =============================================================================


class TestTemplates:
    """고정 템플릿 테스트."""

    def test_no_evidence_template_content(self):
        """RAG 근거 없음 템플릿 내용 확인."""
        template = AnswerTemplates.NO_EVIDENCE
        assert "찾지 못했어요" in template
        assert "가능한 원인" in template
        assert "문서 업로드" in template

    def test_citation_blocked_template_content(self):
        """가짜 조항 차단 템플릿 내용 확인."""
        template = AnswerTemplates.CITATION_BLOCKED
        assert "근거를 확인할 수 없는" in template

    def test_language_error_template_content(self):
        """언어 오류 템플릿 내용 확인."""
        template = AnswerTemplates.LANGUAGE_ERROR
        assert "언어 오류" in template
        assert "다시 질문" in template

    def test_complaint_templates(self):
        """불만 템플릿 내용 확인."""
        assert "미안해요" in AnswerTemplates.COMPLAINT_APOLOGY
        assert "문서 근거" in AnswerTemplates.COMPLAINT_REASON_NO_DOC
        assert "오류가 발생" in AnswerTemplates.COMPLAINT_REASON_ROUTING_ERROR


# =============================================================================
# Singleton Tests
# =============================================================================


class TestSingleton:
    """싱글톤 패턴 테스트."""

    def test_get_answer_guard_service_singleton(self):
        """싱글톤 인스턴스 반환."""
        reset_answer_guard_service()
        service1 = get_answer_guard_service()
        service2 = get_answer_guard_service()
        assert service1 is service2

    def test_reset_clears_singleton(self):
        """리셋 후 새 인스턴스 생성."""
        service1 = get_answer_guard_service()
        reset_answer_guard_service()
        service2 = get_answer_guard_service()
        assert service1 is not service2


# =============================================================================
# Integration Test (Mock-based)
# =============================================================================


class TestIntegration:
    """통합 테스트 (모킹 기반)."""

    def test_full_guard_flow_success(self, answer_guard, sample_sources):
        """전체 가드 플로우 - 성공 케이스."""
        # 1. 불만 체크 - 통과
        complaint = answer_guard.check_complaint_fast_path("연차 규정 알려줘", None)
        assert complaint is None

        # 2. Answerability 체크 - 통과
        is_answerable, _ = answer_guard.check_answerability(
            intent=Tier0Intent.POLICY_QA,
            sources=sample_sources,
            route_type=RouterRouteType.RAG_INTERNAL,
        )
        assert is_answerable is True

        # 3. Citation 검증 - 통과
        answer = "제10조에 따르면 연차휴가는 1년 이상 근무 시 발생합니다."
        is_valid, result = answer_guard.validate_citation(answer, sample_sources)
        assert is_valid is True

    def test_full_guard_flow_allowed_no_rag_with_soft_guardrail(self, answer_guard, empty_sources):
        """전체 가드 플로우 - RAG 없음이어도 허용 (Phase 44/45).

        Phase 44: 차단 대신 경고만 로그하고 LLM 일반 지식으로 답변 허용.
        Phase 45: 소프트 가드레일 prefix 추가로 사용자에게 주의 안내.
        """
        # 1. 불만 체크 - 통과
        complaint = answer_guard.check_complaint_fast_path("퇴직금 규정", None)
        assert complaint is None

        # 2. Answerability 체크 - 허용 (Phase 44)
        is_answerable, template = answer_guard.check_answerability(
            intent=Tier0Intent.POLICY_QA,
            sources=empty_sources,
            route_type=RouterRouteType.RAG_INTERNAL,
        )
        assert is_answerable is True
        assert template is None

        # 3. 소프트 가드레일 체크 - POLICY_QA + sources=0 → 활성화 (Phase 45)
        needs_soft_guardrail, prefix = answer_guard.check_soft_guardrail(
            intent=Tier0Intent.POLICY_QA,
            sources=empty_sources,
            domain="POLICY",
        )
        assert needs_soft_guardrail is True
        assert prefix is not None
        assert "승인된 사내 문서" in prefix

    def test_edu_status_template_not_mixed_with_policy(self, answer_guard, sample_sources):
        """[C] 교육 현황 템플릿이 정책 질문에 섞이지 않음 확인."""
        # POLICY_QA 의도로 요청
        debug_info = answer_guard.create_debug_info(
            intent=Tier0Intent.POLICY_QA,
            domain="POLICY",
            route_type=RouterRouteType.RAG_INTERNAL,
        )

        # Answerability 체크
        is_answerable, template = answer_guard.check_answerability(
            intent=Tier0Intent.POLICY_QA,
            sources=sample_sources,
            route_type=RouterRouteType.RAG_INTERNAL,
            debug_info=debug_info,
        )

        # POLICY 의도인데 교육 템플릿이 나오면 안 됨
        assert is_answerable is True
        # debug_info도 POLICY로 유지
        assert debug_info.intent == "POLICY_QA"
