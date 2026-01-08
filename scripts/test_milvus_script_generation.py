"""
Domain → Video Script Generation Test (CONTEXT SAFE)

- Milvus chunk 전체 수집
- chunk를 묶어서 요약 (컨텍스트 초과 방지)
- 요약된 원문으로 VideoScript 생성
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List
import sys
from pathlib import Path

# 프로젝트 루트 추가
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))


from app.clients.milvus_client import get_milvus_client
from app.services.video_script_generation_service import (
    VideoScriptGenerationService,
    ScriptGenerationOptions,
)

# ============================================================
# 설정
# ============================================================

TARGET_DOMAIN = "직장내괴롭힘교육"
VIDEO_ID = f"video-{TARGET_DOMAIN}"

MIN_CHUNK_LEN = 30
SUMMARY_GROUP_SIZE = 12        # chunk 12개씩 요약
MAX_SUMMARY_CHARS = 6000       # 최종 요약 입력 제한

OUTPUT_DIR = Path("test_output_script")
OUTPUT_DIR.mkdir(exist_ok=True)


# ============================================================
# chunk 텍스트 추출 (Milvus 구조 불문)
# ============================================================

def extract_chunk_text(chunk: Dict[str, Any]) -> str:
    for key in ["content", "text", "chunk", "page_content"]:
        v = chunk.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()

    meta = chunk.get("metadata", {})
    for key in ["content", "text", "chunk"]:
        v = meta.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()

    return ""


# ============================================================
# 1️⃣ 도메인 전체 chunk 수집
# ============================================================

async def collect_domain_chunks(domain: str) -> List[str]:
    milvus = get_milvus_client()

    print(f"[1] 도메인 문서 검색: {domain}")

    search_results = await milvus.search(query=domain, top_k=300)

    doc_ids = {
        r["doc_id"]
        for r in search_results
        if r.get("metadata", {}).get("dataset_id") == domain
    }

    if not doc_ids:
        raise RuntimeError(f"[ERROR] 도메인 '{domain}' 문서 없음")

    all_chunks: List[str] = []

    for doc_id in doc_ids:
        chunks = await milvus.get_document_chunks(doc_id)
        for c in chunks:
            text = extract_chunk_text(c)
            if len(text) >= MIN_CHUNK_LEN:
                all_chunks.append(text)

    if not all_chunks:
        raise RuntimeError(f"[ERROR] 도메인 '{domain}' chunk 없음")

    print(f"✓ 수집 완료: {len(all_chunks)}개 chunk")
    return all_chunks


# ============================================================
# 2️⃣ chunk 요약 (컨텍스트 압축)
# ============================================================

async def summarize_chunks(
    chunks: List[str],
    service: VideoScriptGenerationService,
) -> str:
    print("\n[2] Chunk 요약 시작 (컨텍스트 압축)")

    summaries: List[str] = []

    for i in range(0, len(chunks), SUMMARY_GROUP_SIZE):
        group = chunks[i : i + SUMMARY_GROUP_SIZE]
        text = "\n".join(group)

        prompt = f"""
다음은 교육 자료 일부입니다.
이 내용을 교육 영상 스크립트 생성을 위해 핵심만 요약하세요.

규칙:
- 나열하지 말고 설명형 문장
- 정의, 절차, 주의사항 위주
- 불필요한 예시 제거

내용:
{text}
"""

        summary = await service._llm_client.generate_chat_completion(
            messages=[
                {"role": "system", "content": "너는 교육 콘텐츠 요약 전문가이다."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=800,
        )

        summaries.append(summary.strip())
        print(f"  ✓ 요약 {len(summaries)} 완료")

    merged = "\n".join(summaries)

    # 🔒 하드 컷 (절대 컨텍스트 초과 방지)
    if len(merged) > MAX_SUMMARY_CHARS:
        merged = merged[:MAX_SUMMARY_CHARS]

    print(f"✓ 요약 완료 (총 {len(merged)}자)")
    return merged


# ============================================================
# main
# ============================================================

async def main():
    print("\n🚀 Domain → Video Script 생성 테스트")
    print("=" * 70)
    print(f"TARGET_DOMAIN = {TARGET_DOMAIN}")
    print("=" * 70)

    # 1. chunk 수집
    chunks = await collect_domain_chunks(TARGET_DOMAIN)

    service = VideoScriptGenerationService()

    # 2. chunk 요약
    summarized_text = await summarize_chunks(chunks, service)

    # 3. 스크립트 생성
    options = ScriptGenerationOptions(
        language="ko",
        target_minutes=5,
        max_chapters=6,
        max_scenes_per_chapter=5,
        style="friendly_security_training",
    )

    print("\n[LLM] 교육 영상 스크립트 생성 중...")

    script_json = await service.generate_script(
        video_id=VIDEO_ID,
        source_text=summarized_text,
        options=options,
    )

    output_path = OUTPUT_DIR / f"generated_script_{TARGET_DOMAIN}.json"
    output_path.write_text(
        json.dumps(script_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n✅ 스크립트 생성 완료")
    print(f"📄 저장 위치: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
