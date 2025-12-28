"""
Phase 48 변경사항 검증 테스트 스크립트

주요 기능:
1. Low-relevance Gate (score 하드 게이트 + 앵커 키워드 게이트)
2. domain → dataset_id 필터 강제 적용

테스트 항목:
- anchor keyword 추출 테스트
- Low-relevance Gate 로직 테스트
- dataset_id 필터 생성 테스트
"""

import sys
import os

# 프로젝트 루트를 path에 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


def test_config_settings():
    """Phase 48 config 설정 확인"""
    print("\n=== Test 1: Config Settings ===")

    from app.core.config import get_settings

    settings = get_settings()

    all_passed = True

    # RAG_MIN_MAX_SCORE
    has_min_score = hasattr(settings, 'RAG_MIN_MAX_SCORE')
    all_passed = all_passed and has_min_score
    print(f"  [{'PASS' if has_min_score else 'FAIL'}] RAG_MIN_MAX_SCORE exists: {has_min_score}")
    if has_min_score:
        print(f"    -> value: {settings.RAG_MIN_MAX_SCORE}")

    # RAG_ANCHOR_STOPWORDS
    has_stopwords = hasattr(settings, 'RAG_ANCHOR_STOPWORDS')
    all_passed = all_passed and has_stopwords
    print(f"  [{'PASS' if has_stopwords else 'FAIL'}] RAG_ANCHOR_STOPWORDS exists: {has_stopwords}")
    if has_stopwords:
        stopwords_preview = settings.RAG_ANCHOR_STOPWORDS[:50] + "..."
        print(f"    -> preview: {stopwords_preview}")

    # RAG_DATASET_FILTER_ENABLED
    has_filter = hasattr(settings, 'RAG_DATASET_FILTER_ENABLED')
    all_passed = all_passed and has_filter
    print(f"  [{'PASS' if has_filter else 'FAIL'}] RAG_DATASET_FILTER_ENABLED exists: {has_filter}")
    if has_filter:
        print(f"    -> value: {settings.RAG_DATASET_FILTER_ENABLED}")

    return all_passed


def test_anchor_keyword_extraction():
    """앵커 키워드 추출 테스트"""
    print("\n=== Test 2: Anchor Keyword Extraction ===")

    from app.services.chat.rag_handler import (
        get_anchor_stopwords,
        extract_anchor_keywords,
    )

    all_passed = True

    # 불용어 로드 테스트
    stopwords = get_anchor_stopwords()
    has_stopwords = len(stopwords) > 0
    all_passed = all_passed and has_stopwords
    print(f"  [{'PASS' if has_stopwords else 'FAIL'}] Stopwords loaded: {len(stopwords)} words")

    # 앵커 키워드 추출 테스트
    test_cases = [
        ("연차 규정 알려줘", {"연차"}),  # "규정", "알려줘"는 불용어
        ("근태 관련 문서", {"근태"}),      # "관련", "문서"는 불용어
        ("법무 정책 7번", {"법무", "7번"}),
        ("총무 관련 문서 요약해줘", {"총무"}),
    ]

    for query, expected_contains in test_cases:
        keywords = extract_anchor_keywords(query)
        # expected 중 최소 하나는 포함되어야 함
        has_expected = any(kw in keywords for kw in expected_contains)
        all_passed = all_passed and has_expected
        print(f"  [{'PASS' if has_expected else 'FAIL'}] '{query}' -> {keywords} (expected: {expected_contains})")

    return all_passed


def test_anchor_keyword_check():
    """앵커 키워드 매칭 테스트"""
    print("\n=== Test 3: Anchor Keyword in Sources Check ===")

    from app.services.chat.rag_handler import check_anchor_keywords_in_sources
    from app.models.chat import ChatSource

    all_passed = True

    # 테스트 소스 생성
    source1 = ChatSource(
        doc_id="doc1",
        title="사내규정 문서",
        snippet="제1조 (목적) 이 규정은 회사의 복무 규정을 정하는 것을 목적으로 한다.",
        score=0.85,
    )
    source2 = ChatSource(
        doc_id="doc2",
        title="정보보안 교육",
        snippet="개인정보 보호에 대한 교육 내용입니다. 개인정보 처리 절차를 설명합니다.",
        score=0.78,
    )

    # 테스트 1: "복무" 키워드 - 매칭되어야 함
    keywords1 = {"복무"}
    result1 = check_anchor_keywords_in_sources(keywords1, [source1, source2])
    passed1 = result1 is True
    all_passed = all_passed and passed1
    print(f"  [{'PASS' if passed1 else 'FAIL'}] keywords={keywords1} in sources: {result1}")

    # 테스트 2: "연차" 키워드 - 매칭 안 되어야 함
    keywords2 = {"연차"}
    result2 = check_anchor_keywords_in_sources(keywords2, [source1, source2])
    passed2 = result2 is False
    all_passed = all_passed and passed2
    print(f"  [{'PASS' if passed2 else 'FAIL'}] keywords={keywords2} not in sources: {not result2}")

    # 테스트 3: "개인정보" 키워드 - 매칭되어야 함
    keywords3 = {"개인정보"}
    result3 = check_anchor_keywords_in_sources(keywords3, [source1, source2])
    passed3 = result3 is True
    all_passed = all_passed and passed3
    print(f"  [{'PASS' if passed3 else 'FAIL'}] keywords={keywords3} in sources: {result3}")

    # 테스트 4: 빈 키워드 - 항상 통과
    keywords4 = set()
    result4 = check_anchor_keywords_in_sources(keywords4, [source1, source2])
    passed4 = result4 is True
    all_passed = all_passed and passed4
    print(f"  [{'PASS' if passed4 else 'FAIL'}] empty keywords always passes: {result4}")

    return all_passed


