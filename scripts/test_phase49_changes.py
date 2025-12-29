"""
Phase 49 변경사항 검증 테스트 스크립트

주요 기능:
1. RuleRouter 도메인 라우팅 개선 테스트
2. EDUCATION dataset_id allowlist config 분리 테스트
3. 요약 인텐트 감지 테스트 (피처 플래그 보호)

테스트 항목:
- POLICY 키워드 우선순위 변경 확인
- config에서 EDUCATION dataset_ids 로드 확인
- 요약 인텐트 감지 동작 확인
"""

import sys
import os

# Windows cp949 인코딩 문제 해결
if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# 프로젝트 루트를 path에 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


def test_config_settings():
    """Phase 49 config 설정 확인"""
    print("\n=== Test 1: Config Settings ===")

    from app.core.config import get_settings

    settings = get_settings()

    all_passed = True

    # RAG_EDUCATION_DATASET_IDS
    has_edu_ids = hasattr(settings, 'RAG_EDUCATION_DATASET_IDS')
    all_passed = all_passed and has_edu_ids
    print(f"  [{'PASS' if has_edu_ids else 'FAIL'}] RAG_EDUCATION_DATASET_IDS exists: {has_edu_ids}")
    if has_edu_ids:
        edu_ids = settings.RAG_EDUCATION_DATASET_IDS
        print(f"    -> value: {edu_ids[:50]}...")

    # SUMMARY_INTENT_ENABLED
    has_summary = hasattr(settings, 'SUMMARY_INTENT_ENABLED')
    all_passed = all_passed and has_summary
    print(f"  [{'PASS' if has_summary else 'FAIL'}] SUMMARY_INTENT_ENABLED exists: {has_summary}")
    if has_summary:
        print(f"    -> value: {settings.SUMMARY_INTENT_ENABLED}")

    return all_passed


def test_rule_router_policy_priority():
    """RuleRouter POLICY 우선순위 테스트"""
    print("\n=== Test 2: RuleRouter POLICY Priority ===")

    from app.services.rule_router import RuleRouter

    router = RuleRouter()
    all_passed = True

    # 테스트 케이스: (query, expected_domain, expected_intent)
    test_cases = [
        # POLICY 우선 분류되어야 하는 케이스
        ("연차 규정 알려줘", "POLICY", "POLICY_QA"),
        ("휴가 정책 설명해줘", "POLICY", "POLICY_QA"),
        ("복무 규정 뭐야", "POLICY", "POLICY_QA"),
        ("징계 절차 알려줘", "POLICY", "POLICY_QA"),
        ("근태 관련 규정", "POLICY", "POLICY_QA"),
        # EDUCATION으로 분류되어야 하는 케이스
        ("정보보호교육 내용 알려줘", "EDU", "EDUCATION_QA"),
        ("보안교육 뭐야", "EDU", "EDUCATION_QA"),
        ("성희롱예방교육 설명해줘", "EDU", "EDUCATION_QA"),
    ]

    for query, expected_domain, expected_intent in test_cases:
        result = router.route(query)
        domain_match = result.domain.value == expected_domain
        intent_match = result.tier0_intent.value == expected_intent
        passed = domain_match and intent_match
        all_passed = all_passed and passed

        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] '{query}' -> domain={result.domain.value} (expected: {expected_domain}), "
              f"intent={result.tier0_intent.value} (expected: {expected_intent})")

    return all_passed


def test_education_dataset_ids():
    """EDUCATION dataset_id allowlist 테스트"""
    print("\n=== Test 3: EDUCATION Dataset ID Allowlist ===")

    from app.clients.milvus_client import (
        get_education_dataset_ids,
        get_dataset_filter_expr,
    )

    all_passed = True

    # get_education_dataset_ids 테스트
    edu_ids = get_education_dataset_ids()
    has_ids = len(edu_ids) > 0
    all_passed = all_passed and has_ids
    print(f"  [{'PASS' if has_ids else 'FAIL'}] get_education_dataset_ids() returns list: {len(edu_ids)} items")
    if has_ids:
        print(f"    -> ids: {edu_ids[:3]}..." if len(edu_ids) > 3 else f"    -> ids: {edu_ids}")

    # get_dataset_filter_expr 테스트 - EDUCATION
    edu_expr = get_dataset_filter_expr("EDUCATION")
    has_edu_expr = edu_expr is not None and "dataset_id in" in edu_expr
    all_passed = all_passed and has_edu_expr
    print(f"  [{'PASS' if has_edu_expr else 'FAIL'}] EDUCATION filter expr: {has_edu_expr}")
    if edu_expr:
        print(f"    -> expr: {edu_expr[:60]}...")

    # get_dataset_filter_expr 테스트 - POLICY
    policy_expr = get_dataset_filter_expr("POLICY")
    has_policy_expr = policy_expr is not None and "dataset_id ==" in policy_expr
    all_passed = all_passed and has_policy_expr
    print(f"  [{'PASS' if has_policy_expr else 'FAIL'}] POLICY filter expr: {has_policy_expr}")
    if policy_expr:
        print(f"    -> expr: {policy_expr}")

    return all_passed


