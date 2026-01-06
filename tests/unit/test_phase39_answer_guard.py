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

    def test_hallucinated_citation_blocked(self, answer_guard, sample_sources):
        """RAG 소스에 없는 조항 인용 → 차단 (Phase 55 정책 강화).

        Phase 55: RAG sources에 없는 조항 인용은 환각으로 간주하여 차단.
        (Phase 44 롤백: 환각 방지를 위해 strict 모드 기본 적용)
        """
        answer = "제99조 제5항에 따르면 특별휴가를 사용할 수 있습니다."
        is_valid, result = answer_guard.validate_citation(answer, sample_sources)
        # Phase 55: 환각 인용 차단
        assert is_valid is False
        assert "근거 문서를 확인" in result or "차단" in result.lower() or "죄송" in result

    def test_no_citation_in_answer_passes(self, answer_guard, sample_sources):
        """조항 인용이 없는 답변은 통과."""
        answer = "연차휴가에 대해 설명드리겠습니다. 1년 이상 근무하면 발생합니다."
        is_valid, result = answer_guard.validate_citation(answer, sample_sources)
        assert is_valid is True
        assert result == answer

    def test_citation_without_sources_blocked(self, answer_guard, empty_sources):
        """RAG 소스 없이 조항 인용 시 → 차단 (Phase 55 정책 강화).

        Phase 55: RAG sources가 없는 상태에서 조항 인용은 환각으로 간주하여 차단.
        (Phase 44 롤백: 환각 방지를 위해 strict 모드 기본 적용)
        """
        answer = "제10조에 의하면 연차가 발생합니다."
        is_valid, result = answer_guard.validate_citation(answer, empty_sources)
        # Phase 55: 환각 인용 차단
        assert is_valid is False
        assert "근거 문서를 확인" in result or "죄송" in result

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


# =============================================================================
# [H] Phase 56: Stats Out-of-Scope Fast Path Tests (통계 질문 조기 차단)
# =============================================================================


class TestStatsOutOfScopeFastPath:
    """Phase 56: 통계 질문 조기 차단 테스트."""

    def test_incident_stats_blocked(self, answer_guard):
        """INCIDENT + 통계 신호 → 차단."""
        test_queries = [
            "최근 1년 동안 가장 많이 위반된 보안 규정 TOP 5",
            "지난 3개월간 보안 사고 통계 알려줘",
            "올해 가장 빈번한 위반 유형이 뭐야",
            "상위 10개 보안 사고 유형",
            "보안 침해 건수 통계",
        ]
        for query in test_queries:
            result = answer_guard.check_stats_out_of_scope_fast_path(query, domain="INCIDENT")
            assert result is not None, f"Should block: {query}"
            assert "집계 데이터" in result or "통계" in result

    def test_incident_report_not_blocked(self, answer_guard):
        """INCIDENT + 신고/제보 → 통과 (차단 금지)."""
        bypass_queries = [
            "사고 신고할게요",
            "보안 사고 제보합니다",
            "해킹 발생 신고",
            "침해사고 접수하려고요",
        ]
        for query in bypass_queries:
            result = answer_guard.check_stats_out_of_scope_fast_path(query, domain="INCIDENT")
            assert result is None, f"Should bypass (not block): {query}"

    def test_non_incident_stats_not_blocked(self, answer_guard):
        """비-INCIDENT 도메인 통계 질문 → 차단 금지."""
        non_incident_queries = [
            ("최근 3개월 교육 이수율 통계", "EDUCATION"),
            ("TOP 3 인기 교육과정", "EDUCATION"),
            ("가장 많이 신청된 휴가 유형", "POLICY"),
            ("평균 근무시간 통계", "HR"),
        ]
        for query, domain in non_incident_queries:
            result = answer_guard.check_stats_out_of_scope_fast_path(query, domain=domain)
            assert result is None, f"Should not block non-incident: {query} (domain={domain})"

    def test_normal_incident_query_not_blocked(self, answer_guard):
        """일반 INCIDENT 질문 (통계 아님) → 차단 금지."""
        normal_queries = [
            "보안 사고 발생 시 대응 절차는?",
            "해킹 신고 방법 알려줘",
            "정보 유출 시 보고 체계",
        ]
        for query in normal_queries:
            result = answer_guard.check_stats_out_of_scope_fast_path(query, domain="INCIDENT")
            assert result is None, f"Should not block normal query: {query}"


# =============================================================================
# [I] Phase 56: Stats Language Sanitizer Tests (통계 표현 후처리)
# =============================================================================


