"""
Phase 15: RAG Gap 보완 제안 API 테스트

테스트 항목:
1. GapSuggestionRequest/Response 모델 테스트
2. GapSuggestionService 단위 테스트
3. FastAPI 엔드포인트 통합 테스트
4. 에러 케이스 테스트
"""

import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.gap_suggestion import (
    GapQuestion,
    GapSuggestionItem,
    GapSuggestionRequest,
    GapSuggestionResponse,
    LLMSuggestionResponse,
    TimeRange,
)
from app.services.gap_suggestion_service import GapSuggestionService


@pytest.fixture
def anyio_backend() -> str:
    """pytest-anyio backend configuration."""
    return "asyncio"


@pytest.fixture
def test_client() -> TestClient:
    """FastAPI 테스트 클라이언트."""
    return TestClient(app)


# =============================================================================
# 1. DTO/모델 테스트
# =============================================================================


class TestGapSuggestionModels:
    """Gap Suggestion 모델 테스트."""

    def test_gap_question_creation(self) -> None:
        """GapQuestion 모델 생성 테스트."""
        question = GapQuestion(
            question_id="log-123",
            text="재택근무할 때 VPN 안 쓰면 어떻게 되나요?",
            user_role="EMPLOYEE",
            intent="POLICY_QA",
            domain="POLICY",
            asked_count=5,
        )

        assert question.question_id == "log-123"
        assert question.text == "재택근무할 때 VPN 안 쓰면 어떻게 되나요?"
        assert question.user_role == "EMPLOYEE"
        assert question.intent == "POLICY_QA"
        assert question.domain == "POLICY"
        assert question.asked_count == 5

    def test_gap_question_camelcase_alias(self) -> None:
        """GapQuestion camelCase alias 테스트."""
        # camelCase로 입력
        data = {
            "questionId": "log-123",
            "text": "테스트 질문",
            "userRole": "EMPLOYEE",
            "intent": "POLICY_QA",
            "domain": "POLICY",
            "askedCount": 3,
        }
        question = GapQuestion(**data)

        assert question.question_id == "log-123"
        assert question.user_role == "EMPLOYEE"
        assert question.asked_count == 3

    def test_gap_suggestion_request_creation(self) -> None:
        """GapSuggestionRequest 모델 생성 테스트."""
        request = GapSuggestionRequest(
            domain="POLICY",
            questions=[
                GapQuestion(
                    question_id="log-1",
                    text="질문 1",
                    user_role="EMPLOYEE",
                    intent="POLICY_QA",
                    domain="POLICY",
                )
            ],
        )

        assert request.domain == "POLICY"
        assert len(request.questions) == 1

    def test_gap_suggestion_request_empty_questions(self) -> None:
        """GapSuggestionRequest 빈 질문 목록 테스트."""
        request = GapSuggestionRequest(questions=[])

        assert len(request.questions) == 0

    def test_gap_suggestion_item_creation(self) -> None:
        """GapSuggestionItem 모델 생성 테스트."""
        item = GapSuggestionItem(
            id="SUG-001",
            title="재택근무 보안 수칙 추가",
            description="VPN 사용 의무 등을 명시해야 합니다.",
            related_question_ids=["log-123", "log-456"],
            priority="HIGH",
        )

        assert item.id == "SUG-001"
        assert item.title == "재택근무 보안 수칙 추가"
        assert item.priority == "HIGH"
        assert len(item.related_question_ids) == 2

    def test_gap_suggestion_response_creation(self) -> None:
        """GapSuggestionResponse 모델 생성 테스트."""
        response = GapSuggestionResponse(
            summary="총 2개의 보완 제안이 있습니다.",
            suggestions=[
                GapSuggestionItem(
                    id="SUG-001",
                    title="제안 1",
                    description="설명 1",
                    related_question_ids=["log-1"],
                )
            ],
        )

        assert "2개의 보완 제안" in response.summary
        assert len(response.suggestions) == 1

    def test_gap_suggestion_response_json_serialization(self) -> None:
        """GapSuggestionResponse JSON 직렬화 테스트."""
        response = GapSuggestionResponse(
            summary="테스트 요약",
            suggestions=[
                GapSuggestionItem(
                    id="SUG-001",
                    title="제안 제목",
                    description="제안 설명",
                    related_question_ids=["log-1"],
                    priority="HIGH",
                )
            ],
        )

        json_data = response.model_dump(by_alias=True)

        assert "summary" in json_data
        assert "suggestions" in json_data
        assert json_data["suggestions"][0]["relatedQuestionIds"] == ["log-1"]


# =============================================================================
# 2. GapSuggestionService 단위 테스트
# =============================================================================


