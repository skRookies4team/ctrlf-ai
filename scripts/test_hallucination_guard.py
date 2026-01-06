"""
Phase 55: 환각 방지 기능 테스트 스크립트

테스트 항목:
1. 통계/순위 질문 Out-of-Scope 감지
2. Citation Validation 강화
3. 통계 주장 감지
4. RAG 0건 시 고정 템플릿 반환
"""

import sys
import os
import io

# Windows 콘솔 UTF-8 설정
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.answer_guard_service import (
    AnswerGuardService,
    AnswerTemplates,
    STATISTICAL_CLAIM_REGEX,
    _STATS_SIGNAL_RE,
    _INCIDENT_METRIC_RE,
)
from app.models.chat import ChatSource


def print_header(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_result(test_name: str, passed: bool, details: str = ""):
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"\n{status}: {test_name}")
    if details:
        print(f"       {details}")


def test_stats_out_of_scope():
    """테스트 1: 통계/순위 질문 Out-of-Scope 감지"""
    print_header("테스트 1: 통계/순위 질문 Out-of-Scope 감지")

    guard = AnswerGuardService()

    test_cases = [
        # (질문, 도메인, 차단 예상 여부)
        ("최근 1년 동안 가장 많이 위반된 보안 규정 TOP 5", "POLICY", True),
        ("지난 3개월간 보안 사고 통계 알려줘", "INCIDENT", True),
        ("가장 빈번한 개인정보 유출 사례는?", "POLICY", True),
        ("상위 10개 위반 사례 알려줘", "INCIDENT", True),
        ("연차휴가 규정이 뭐야?", "POLICY", False),  # 일반 질문 - 통과
        ("보안 사고 신고하려면 어떻게 해?", "INCIDENT", False),  # 신고 액션 - 통과
        ("개인정보 유출 신고 절차", "POLICY", False),  # 신고 절차 - 통과
    ]

    passed_count = 0
    for query, domain, should_block in test_cases:
        result = guard.check_stats_out_of_scope_fast_path(query, domain)
        is_blocked = result is not None
        passed = is_blocked == should_block

        if passed:
            passed_count += 1

        expected = "차단" if should_block else "통과"
        actual = "차단" if is_blocked else "통과"
        print_result(
            f"'{query[:30]}...'",
            passed,
            f"예상: {expected}, 실제: {actual}"
        )

    print(f"\n통계 Out-of-Scope 테스트: {passed_count}/{len(test_cases)} 통과")
    return passed_count == len(test_cases)


def test_statistical_claim_detection():
    """테스트 2: 통계 주장 감지"""
    print_header("테스트 2: 통계 주장 감지 (답변 내 환각 체크)")

    guard = AnswerGuardService()

    test_cases = [
        # (답변, 통계 주장 포함 여부)
        ("TOP 5 위반 규정은 다음과 같습니다.", True),
        ("가장 많이 위반된 규정은 접근권한 관리입니다.", True),
        ("최근 3년간 통계에 따르면...", True),
        ("약 45%의 직원이 위반했습니다.", True),
        ("1위는 비밀번호 정책 위반입니다.", True),
        ("연차휴가는 법정 휴가입니다.", False),  # 일반 설명
        ("제5조에 따르면 접근권한을 관리해야 합니다.", False),  # 일반 조항 인용
    ]

    passed_count = 0
    for answer, should_detect in test_cases:
        detected = guard._contains_statistical_claim(answer)
        passed = detected == should_detect

        if passed:
            passed_count += 1

        expected = "감지" if should_detect else "미감지"
        actual = "감지" if detected else "미감지"
        print_result(
            f"'{answer[:40]}...'",
            passed,
            f"예상: {expected}, 실제: {actual}"
        )

    print(f"\n통계 주장 감지 테스트: {passed_count}/{len(test_cases)} 통과")
    return passed_count == len(test_cases)


def test_citation_validation():
    """테스트 3: Citation Validation 강화"""
    print_header("테스트 3: Citation Validation 강화")

    guard = AnswerGuardService()

    # 테스트 케이스: (답변, sources, 유효 여부)
    test_cases = []

    # Case 1: sources 없이 조항 인용 → 차단
    test_cases.append((
        "제5조 제2항에 따르면 비밀번호를 관리해야 합니다.",
        [],  # sources 없음
        False,  # 차단되어야 함
        "sources 없이 조항 인용"
    ))

    # Case 2: sources 없이 통계 주장 → 차단
    test_cases.append((
        "가장 많이 위반된 규정은 접근권한 관리입니다.",
        [],  # sources 없음
        False,  # 차단되어야 함
        "sources 없이 통계 주장"
    ))

    # Case 3: sources 없고 조항/통계 없음 → 통과
    test_cases.append((
        "연차휴가는 법정 휴가입니다.",
        [],  # sources 없음
        True,  # 통과해야 함 (조항/통계 없음)
        "sources 없지만 조항/통계 없음"
    ))

    # Case 4: sources 있고 조항이 sources에 있음 → 통과
    mock_source = ChatSource(
        doc_id="test_doc",
        title="테스트 문서",
        snippet="제5조(접근권한의 관리) 비밀번호는 일정 횟수 이상 잘못 입력 시 접근 제한",
        article_label="제5조",
        score=0.5,
    )
    test_cases.append((
        "제5조에 따르면 비밀번호를 관리해야 합니다.",
        [mock_source],
        True,  # 통과해야 함
        "sources에 조항 있음"
    ))

    # Case 5: sources 있지만 조항이 sources에 없음 → 차단
    test_cases.append((
        "제99조 제5항에 따르면 특별 휴가를 사용할 수 있습니다.",
        [mock_source],  # 제5조만 있음
        False,  # 차단되어야 함 (제99조는 없음)
        "sources에 없는 조항 인용"
    ))

    passed_count = 0
    for answer, sources, should_be_valid, description in test_cases:
        is_valid, validated = guard.validate_citation(answer, sources)
        passed = is_valid == should_be_valid

        if passed:
            passed_count += 1

        expected = "유효" if should_be_valid else "차단"
        actual = "유효" if is_valid else "차단"
        print_result(
            description,
            passed,
            f"예상: {expected}, 실제: {actual}"
        )

    print(f"\nCitation Validation 테스트: {passed_count}/{len(test_cases)} 통과")
    return passed_count == len(test_cases)


