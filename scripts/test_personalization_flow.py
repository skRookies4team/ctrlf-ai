#!/usr/bin/env python3
"""
개인화 흐름 디버깅 테스트 스크립트

RuleRouter -> PersonalizationMapper -> PersonalizationClient 흐름을 테스트합니다.
"""

import asyncio
import sys
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.rule_router import RuleRouter
from app.services.personalization_mapper import (
    to_personalization_q,
    is_personalization_request,
    extract_period_from_query,
)
from app.clients.personalization_client import PersonalizationClient
from app.core.config import get_settings


def test_rule_router():
    """RuleRouter 테스트"""
    print("\n" + "=" * 60)
    print("1. RuleRouter 테스트")
    print("=" * 60)

    router = RuleRouter()

    test_queries = [
        "내 연차 몇 개 남았어?",
        "연차 며칠 남았어?",
        "남은 연차 알려줘",
        "내 성희롱 교육 퀴즈 점수 몇점이야?",
        "퀴즈 점수 알려줘",
        "내 복지포인트 얼마야?",
        "이번 주 해야할 교육 뭐야?",
        "미이수 교육 알려줘",
    ]

    for query in test_queries:
        result = router.route(query)
        print(f"\n질문: {query}")
        print(f"  -> tier0_intent: {result.tier0_intent.value}")
        print(f"  -> domain: {result.domain.value}")
        print(f"  -> route_type: {result.route_type.value}")
        print(f"  -> sub_intent_id: {result.sub_intent_id}")
        print(f"  -> confidence: {result.confidence}")
        print(f"  -> rule_hits: {result.debug.rule_hits if result.debug else 'N/A'}")


def test_personalization_mapper():
    """PersonalizationMapper 테스트"""
    print("\n" + "=" * 60)
    print("2. PersonalizationMapper 테스트")
    print("=" * 60)

    test_cases = [
        ("HR_LEAVE_CHECK", "내 연차 몇 개 남았어?"),
        ("HR_LEAVE_CHECK", "복지포인트 얼마야?"),
        ("HR_LEAVE_CHECK", "연차 사용 내역 알려줘"),
        ("EDU_STATUS_CHECK", "미이수 교육 알려줘"),
        ("EDU_STATUS_CHECK", "이번 주 해야할 교육 뭐야?"),
        ("QUIZ_SCORE_CHECK", "내 퀴즈 점수 알려줘"),
        ("QUIZ_SCORE_CHECK", "성희롱 교육 퀴즈 점수 몇점이야?"),
    ]

    for sub_intent_id, query in test_cases:
        q = to_personalization_q(sub_intent_id, query)
        is_personal = is_personalization_request(sub_intent_id)
        period = extract_period_from_query(query)

        print(f"\n질문: {query}")
        print(f"  -> sub_intent_id: {sub_intent_id}")
        print(f"  -> is_personalization: {is_personal}")
        print(f"  -> mapped Q: {q}")
        print(f"  -> period: {period}")


async def test_personalization_client():
    """PersonalizationClient 테스트"""
    print("\n" + "=" * 60)
    print("3. PersonalizationClient 테스트")
    print("=" * 60)

    settings = get_settings()
    print(f"\nPERSONALIZATION_MODE: {settings.PERSONALIZATION_MODE}")
    print(f"BACKEND_BASE_URL: {settings.BACKEND_BASE_URL}")

    client = PersonalizationClient()

    test_cases = [
        ("Q11", "this-year"),   # 남은 연차
        ("Q14", "this-year"),   # 복지포인트
        ("Q1", "this-year"),    # 미이수 필수교육
        ("Q9", "this-week"),    # 이번 주 할 일
        ("Q5", "this-year"),    # 퀴즈 평균 점수
    ]

    for q, period in test_cases:
        print(f"\n테스트: Q={q}, period={period}")
        try:
            facts = await client.resolve_facts(
                sub_intent_id=q,
                user_id="test-user-123",
                period=period,
            )
            print(f"  -> sub_intent_id: {facts.sub_intent_id}")
            print(f"  -> error: {facts.error}")
            print(f"  -> metrics: {facts.metrics}")
            print(f"  -> items_count: {len(facts.items)}")
        except Exception as e:
            print(f"  -> ERROR: {e}")


def test_full_flow():
    """전체 흐름 테스트 (Router -> Mapper -> Q 확정)"""
    print("\n" + "=" * 60)
    print("4. 전체 흐름 테스트 (Router -> Mapper)")
    print("=" * 60)

    router = RuleRouter()

    test_queries = [
        "내 연차 몇 개 남았어?",
        "내 성희롱 교육 퀴즈 점수 몇점이야?",
        "복지포인트 잔액 알려줘",
        "이번 주 해야할 교육 있어?",
    ]

    for query in test_queries:
        print(f"\n질문: {query}")

        # Step 1: RuleRouter
        result = router.route(query)
        print(f"  [RuleRouter]")
        print(f"    -> tier0_intent: {result.tier0_intent.value}")
        print(f"    -> sub_intent_id: {result.sub_intent_id}")

        # Step 2: 개인화 여부 확인
        sub_intent_id = result.sub_intent_id or ""
        is_personal = is_personalization_request(sub_intent_id)
        print(f"  [is_personalization_request]")
        print(f"    -> {is_personal}")

        # Step 3: Q 매핑
        if sub_intent_id:
            q = to_personalization_q(sub_intent_id, query)
            print(f"  [to_personalization_q]")
            print(f"    -> Q: {q}")
        else:
            print(f"  [to_personalization_q]")
            print(f"    -> sub_intent_id 없음 - 개인화 불가!")

        # Step 4: tier0_intent가 BACKEND_STATUS인지 확인
        from app.models.router_types import Tier0Intent
        if result.tier0_intent != Tier0Intent.BACKEND_STATUS:
            print(f"  [WARNING] tier0_intent가 BACKEND_STATUS가 아님!")
            print(f"    -> 개인화 흐름으로 진입하지 않음")


def main():
    print("=" * 60)
    print("개인화 흐름 디버깅 테스트")
    print("=" * 60)

    # 1. RuleRouter 테스트
    test_rule_router()

    # 2. PersonalizationMapper 테스트
    test_personalization_mapper()

    # 3. 전체 흐름 테스트
    test_full_flow()

    # 4. PersonalizationClient 테스트 (async)
    print("\n비동기 테스트 실행...")
    asyncio.run(test_personalization_client())

    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)


if __name__ == "__main__":
    main()