def test_low_relevance_gate():
    """Low-relevance Gate 전체 로직 테스트"""
    print("\n=== Test 4: Low-relevance Gate ===")

    from app.services.chat.rag_handler import apply_low_relevance_gate
    from app.models.chat import ChatSource

    all_passed = True

    # 테스트 소스 - 높은 점수
    high_score_sources = [
        ChatSource(doc_id="doc1", title="사규", snippet="복무 규정 내용", score=0.85),
        ChatSource(doc_id="doc2", title="사규", snippet="휴가 규정 내용", score=0.72),
    ]

    # 테스트 소스 - 낮은 점수
    low_score_sources = [
        ChatSource(doc_id="doc1", title="사규", snippet="목적 조항", score=0.45),
        ChatSource(doc_id="doc2", title="사규", snippet="정의 조항", score=0.38),
    ]

    # 테스트 1: 높은 점수 + 키워드 매칭 -> 통과
    result1, reason1 = apply_low_relevance_gate(
        sources=high_score_sources,
        query="복무 규정 알려줘",
        domain="POLICY",
    )
    passed1 = len(result1) > 0 and reason1 is None
    all_passed = all_passed and passed1
    print(f"  [{'PASS' if passed1 else 'FAIL'}] High score + keyword match -> PASSED: {len(result1)} sources")

    # 테스트 2: 낮은 점수 -> 강등 (score gate)
    result2, reason2 = apply_low_relevance_gate(
        sources=low_score_sources,
        query="휴가 규정 알려줘",
        domain="POLICY",
    )
    passed2 = len(result2) == 0 and reason2 == "max_score_below_threshold"
    all_passed = all_passed and passed2
    print(f"  [{'PASS' if passed2 else 'FAIL'}] Low score -> DEMOTED: reason={reason2}")

    # 테스트 3: 높은 점수 + 키워드 미매칭 -> 강등 (anchor gate)
    result3, reason3 = apply_low_relevance_gate(
        sources=high_score_sources,
        query="연차 규정 알려줘",  # "연차"는 소스에 없음
        domain="POLICY",
    )
    passed3 = len(result3) == 0 and reason3 == "no_anchor_term_match"
    all_passed = all_passed and passed3
    print(f"  [{'PASS' if passed3 else 'FAIL'}] Keyword mismatch -> DEMOTED: reason={reason3}")

    # 테스트 4: 빈 소스 -> 그대로 반환
    result4, reason4 = apply_low_relevance_gate(
        sources=[],
        query="테스트 쿼리",
        domain="POLICY",
    )
    passed4 = len(result4) == 0 and reason4 is None
    all_passed = all_passed and passed4
    print(f"  [{'PASS' if passed4 else 'FAIL'}] Empty sources -> stays empty: {len(result4)}")

    return all_passed


def test_dataset_filter_expr():
    """domain -> dataset_id 필터 표현식 생성 테스트"""
    print("\n=== Test 5: Dataset Filter Expression ===")

    from app.clients.milvus_client import get_dataset_filter_expr, DOMAIN_DATASET_MAPPING

    all_passed = True

    # 매핑 확인
    has_policy = "POLICY" in DOMAIN_DATASET_MAPPING
    has_edu = "EDUCATION" in DOMAIN_DATASET_MAPPING
    all_passed = all_passed and has_policy and has_edu
    print(f"  [{'PASS' if has_policy else 'FAIL'}] POLICY in mapping: {has_policy}")
    print(f"  [{'PASS' if has_edu else 'FAIL'}] EDUCATION in mapping: {has_edu}")

    # 필터 표현식 생성 테스트
    test_cases = [
        ("POLICY", True),     # 필터 생성되어야 함
        ("policy", True),     # 소문자도 동작
        ("EDUCATION", True),  # 필터 생성되어야 함
        ("GENERAL", False),   # 매핑 없음 -> None
        (None, False),        # None -> None
    ]

    for domain, should_have_filter in test_cases:
        expr = get_dataset_filter_expr(domain)
        has_filter = expr is not None
        passed = has_filter == should_have_filter
        all_passed = all_passed and passed
        print(f"  [{'PASS' if passed else 'FAIL'}] domain={domain} -> filter={expr}")

    return all_passed


def main():
    print("=" * 60)
    print("Phase 48 Low-relevance Gate Test")
    print("=" * 60)

    results = {}

    try:
        results['test_config_settings'] = test_config_settings()
    except Exception as e:
        print(f"  [ERROR] {e}")
        results['test_config_settings'] = False

    try:
        results['test_anchor_keyword_extraction'] = test_anchor_keyword_extraction()
    except Exception as e:
        print(f"  [ERROR] {e}")
        results['test_anchor_keyword_extraction'] = False

    try:
        results['test_anchor_keyword_check'] = test_anchor_keyword_check()
    except Exception as e:
        print(f"  [ERROR] {e}")
        results['test_anchor_keyword_check'] = False

    try:
        results['test_low_relevance_gate'] = test_low_relevance_gate()
    except Exception as e:
        print(f"  [ERROR] {e}")
        results['test_low_relevance_gate'] = False

    try:
        results['test_dataset_filter_expr'] = test_dataset_filter_expr()
    except Exception as e:
        print(f"  [ERROR] {e}")
        results['test_dataset_filter_expr'] = False

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
        print("\n[OK] All Phase 48 tests passed!")
        return 0
    else:
        print("\n[FAIL] Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
