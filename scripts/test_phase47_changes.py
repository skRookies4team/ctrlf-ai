"""
Phase 47 변경사항 검증 테스트 스크립트

GPT 피드백 반영 내용:
1. '~입니다' 금지 표현에서 제거
2. soft_guardrail_instruction 모든 경로 적용
3. DOMAIN_CONTACT_INFO 도메인/카테고리 정규화
"""

import sys
import os

# 프로젝트 루트를 path에 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

def test_soft_guardrail_instruction():
    """필수1: '~입니다' 금지 표현에서 제거 확인"""
    print("\n=== Test 1: Soft Guardrail Instruction ===")

    from app.services.answer_guard_service import AnswerGuardService

    guard = AnswerGuardService()
    instruction = guard.get_soft_guardrail_system_instruction()

    # 금지 표현 섹션 추출
    forbidden_section = instruction.split('【금지')[1].split('【허용')[0] if '【금지' in instruction else ""
    allowed_section = instruction.split('【허용')[1].split('【답변')[0] if '【허용' in instruction else ""

    # 테스트 1.1: '~입니다'가 금지 표현에 없어야 함
    has_ipnida_in_forbidden = "'~입니다'" in forbidden_section or "~입니다" in forbidden_section
    print(f"  [{'FAIL' if has_ipnida_in_forbidden else 'PASS'}] '~입니다'가 금지 표현에 없음: {not has_ipnida_in_forbidden}")

    # 테스트 1.2: '~입니다'가 허용 표현에 있어야 함
    has_ipnida_in_allowed = "입니다" in allowed_section
    print(f"  [{'PASS' if has_ipnida_in_allowed else 'FAIL'}] '~입니다'가 허용 표현에 있음: {has_ipnida_in_allowed}")

    # 테스트 1.3: '회사 규정상'이 금지 표현에 있어야 함
    has_company_rule_forbidden = "회사 규정상" in forbidden_section
    print(f"  [{'PASS' if has_company_rule_forbidden else 'FAIL'}] '회사 규정상'이 금지 표현에 있음: {has_company_rule_forbidden}")

    # 테스트 1.4: '사규에 따르면'이 금지 표현에 있어야 함
    has_saryu_forbidden = "사규에 따르면" in forbidden_section
    print(f"  [{'PASS' if has_saryu_forbidden else 'FAIL'}] '사규에 따르면'이 금지 표현에 있음: {has_saryu_forbidden}")

    return (not has_ipnida_in_forbidden and has_ipnida_in_allowed and
            has_company_rule_forbidden and has_saryu_forbidden)


def test_domain_normalization():
    """필수3: 도메인 정규화 함수 테스트"""
    print("\n=== Test 2: Domain Normalization ===")

    from app.services.answer_guard_service import AnswerGuardService

    guard = AnswerGuardService()

    test_cases = [
        ("EDU", "EDUCATION"),
        ("EDUCATION", "EDUCATION"),
        ("edu", "EDUCATION"),
        ("PIP", "EDUCATION"),  # 교육 주제 카테고리 → EDUCATION
        ("SHP", "EDUCATION"),
        ("BHP", "EDUCATION"),
        ("DEP", "EDUCATION"),
        ("JOB", "EDUCATION"),
        ("POLICY", "POLICY"),
        ("INCIDENT", "INCIDENT"),
        ("GENERAL", "GENERAL"),
        (None, "DEFAULT"),
        ("UNKNOWN", "DEFAULT"),
    ]

    all_passed = True
    for input_val, expected in test_cases:
        result = guard.normalize_domain_key(input_val)
        passed = result == expected
        all_passed = all_passed and passed
        print(f"  [{'PASS' if passed else 'FAIL'}] normalize_domain_key({repr(input_val)}) = {result} (expected: {expected})")

    return all_passed


def test_contact_info():
    """필수3: 담당부서 안내 함수 테스트"""
    print("\n=== Test 3: Contact Info ===")

    from app.services.answer_guard_service import AnswerGuardService

    guard = AnswerGuardService()

    all_passed = True

    # 테스트 3.1: 도메인 기준 안내
    policy_contact = guard.get_contact_info("POLICY")
    passed = "인사팀" in policy_contact or "총무팀" in policy_contact
    all_passed = all_passed and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] POLICY → {policy_contact}")

    edu_contact = guard.get_contact_info("EDUCATION")
    passed = "교육팀" in edu_contact or "HR팀" in edu_contact
    all_passed = all_passed and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] EDUCATION → {edu_contact}")

    # 테스트 3.2: 토픽 기준 안내 (더 구체적)
    pip_contact = guard.get_contact_info("EDUCATION", topic="PIP")
    passed = "개인정보보호팀" in pip_contact
    all_passed = all_passed and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] EDUCATION + PIP topic → {pip_contact}")

    shp_contact = guard.get_contact_info("EDUCATION", topic="SHP")
    passed = "고충처리위원회" in shp_contact
    all_passed = all_passed and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] EDUCATION + SHP topic → {shp_contact}")

    return all_passed


