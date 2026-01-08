import sys
import io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 프로젝트 루트 경로 추가
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import json
from app.utils.script_cleanup import cleanup_video_script

INPUT_PATH = Path("test_output_script/video_script_직장내괴롭힘교육.json")
OUTPUT_PATH = Path("test_output_script/video_script_직장내괴롭힘교육.cleaned.json")

script = json.loads(INPUT_PATH.read_text(encoding="utf-8"))

cleaned = cleanup_video_script(script)

OUTPUT_PATH.write_text(
    json.dumps(cleaned, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print("✅ narration 클린업 완료")
print(f"📄 저장 위치: {OUTPUT_PATH}")
