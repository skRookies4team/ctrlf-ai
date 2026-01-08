import sys
import io
from pathlib import Path
import json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 프로젝트 루트 추가
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.utils.heygen_converter import convert_video_script_to_heygen


INPUT_PATH = Path(
    "test_output_script/video_script_직장내괴롭힘교육.cleaned.json"
)
OUTPUT_PATH = Path(
    "test_output_script/heygen_script_직장내괴롭힘교육.json"
)

video_script = json.loads(INPUT_PATH.read_text(encoding="utf-8"))

heygen_json = convert_video_script_to_heygen(video_script)

OUTPUT_PATH.write_text(
    json.dumps(heygen_json, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print("🎬 HeyGen JSON 변환 완료")
print(f"📄 저장 위치: {OUTPUT_PATH}")
