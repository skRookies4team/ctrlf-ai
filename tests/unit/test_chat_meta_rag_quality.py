"""
Phase 59: RAG Quality Meta Fields Unit Tests

ChatAnswerMeta에 추가된 rag_quality_* 필드 테스트
- 필드 존재 확인
- 직렬화 테스트
- 값 설정 테스트
"""

import pytest
from app.models.chat import ChatAnswerMeta, ChatResponse, ChatSource


class TestChatAnswerMetaRagQualityFields:
    """ChatAnswerMeta의 rag_quality_* 필드 테스트"""

    def test_rag_quality_fields_exist(self):
        """rag_quality_* 필드가 존재하는지 확인"""
        meta = ChatAnswerMeta()

        # 필드 존재 확인 (기본값 None)
        assert hasattr(meta, "rag_quality_grade")
        assert hasattr(meta, "rag_quality_action")
        assert hasattr(meta, "rag_quality_min_l2")
        assert hasattr(meta, "rag_quality_warning")
        assert hasattr(meta, "rag_quality_insufficient")

    def test_rag_quality_fields_default_none(self):
        """rag_quality_* 필드 기본값이 None인지 확인"""
        meta = ChatAnswerMeta()

        assert meta.rag_quality_grade is None
        assert meta.rag_quality_action is None
        assert meta.rag_quality_min_l2 is None
        assert meta.rag_quality_warning is None
        assert meta.rag_quality_insufficient is None

    def test_rag_quality_ok_case(self):
        """OK 등급 케이스 - 모든 필드 None (경고 없음)"""
        meta = ChatAnswerMeta(
            rag_quality_grade="OK",
            rag_quality_action="PROCEED",
            rag_quality_min_l2=1.2,
            rag_quality_warning=None,
            rag_quality_insufficient=False,
        )

        assert meta.rag_quality_grade == "OK"
        assert meta.rag_quality_action == "PROCEED"
        assert meta.rag_quality_min_l2 == 1.2
        assert meta.rag_quality_warning is None
        assert meta.rag_quality_insufficient is False

    def test_rag_quality_low_case(self):
        """LOW 등급 케이스 - 경고 메시지 포함"""
        warning_msg = "관련 문서가 제한적이어서 답변의 정확도가 낮을 수 있습니다."
        meta = ChatAnswerMeta(
            rag_quality_grade="LOW",
            rag_quality_action="PROCEED_WITH_WARNING",
            rag_quality_min_l2=1.5,
            rag_quality_warning=warning_msg,
            rag_quality_insufficient=False,
        )

        assert meta.rag_quality_grade == "LOW"
        assert meta.rag_quality_action == "PROCEED_WITH_WARNING"
        assert meta.rag_quality_min_l2 == 1.5
        assert meta.rag_quality_warning == warning_msg
        assert meta.rag_quality_insufficient is False

    def test_rag_quality_insufficient_case(self):
        """INSUFFICIENT 등급 케이스 - REJECT"""
        meta = ChatAnswerMeta(
            rag_quality_grade="INSUFFICIENT",
            rag_quality_action="REJECT",
            rag_quality_min_l2=1.8,
            rag_quality_warning=None,
            rag_quality_insufficient=True,
        )

        assert meta.rag_quality_grade == "INSUFFICIENT"
        assert meta.rag_quality_action == "REJECT"
        assert meta.rag_quality_min_l2 == 1.8
        assert meta.rag_quality_warning is None
        assert meta.rag_quality_insufficient is True

    def test_rag_quality_serialization(self):
        """직렬화 테스트 - model_dump()에 필드 포함"""
        meta = ChatAnswerMeta(
            rag_quality_grade="LOW",
            rag_quality_action="PROCEED_WITH_WARNING",
            rag_quality_min_l2=1.45,
            rag_quality_warning="경고 메시지",
            rag_quality_insufficient=False,
        )

        # Pydantic v2 model_dump()
        data = meta.model_dump()

        assert "rag_quality_grade" in data
        assert "rag_quality_action" in data
        assert "rag_quality_min_l2" in data
        assert "rag_quality_warning" in data
        assert "rag_quality_insufficient" in data

        assert data["rag_quality_grade"] == "LOW"
        assert data["rag_quality_action"] == "PROCEED_WITH_WARNING"
        assert data["rag_quality_min_l2"] == 1.45
        assert data["rag_quality_warning"] == "경고 메시지"
        assert data["rag_quality_insufficient"] is False

    def test_rag_quality_json_serialization(self):
        """JSON 직렬화 테스트"""
        meta = ChatAnswerMeta(
            rag_quality_grade="OK",
            rag_quality_action="PROCEED",
            rag_quality_min_l2=1.1,
        )

        # model_dump_json()으로 JSON 문자열 생성
        json_str = meta.model_dump_json()

        assert "rag_quality_grade" in json_str
        assert "rag_quality_action" in json_str
        assert "rag_quality_min_l2" in json_str
        assert '"OK"' in json_str
        assert '"PROCEED"' in json_str


