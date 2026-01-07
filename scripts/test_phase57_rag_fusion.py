"""
Phase 57: Query Expansion + RRF Fusion 단위 테스트

실행:
    python scripts/test_phase57_rag_fusion.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import dataclass
from typing import Optional


def test_query_expansion():
    """Query Expansion 테스트"""
    print("\n" + "=" * 60)
    print("TEST 1: Query Expansion (규칙 기반)")
    print("=" * 60)

    from app.services.chat.query_rewriter import expand_query_sync

    test_cases = [
        ("연차", "POLICY", True),      # 짧은 쿼리 - 확장 대상
        ("휴가", "POLICY", True),      # 짧은 쿼리 - 확장 대상
        ("급여", "POLICY", True),      # 짧은 쿼리 - 확장 대상
        ("징계", "POLICY", True),      # 짧은 쿼리 - 확장 대상
        ("교육", "EDU", True),         # 짧은 쿼리 - 확장 대상
        ("보안", "POLICY", True),      # 짧은 쿼리 - 확장 대상
        ("비밀번호", "POLICY", True),   # 짧은 쿼리 - 확장 대상
        ("이 문서는 굉장히 긴 질문이라서 확장이 필요하지 않습니다", "POLICY", False),  # 긴 쿼리
    ]

    passed = 0
    for query, domain, expect_expand in test_cases:
        result = expand_query_sync(query, domain)
        status = "PASS" if result.used == expect_expand else "FAIL"
        if status == "PASS":
            passed += 1

        print(f"  [{status}] '{query}' -> used={result.used}")
        if result.used:
            print(f"        expanded: '{result.rewritten[:50]}...'")

    print(f"\n  Result: {passed}/{len(test_cases)} passed")
    return passed == len(test_cases)


def test_rrf_fusion():
    """RRF Fusion 테스트"""
    print("\n" + "=" * 60)
    print("TEST 2: RRF Fusion")
    print("=" * 60)

    from app.services.search_merger import rrf_fuse_with_sources

    # Mock ChatSource
    @dataclass
    class MockSource:
        doc_id: str
        title: str
        snippet: str
        score: float
        page: Optional[int] = None
        article_label: Optional[str] = None
        article_path: Optional[str] = None
        source_type: Optional[str] = None

    # 테스트 케이스 1: 기본 융합
    print("\n  Case 1: 기본 RRF 융합")
    original = [
        MockSource('doc1', '연차휴가 규정', '연차휴가는...', 0.9),
        MockSource('doc2', '휴가 신청', '휴가 신청 방법...', 0.8),
        MockSource('doc3', '복무규정', '복무에 관한...', 0.7),
    ]

    expanded = [
        MockSource('doc2', '휴가 신청', '휴가 신청 방법...', 0.85),
        MockSource('doc4', '연차 잔여일수', '잔여일수 확인...', 0.82),
        MockSource('doc1', '연차휴가 규정', '연차휴가는...', 0.75),
    ]

    result = rrf_fuse_with_sources(original, expanded, k=60, top_n=5)

    print(f"    Fusion applied: {result.fusion_applied}")
    print(f"    Results: {[r.doc_id for r in result.results]}")

    # doc1, doc2가 양쪽에 있으므로 RRF 점수가 높아야 함
    top2_ids = [r.doc_id for r in result.results[:2]]
    case1_pass = 'doc1' in top2_ids and 'doc2' in top2_ids
    print(f"    [{'PASS' if case1_pass else 'FAIL'}] 공통 문서(doc1, doc2)가 상위 2개에 포함")

    # 테스트 케이스 2: 확장 결과 없음
    print("\n  Case 2: 확장 결과 없음 (융합 안 함)")
    result2 = rrf_fuse_with_sources(original, [], k=60, top_n=5)
    case2_pass = not result2.fusion_applied
    print(f"    Fusion applied: {result2.fusion_applied}")
    print(f"    [{'PASS' if case2_pass else 'FAIL'}] 확장 없으면 융합 안 함")

    # 테스트 케이스 3: 원문 결과 없음
    print("\n  Case 3: 원문 결과 없음 (융합 안 함)")
    result3 = rrf_fuse_with_sources([], expanded, k=60, top_n=5)
    case3_pass = not result3.fusion_applied
    print(f"    Fusion applied: {result3.fusion_applied}")
    print(f"    [{'PASS' if case3_pass else 'FAIL'}] 원문 없으면 융합 안 함")

    all_pass = case1_pass and case2_pass and case3_pass
    print(f"\n  Result: {'3/3 passed' if all_pass else 'FAILED'}")
    return all_pass


def test_config_settings():
    """설정 테스트"""
    print("\n" + "=" * 60)
    print("TEST 3: Config Settings")
    print("=" * 60)

    from app.core.config import get_settings
    settings = get_settings()

    checks = [
        ("QUERY_EXPANSION_ENABLED", hasattr(settings, 'QUERY_EXPANSION_ENABLED')),
        ("QUERY_EXPANSION_MAX_LENGTH", hasattr(settings, 'QUERY_EXPANSION_MAX_LENGTH')),
        ("RAG_FUSION_ENABLED", hasattr(settings, 'RAG_FUSION_ENABLED')),
        ("RRF_K_PARAMETER", hasattr(settings, 'RRF_K_PARAMETER')),
    ]

    passed = 0
    for name, exists in checks:
        status = "PASS" if exists else "FAIL"
        if exists:
            passed += 1
            value = getattr(settings, name)
            print(f"  [{status}] {name} = {value}")
        else:
            print(f"  [{status}] {name} not found")

    print(f"\n  Result: {passed}/{len(checks)} passed")
    return passed == len(checks)


def main():
    print("\n" + "#" * 60)
    print("# Phase 57: Query Expansion + RRF Fusion 테스트")
    print("#" * 60)

    results = []

    results.append(("Query Expansion", test_query_expansion()))
    results.append(("RRF Fusion", test_rrf_fusion()))
    results.append(("Config Settings", test_config_settings()))

    print("\n" + "=" * 60)
    print("최종 결과")
    print("=" * 60)

    all_pass = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"  [{status}] {name}")

    print("\n" + ("모든 테스트 통과!" if all_pass else "일부 테스트 실패"))
    return 0 if all_pass else 1


if __name__ == "__main__":
    exit(main())