class TestStatsLanguageSanitizer:
    """Phase 56: 통계 표현 후처리 테스트."""

    def test_sanitize_temporal_expressions(self, answer_guard, empty_sources):
        """시간 표현 치환 테스트."""
        answer = "최근 1년 동안 가장 많이 위반된 보안 규정은 다음과 같습니다."
        query = "최근 1년 위반 통계"

        sanitized, modified = answer_guard.sanitize_stats_language(
            answer=answer,
            query=query,
            sources=empty_sources,
        )

        assert modified is True
        assert "최근 1년" not in sanitized
        assert "일반적으로" in sanitized or "⚠️" in sanitized

    def test_sanitize_top_n_expressions(self, answer_guard, empty_sources):
        """TOP N 표현 치환 테스트."""
        answer = "TOP 5 위반 규정: 1. 비밀번호 정책..."
        query = "TOP 5 위반 규정"

        sanitized, modified = answer_guard.sanitize_stats_language(
            answer=answer,
            query=query,
            sources=empty_sources,
        )

        assert modified is True
        assert "TOP 5" not in sanitized
        assert "주요" in sanitized or "⚠️" in sanitized

    def test_sanitize_numeric_claims_removed(self, answer_guard, empty_sources):
        """수치 주장 문장 제거 테스트 - 시간 표현 포함 시."""
        # 시간 표현이 있어야 치환이 발생함 (통계 신호 + 시간 표현)
        answer = "최근 1년간 보안 위반 건수는 총 150 건입니다. 비밀번호 관련이 가장 많습니다."
        query = "최근 1년 위반 건수 통계"

        sanitized, modified = answer_guard.sanitize_stats_language(
            answer=answer,
            query=query,
            sources=empty_sources,
        )

        assert modified is True
        # 시간 표현이 치환됨
        assert "최근 1년" not in sanitized

    def test_no_sanitize_with_evidence(self, answer_guard):
        """소스에 근거가 있으면 치환 안 함."""
        sources_with_evidence = [
            ChatSource(
                doc_id="doc1",
                title="2024년 보안 보고서",
                snippet="2024년 상반기 보안 위반 건수는 총 150건으로 집계되었습니다.",
                score=0.9,
            ),
        ]
        answer = "최근 1년 동안 150건의 위반이 발생했습니다."
        query = "최근 1년 위반 통계"

        sanitized, modified = answer_guard.sanitize_stats_language(
            answer=answer,
            query=query,
            sources=sources_with_evidence,
        )

        # 소스에 수치 근거가 있으므로 치환 안 함
        assert modified is False
        assert sanitized == answer

    def test_no_sanitize_non_stats_query(self, answer_guard, empty_sources):
        """통계 신호 없는 질문 → 치환 안 함."""
        answer = "연차휴가는 1년 근무 시 15일이 발생합니다."
        query = "연차휴가 규정 알려줘"

        sanitized, modified = answer_guard.sanitize_stats_language(
            answer=answer,
            query=query,
            sources=empty_sources,
        )

        assert modified is False
        assert sanitized == answer

    def test_prefix_added_when_modified(self, answer_guard, empty_sources):
        """수정된 경우 prefix 추가 확인."""
        answer = "최근 3개월 동안 가장 많이 발생한 사고 유형입니다."
        query = "최근 3개월 사고 통계"

        sanitized, modified = answer_guard.sanitize_stats_language(
            answer=answer,
            query=query,
            sources=empty_sources,
            add_prefix=True,
        )

        assert modified is True
        assert sanitized.startswith("⚠️")
        assert "통계 집계" in sanitized or "안내문서" in sanitized


class TestHasStatsSignal:
    """has_stats_signal 헬퍼 테스트."""

    def test_detects_stats_signals(self, answer_guard):
        """통계 신호 감지 테스트."""
        stats_queries = [
            "최근 1년 통계",
            "TOP 5 순위",
            "가장 많이 발생한",
            "상위 10개",
            "위반 건수",
        ]
        for query in stats_queries:
            assert answer_guard.has_stats_signal(query) is True, f"Should detect: {query}"

    def test_no_stats_signal(self, answer_guard):
        """통계 신호 없는 질문."""
        normal_queries = [
            "연차휴가 규정 알려줘",
            "출장비 정산 방법",
            "보안 교육 이수 방법",
        ]
        for query in normal_queries:
            assert answer_guard.has_stats_signal(query) is False, f"Should not detect: {query}"
