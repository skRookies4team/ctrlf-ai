"""
FAQ 서비스 Milvus 통합 테스트

실행: python scripts/test_faq_milvus.py

테스트 항목:
1. FaqDraftService 초기화 시 Milvus 클라이언트 로드
2. _get_context_docs에서 Milvus 검색 사용
3. answer_source = "MILVUS" 확인
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


async def test_faq_milvus_integration():
    """FAQ 서비스 Milvus 통합 테스트."""
    from app.core.config import get_settings, clear_settings_cache
    from app.services.faq_service import FaqDraftService
    from app.models.faq import FaqDraftGenerateRequest

    # 설정 캐시 클리어 (환경변수 재로드)
    clear_settings_cache()
    settings = get_settings()

    print("=" * 60)
    print("  FAQ 서비스 Milvus 통합 테스트")
    print("=" * 60)

    print(f"\n[설정 확인]")
    print(f"   MILVUS_ENABLED: {settings.MILVUS_ENABLED}")
    print(f"   MILVUS_HOST: {settings.MILVUS_HOST}")
    print(f"   MILVUS_PORT: {settings.MILVUS_PORT}")
    print(f"   MILVUS_COLLECTION: {settings.MILVUS_COLLECTION_NAME}")

    if not settings.MILVUS_ENABLED:
        print("\n   ⚠️ MILVUS_ENABLED=false 입니다. .env 파일을 확인하세요.")
        return

    print("\n[1] FaqDraftService 초기화")
    try:
        service = FaqDraftService()
        print(f"   ✅ 서비스 초기화 성공")
        print(f"   Milvus 활성화: {service._milvus_enabled}")
        print(f"   Milvus 클라이언트: {'있음' if service._milvus_client else '없음'}")
    except Exception as e:
        print(f"   ❌ 서비스 초기화 실패: {e}")
        import traceback
        traceback.print_exc()
        return

    print("\n[2] Milvus 검색 테스트 (_search_milvus)")
    try:
        # 테스트 요청 생성 (domain은 RAGFLOW_DATASET_MAPPING에 있는 값 사용)
        req = FaqDraftGenerateRequest(
            cluster_id="test-cluster-001",
            domain="POLICY",  # .env의 RAGFLOW_DATASET_MAPPING에 있는 값
            canonical_question="코드리뷰는 어떻게 하나요?",
            sample_questions=["코드리뷰 절차가 어떻게 되나요?", "PR 리뷰 방법"],
        )

        context_docs, source_type = await service._get_context_docs(req)

        print(f"   ✅ 검색 성공")
        print(f"   소스 타입: {source_type}")
        print(f"   문서 수: {len(context_docs)}")

        if context_docs:
            for i, doc in enumerate(context_docs[:3], 1):
                print(f"\n   [{i}] title: {doc.title[:50] if doc.title else 'N/A'}...")
                print(f"       score: {doc.score:.4f}")
                snippet = doc.snippet[:100].replace('\n', ' ') if doc.snippet else 'N/A'
                print(f"       snippet: {snippet}...")

        if source_type == "MILVUS":
            print("\n   🎯 Milvus 직접 검색 성공!")
        else:
            print(f"\n   ⚠️ 소스 타입이 MILVUS가 아닙니다: {source_type}")

    except Exception as e:
        print(f"   ❌ 검색 실패: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)
    print("  테스트 완료")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_faq_milvus_integration())
