"""
FAQ API 실제 HTTP 호출 테스트 스크립트

사용법:
    python scripts/test_faq_api_http.py

테스트 대상:
    1. POST /ai/faq/generate - FAQ 초안 생성 (단건)
    2. POST /ai/faq/generate/batch - FAQ 초안 배치 생성
    3. POST /ai/faq/generate/auto - FAQ 자동 생성
"""

import asyncio
import sys
from pathlib import Path

# 프로젝트 루트를 PYTHONPATH에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


def create_mock_settings():
    """테스트용 설정 생성"""
    mock_settings = MagicMock()
    mock_settings.ELASTICSEARCH_URL = "http://localhost:9200"
    mock_settings.MILVUS_HOST = "localhost"
    mock_settings.MILVUS_PORT = 19530
    mock_settings.MILVUS_ENABLED = False  # Milvus 비활성화
    mock_settings.LLM_PROVIDER = "openai"
    mock_settings.LLM_MODEL = "gpt-4o-mini"
    mock_settings.LLM_API_KEY = "test-key"
    mock_settings.FAQ_INTENT_CONFIDENCE_THRESHOLD = 0.7
    mock_settings.FAQ_INTENT_CONFIDENCE_REQUIRED = False
    mock_settings.FAQ_BATCH_CONCURRENCY = 3
    mock_settings.FAQ_LOW_RELEVANCE_BLOCK = False
    mock_settings.FAQ_CONFIDENCE_WARN_THRESHOLD = 0.6
    mock_settings.FORBIDDEN_QUERY_FILTER_ENABLED = False
    mock_settings.FORBIDDEN_QUERY_PROFILE = "default"
    mock_settings.PII_ENABLED = False
    mock_settings.LOG_LEVEL = "INFO"
    mock_settings.BACKEND_INTERNAL_TOKEN = "test-token"
    mock_settings.BACKEND_BASE_URL = "http://localhost:8080"
    return mock_settings


def run_tests():
    """FAQ API 테스트 실행"""
    print("=" * 60)
    print("FAQ API 실제 HTTP 호출 테스트")
    print("=" * 60)

    # 설정 모킹
    mock_settings = create_mock_settings()

    # LLM 응답 모킹
    mock_llm_response = """status: SUCCESS
question: USB 메모리 반출 시 어떤 절차가 필요한가요?
summary: USB 반출 시 정보보호팀 사전 승인이 필요합니다.
answer_markdown: |
  USB 메모리를 외부로 반출하려면 정보보호팀의 사전 승인이 필요합니다.

  - USB 반출 신청서 작성 후 정보보호팀에 제출
  - 담당자 검토 및 승인 (1~2 영업일 소요)
  - 승인 완료 후 반출 가능
  - 반출 기록은 6개월간 보관

  **참고**
  - 정보보안 정책 (p.15)
ai_confidence: 0.85
"""

    with patch("app.core.config.get_settings", return_value=mock_settings):
        with patch("app.core.config.Settings", return_value=mock_settings):
            # FaqDraftService의 의존성 모킹
            with patch("app.services.faq_service.LLMClient") as mock_llm_class:
                mock_llm = AsyncMock()
                mock_llm.generate_chat_completion = AsyncMock(return_value=mock_llm_response)
                mock_llm_class.return_value = mock_llm

                with patch("app.services.faq_service.PiiService") as mock_pii_class:
                    mock_pii = AsyncMock()
                    mock_pii_result = MagicMock()
                    mock_pii_result.has_pii = False
                    mock_pii_result.tags = []
                    mock_pii_result.masked_text = ""
                    mock_pii.detect_and_mask = AsyncMock(return_value=mock_pii_result)
                    mock_pii_class.return_value = mock_pii

                    with patch("app.services.faq_service.RagHandler") as mock_rag_class:
                        mock_rag = AsyncMock()
                        # RagHandler 검색 결과 모킹
                        mock_source = MagicMock()
                        mock_source.title = "정보보안 정책"
                        mock_source.doc_id = "SEC-001"
                        mock_source.snippet = "USB 메모리 반출 시 정보보호팀 승인 필요"
                        mock_source.score = 0.85
                        mock_source.page = 15
                        mock_rag.perform_search_with_fallback = AsyncMock(
                            return_value=([mock_source], False, "milvus")
                        )
                        mock_rag_class.return_value = mock_rag

                        # 서비스 싱글턴 초기화 방지
                        with patch("app.api.v1.faq._faq_service", None):
                            with patch("app.api.v1.faq._faq_auto_service", None):
                                from app.main import app

                                client = TestClient(app)

                                # 테스트 1: FAQ 단건 생성
                                test_faq_generate_single(client)

                                # 테스트 2: FAQ 배치 생성
                                test_faq_generate_batch(client)

                                # 테스트 3: FAQ 자동 생성
                                test_faq_generate_auto(client)

    print("\n" + "=" * 60)
    print("모든 FAQ API 테스트 완료!")
    print("=" * 60)


