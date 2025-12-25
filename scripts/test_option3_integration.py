"""
Option 3 통합 테스트: Milvus 직접 검색 + 텍스트 조회

실행: python scripts/test_option3_integration.py

테스트 항목:
1. verify_embedding_contract - 임베딩 dim 검증 (Fail-fast)
2. search_as_sources - 벡터 검색 + ChatSource 반환
3. get_document_chunks - doc_id로 전체 청크 조회 (pagination)
4. get_full_document_text - 전체 문서 텍스트 조회
"""
import asyncio
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))


class TestResult:
    """테스트 결과 추적"""
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.results = []

    def add(self, name: str, success: bool, message: str = ""):
        status = "✅ PASS" if success else "❌ FAIL"
        self.results.append((name, status, message))
        if success:
            self.passed += 1
        else:
            self.failed += 1

    def summary(self):
        print("\n" + "=" * 60)
        print("  테스트 결과 요약")
        print("=" * 60)
        for name, status, message in self.results:
            print(f"   {status}: {name}")
            if message:
                print(f"         {message}")
        print(f"\n   총 {self.passed + self.failed}개 테스트: "
              f"{self.passed} 통과, {self.failed} 실패")
        print("=" * 60)
        return self.failed == 0


async def run_tests():
    """전체 테스트 실행."""
    from app.core.config import get_settings, clear_settings_cache
    from app.clients.milvus_client import (
        MilvusSearchClient,
        get_milvus_client,
        clear_milvus_client,
        EmbeddingContractError,
    )

    clear_settings_cache()
    clear_milvus_client()
    settings = get_settings()

    results = TestResult()

    print("=" * 60)
    print("  Option 3 통합 테스트")
    print("=" * 60)

    print(f"\n[설정]")
    print(f"   MILVUS_ENABLED: {settings.MILVUS_ENABLED}")
    print(f"   RETRIEVAL_BACKEND: {settings.RETRIEVAL_BACKEND}")
    print(f"   MILVUS_HOST: {settings.MILVUS_HOST}:{settings.MILVUS_PORT}")
    print(f"   MILVUS_COLLECTION: {settings.MILVUS_COLLECTION_NAME}")
    print(f"   EMBEDDING_MODEL: {settings.EMBEDDING_MODEL_NAME[:50]}...")
    print(f"   EMBEDDING_DIMENSION: {settings.EMBEDDING_DIMENSION}")

    client = get_milvus_client()

    # =========================================================================
    # 테스트 1: 임베딩 계약 검증 (Fail-fast)
    # =========================================================================
    print("\n[1] 임베딩 계약 검증 (verify_embedding_contract)")
    try:
        success, message = await client.verify_embedding_contract()
        results.add("verify_embedding_contract", success, message[:80])
        if success:
            print(f"   ✅ {message}")
        else:
            print(f"   ⚠️ {message}")
    except EmbeddingContractError as e:
        results.add("verify_embedding_contract", False, str(e)[:80])
        print(f"   ❌ EmbeddingContractError: {e}")
        print("\n   🛑 임베딩 dim 불일치! 서버 기동 불가.")
        results.summary()
        return False
    except Exception as e:
        results.add("verify_embedding_contract", False, str(e)[:80])
        print(f"   ❌ 예외: {e}")

    # =========================================================================
    # 테스트 2: search_as_sources (벡터 검색 + ChatSource)
    # =========================================================================
    print("\n[2] search_as_sources (벡터 검색 + ChatSource 반환)")
    try:
        sources = await client.search_as_sources(
            query="장애인 인식개선 교육 방법",
            domain=None,
            top_k=5,
            request_id="test-001",
        )

        if len(sources) > 0:
            results.add("search_as_sources", True, f"{len(sources)}개 결과")
            print(f"   ✅ 검색 결과: {len(sources)}개")
            for i, src in enumerate(sources[:3], 1):
                print(f"\n   [{i}] doc_id: {src.doc_id[:50]}...")
                print(f"       title: {src.title[:50]}...")
                print(f"       score: {src.score:.4f}")
                snippet = src.snippet[:80].replace('\n', ' ') if src.snippet else 'N/A'
                print(f"       snippet: {snippet}...")
        else:
            results.add("search_as_sources", False, "결과 없음")
            print("   ❌ 검색 결과 없음")
    except Exception as e:
        results.add("search_as_sources", False, str(e)[:80])
        print(f"   ❌ 예외: {e}")
        import traceback
        traceback.print_exc()

    # =========================================================================
    # 테스트 3: get_document_chunks (pagination 포함)
    # =========================================================================
    print("\n[3] get_document_chunks (doc_id로 전체 청크 조회)")
    test_doc_id = None

    # 먼저 doc_id 샘플 가져오기
    try:
        from pymilvus import Collection
        collection = client._get_collection()
        sample = collection.query(
            expr="chunk_id >= 0",
            output_fields=["doc_id"],
            limit=1
        )
        if sample:
            test_doc_id = sample[0].get("doc_id")
    except Exception as e:
        print(f"   ⚠️ doc_id 샘플 조회 실패: {e}")

    if test_doc_id:
        print(f"   테스트 doc_id: {test_doc_id[:50]}...")
        try:
            chunks = await client.get_document_chunks(test_doc_id)

            if chunks:
                # 모든 청크가 로드되었는지 확인
                chunk_ids = [c.get("chunk_id", 0) for c in chunks]
                is_sorted = chunk_ids == sorted(chunk_ids)
                has_text = all(c.get("text") for c in chunks)

                results.add("get_document_chunks", True,
                           f"{len(chunks)}개 청크, 정렬={is_sorted}, 텍스트={has_text}")
                print(f"   ✅ 조회된 청크: {len(chunks)}개")
                print(f"       chunk_id 범위: {min(chunk_ids)} ~ {max(chunk_ids)}")
                print(f"       정렬 상태: {'정렬됨' if is_sorted else '비정렬'}")
                print(f"       텍스트 포함: {'모두 있음' if has_text else '일부 누락'}")
            else:
                results.add("get_document_chunks", False, "청크 없음")
                print("   ❌ 청크가 없습니다")
        except Exception as e:
            results.add("get_document_chunks", False, str(e)[:80])
            print(f"   ❌ 예외: {e}")
    else:
        results.add("get_document_chunks", False, "doc_id 없음")
        print("   ⚠️ 테스트할 doc_id가 없습니다")

    # =========================================================================
    # 테스트 4: get_full_document_text
    # =========================================================================
    print("\n[4] get_full_document_text (전체 문서 텍스트)")
    if test_doc_id:
        try:
            full_text = await client.get_full_document_text(test_doc_id)

            if full_text:
                results.add("get_full_document_text", True, f"{len(full_text)}자")
                print(f"   ✅ 전체 텍스트: {len(full_text)}자")
                preview = full_text[:150].replace('\n', ' ')
                print(f"       미리보기: {preview}...")
            else:
                results.add("get_full_document_text", False, "텍스트 없음")
                print("   ❌ 텍스트가 비어있습니다")
        except Exception as e:
            results.add("get_full_document_text", False, str(e)[:80])
            print(f"   ❌ 예외: {e}")
    else:
        results.add("get_full_document_text", False, "doc_id 없음")
        print("   ⚠️ 테스트할 doc_id가 없습니다")

    # =========================================================================
    # 테스트 5: doc_id escape 안전성
    # =========================================================================
    print("\n[5] doc_id escape 안전성 테스트")
    try:
        from app.clients.milvus_client import escape_milvus_string, is_safe_doc_id

        test_cases = [
            ('normal_file.docx', True),
            ('한글파일명.pdf', True),
            ('file with spaces.txt', True),
            ('uuid-12345678-1234-1234-1234-123456789abc', True),
            ('injection"; DROP TABLE--', False),
            ('path/../../etc/passwd', False),
        ]

        all_safe = True
        for doc_id, expected_safe in test_cases:
            is_safe = is_safe_doc_id(doc_id)
            escaped = escape_milvus_string(doc_id)
            status = "✓" if is_safe == expected_safe else "✗"
            if is_safe != expected_safe:
                all_safe = False
            print(f"       {status} '{doc_id[:30]}...' safe={is_safe}, escaped='{escaped[:30]}...'")

        results.add("doc_id_escape", all_safe, "모든 케이스 통과" if all_safe else "일부 실패")
    except Exception as e:
        results.add("doc_id_escape", False, str(e)[:80])
        print(f"   ❌ 예외: {e}")

    # =========================================================================
    # 결과 요약
    # =========================================================================
    all_passed = results.summary()

    # 연결 해제
    client.disconnect()

    return all_passed


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