def test_no_source_template():
    """테스트 4: RAG 0건 시 고정 템플릿 반환"""
    print_header("테스트 4: RAG 0건 시 고정 템플릿 반환")

    guard = AnswerGuardService()

    # 고정 템플릿 생성 테스트
    template = guard.get_no_source_template(domain="POLICY")

    # 검증: 템플릿에 필수 요소가 포함되어 있는지
    checks = [
        ("'죄송합니다' 포함", "죄송합니다" in template),
        ("'찾지 못했습니다' 포함", "찾지 못했습니다" in template),
        ("'담당 부서' 안내 포함", "담당 부서" in template),
    ]

    passed_count = 0
    for check_name, passed in checks:
        if passed:
            passed_count += 1
        print_result(check_name, passed)

    print(f"\n고정 템플릿 테스트: {passed_count}/{len(checks)} 통과")
    print(f"\n생성된 템플릿:\n{template}")

    return passed_count == len(checks)


def test_stats_language_sanitizer():
    """테스트 5: 통계 표현 Sanitizer"""
    print_header("테스트 5: 통계 표현 Sanitizer")

    guard = AnswerGuardService()

    # 테스트 케이스: (질문, 답변, sources, 수정 예상 여부)
    test_cases = [
        # 통계 질문 + sources 없음 → 수정됨
        (
            "최근 1년간 가장 많이 위반된 규정은?",
            "최근 1년간 가장 많이 위반된 규정은 비밀번호 정책입니다. 약 45%의 직원이 위반했습니다.",
            [],
            True,
            "통계 질문 + sources 없음"
        ),
        # 일반 질문 → 수정 안 됨
        (
            "연차휴가 규정이 뭐야?",
            "연차휴가는 법정 휴가로 1년 근무 시 15일이 부여됩니다.",
            [],
            False,
            "일반 질문"
        ),
    ]

    passed_count = 0
    for query, answer, sources, should_modify, description in test_cases:
        sanitized, was_modified = guard.sanitize_stats_language(answer, query, sources)
        passed = was_modified == should_modify

        if passed:
            passed_count += 1

        expected = "수정됨" if should_modify else "원본 유지"
        actual = "수정됨" if was_modified else "원본 유지"
        print_result(
            description,
            passed,
            f"예상: {expected}, 실제: {actual}"
        )

        if was_modified:
            print(f"       원본: {answer[:50]}...")
            print(f"       수정: {sanitized[:50]}...")

    print(f"\nSanitizer 테스트: {passed_count}/{len(test_cases)} 통과")
    return passed_count == len(test_cases)


def test_config_settings():
    """테스트 6: 환경 설정 확인"""
    print_header("테스트 6: 환경 설정 확인")

    from app.core.config import get_settings

    settings = get_settings()

    checks = [
        ("HALLUCINATION_GUARD_STRICT", getattr(settings, 'HALLUCINATION_GUARD_STRICT', None)),
        ("CITATION_VALIDATION_STRICT", getattr(settings, 'CITATION_VALIDATION_STRICT', None)),
        ("STATISTICAL_CLAIM_VALIDATION", getattr(settings, 'STATISTICAL_CLAIM_VALIDATION', None)),
    ]

    passed_count = 0
    for setting_name, value in checks:
        # 기본값 True여야 함
        passed = value is True
        if passed:
            passed_count += 1
        print_result(f"{setting_name} = {value}", passed, "기본값 True 예상")

    print(f"\n환경 설정 테스트: {passed_count}/{len(checks)} 통과")
    return passed_count == len(checks)


def main():
    print("\n" + "=" * 70)
    print("  Phase 55: 환각 방지 기능 테스트")
    print("=" * 70)

    results = []

    # 테스트 실행
    results.append(("통계 Out-of-Scope 감지", test_stats_out_of_scope()))
    results.append(("통계 주장 감지", test_statistical_claim_detection()))
    results.append(("Citation Validation", test_citation_validation()))
    results.append(("고정 템플릿 반환", test_no_source_template()))
    results.append(("통계 표현 Sanitizer", test_stats_language_sanitizer()))
    results.append(("환경 설정", test_config_settings()))

    # 최종 결과
    print_header("최종 테스트 결과")
    passed = sum(1 for _, r in results if r)
    total = len(results)

    for name, result in results:
        status = "✅" if result else "❌"
        print(f"  {status} {name}")

    print(f"\n총 {passed}/{total} 테스트 통과")

    if passed == total:
        print("\n🎉 모든 환각 방지 기능이 정상 작동합니다!")
    else:
        print("\n⚠️ 일부 테스트가 실패했습니다. 코드를 확인해주세요.")

    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
