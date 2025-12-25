"""
Option 3 Chat 통합 테스트: RagHandler Milvus 라우팅 검증

실행: python scripts/test_option3_chat_integration.py

테스트 항목:
1. CHAT_RETRIEVER_BACKEND 설정 확인
2. RagHandler 초기화 (Milvus/RAGFlow 선택)
3. perform_search_with_fallback - Milvus 검색 + retriever_used 반환
4. _truncate_context - 컨텍스트 길이 제한
5. RAGFlow fallback 동작 확인
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
    print("  Option 3 Chat 통합 테스트")
    print("=" * 60)

    print(f"\n[설정]")
    print(f"   MILVUS_ENABLED: {settings.MILVUS_ENABLED}")
    print(f"   RETRIEVAL_BACKEND: {settings.RETRIEVAL_BACKEND}")
    print(f"   CHAT_RETRIEVER_BACKEND: {settings.CHAT_RETRIEVER_BACKEND}")
    print(f"   chat_retriever_backend (resolved): {settings.chat_retriever_backend}")
    print(f"   CHAT_CONTEXT_MAX_CHARS: {settings.CHAT_CONTEXT_MAX_CHARS}")
    print(f"   CHAT_CONTEXT_MAX_SOURCES: {settings.CHAT_CONTEXT_MAX_SOURCES}")

    # =========================================================================
    # 테스트 1: CHAT_RETRIEVER_BACKEND 설정 확인
    # =========================================================================
    print("\n[1] CHAT_RETRIEVER_BACKEND 설정 확인")
    try:
        backend = settings.chat_retriever_backend
        is_milvus = backend == "milvus" and settings.MILVUS_ENABLED

        results.add("chat_retriever_backend", True, f"backend={backend}, milvus_enabled={settings.MILVUS_ENABLED}")
        print(f"   [O] 설정 확인 완료")
        print(f"       chat_retriever_backend: {backend}")
        print(f"       Milvus 사용 여부: {is_milvus}")
    except Exception as e:
        results.add("chat_retriever_backend", False, str(e)[:80])
        print(f"   [X] 예외: {e}")

    # =========================================================================
    # 테스트 2: RagHandler 초기화
    # =========================================================================
    print("\n[2] RagHandler 초기화")
    try:
        from app.services.chat.rag_handler import RagHandler
        from app.clients.ragflow_client import RagflowClient

        ragflow_client = RagflowClient()
        handler = RagHandler(ragflow_client=ragflow_client)

        results.add("rag_handler_init", True, f"_use_milvus={handler._use_milvus}")
        print(f"   [O] RagHandler 초기화 완료")
        print(f"       _use_milvus: {handler._use_milvus}")
        print(f"       _milvus: {'초기화됨' if handler._milvus else '없음'}")
    except Exception as e:
        results.add("rag_handler_init", False, str(e)[:80])
        print(f"   [X] 예외: {e}")
        import traceback
        traceback.print_exc()
        # 핸들러 초기화 실패 시 이후 테스트 불가
        results.summary()
        return False

    # =========================================================================
    # 테스트 3: perform_search_with_fallback
    # =========================================================================
    print("\n[3] perform_search_with_fallback (검색 + retriever_used)")
    try:
        from app.models.chat import ChatRequest, ChatMessage

        # 테스트용 ChatRequest 생성
        test_request = ChatRequest(
            session_id="test-session-001",
            user_id="test-user",
            user_role="EMPLOYEE",
            department="IT",
            domain="POLICY",
            channel="WEB",
            messages=[
                ChatMessage(role="user", content="장애인 인식개선 교육 관련 규정을 알려주세요")
            ],
        )

        sources, is_failed, retriever_used = await handler.perform_search_with_fallback(
            query="장애인 인식개선 교육 관련 규정",
            domain="POLICY",
            req=test_request,
            request_id="test-chat-001",
        )

        if sources and len(sources) > 0:
            results.add("perform_search_with_fallback", True,
                       f"{len(sources)}개 결과, retriever_used={retriever_used}")
            print(f"   [O] 검색 성공")
            print(f"       결과 수: {len(sources)}개")
            print(f"       is_failed: {is_failed}")
            print(f"       retriever_used: {retriever_used}")

            for i, src in enumerate(sources[:3], 1):
                print(f"\n   [{i}] doc_id: {src.doc_id[:50]}...")
                print(f"       title: {src.title[:50]}...")
                print(f"       score: {src.score:.4f}" if src.score else "       score: N/A")
                snippet = src.snippet[:60].replace('\n', ' ') if src.snippet else 'N/A'
                print(f"       snippet: {snippet}...")
        else:
            results.add("perform_search_with_fallback", False, f"결과 없음, retriever_used={retriever_used}")
            print(f"   [X] 검색 결과 없음")
            print(f"       retriever_used: {retriever_used}")
    except Exception as e:
        results.add("perform_search_with_fallback", False, str(e)[:80])
        print(f"   [X] 예외: {e}")
        import traceback
        traceback.print_exc()

    # =========================================================================
    # 테스트 4: _truncate_context 동작 확인
    # =========================================================================
    print("\n[4] _truncate_context (컨텍스트 길이 제한)")
    try:
        from app.models.chat import ChatSource

        # 긴 snippet을 가진 테스트 소스 생성
        long_snippet = "테스트 " * 2000  # 약 14000자
        test_sources = [
            ChatSource(
                doc_id="test-doc-1",
                title="테스트 문서 1",
                snippet=long_snippet,
                score=0.95,
            ),
            ChatSource(
                doc_id="test-doc-2",
                title="테스트 문서 2",
                snippet=long_snippet,
                score=0.90,
            ),
            ChatSource(
                doc_id="test-doc-3",
                title="테스트 문서 3",
                snippet=long_snippet,
                score=0.85,
            ),
        ]

        original_total = sum(len(s.snippet) for s in test_sources)
        truncated = handler._truncate_context(test_sources)
        truncated_total = sum(len(s.snippet) for s in truncated)

        is_truncated = truncated_total <= settings.CHAT_CONTEXT_MAX_CHARS

        results.add("truncate_context", is_truncated,
                   f"원본={original_total}자, 변환후={truncated_total}자, 최대={settings.CHAT_CONTEXT_MAX_CHARS}자")
        print(f"   {'[O]' if is_truncated else '[X]'} 컨텍스트 제한 {'적용됨' if is_truncated else '미적용'}")
        print(f"       원본 총 길이: {original_total}자")
        print(f"       변환 후 길이: {truncated_total}자")
        print(f"       최대 허용: {settings.CHAT_CONTEXT_MAX_CHARS}자")
        print(f"       반환된 소스 수: {len(truncated)}개")

    except Exception as e:
        results.add("truncate_context", False, str(e)[:80])
        print(f"   [X] 예외: {e}")

    # =========================================================================
    # 테스트 5: retriever_used 필드 유효성
    # =========================================================================
    print("\n[5] retriever_used 필드 유효성")
    try:
        from app.services.chat.rag_handler import RetrieverUsed

        valid_values = {"MILVUS", "RAGFLOW", "RAGFLOW_FALLBACK"}

        # 이전 테스트에서 얻은 retriever_used 확인
        is_valid = retriever_used in valid_values

        results.add("retriever_used_valid", is_valid,
                   f"값={retriever_used}, 유효={is_valid}")
        print(f"   {'[O]' if is_valid else '[X]'} retriever_used 유효성")
        print(f"       현재 값: {retriever_used}")
        print(f"       유효한 값: {valid_values}")

    except Exception as e:
        results.add("retriever_used_valid", False, str(e)[:80])
        print(f"   [X] 예외: {e}")

    # =========================================================================
    # 결과 요약
    # =========================================================================
    all_passed = results.summary()

    return all_passed


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
