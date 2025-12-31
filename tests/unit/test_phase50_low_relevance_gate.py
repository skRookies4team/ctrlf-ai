"""
Phase 50: LowRelevanceGate 개선 회귀 테스트

테스트 목표:
- anchor_keywords 생성 시 행동 표현 제거 확인
- anchor_gate 매칭 대상 확장 (title+snippet+article_label+article_path)
- anchor_gate 안전장치 (최소 1개 보장)
- score_gate 임계값 0.55 확인

요구사항 쿼리:
- "연차휴가 규정 알려줘"
- "보안 관련 문서 요약해줘"
- "정책 관련 문서 요약해줘"
"""

import pytest
from unittest.mock import patch

from app.models.chat import ChatSource
from app.services.chat.rag_handler import (
    extract_anchor_keywords,
    check_anchor_keywords_in_sources,
    apply_low_relevance_gate,
    ACTION_TOKENS,
    ANCHOR_GATE_MIN_KEEP,
)


class TestExtractAnchorKeywords:
    """anchor_keywords 추출 테스트"""

    def test_removes_action_tokens_simple(self):
        """행동 표현 토큰이 제거되는지 확인"""
        # "알려줘"는 ACTION_TOKENS에 있으므로 제거됨
        result = extract_anchor_keywords("연차휴가 규정 알려줘")
        assert "알려줘" not in result
        assert "연차휴가" in result

    def test_removes_action_tokens_yoyak(self):
        """'요약해줘' 행동 표현이 제거되는지 확인"""
        result = extract_anchor_keywords("보안 관련 문서 요약해줘")
        assert "요약해줘" not in result
        assert "요약" not in result  # ACTION_TOKENS에 포함
        assert "보안" in result

    def test_removes_action_suffix(self):
        """행동 접미사가 제거되고 명사만 남는지 확인"""
        # "정책설명해줘" → "정책" (접미사 제거)
        result = extract_anchor_keywords("정책설명해줘")
        # 접미사 제거 후 "정책"만 남아야 하지만, "정책"이 stopwords일 수 있음
        # stopwords에 있으면 빈 결과
        # 이 테스트는 접미사가 정상 제거됨을 확인
        assert "설명해줘" not in result
        assert "정책설명해줘" not in result

    def test_preserves_core_nouns(self):
        """핵심 명사는 보존되는지 확인"""
        result = extract_anchor_keywords("정보보호 교육 내용 알려줘")
        # "정보보호"는 명사이므로 유지되어야 함
        assert "정보보호" in result
        # "알려줘"는 제거
        assert "알려줘" not in result

    def test_query_with_only_action_tokens(self):
        """행동 표현만 있는 쿼리는 빈 결과"""
        result = extract_anchor_keywords("알려줘 해줘 설명해")
        # 모든 토큰이 ACTION_TOKENS이므로 빈 결과
        assert len(result) == 0

    def test_compound_action_expressions(self):
        """복합 행동 표현 테스트"""
        result = extract_anchor_keywords("연차 규정 정리해주세요")
        assert "정리해주세요" not in result
        assert "정리" not in result  # ACTION_TOKENS에 포함


class TestCheckAnchorKeywordsInSources:
    """anchor_keywords 매칭 테스트"""

    def _make_source(
        self,
        snippet: str = "",
        title: str = "",
        article_label: str = None,
        article_path: str = None,
        score: float = 0.7,
    ) -> ChatSource:
        """테스트용 ChatSource 생성"""
        return ChatSource(
            doc_id="test-doc",
            title=title,
            snippet=snippet,
            score=score,
            article_label=article_label,
            article_path=article_path,
        )

    def test_matches_in_snippet(self):
        """snippet에서 키워드 매칭"""
        sources = [self._make_source(snippet="연차휴가 관련 규정입니다.")]
        result = check_anchor_keywords_in_sources({"연차휴가"}, sources)
        assert result is True

    def test_matches_in_title(self):
        """title에서 키워드 매칭"""
        sources = [self._make_source(title="연차휴가 규정")]
        result = check_anchor_keywords_in_sources({"연차휴가"}, sources)
        assert result is True

    def test_matches_in_article_label(self):
        """article_label에서 키워드 매칭 (Phase 50 확장)"""
        sources = [self._make_source(
            snippet="일반 내용",
            article_label="제5조 (보안 관련 조항)"
        )]
        result = check_anchor_keywords_in_sources({"보안"}, sources)
        assert result is True

    def test_matches_in_article_path(self):
        """article_path에서 키워드 매칭 (Phase 50 확장)"""
        sources = [self._make_source(
            snippet="일반 내용",
            article_path="제3장 정보보호 > 제10조"
        )]
        result = check_anchor_keywords_in_sources({"정보보호"}, sources)
        assert result is True

    def test_no_match_returns_false(self):
        """매칭 없으면 False 반환"""
        sources = [self._make_source(snippet="관련 없는 내용입니다.")]
        result = check_anchor_keywords_in_sources({"보안"}, sources)
        assert result is False

    def test_empty_keywords_returns_true(self):
        """빈 키워드 세트는 통과"""
        sources = [self._make_source(snippet="어떤 내용")]
        result = check_anchor_keywords_in_sources(set(), sources)
        assert result is True