def test_soft_guardrail_check():
    """필수3: check_soft_guardrail에 topic 파라미터 테스트"""
    print("\n=== Test 4: Soft Guardrail Check with Topic ===")

    from app.services.answer_guard_service import AnswerGuardService
    from app.models.router_types import Tier0Intent

    guard = AnswerGuardService()

    all_passed = True

    # 테스트 4.1: POLICY_QA + sources=0 → 소프트 가드레일 활성화
    needs, prefix = guard.check_soft_guardrail(
        intent=Tier0Intent.POLICY_QA,
        sources=[],
        domain="POLICY",
    )
    passed = needs is True and prefix is not None
    all_passed = all_passed and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] POLICY_QA + sources=0 → needs_soft_guardrail={needs}")

    # 테스트 4.2: EDUCATION_QA + sources=0 + topic=PIP → 구체적 안내
    needs, prefix = guard.check_soft_guardrail(
        intent=Tier0Intent.EDUCATION_QA,
        sources=[],
        domain="EDUCATION",
        topic="PIP",
    )
    passed = needs is True and prefix is not None and "개인정보보호팀" in prefix
    all_passed = all_passed and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] EDUCATION_QA + PIP topic → '개인정보보호팀' in prefix: {'개인정보보호팀' in (prefix or '')}")

    # 테스트 4.3: GENERAL_CHAT → 소프트 가드레일 비활성화
    needs, prefix = guard.check_soft_guardrail(
        intent=Tier0Intent.GENERAL_CHAT,
        sources=[],
        domain="GENERAL",
    )
    passed = needs is False and prefix is None
    all_passed = all_passed and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] GENERAL_CHAT → needs_soft_guardrail={needs}")

    return all_passed


def test_message_builder_signatures():
    """필수2: MessageBuilder 함수에 soft_guardrail_instruction 파라미터 존재 확인"""
    print("\n=== Test 5: MessageBuilder Signatures ===")

    import inspect
    from app.services.chat.message_builder import MessageBuilder

    all_passed = True

    # build_rag_messages
    sig = inspect.signature(MessageBuilder.build_rag_messages)
    has_param = 'soft_guardrail_instruction' in sig.parameters
    all_passed = all_passed and has_param
    print(f"  [{'PASS' if has_param else 'FAIL'}] build_rag_messages has soft_guardrail_instruction: {has_param}")

    # build_mixed_messages
    sig = inspect.signature(MessageBuilder.build_mixed_messages)
    has_param = 'soft_guardrail_instruction' in sig.parameters
    all_passed = all_passed and has_param
    print(f"  [{'PASS' if has_param else 'FAIL'}] build_mixed_messages has soft_guardrail_instruction: {has_param}")

    # build_backend_api_messages
    sig = inspect.signature(MessageBuilder.build_backend_api_messages)
    has_param = 'soft_guardrail_instruction' in sig.parameters
    all_passed = all_passed and has_param
    print(f"  [{'PASS' if has_param else 'FAIL'}] build_backend_api_messages has soft_guardrail_instruction: {has_param}")

    return all_passed


def test_similarity_logging_defense():
    """권장: Similarity 로깅 빈 배열 방어 로직 확인"""
    print("\n=== Test 6: Similarity Logging Defense ===")

    from app.services.chat.rag_handler import log_similarity_distribution

    # 빈 소스로 호출해도 에러가 발생하지 않아야 함
    try:
        log_similarity_distribution(
            sources=[],
            search_stage="test",
            domain="TEST",
            query="test query",
        )
        passed = True
        print(f"  [PASS] log_similarity_distribution with empty sources: No error")
    except Exception as e:
        passed = False
        print(f"  [FAIL] log_similarity_distribution with empty sources: {e}")

    return passed


def main():
    print("=" * 60)
    print("Phase 47 변경사항 검증 테스트")
    print("=" * 60)

    results = {}

    try:
        results['test_soft_guardrail_instruction'] = test_soft_guardrail_instruction()
    except Exception as e:
        print(f"  [ERROR] {e}")
        results['test_soft_guardrail_instruction'] = False

    try:
        results['test_domain_normalization'] = test_domain_normalization()
    except Exception as e:
        print(f"  [ERROR] {e}")
        results['test_domain_normalization'] = False

    try:
        results['test_contact_info'] = test_contact_info()
    except Exception as e:
        print(f"  [ERROR] {e}")
        results['test_contact_info'] = False

    try:
        results['test_soft_guardrail_check'] = test_soft_guardrail_check()
    except Exception as e:
        print(f"  [ERROR] {e}")
        results['test_soft_guardrail_check'] = False

    try:
        results['test_message_builder_signatures'] = test_message_builder_signatures()
    except Exception as e:
        print(f"  [ERROR] {e}")
        results['test_message_builder_signatures'] = False

    try:
        results['test_similarity_logging_defense'] = test_similarity_logging_defense()
    except Exception as e:
        print(f"  [ERROR] {e}")
        results['test_similarity_logging_defense'] = False

    print("\n" + "=" * 60)
    print("테스트 결과 요약")
    print("=" * 60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, result in results.items():
        status = "PASS" if result else "FAIL"
        print(f"  [{status}] {name}")

    print(f"\n총 {passed}/{total} 테스트 통과")

    if passed == total:
        print("\n✅ 모든 Phase 47 변경사항 검증 완료!")
        return 0
    else:
        print("\n❌ 일부 테스트 실패")
        return 1


if __name__ == "__main__":
    sys.exit(main())
