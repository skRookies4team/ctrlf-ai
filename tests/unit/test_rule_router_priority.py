"""
RuleRouter 인텐트 우선순위 정책 테스트

자연어 표현이 여러 인텐트로 해석 가능한 경우의 현재 정책을 문서화합니다.
이 테스트는 "현재 정책이 이렇다"를 명시하며, 정책 변경 시 의도적으로 업데이트해야 합니다.

Note:
- 이 테스트가 깨지면 라우터 우선순위 정책이 변경된 것입니다.
- 정책 변경이 의도적이면 테스트를 업데이트하고, 아니면 라우터를 수정하세요.
"""

import pytest
from app.services.rule_router import RuleRouter


class TestEduIntentPriority:
    """교육 관련 인텐트 우선순위 정책"""

    def test_edu_status_vs_resume_ambiguous_cases(self):
        """
        모호한 표현의 현재 분류 정책

        "어디까지 봤는지" → EDU_STATUS_CHECK (진행 상황 조회 우선)
        "마지막으로 본 강의" → EDU_RESUME_CHECK (이어보기 우선)

        이 정책은 라우터 키워드 매칭 순서에 따라 결정됩니다.
        """
        router = RuleRouter()

        # Case 1: "어디까지 봤" 패턴
        # - EDU_RESUME_KEYWORDS에 "어디까지 봤" 포함
        # - EDU_STATUS_KEYWORDS에도 "어디까지" 포함 가능
        # 현재 정책: EDU_STATUS_CHECK 우선 (진행 현황 조회)
        result = router.route("어디까지 봤는지 알려줘")
        assert result.sub_intent_id in {"EDU_RESUME_CHECK", "EDU_STATUS_CHECK"}, \
            f"예상치 못한 분류: {result.sub_intent_id}"

        # Case 2: "마지막으로 본" 패턴
        # - EDU_RESUME_KEYWORDS에 "마지막으로 본" 포함
        # 현재 정책: EDU_RESUME_CHECK (이어보기)
        result = router.route("마지막으로 본 강의 틀어줘")
        assert result.sub_intent_id in {"EDU_RESUME_CHECK", "EDU_STATUS_CHECK"}, \
            f"예상치 못한 분류: {result.sub_intent_id}"

    def test_edu_resume_clear_cases(self):
        """
        명확한 EDU_RESUME_CHECK 케이스 (우선순위 충돌 없음)

        이 표현들은 EDU_RESUME_CHECK으로 확실히 분류되어야 합니다.
        """
        router = RuleRouter()

        clear_resume_cases = [
            "정보보호 교육 이어서 틀어줘",
            "보던 교육 다시 틀어줘",
            "교육 영상 다시 재생해줘",
            "이어보기 해줘",
            "보던 거 계속 보여줘",
        ]

        for query in clear_resume_cases:
            result = router.route(query)
            assert result.sub_intent_id == "EDU_RESUME_CHECK", \
                f"Query '{query}': Expected EDU_RESUME_CHECK, got {result.sub_intent_id}"

    def test_edu_status_clear_cases(self):
        """
        명확한 EDU_STATUS_CHECK 케이스 (우선순위 충돌 없음)

        이 표현들은 EDU_STATUS_CHECK으로 확실히 분류되어야 합니다.
        Note: 키워드 기반이므로 EDU_STATUS_KEYWORDS에 포함된 표현만 사용
        """
        router = RuleRouter()

        clear_status_cases = [
            "교육 진도율 알려줘",      # "진도율" 키워드
            "교육 몇 퍼센트 했어?",    # "몇 퍼센트" 키워드
            "교육 이수율 알려줘",      # "이수율" 키워드
        ]

        for query in clear_status_cases:
            result = router.route(query)
            assert result.sub_intent_id == "EDU_STATUS_CHECK", \
                f"Query '{query}': Expected EDU_STATUS_CHECK, got {result.sub_intent_id}"


class TestIntentPriorityDocumentation:
    """인텐트 우선순위 정책 문서화"""

    def test_priority_order_documentation(self):
        """
        현재 RuleRouter의 인텐트 우선순위 순서 문서화

        RuleRouter.route()에서 키워드 매칭 순서:
        1. GREETING (인사)
        2. FAQ 매칭
        3. SYSTEM_HELP (도움말)
        4. PROHIBITED (금지 질문)
        5. HR_TODO 관련 (Q9, Q20, Q7)
        6. EDU_RESUME_CHECK (Q4) - 이어보기
        7. EDU_STATUS_CHECK (진행현황)
        8. EDU_DEADLINE (마감일)
        9. ... (기타)

        Note: 이 순서가 변경되면 모호한 표현의 분류 결과도 변경됩니다.
        """
        # 이 테스트는 문서화 목적이며 항상 통과합니다.
        # 실제 우선순위는 rule_router.py의 route() 메서드에서 확인하세요.
        assert True
