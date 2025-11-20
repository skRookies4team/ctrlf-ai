"""
HWP 변환 어댑터 테스트 스크립트

사용법:
    python test_hwp_converter.py [hwp_file_path]

예시:
    python test_hwp_converter.py data/구매업무처리규정.hwp
"""
import sys
from pathlib import Path

from core.hwp_converter import (
    convert_hwp_to_text,
    get_available_methods,
    get_recommended_method
)


def test_hwp_converter(hwp_path: str = None):
    """HWP 변환 어댑터 테스트"""

    print("=" * 70)
    print("HWP 변환 어댑터 테스트")
    print("=" * 70)

    # 1. 사용 가능한 변환 방법 확인
    print("\n[1] 사용 가능한 변환 방법:")
    methods = get_available_methods()
    if methods:
        for method in methods:
            print(f"  ✅ {method}")
    else:
        print("  ❌ 변환 방법 없음")
        print("\n권장 설치:")
        print("  - Linux/Docker: sudo apt-get install hwp5 && pip install hwp5")
        print("  - Windows: choco install libreoffice")
        return

    # 2. 권장 변환 방법
    recommended = get_recommended_method()
    print(f"\n[2] 권장 변환 방법: {recommended}")

    # 3. HWP 파일 변환 테스트
    if hwp_path:
        hwp_file = Path(hwp_path)

        if not hwp_file.exists():
            print(f"\n❌ 파일을 찾을 수 없습니다: {hwp_path}")
            return

        print(f"\n[3] HWP 파일 변환 테스트:")
        print(f"  파일: {hwp_file.name}")
        print(f"  크기: {hwp_file.stat().st_size / 1024:.1f} KB")

        try:
            text = convert_hwp_to_text(str(hwp_file))

            if text:
                print(f"\n✅ 변환 성공!")
                print(f"  추출된 텍스트 길이: {len(text):,} 자")
                print(f"  줄 수: {len(text.splitlines()):,} 줄")

                # 미리보기 (처음 500자)
                print("\n  미리보기 (처음 500자):")
                print("  " + "-" * 60)
                preview = text[:500]
                for line in preview.splitlines()[:10]:
                    print(f"  {line}")
                if len(text) > 500:
                    print("  ...")
                print("  " + "-" * 60)

            else:
                print("\n❌ 변환 실패: 텍스트 추출되지 않음")

        except Exception as e:
            print(f"\n❌ 변환 오류: {e}")

    else:
        print("\n[3] HWP 파일 변환 테스트:")
        print("  파일 경로를 지정하세요.")
        print("  예: python test_hwp_converter.py data/구매업무처리규정.hwp")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        hwp_file_path = sys.argv[1]
    else:
        hwp_file_path = None

    test_hwp_converter(hwp_file_path)
