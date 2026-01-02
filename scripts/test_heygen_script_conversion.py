"""
HeyGen Script 변환 단독 테스트
실행:
  python scripts/test_heygen_script_conversion.py
"""

import json
import sys
from pathlib import Path

# ===============================
# PROJECT ROOT 추가
# ===============================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.adapters.heygen_script_adapter import convert_to_heygen_script

def main():
    input_path = Path("test_output_script/video_script.json")

    if not input_path.exists():
        raise FileNotFoundError(
            f"❌ video_script.json 없음: {input_path.resolve()}"
        )

    print("📄 video_script.json 로드 중...")
    video_script = json.loads(
        input_path.read_text(encoding="utf-8")
    )

    print("🔄 HeyGen 스크립트로 변환 중...")
    heygen_script = convert_to_heygen_script(video_script)

    print("\n===== HEYGEN SCRIPT =====")
    print(json.dumps(heygen_script, ensure_ascii=False, indent=2))

    output_dir = Path("test_output")
    output_dir.mkdir(exist_ok=True)

    output_path = output_dir / "heygen_script.json"
    output_path.write_text(
        json.dumps(heygen_script, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n✅ HeyGen 스크립트 저장 완료:")
    print(f"   {output_path.resolve()}")

if __name__ == "__main__":
    main()
