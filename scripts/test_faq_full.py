"""
FAQ 전체 초안 생성 테스트 (Milvus + LLM)

실행: python scripts/test_faq_full.py
"""
import asyncio
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))


async def test_full_faq_generation():
    """전체 FAQ 초안 생성 테스트."""
    from app.core.config import get_settings, clear_settings_cache
    from app.services.faq_service import FaqDraftService, FaqGenerationError
    from app.models.faq import FaqDraftGenerateRequest

    clear_settings_cache()
    settings = get_settings()

    print("=" * 60)
    print("  FAQ 전체 초안 생성 테스트 (Milvus + LLM)")
    print("=" * 60)

    print(f"\n[설정]")
    print(f"   MILVUS_ENABLED: {settings.MILVUS_ENABLED}")
    print(f"   LLM_BASE_URL: {settings.llm_base_url}")
    print(f"   EMBEDDING_BASE_URL: {settings.embedding_base_url}")

    print("\n[1] FaqDraftService 초기화")
    service = FaqDraftService()
    print(f"   ✅ 초기화 완료 (Milvus: {service._milvus_enabled})")

    print("\n[2] FAQ 초안 생성 요청")
    req = FaqDraftGenerateRequest(
        cluster_id="test-cluster-001",
        domain="POLICY",
        canonical_question="장애인 인식개선 교육은 얼마나 자주 받아야 하나요?",
        sample_questions=[
            "장애인 교육 주기가 어떻게 되나요?",
            "장애인 인식 교육 의무인가요?",
        ],
    )
    print(f"   질문: {req.canonical_question}")

    print("\n[3] FAQ 초안 생성 중...")
    try:
        draft = await service.generate_faq_draft(req)

        print(f"\n   ✅ FAQ 초안 생성 성공!")
        print(f"\n   === 결과 ===")
        print(f"   ID: {draft.faq_draft_id}")
        print(f"   answer_source: {draft.answer_source}")
        print(f"   ai_confidence: {draft.ai_confidence}")
        print(f"\n   질문: {draft.question}")
        print(f"\n   요약: {draft.summary}")
        print(f"\n   답변 (markdown):")
        print("-" * 40)
        print(draft.answer_markdown[:500] if draft.answer_markdown else "(없음)")
        if draft.answer_markdown and len(draft.answer_markdown) > 500:
            print("... (truncated)")
        print("-" * 40)

    except FaqGenerationError as e:
        print(f"\n   ❌ FAQ 생성 실패: {e}")
    except Exception as e:
        print(f"\n   ❌ 예외 발생: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)


if __name__ == "__main__":
    asyncio.run(test_full_faq_generation())