class TestGapSuggestionService:
    """GapSuggestionService 단위 테스트."""

    @pytest.mark.anyio
    async def test_empty_questions_returns_empty_response(self) -> None:
        """질문이 0개일 때 빈 응답 반환 테스트."""
        service = GapSuggestionService()
        request = GapSuggestionRequest(questions=[])

        response = await service.generate_suggestions(request)

        assert "분석할 RAG Gap 질문이 없습니다" in response.summary
        assert len(response.suggestions) == 0

    @pytest.mark.anyio
    async def test_llm_response_parsing(self) -> None:
        """LLM 응답 파싱 테스트."""
        mock_llm_response = json.dumps({
            "summary": "재택근무 관련 보안 규정이 부족합니다.",
            "suggestions": [
                {
                    "title": "재택근무 VPN 사용 의무 조항 추가",
                    "description": "VPN 미사용 시 제재 조항을 신설해야 합니다.",
                    "related_question_ids": ["log-123"],
                    "priority": "HIGH"
                }
            ]
        })

        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.return_value = mock_llm_response

        service = GapSuggestionService(llm_client=mock_llm)

        request = GapSuggestionRequest(
            domain="POLICY",
            questions=[
                GapQuestion(
                    question_id="log-123",
                    text="재택근무할 때 VPN 안 쓰면 어떻게 되나요?",
                    user_role="EMPLOYEE",
                    intent="POLICY_QA",
                    domain="POLICY",
                )
            ],
        )

        response = await service.generate_suggestions(request)

        assert "재택근무" in response.summary
        assert len(response.suggestions) == 1
        assert response.suggestions[0].title == "재택근무 VPN 사용 의무 조항 추가"
        assert response.suggestions[0].priority == "HIGH"
        assert "log-123" in response.suggestions[0].related_question_ids

    @pytest.mark.anyio
    async def test_llm_response_with_json_block(self) -> None:
        """LLM이 ```json``` 블록으로 응답할 때 파싱 테스트."""
        mock_llm_response = """
        다음은 분석 결과입니다:

        ```json
        {
            "summary": "BYOD 관련 규정이 필요합니다.",
            "suggestions": [
                {
                    "title": "BYOD 정책 신설",
                    "description": "개인 기기 사용 가이드라인 필요",
                    "related_question_ids": ["log-456"],
                    "priority": "MEDIUM"
                }
            ]
        }
        ```
        """

        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.return_value = mock_llm_response

        service = GapSuggestionService(llm_client=mock_llm)

        request = GapSuggestionRequest(
            domain="POLICY",
            questions=[
                GapQuestion(
                    question_id="log-456",
                    text="개인 휴대폰으로 회사 메일 보면 보안 위반인가요?",
                    user_role="EMPLOYEE",
                    intent="POLICY_QA",
                    domain="POLICY",
                )
            ],
        )

        response = await service.generate_suggestions(request)

        assert "BYOD" in response.summary
        assert len(response.suggestions) == 1
        assert response.suggestions[0].priority == "MEDIUM"

    @pytest.mark.anyio
    async def test_llm_failure_returns_fallback(self) -> None:
        """LLM 호출 실패 시 fallback 응답 테스트."""
        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.side_effect = Exception("LLM Error")

        service = GapSuggestionService(llm_client=mock_llm)

        request = GapSuggestionRequest(
            domain="POLICY",
            questions=[
                GapQuestion(
                    question_id="log-789",
                    text="테스트 질문",
                    user_role="EMPLOYEE",
                    intent="POLICY_QA",
                    domain="POLICY",
                )
            ],
        )

        response = await service.generate_suggestions(request)

        # fallback 응답이 반환되어야 함
        assert "기본 제안" in response.summary or "RAG Gap 질문" in response.summary
        assert len(response.suggestions) >= 1

    @pytest.mark.anyio
    async def test_invalid_json_returns_fallback(self) -> None:
        """LLM이 잘못된 JSON 반환 시 fallback 테스트."""
        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.return_value = "이것은 유효하지 않은 응답입니다."

        service = GapSuggestionService(llm_client=mock_llm)

        request = GapSuggestionRequest(
            domain="EDU",
            questions=[
                GapQuestion(
                    question_id="log-edu-1",
                    text="교육 관련 질문",
                    user_role="EMPLOYEE",
                    intent="EDU_QA",
                    domain="EDU",
                )
            ],
        )

        response = await service.generate_suggestions(request)

        # fallback 응답
        assert len(response.suggestions) >= 1
        assert "EDU" in response.suggestions[0].title or "문서 보완" in response.suggestions[0].title

    @pytest.mark.anyio
    async def test_priority_normalization(self) -> None:
        """우선순위 정규화 테스트."""
        service = GapSuggestionService()

        # 다양한 형태의 우선순위 정규화
        assert service._normalize_priority("HIGH") == "HIGH"
        assert service._normalize_priority("높음") == "HIGH"
        assert service._normalize_priority("H") == "HIGH"
        assert service._normalize_priority("MEDIUM") == "MEDIUM"
        assert service._normalize_priority("중간") == "MEDIUM"
        assert service._normalize_priority("LOW") == "LOW"
        assert service._normalize_priority("낮음") == "LOW"
        assert service._normalize_priority("UNKNOWN") is None
        assert service._normalize_priority(None) is None

    @pytest.mark.anyio
    async def test_multiple_questions_grouped(self) -> None:
        """여러 질문이 적절히 처리되는지 테스트."""
        mock_llm_response = json.dumps({
            "summary": "재택근무와 BYOD 관련 규정이 필요합니다.",
            "suggestions": [
                {
                    "title": "재택근무 보안 규정",
                    "description": "VPN 사용 등 명시",
                    "related_question_ids": ["log-1", "log-2"],
                    "priority": "HIGH"
                },
                {
                    "title": "BYOD 정책",
                    "description": "개인 기기 사용 가이드",
                    "related_question_ids": ["log-3"],
                    "priority": "MEDIUM"
                }
            ]
        })

        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.return_value = mock_llm_response

        service = GapSuggestionService(llm_client=mock_llm)

        request = GapSuggestionRequest(
            domain="POLICY",
            questions=[
                GapQuestion(
                    question_id="log-1",
                    text="재택근무 VPN 질문 1",
                    user_role="EMPLOYEE",
                    intent="POLICY_QA",
                    domain="POLICY",
                ),
                GapQuestion(
                    question_id="log-2",
                    text="재택근무 VPN 질문 2",
                    user_role="EMPLOYEE",
                    intent="POLICY_QA",
                    domain="POLICY",
                ),
                GapQuestion(
                    question_id="log-3",
                    text="BYOD 관련 질문",
                    user_role="EMPLOYEE",
                    intent="POLICY_QA",
                    domain="POLICY",
                ),
            ],
        )

        response = await service.generate_suggestions(request)

        assert len(response.suggestions) == 2
        # 첫 번째 제안에 2개의 관련 질문
        assert len(response.suggestions[0].related_question_ids) == 2


