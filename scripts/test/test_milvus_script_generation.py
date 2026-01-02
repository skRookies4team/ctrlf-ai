"""
Milvus 문서 → 스크립트 생성 테스트 스크립트

사용법:
    python test_milvus_script_generation.py

이 스크립트는 다음을 테스트합니다:
1. Milvus 연결 및 헬스체크
2. 특정 문서 검색 및 조회
3. 문서 텍스트로 교육 영상 스크립트 생성 (실제 LLM 호출)
"""

import asyncio
import json
import sys

from app.clients.milvus_client import get_milvus_client
from app.services.video_script_generation_service import (
    VideoScriptGenerationService,
    ScriptGenerationOptions,
)


async def test_milvus_connection():
    """Milvus 연결 테스트"""
    print("=" * 60)
    print("1. Milvus 연결 테스트")
    print("=" * 60)

    client = get_milvus_client()
    health = await client.health_check()

    if health:
        print("✓ Milvus 연결 성공!")
        return True
    else:
        print("✗ Milvus 연결 실패!")
        return False


async def test_document_search(query: str = "사내 보안형 AI 챗봇"):
    """문서 검색 테스트"""
    print("\n" + "=" * 60)
    print(f"2. 문서 검색: '{query}'")
    print("=" * 60)

    client = get_milvus_client()
    results = await client.search(query, top_k=5)

    print(f"검색 결과: {len(results)}개 문서\n")

    for i, r in enumerate(results):
        doc_id = r.get("doc_id", "unknown")
        score = r.get("score", 0)
        content = r.get("content", "")[:80]
        print(f"{i+1}. {doc_id}")
        print(f"   점수: {score:.4f}")
        print(f"   내용: {content}...")
        print()

    return results


async def test_get_document(doc_id: str):
    """문서 전체 텍스트 조회 테스트"""
    print("\n" + "=" * 60)
    print(f"3. 문서 조회: '{doc_id}'")
    print("=" * 60)

    client = get_milvus_client()

    # 청크 조회
    chunks = await client.get_document_chunks(doc_id)
    print(f"총 청크 수: {len(chunks)}")

    # 전체 텍스트
    full_text = await client.get_full_document_text(doc_id)
    print(f"전체 텍스트 길이: {len(full_text)} 자")

    # 미리보기
    print("\n[문서 내용 미리보기 (처음 500자)]")
    print("-" * 40)
    print(full_text[:500])
    print("-" * 40)

    return full_text


async def test_script_generation(source_text: str, max_chars: int = 3000):
    """스크립트 생성 테스트"""
    print("\n" + "=" * 60)
    print("4. 스크립트 생성 (실제 LLM 호출)")
    print("=" * 60)

    # 텍스트 길이 제한 (LLM 컨텍스트 제한 회피)
    truncated_text = source_text[:max_chars]
    print(f"사용할 텍스트 길이: {len(truncated_text)} 자 (원본: {len(source_text)} 자)")

    service = VideoScriptGenerationService()
    options = ScriptGenerationOptions(
        target_minutes=2,
        max_chapters=3,
        max_scenes_per_chapter=3,
        style="friendly_security_training",
    )

    print("\nLLM 호출 중... (30초~1분 소요)")

    try:
        result = await service.generate_script(
            video_id="test-milvus-001",
            source_text=truncated_text,
            options=options,
        )

        print("\n✓ 스크립트 생성 성공!")
        return result

    except Exception as e:
        print(f"\n✗ 스크립트 생성 실패: {e}")
        return None


def print_script_summary(result: dict):
    """생성된 스크립트 요약 출력"""
    print("\n" + "=" * 60)
    print("5. 생성된 스크립트")
    print("=" * 60)

    chapters = result.get("chapters", [])

    for ch in chapters:
        print(f"\n📖 챕터 {ch['chapter_id']}: {ch['title']}")
        for sc in ch.get("scenes", []):
            duration = sc.get("duration_sec", 0) or 0
            print(f"   🎬 씬 {sc['scene_id']} ({duration:.0f}초)")
            print(f"      나레이션: {sc['narration'][:60]}...")
            if sc.get("on_screen_text"):
                print(f"      화면텍스트: {sc['on_screen_text']}")

    # 통계
    total_scenes = sum(len(c.get("scenes", [])) for c in chapters)
    total_duration = sum(
        s.get("duration_sec", 0) or 0
        for c in chapters
        for s in c.get("scenes", [])
    )

    print("\n" + "-" * 40)
    print(f"📊 요약: {len(chapters)}개 챕터, {total_scenes}개 씬, 총 {total_duration:.0f}초 ({total_duration/60:.1f}분)")

    # JSON 저장
    output_file = "generated_script.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"📁 전체 스크립트 저장됨: {output_file}")


async def main():
    """메인 테스트 함수"""
    print("\n🚀 Milvus → 스크립트 생성 End-to-End 테스트")
    print("=" * 60)

    # 1. Milvus 연결
    if not await test_milvus_connection():
        sys.exit(1)

    # 2. 문서 검색
    results = await test_document_search("사내 보안형 AI 챗봇 사용 안내")

    if not results:
        print("검색 결과가 없습니다.")
        sys.exit(1)

    # 3. 첫 번째 문서의 전체 텍스트 조회
    doc_id = results[0].get("doc_id")
    full_text = await test_get_document(doc_id)

    if not full_text:
        print("문서 텍스트를 가져올 수 없습니다.")
        sys.exit(1)

    # 4. 스크립트 생성
    script = await test_script_generation(full_text)

    if script:
        # 5. 결과 출력
        print_script_summary(script)
        print("\n✅ 테스트 완료!")
    else:
        print("\n❌ 스크립트 생성 실패. LLM 응답을 확인하세요.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