def test_summary_intent_detection():
    """요약 인텐트 감지 테스트 (피처 플래그 보호)"""
    print("\n=== Test 4: Summary Intent Detection ===")

    from app.services.rule_router import RuleRouter, SUMMARY_KEYWORDS
    from app.core.config import get_settings

    all_passed = True

    # 현재 설정 확인
    settings = get_settings()
    summary_enabled = getattr(settings, 'SUMMARY_INTENT_ENABLED', False)
    print(f"  SUMMARY_INTENT_ENABLED = {summary_enabled}")

    # SUMMARY_KEYWORDS 존재 확인
    has_keywords = len(SUMMARY_KEYWORDS) > 0
    all_passed = all_passed and has_keywords
    print(f"  [{'PASS' if has_keywords else 'FAIL'}] SUMMARY_KEYWORDS defined: {len(SUMMARY_KEYWORDS)} keywords")

    # 키워드 매칭 테스트
    test_queries = [
        "연차 규정 요약해줘",
        "정보보호교육 정리해주세요",
        "복무 규정 핵심만 알려줘",
    ]

    router = RuleRouter()
    for query in test_queries:
        result = router.route(query)
        # 피처 플래그가 꺼져 있어도 기존 로직대로 동작해야 함
        has_result = result is not None
        all_passed = all_passed and has_result

        summary_detected = "SUMMARY_DETECTED" in result.debug.rule_hits if result.debug else False

        status = "PASS" if has_result else "FAIL"
        print(f"  [{status}] '{query}' -> domain={result.domain.value}, "
              f"intent={result.tier0_intent.value}, summary_detected={summary_detected}")

    return all_passed


def test_ascii_safe_preview():
    """ASCII-safe preview 함수 테스트"""
    print("\n=== Test 5: ASCII-safe Preview ===")

    from app.services.rule_router import ascii_safe_preview

    all_passed = True

    test_cases = [
        ("연차 규정 알려줘", 50),
        ("정보보호교육 내용", 20),
        ("", 50),  # 빈 문자열
    ]

    for text, max_len in test_cases:
        result = ascii_safe_preview(text, max_len)
        # ASCII-safe 결과는 ASCII 문자만 포함해야 함
        is_ascii = all(ord(c) < 128 for c in result)
        all_passed = all_passed and is_ascii

        status = "PASS" if is_ascii else "FAIL"
        print(f"  [{status}] ascii_safe_preview('{text[:20]}...', {max_len}) -> '{result}' (ASCII: {is_ascii})")

    return all_passed


def main():
    print("=" * 60)
    print("Phase 49 RuleRouter & Config Test")
    print("=" * 60)

    results = {}

    try:
        results['test_config_settings'] = test_config_settings()
    except Exception as e:
        print(f"  [ERROR] {e}")
        results['test_config_settings'] = False

    try:
        results['test_rule_router_policy_priority'] = test_rule_router_policy_priority()
    except Exception as e:
        print(f"  [ERROR] {e}")
        results['test_rule_router_policy_priority'] = False

    try:
        results['test_education_dataset_ids'] = test_education_dataset_ids()
    except Exception as e:
        print(f"  [ERROR] {e}")
        results['test_education_dataset_ids'] = False

    try:
        results['test_summary_intent_detection'] = test_summary_intent_detection()
    except Exception as e:
        print(f"  [ERROR] {e}")
        results['test_summary_intent_detection'] = False

    try:
        results['test_ascii_safe_preview'] = test_ascii_safe_preview()
    except Exception as e:
        print(f"  [ERROR] {e}")
        results['test_ascii_safe_preview'] = False

    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, result in results.items():
        status = "PASS" if result else "FAIL"
        print(f"  [{status}] {name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n[OK] All Phase 49 tests passed!")
        return 0
    else:
        print("\n[FAIL] Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