# =============================================================================
# 3. FastAPI 엔드포인트 통합 테스트
# =============================================================================


class TestGapSuggestionsAPI:
    """Gap Suggestions API 통합 테스트."""

    def test_endpoint_exists(self, test_client: TestClient) -> None:
        """엔드포인트 존재 확인 테스트."""
        # 빈 요청으로 테스트 (400 or 422가 아닌 것 확인)
        response = test_client.post(
            "/ai/gap/policy-edu/suggestions",
            json={"questions": []},
        )

        # 200 OK여야 함 (빈 질문 처리)
        assert response.status_code == 200

    def test_empty_questions_response(self, test_client: TestClient) -> None:
        """빈 질문 목록 요청 테스트."""
        response = test_client.post(
            "/ai/gap/policy-edu/suggestions",
            json={"questions": []},
        )

        assert response.status_code == 200
        data = response.json()

        assert "summary" in data
        assert "suggestions" in data
        assert "분석할 RAG Gap 질문이 없습니다" in data["summary"]
        assert len(data["suggestions"]) == 0

    def test_valid_request_with_mock_llm(self, test_client: TestClient) -> None:
        """유효한 요청 테스트 (LLM mock)."""
        mock_llm_response = json.dumps({
            "summary": "테스트 요약",
            "suggestions": [
                {
                    "title": "테스트 제안",
                    "description": "테스트 설명",
                    "related_question_ids": ["log-test"],
                    "priority": "HIGH"
                }
            ]
        })

        with patch(
            "app.services.gap_suggestion_service.GapSuggestionService.generate_suggestions"
        ) as mock_generate:
            mock_generate.return_value = GapSuggestionResponse(
                summary="테스트 요약",
                suggestions=[
                    GapSuggestionItem(
                        id="SUG-001",
                        title="테스트 제안",
                        description="테스트 설명",
                        related_question_ids=["log-test"],
                        priority="HIGH",
                    )
                ],
            )

            response = test_client.post(
                "/ai/gap/policy-edu/suggestions",
                json={
                    "domain": "POLICY",
                    "questions": [
                        {
                            "questionId": "log-test",
                            "text": "테스트 질문",
                            "userRole": "EMPLOYEE",
                            "intent": "POLICY_QA",
                            "domain": "POLICY",
                        }
                    ],
                },
            )

            assert response.status_code == 200
            data = response.json()

            assert data["summary"] == "테스트 요약"
            assert len(data["suggestions"]) == 1
            assert data["suggestions"][0]["title"] == "테스트 제안"

    def test_request_with_time_range(self, test_client: TestClient) -> None:
        """timeRange 포함 요청 테스트."""
        response = test_client.post(
            "/ai/gap/policy-edu/suggestions",
            json={
                "timeRange": {
                    "from": "2025-12-01T00:00:00",
                    "to": "2025-12-10T23:59:59",
                },
                "domain": "POLICY",
                "questions": [],
            },
        )

        assert response.status_code == 200

    def test_request_validation_error(self, test_client: TestClient) -> None:
        """유효성 검사 오류 테스트."""
        # questions 필드 누락
        response = test_client.post(
            "/ai/gap/policy-edu/suggestions",
            json={"domain": "POLICY"},
        )

        assert response.status_code == 422  # Validation Error

    def test_response_schema_completeness(self, test_client: TestClient) -> None:
        """응답 스키마 완전성 테스트."""
        with patch(
            "app.services.gap_suggestion_service.GapSuggestionService.generate_suggestions"
        ) as mock_generate:
            mock_generate.return_value = GapSuggestionResponse(
                summary="완전성 테스트 요약",
                suggestions=[
                    GapSuggestionItem(
                        id="SUG-001",
                        title="제안 1",
                        description="설명 1",
                        related_question_ids=["q1", "q2"],
                        priority="HIGH",
                    ),
                    GapSuggestionItem(
                        id="SUG-002",
                        title="제안 2",
                        description="설명 2",
                        related_question_ids=["q3"],
                        priority="MEDIUM",
                    ),
                ],
            )

            response = test_client.post(
                "/ai/gap/policy-edu/suggestions",
                json={
                    "domain": "POLICY",
                    "questions": [
                        {
                            "questionId": "q1",
                            "text": "질문 1",
                            "userRole": "EMPLOYEE",
                            "intent": "POLICY_QA",
                            "domain": "POLICY",
                        }
                    ],
                },
            )

            assert response.status_code == 200
            data = response.json()

            # 응답 스키마 확인
            assert "summary" in data
            assert "suggestions" in data
            assert len(data["suggestions"]) == 2

            # 각 suggestion 항목 확인
            sug1 = data["suggestions"][0]
            assert "id" in sug1
            assert "title" in sug1
            assert "description" in sug1
            assert "relatedQuestionIds" in sug1
            assert "priority" in sug1