class TestApplyLowRelevanceGate:
    """LowRelevanceGate 적용 테스트"""

    def _make_source(self, score: float, snippet: str = "테스트") -> ChatSource:
        """테스트용 ChatSource 생성"""
        return ChatSource(
            doc_id="test-doc",
            title="테스트 문서",
            snippet=snippet,
            score=score,
        )

    def test_passes_high_score_with_anchor_match(self):
        """높은 점수 + anchor 매칭 시 통과"""
        sources = [
            self._make_source(0.8, "연차휴가 관련 내용"),
            self._make_source(0.7, "휴가 규정 상세"),
        ]
        result, reason = apply_low_relevance_gate(sources, "연차휴가 규정 알려줘", "POLICY")
        assert len(result) == 2
        assert reason is None

    def test_soft_demote_low_score_keeps_min(self):
        """낮은 점수도 최소 1개는 유지 (Phase 50 안전장치)"""
        sources = [
            self._make_source(0.50, "낮은 점수 문서"),
            self._make_source(0.45, "더 낮은 점수"),
        ]
        result, reason = apply_low_relevance_gate(sources, "테스트 쿼리", "POLICY")
        # max_score=0.50 < threshold=0.55 이지만 최소 1개 유지
        assert len(result) >= ANCHOR_GATE_MIN_KEEP
        assert "soft" in reason.lower() if reason else True

    def test_soft_demote_no_anchor_match_keeps_min(self):
        """anchor 미매칭도 최소 1개는 유지 (Phase 50 안전장치)"""
        sources = [
            self._make_source(0.8, "완전 다른 내용"),
            self._make_source(0.7, "매칭 안 되는 내용"),
        ]
        result, reason = apply_low_relevance_gate(sources, "보안 정책 알려줘", "POLICY")
        # anchor "보안"이 sources에 없지만 최소 1개 유지
        assert len(result) >= ANCHOR_GATE_MIN_KEEP

    def test_empty_sources_returns_empty(self):
        """빈 sources는 그대로 반환"""
        result, reason = apply_low_relevance_gate([], "쿼리", "POLICY")
        assert result == []
        assert reason is None


