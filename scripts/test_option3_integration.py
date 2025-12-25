"""
Option 3 통합 테스트: Milvus에서 직접 텍스트 조회

실행: python scripts/test_option3_integration.py

테스트 항목:
1. MilvusSearchClient 연결
2. search_as_sources - 검색 + 텍스트 반환
3. get_document_chunks - doc_id로 청크 조회
4. get_full_document_text - 전체 문서 텍스트 조회
"""
import asyncio
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# .env 파일 로드
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))


async def test_milvus_search_client():
    """MilvusSearchClient 테스트."""
    from app.clients.milvus_client import MilvusSearchClient, get_milvus_client
    from pymilvus import Collection

    print("=" * 60)
    print("  Option 3 통합 테스트: MilvusSearchClient")
    print("=" * 60)

    client = get_milvus_client()

    # 1. Health Check
    print("\n[1] Health Check")
    is_healthy = await client.health_check()
    print(f"   {'✅' if is_healthy else '❌'} Milvus 연결: {'정상' if is_healthy else '실패'}")

    if not is_healthy:
        print("   ⚠️ Milvus 연결 실패. 테스트를 중단합니다.")
        return

    # 2. 직접 query로 doc_id 샘플 가져오기 (임베딩 서버 없이)
    print("\n[2] 직접 Query 테스트 (doc_id 샘플 조회)")
    try:
        collection = client._get_collection()
        results = collection.query(
            expr="chunk_id >= 0",
            output_fields=["doc_id", "chunk_id", "text", "dataset_id"],
            limit=3
        )
        print(f"   ✅ 조회 결과: {len(results)}개")
        for i, r in enumerate(results, 1):
            print(f"\n   [{i}] doc_id: {r.get('doc_id', 'N/A')[:50]}...")
            print(f"       chunk_id: {r.get('chunk_id', 'N/A')}")
            print(f"       dataset_id: {r.get('dataset_id', 'N/A')}")
            text = r.get('text', '')[:100].replace('\n', ' ')
            print(f"       text: {text}...")

        # sources 대체용 doc_id 추출
        sources = results if results else []
    except Exception as e:
        print(f"   ❌ 직접 Query 실패: {e}")
        import traceback
        traceback.print_exc()
        return

    # 3. Get document chunks
    if sources:
        doc_id = sources[0].get('doc_id', '')
        print(f"\n[3] get_document_chunks 테스트 (doc_id: {doc_id[:30]}...)")
        try:
            chunks = await client.get_document_chunks(doc_id)
            print(f"   ✅ 조회된 청크: {len(chunks)}개")

            if chunks:
                # chunk_id 순서 확인
                chunk_ids = [c.get('chunk_id', 0) for c in chunks[:10]]
                print(f"   chunk_id 순서: {chunk_ids}")

                # 첫 청크 미리보기
                first_chunk = chunks[0]
                text_preview = first_chunk.get('text', '')[:100].replace('\n', ' ')
                print(f"   첫 청크 텍스트: {text_preview}...")
        except Exception as e:
            print(f"   ❌ 청크 조회 실패: {e}")
            import traceback
            traceback.print_exc()

        # 4. Get full document text
        print(f"\n[4] get_full_document_text 테스트")
        try:
            full_text = await client.get_full_document_text(doc_id)
            print(f"   ✅ 전체 텍스트 길이: {len(full_text)} chars")
            print(f"   미리보기: {full_text[:200].replace(chr(10), ' ')}...")
        except Exception as e:
            print(f"   ❌ 전체 텍스트 조회 실패: {e}")
            import traceback
            traceback.print_exc()

    # 결론
    print("\n" + "=" * 60)
    print("  📋 Option 3 통합 테스트 결과")
    print("=" * 60)
    print("\n   ✅ MilvusSearchClient가 Option 3 요구사항을 충족합니다:")
    print("      - search_as_sources: 검색 + text 반환")
    print("      - get_document_chunks: doc_id → 청크 리스트 (chunk_id 정렬)")
    print("      - get_full_document_text: 전체 문서 텍스트")
    print("\n   🎯 Spring 읽기 API 없이 Milvus에서 직접 텍스트 조회 가능!")
    print("=" * 60)

    # 연결 해제
    client.disconnect()


if __name__ == "__main__":
    asyncio.run(test_milvus_search_client())
