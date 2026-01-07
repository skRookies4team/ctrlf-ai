"""
Milvus 기반 영상 스크립트 생성 테스트
- 도메인 단위 선택
"""

import asyncio
import json
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from app.core.config import clear_settings_cache, get_settings
from app.services.video_script_generation_service import (
    VideoScriptGenerationService,
    ScriptGenerationOptions,
)
from app.clients.milvus_client import get_milvus_client

# ===============================
# ✅ 테스트할 도메인 선택
# ===============================
TARGET_DOMAIN = "직장내괴롭힘교육"
# 예:
# "직무교육"
# "장애인인식개선교육"
# "직장내괴롭힘교육"
# "직장내성희롱교육"
# "정보보안교육"

async def build_source_text_from_domain(domain: str) -> str:
    """
    Milvus에서 특정 도메인의 문서 텍스트를 모아 source_text 구성
    """
    milvus = get_milvus_client()

    results = await milvus.search(
        query="교육 전체 내용 요약",
        domain=domain,  # 도메인명 = dataset_id
        top_k=50,
    )

    texts = [
        r["content"]
        for r in results
        if domain in r.get("metadata", {}).get("dataset_id", "")
    ]

    if not texts:
        raise RuntimeError(f"No documents found for domain: {domain}")

    return "\n\n".join(texts)


async def main():
    clear_settings_cache()
    settings = get_settings()

    print("=" * 60)
    print(" Video Script Generation (Domain-based)")
    print("=" * 60)
    print(f"TARGET_DOMAIN: {TARGET_DOMAIN}")
    print(f"AI_ENV: {settings.AI_ENV}")
    print(f"MILVUS_ENABLED: {settings.MILVUS_ENABLED}")
    print("=" * 60)

    print("\n[1] Milvus → source_text 구성")
    source_text = await build_source_text_from_domain(TARGET_DOMAIN)
    print(f"   source_text length: {len(source_text)}")

    service = VideoScriptGenerationService()

    options = ScriptGenerationOptions(
        language="ko",
        target_minutes=4,          # ✅ 도메인당 3~5분
        max_chapters=2,            # 과도한 분할 방지
        max_scenes_per_chapter=5,  # 총 8~10씬 유도
        style="friendly_security_training",
    )

    print("\n[2] 영상 스크립트 생성")
    video_script = await service.generate_script(
        video_id=f"{TARGET_DOMAIN}-video",
        source_text=source_text,
        options=options,
    )

    output_dir = PROJECT_ROOT / "test_output_script"
    output_dir.mkdir(exist_ok=True)

    output_path = output_dir / f"video_script_{TARGET_DOMAIN}.json"
    output_path.write_text(
        json.dumps(video_script, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n✅ 생성 완료")
    print(f"📄 저장 위치: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