class TestRegressionQueries:
    """프롬프트 요구사항 회귀 테스트

    테스트 쿼리:
    - "연차휴가 규정 알려줘"
    - "보안 관련 문서 요약해줘"
    - "정책 관련 문서 요약해줘"
    """

    def _make_source(
        self,
        score: float,
        snippet: str,
        title: str = "테스트 문서",
    ) -> ChatSource:
        return ChatSource(
            doc_id="test-doc",
            title=title,
            snippet=snippet,
            score=score,
        )

    def test_regression_yeoncha_query(self):
        """'연차휴가 규정 알려줘' 쿼리 테스트

        기대: anchor_keywords={'연차휴가'}, '알려줘' 제거됨
        """
        query = "연차휴가 규정 알려줘"
        keywords = extract_anchor_keywords(query)

        # 행동 표현 제거 확인
        assert "알려줘" not in keywords
        # 핵심 명사 유지 확인
        assert "연차휴가" in keywords

        # Gate 통과 테스트 (연차휴가가 포함된 문서)
        sources = [
            self._make_source(0.65, "연차휴가 신청 방법 및 규정 안내"),
            self._make_source(0.58, "휴가 관련 일반 정보"),
        ]
        result, reason = apply_low_relevance_gate(sources, query, "POLICY")

        # 결과가 0개가 아니어야 함
        assert len(result) >= 1

    def test_regression_boan_yoyak_query(self):
        """'보안 관련 문서 요약해줘' 쿼리 테스트

        기대: anchor_keywords={'보안'}, '요약해줘' 제거됨
        """
        query = "보안 관련 문서 요약해줘"
        keywords = extract_anchor_keywords(query)

        # 행동 표현 제거 확인
        assert "요약해줘" not in keywords
        assert "요약" not in keywords
        # 핵심 명사 유지 확인
        assert "보안" in keywords

        # Gate 통과 테스트 (보안이 포함된 문서)
        sources = [
            self._make_source(0.57, "보안 정책 개요"),
            self._make_source(0.54, "정보보호 지침"),
        ]
        result, reason = apply_low_relevance_gate(sources, query, "POLICY")

        # 점수가 낮아도 최소 1개는 유지
        assert len(result) >= 1

    def test_regression_jeongchaek_yoyak_query(self):
        """'정책 관련 문서 요약해줘' 쿼리 테스트

        기대: '요약해줘' 제거, '정책'은 stopwords일 수 있음
        """
        query = "정책 관련 문서 요약해줘"
        keywords = extract_anchor_keywords(query)

        # 행동 표현 제거 확인
        assert "요약해줘" not in keywords
        assert "요약" not in keywords

        # '정책'과 '관련'은 stopwords이므로 빈 결과일 수 있음
        # 이 경우에도 gate가 통과해야 함 (빈 keywords → 자동 통과)

        sources = [
            self._make_source(0.56, "회사 정책 문서"),
            self._make_source(0.52, "일반 규정 안내"),
        ]
        result, reason = apply_low_relevance_gate(sources, query, "POLICY")

        # 결과가 0개가 아니어야 함
        assert len(result) >= 1


class TestConfigValues:
    """설정값 확인 테스트"""

    def test_min_max_score_threshold(self):
        """RAG_MIN_MAX_SCORE가 0.55인지 확인"""
        from app.core.config import Settings
        # 캐시 문제 방지를 위해 Settings 클래스 직접 사용
        settings = Settings()
        assert settings.RAG_MIN_MAX_SCORE == 0.55

    def test_anchor_gate_min_keep(self):
        """ANCHOR_GATE_MIN_KEEP이 1 이상인지 확인"""
        assert ANCHOR_GATE_MIN_KEEP >= 1

    def test_action_tokens_contain_key_expressions(self):
        """ACTION_TOKENS에 주요 행동 표현이 포함되어 있는지 확인"""
        assert "요약해줘" in ACTION_TOKENS
        assert "알려줘" in ACTION_TOKENS
        assert "설명해줘" in ACTION_TOKENS
        assert "정리해줘" in ACTION_TOKENS
        assert "요약" in ACTION_TOKENS


class TestAiLogUrlFix:
    """ai_log URL 슬래시 중복 수정 테스트"""

    def test_url_no_double_slash(self):
        """URL에 이중 슬래시가 없는지 확인"""
        from unittest.mock import patch, MagicMock
        from app.services.ai_log_service import AILogService

        # backend_base_url이 trailing slash로 끝나는 경우 테스트
        with patch('app.services.ai_log_service.settings') as mock_settings:
            mock_settings.backend_base_url = "http://backend:8080/"
            mock_settings.BACKEND_API_TOKEN = None

            service = AILogService()

            # 이중 슬래시가 없어야 함
            assert "//" not in service._backend_log_endpoint.replace("http://", "")
            assert service._backend_log_endpoint == "http://backend:8080/api/ai-logs"

    def test_url_without_trailing_slash(self):
        """trailing slash 없는 URL도 정상 동작"""
        from unittest.mock import patch
        from app.services.ai_log_service import AILogService

        with patch('app.services.ai_log_service.settings') as mock_settings:
            mock_settings.backend_base_url = "http://backend:8080"
            mock_settings.BACKEND_API_TOKEN = None

            service = AILogService()

            assert service._backend_log_endpoint == "http://backend:8080/api/ai-logs"