class TestChatResponseWithRagQualityMeta:
    """ChatResponse에 rag_quality_* meta가 포함되는지 테스트"""

    def test_chat_response_with_quality_meta(self):
        """ChatResponse 생성 시 meta에 rag_quality_* 포함"""
        meta = ChatAnswerMeta(
            route="RAG_INTERNAL",
            domain="POLICY",
            rag_quality_grade="LOW",
            rag_quality_action="PROCEED_WITH_WARNING",
            rag_quality_min_l2=1.5,
            rag_quality_warning="경고",
            rag_quality_insufficient=False,
        )

        response = ChatResponse(
            answer="테스트 답변입니다.",
            sources=[],
            meta=meta,
        )

        assert response.meta.rag_quality_grade == "LOW"
        assert response.meta.rag_quality_action == "PROCEED_WITH_WARNING"
        assert response.meta.rag_quality_warning == "경고"

    def test_chat_response_reject_case(self):
        """REJECT 케이스 - sources=[], insufficient=True"""
        meta = ChatAnswerMeta(
            used_model="quality-gate",
            llm_provider="none",
            route="RAG_INTERNAL",
            domain="POLICY",
            rag_used=True,
            rag_source_count=0,
            rag_quality_grade="INSUFFICIENT",
            rag_quality_action="REJECT",
            rag_quality_min_l2=float("inf"),
            rag_quality_warning=None,
            rag_quality_insufficient=True,
        )

        response = ChatResponse(
            answer="죄송합니다. 현재 질문에 대해 충분한 근거를 찾지 못했습니다.",
            model="quality-gate",
            sources=[],
            meta=meta,
        )

        # REJECT 특성 확인
        assert response.model == "quality-gate"
        assert response.sources == []
        assert response.meta.rag_quality_grade == "INSUFFICIENT"
        assert response.meta.rag_quality_action == "REJECT"
        assert response.meta.rag_quality_insufficient is True

    def test_chat_response_full_serialization(self):
        """전체 ChatResponse JSON 직렬화"""
        meta = ChatAnswerMeta(
            route="RAG_INTERNAL",
            intent="POLICY_QA",
            domain="POLICY",
            rag_used=True,
            rag_source_count=3,
            rag_quality_grade="OK",
            rag_quality_action="PROCEED",
            rag_quality_min_l2=1.15,
        )

        sources = [
            ChatSource(doc_id="doc1", title="규정1", score=1.15),
            ChatSource(doc_id="doc2", title="규정2", score=1.25),
        ]

        response = ChatResponse(
            answer="답변입니다.",
            sources=sources,
            meta=meta,
        )

        # 전체 직렬화
        data = response.model_dump()

        assert data["meta"]["rag_quality_grade"] == "OK"
        assert data["meta"]["rag_quality_action"] == "PROCEED"
        assert data["meta"]["rag_quality_min_l2"] == 1.15


class TestWarningAppendLogic:
    """경고 메시지 append 로직 테스트 (결정론적)"""

    def test_warning_append_format(self):
        """경고 메시지 append 형식 테스트"""
        base_answer = "연차 신청은 HR 시스템을 통해 가능합니다."
        warning_msg = "관련 문서가 제한적이어서 답변의 정확도가 낮을 수 있습니다."

        # chat_service.py의 append 로직과 동일
        final_answer = f"{base_answer}\n\n---\n⚠️ {warning_msg}"

        # 형식 확인
        assert "---" in final_answer
        assert "⚠️" in final_answer
        assert warning_msg in final_answer
        assert final_answer.startswith(base_answer)

    def test_warning_not_appended_when_none(self):
        """경고 메시지가 None이면 append 안 함"""
        base_answer = "답변입니다."
        warning_msg = None

        # chat_service.py 로직: if quality_warning_message:
        if warning_msg:
            final_answer = f"{base_answer}\n\n---\n⚠️ {warning_msg}"
        else:
            final_answer = base_answer

        assert final_answer == base_answer
        assert "---" not in final_answer
        assert "⚠️" not in final_answer

    def test_warning_separation_from_answer(self):
        """경고와 본문이 구분되는지 테스트"""
        base_answer = "본문 답변입니다."
        warning_msg = "경고 메시지"

        final_answer = f"{base_answer}\n\n---\n⚠️ {warning_msg}"

        # 분리 가능성 테스트
        parts = final_answer.split("\n\n---\n")
        assert len(parts) == 2
        assert parts[0] == base_answer
        assert parts[1] == f"⚠️ {warning_msg}"