def test_faq_generate_single(client: TestClient):
    """테스트 1: POST /ai/faq/generate - FAQ 초안 생성 (단건)"""
    print("\n" + "-" * 40)
    print("테스트 1: POST /ai/faq/generate (단건 생성)")
    print("-" * 40)

    request_body = {
        "domain": "SEC_POLICY",
        "cluster_id": "cluster-usb-001",
        "canonical_question": "USB 메모리 반출 시 어떤 절차가 필요한가요?",
        "sample_questions": [
            "USB 외부 반출하려면 어떻게 해야 해요?",
            "USB 반출 승인 어디서 받아요?"
        ],
        "top_docs": [
            {
                "doc_id": "SEC-001",
                "title": "정보보안 정책",
                "snippet": "USB 메모리를 외부로 반출하려면 정보보호팀의 사전 승인이 필요합니다.",
                "article_label": "제3장 제2조"
            }
        ],
        "avg_intent_confidence": 0.85
    }

    print(f"요청 URL: POST /ai/faq/generate")
    print(f"요청 본문: {request_body}")

    response = client.post("/ai/faq/generate", json=request_body)

    print(f"\n응답 상태 코드: {response.status_code}")
    print(f"응답 본문: {response.json()}")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert data["status"] == "SUCCESS", f"Expected SUCCESS, got {data['status']}"
    assert data["faq_draft"] is not None, "faq_draft should not be None"
    assert "question" in data["faq_draft"], "faq_draft should have question"
    assert "answer_markdown" in data["faq_draft"], "faq_draft should have answer_markdown"

    print("\n✅ 테스트 1 통과: FAQ 단건 생성 성공")


def test_faq_generate_batch(client: TestClient):
    """테스트 2: POST /ai/faq/generate/batch - FAQ 배치 생성"""
    print("\n" + "-" * 40)
    print("테스트 2: POST /ai/faq/generate/batch (배치 생성)")
    print("-" * 40)

    request_body = {
        "items": [
            {
                "domain": "SEC_POLICY",
                "cluster_id": "cluster-usb-001",
                "canonical_question": "USB 메모리 반출 절차는?",
                "sample_questions": []
            },
            {
                "domain": "HR_POLICY",
                "cluster_id": "cluster-leave-001",
                "canonical_question": "연차 휴가 신청 방법은?",
                "sample_questions": ["연차 어떻게 써요?"]
            }
        ],
        "concurrency": 2
    }

    print(f"요청 URL: POST /ai/faq/generate/batch")
    print(f"요청 본문: {request_body}")

    response = client.post("/ai/faq/generate/batch", json=request_body)

    print(f"\n응답 상태 코드: {response.status_code}")
    print(f"응답 본문: {response.json()}")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert "items" in data, "Response should have items"
    assert "total_count" in data, "Response should have total_count"
    assert "success_count" in data, "Response should have success_count"
    assert data["total_count"] == 2, f"Expected total_count=2, got {data['total_count']}"

    print(f"\n✅ 테스트 2 통과: FAQ 배치 생성 성공")
    print(f"   - 전체: {data['total_count']}개")
    print(f"   - 성공: {data['success_count']}개")
    print(f"   - 실패: {data['failed_count']}개")


def test_faq_generate_auto(client: TestClient):
    """테스트 3: POST /ai/faq/generate/auto - FAQ 자동 생성"""
    print("\n" + "-" * 40)
    print("테스트 3: POST /ai/faq/generate/auto (자동 생성)")
    print("-" * 40)

    request_body = {
        "domain": "SEC_POLICY",
        "min_frequency": 3,
        "days_back": 30,
        "max_candidates": 10,
        "auto_generate_drafts": False  # 후보만 선정
    }

    print(f"요청 URL: POST /ai/faq/generate/auto")
    print(f"요청 본문: {request_body}")

    response = client.post("/ai/faq/generate/auto", json=request_body)

    print(f"\n응답 상태 코드: {response.status_code}")
    print(f"응답 본문: {response.json()}")

    # 자동 생성은 백엔드 API가 없으면 FAILED 반환 (정상)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert "status" in data, "Response should have status"
    assert "candidates_found" in data, "Response should have candidates_found"

    if data["status"] == "FAILED":
        print(f"\n⚠️ 테스트 3 통과 (예상된 실패): {data.get('error_message', 'No error message')}")
        print("   - 백엔드 질문 로그 API가 구현되지 않아 실패 (정상 동작)")
    else:
        print(f"\n✅ 테스트 3 통과: FAQ 자동 생성 성공")
        print(f"   - 후보 수: {data['candidates_found']}개")


if __name__ == "__main__":
    run_tests()
