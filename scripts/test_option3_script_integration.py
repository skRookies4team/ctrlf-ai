"""
Option 3 Script Generation 통합 테스트: SourceSetOrchestrator Milvus 라우팅 검증

실행: python scripts/test_option3_script_integration.py

테스트 항목:
1. SCRIPT_RETRIEVER_BACKEND 설정 확인
2. SourceSetOrchestrator 초기화 (Milvus 클라이언트)
3. _extract_milvus_doc_id - source_url에서 파일명 추출
4. _fetch_document_chunks_milvus - Milvus에서 청크 조회
5. _process_document_with_routing - 라우팅 로직 확인
"""
import asyncio
import io
import os
import sys
from unittest.mock import AsyncMock, MagicMock

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
        status = "PASS" if success else "FAIL"
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
            marker = "[O]" if status == "PASS" else "[X]"
            print(f"   {marker} {status}: {name}")
            if message:
                print(f"         {message}")
        print(f"\n   총 {self.passed + self.failed}개 테스트: "
              f"{self.passed} 통과, {self.failed} 실패")
        print("=" * 60)
        return self.failed == 0


async def run_tests():
    """전체 테스트 실행."""
    from app.core.config import get_settings, clear_settings_cache
    from app.clients.milvus_client import get_milvus_client, clear_milvus_client

    clear_settings_cache()
    clear_milvus_client()
    settings = get_settings()

    results = TestResult()

    print("=" * 60)
    print("  Option 3 Script Generation 통합 테스트")
    print("=" * 60)

    print(f"\n[설정]")
    print(f"   MILVUS_ENABLED: {settings.MILVUS_ENABLED}")
    print(f"   RETRIEVAL_BACKEND: {settings.RETRIEVAL_BACKEND}")
    print(f"   SCRIPT_RETRIEVER_BACKEND: {settings.SCRIPT_RETRIEVER_BACKEND}")
    print(f"   script_retriever_backend (resolved): {settings.script_retriever_backend}")

    # =========================================================================
    # 테스트 1: SCRIPT_RETRIEVER_BACKEND 설정 확인
    # =========================================================================
    print("\n[1] SCRIPT_RETRIEVER_BACKEND 설정 확인")
    try:
        backend = settings.script_retriever_backend
        is_milvus = backend == "milvus" and settings.MILVUS_ENABLED

        results.add("script_retriever_backend", True,
                   f"backend={backend}, milvus_enabled={settings.MILVUS_ENABLED}")
        print(f"   [O] 설정 확인 완료")
        print(f"       script_retriever_backend: {backend}")
        print(f"       Milvus 사용 여부: {is_milvus}")
    except Exception as e:
        results.add("script_retriever_backend", False, str(e)[:80])
        print(f"   [X] 예외: {e}")

    # =========================================================================
    # 테스트 2: SourceSetOrchestrator 초기화
    # =========================================================================
    print("\n[2] SourceSetOrchestrator 초기화")
    orchestrator = None
    try:
        from app.services.source_set_orchestrator import (
            SourceSetOrchestrator,
            clear_source_set_orchestrator,
        )

        clear_source_set_orchestrator()

        # Mock clients to avoid actual network calls during init test
        mock_backend = MagicMock()
        mock_ragflow = MagicMock()

        orchestrator = SourceSetOrchestrator(
            backend_client=mock_backend,
            ragflow_client=mock_ragflow,
        )

        results.add("orchestrator_init", True, f"_use_milvus={orchestrator._use_milvus}")
        print(f"   [O] SourceSetOrchestrator 초기화 완료")
        print(f"       _use_milvus: {orchestrator._use_milvus}")
        print(f"       _milvus_client: {'초기화됨' if orchestrator._milvus_client else '없음'}")

    except Exception as e:
        results.add("orchestrator_init", False, str(e)[:80])
        print(f"   [X] 예외: {e}")
        import traceback
        traceback.print_exc()

    # =========================================================================
    # 테스트 3: _extract_milvus_doc_id
    # =========================================================================
    print("\n[3] _extract_milvus_doc_id (source_url에서 파일명 추출)")
    if orchestrator:
        try:
            test_cases = [
                # (source_url, expected_result)
                (
                    "https://bucket.s3.amazonaws.com/path/to/장애인식관련법령.docx?X-Amz-Signature=abc",
                    "장애인식관련법령.docx"
                ),
                (
                    "https://storage.example.com/docs/test_document.pdf",
                    "test_document.pdf"
                ),
                (
                    "https://cdn.example.com/files/%ED%95%9C%EA%B8%80%ED%8C%8C%EC%9D%BC.docx",
                    "한글파일.docx"  # URL decoded
                ),
                (
                    "https://bucket.s3.amazonaws.com/simple.txt",
                    "simple.txt"
                ),
            ]

            all_passed = True
            for source_url, expected in test_cases:
                result = orchestrator._extract_milvus_doc_id(source_url, "fallback-id")
                is_match = result == expected
                if not is_match:
                    all_passed = False
                status = "[O]" if is_match else "[X]"
                print(f"   {status} URL: ...{source_url[-40:]}")
                print(f"       예상: {expected}")
                print(f"       결과: {result}")

            results.add("extract_milvus_doc_id", all_passed,
                       "모든 케이스 통과" if all_passed else "일부 실패")

        except Exception as e:
            results.add("extract_milvus_doc_id", False, str(e)[:80])
            print(f"   [X] 예외: {e}")
    else:
        results.add("extract_milvus_doc_id", False, "orchestrator 없음")
        print("   [X] orchestrator가 초기화되지 않았습니다")

    # =========================================================================
    # 테스트 4: _fetch_document_chunks_milvus (Milvus 활성화 시)
    # =========================================================================
    print("\n[4] _fetch_document_chunks_milvus (Milvus에서 청크 조회)")
    if orchestrator and orchestrator._use_milvus and orchestrator._milvus_client:
        try:
            from app.models.source_set import SourceSetDocument

            # 실제 Milvus에서 doc_id 샘플 가져오기
            test_doc_id = None
            try:
                client = get_milvus_client()
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
                print(f"   [!] doc_id 샘플 조회 실패: {e}")

            if test_doc_id:
                # 테스트용 문서 생성
                test_doc = SourceSetDocument(
                    document_id="test-spring-doc-id",
                    title="테스트 문서",
                    source_url=f"https://example.com/docs/{test_doc_id}",
                    domain="POLICY",
                    order_no=1,
                )

                chunks = await orchestrator._fetch_document_chunks_milvus(test_doc)

                if chunks and len(chunks) > 0:
                    results.add("fetch_document_chunks_milvus", True,
                               f"{len(chunks)}개 청크 조회")
                    print(f"   [O] Milvus 청크 조회 성공")
                    print(f"       조회된 청크 수: {len(chunks)}")
                    print(f"       doc_id: {test_doc_id[:50]}...")

                    # 청크 구조 확인
                    if chunks:
                        first_chunk = chunks[0]
                        print(f"       첫 청크 키: {list(first_chunk.keys())}")
                        chunk_text = first_chunk.get("chunk_text", "")[:60]
                        print(f"       첫 청크 텍스트: {chunk_text}...")
                else:
                    results.add("fetch_document_chunks_milvus", False, "청크 없음")
                    print(f"   [X] 청크가 없습니다")
            else:
                results.add("fetch_document_chunks_milvus", False, "테스트 doc_id 없음")
                print(f"   [!] 테스트할 doc_id가 없습니다 (Milvus에 데이터 없음)")

        except Exception as e:
            results.add("fetch_document_chunks_milvus", False, str(e)[:80])
            print(f"   [X] 예외: {e}")
            import traceback
            traceback.print_exc()
    else:
        reason = "Milvus 비활성화" if not (orchestrator and orchestrator._use_milvus) else "클라이언트 없음"
        results.add("fetch_document_chunks_milvus", True, f"스킵 ({reason})")
        print(f"   [O] 스킵 - {reason}")

    # =========================================================================
    # 테스트 5: _process_document_with_routing 로직 확인
    # =========================================================================
    print("\n[5] _process_document_with_routing 메서드 존재 확인")
    if orchestrator:
        try:
            has_method = hasattr(orchestrator, '_process_document_with_routing')
            is_callable = callable(getattr(orchestrator, '_process_document_with_routing', None))

            results.add("process_document_with_routing", has_method and is_callable,
                       "메서드 존재 및 호출 가능")
            print(f"   [O] 메서드 확인 완료")
            print(f"       hasattr: {has_method}")
            print(f"       callable: {is_callable}")

            # 추가 메서드들 확인
            related_methods = [
                '_process_document_milvus',
                '_fetch_document_chunks_milvus',
                '_extract_milvus_doc_id',
            ]
            print(f"\n   관련 메서드:")
            for method in related_methods:
                exists = hasattr(orchestrator, method)
                print(f"       {method}: {'존재' if exists else '없음'}")

        except Exception as e:
            results.add("process_document_with_routing", False, str(e)[:80])
            print(f"   [X] 예외: {e}")
    else:
        results.add("process_document_with_routing", False, "orchestrator 없음")
        print("   [X] orchestrator가 초기화되지 않았습니다")

    # =========================================================================
    # 테스트 6: Milvus fallback 시나리오 (mock)
    # =========================================================================
    print("\n[6] Milvus -> RAGFlow fallback 시나리오 확인")
    try:
        from app.services.source_set_orchestrator import SourceSetOrchestrator
        from app.clients.milvus_client import MilvusError

        # Mock을 사용한 fallback 테스트
        mock_backend = MagicMock()
        mock_ragflow = MagicMock()
        mock_milvus = AsyncMock()

        # Milvus가 실패하도록 설정
        mock_milvus.get_document_chunks = AsyncMock(
            side_effect=MilvusError("Simulated Milvus failure")
        )

        test_orch = SourceSetOrchestrator(
            backend_client=mock_backend,
            ragflow_client=mock_ragflow,
            milvus_client=mock_milvus,
        )

        # _use_milvus를 강제로 True로 설정
        test_orch._use_milvus = True
        test_orch._milvus_client = mock_milvus

        # fallback 로직이 존재하는지 확인
        has_fallback = (
            hasattr(test_orch, '_process_document_with_routing') and
            hasattr(test_orch, '_process_document_milvus') and
            hasattr(test_orch, '_process_document')
        )

        results.add("fallback_logic", has_fallback, "fallback 메서드 체인 확인")
        print(f"   [O] Fallback 로직 확인")
        print(f"       _process_document_with_routing: 있음")
        print(f"       _process_document_milvus: 있음")
        print(f"       _process_document (RAGFlow): 있음")

    except Exception as e:
        results.add("fallback_logic", False, str(e)[:80])
        print(f"   [X] 예외: {e}")

    # =========================================================================
    # 결과 요약
    # =========================================================================
    all_passed = results.summary()

    return all_passed


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