# =============================================================================
# 4. 에러 케이스 테스트
# =============================================================================


class TestGapSuggestionsErrorCases:
    """Gap Suggestions 에러 케이스 테스트."""

    @pytest.mark.anyio
    async def test_service_handles_llm_exception(self) -> None:
        """LLM 예외 처리 테스트."""
        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.side_effect = ConnectionError("Connection failed")

        service = GapSuggestionService(llm_client=mock_llm)

        request = GapSuggestionRequest(
            questions=[
                GapQuestion(
                    question_id="test-1",
                    text="테스트",
                    user_role="EMPLOYEE",
                    intent="POLICY_QA",
                    domain="POLICY",
                )
            ]
        )

        # 예외가 발생해도 fallback 응답을 반환해야 함
        response = await service.generate_suggestions(request)

        assert response.summary is not None
        assert isinstance(response.suggestions, list)

    @pytest.mark.anyio
    async def test_service_handles_malformed_json(self) -> None:
        """잘못된 JSON 처리 테스트."""
        mock_llm = AsyncMock()
        mock_llm.generate_chat_completion.return_value = '{"summary": "test", "suggestions": [invalid json'

        service = GapSuggestionService(llm_client=mock_llm)

        request = GapSuggestionRequest(
            questions=[
                GapQuestion(
                    question_id="test-1",
                    text="테스트",
                    user_role="EMPLOYEE",
                    intent="POLICY_QA",
                    domain="POLICY",
                )
            ]
        )

        response = await service.generate_suggestions(request)

        # fallback 응답 반환
        assert response.summary is not None
        assert len(response.suggestions) >= 1

    def test_api_handles_invalid_question_format(self, test_client: TestClient) -> None:
        """잘못된 질문 형식 처리 테스트."""
        response = test_client.post(
            "/ai/gap/policy-edu/suggestions",
            json={
                "questions": [
                    {
                        # questionId 누락
                        "text": "질문",
                        "userRole": "EMPLOYEE",
                        "intent": "POLICY_QA",
                        "domain": "POLICY",
                    }
                ]
            },
        )

        assert response.status_code == 422  # Validation Error
