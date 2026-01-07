"""
used_doc_ids 기능 테스트

품질 분석용 문서 ID 목록 저장 기능 검증:
1. AILogEntry.used_doc_ids 필드 동작
2. ChatTurnPayload.used_doc_ids 필드 동작
3. emit_chat_turn_once used_doc_ids 파라미터
4. 직렬화 (camelCase) 검증
5. 빈 리스트 기본값 검증
"""

import pytest
from unittest.mock import patch, MagicMock

from app.models.ai_log import AILogEntry
from app.telemetry.models import ChatTurnPayload, RagInfo, RagSource


class TestAILogEntryUsedDocIds:
    """AILogEntry.used_doc_ids 필드 테스트"""

    def test_used_doc_ids_with_values(self):
        """문서 ID 목록이 있는 경우"""
        entry = AILogEntry(
            session_id="test-session",
            user_id="test-user",
            user_role="EMPLOYEE",
            domain="POLICY",
            intent="POLICY_QA",
            route="RAG_INTERNAL",
            latency_ms=100,
            used_doc_ids=["doc1", "doc2", "doc3"],
        )

        assert entry.used_doc_ids == ["doc1", "doc2", "doc3"]
        assert len(entry.used_doc_ids) == 3

    def test_used_doc_ids_default_empty_list(self):
        """기본값이 빈 리스트인지 확인"""
        entry = AILogEntry(
            session_id="test-session",
            user_id="test-user",
            user_role="EMPLOYEE",
            domain="POLICY",
            intent="POLICY_QA",
            route="RAG_INTERNAL",
            latency_ms=100,
        )

        assert entry.used_doc_ids == []
        assert isinstance(entry.used_doc_ids, list)

    def test_used_doc_ids_serialization_camelcase(self):
        """camelCase 직렬화 확인 (usedDocIds)"""
        entry = AILogEntry(
            session_id="test-session",
            user_id="test-user",
            user_role="EMPLOYEE",
            domain="POLICY",
            intent="POLICY_QA",
            route="RAG_INTERNAL",
            latency_ms=100,
            used_doc_ids=["doc1"],
        )

        json_data = entry.model_dump(by_alias=True, exclude_none=True)

        assert "usedDocIds" in json_data
        assert json_data["usedDocIds"] == ["doc1"]
        assert "used_doc_ids" not in json_data

    def test_used_doc_ids_empty_list_serialization(self):
        """빈 리스트도 직렬화되는지 확인"""
        entry = AILogEntry(
            session_id="test-session",
            user_id="test-user",
            user_role="EMPLOYEE",
            domain="POLICY",
            intent="POLICY_QA",
            route="RAG_INTERNAL",
            latency_ms=100,
            used_doc_ids=[],
        )

        json_data = entry.model_dump(by_alias=True)

        assert "usedDocIds" in json_data
        assert json_data["usedDocIds"] == []

    def test_used_doc_ids_consistency_with_rag_source_count(self):
        """used_doc_ids와 rag_source_count 정합성"""
        doc_ids = ["doc1", "doc2", "doc3"]
        entry = AILogEntry(
            session_id="test-session",
            user_id="test-user",
            user_role="EMPLOYEE",
            domain="POLICY",
            intent="POLICY_QA",
            route="RAG_INTERNAL",
            latency_ms=100,
            rag_used=True,
            rag_source_count=3,
            used_doc_ids=doc_ids,
        )

        assert len(entry.used_doc_ids) == entry.rag_source_count


class TestChatTurnPayloadUsedDocIds:
    """ChatTurnPayload.used_doc_ids 필드 테스트"""

    def test_used_doc_ids_with_values(self):
        """문서 ID 목록이 있는 경우"""
        payload = ChatTurnPayload(
            intent_main="POLICY_QA",
            route_type="RAG_INTERNAL",
            domain="POLICY",
            rag_used=True,
            latency_ms_total=100,
            pii_detected_input=False,
            pii_detected_output=False,
            used_doc_ids=["doc1", "doc2"],
        )

        assert payload.used_doc_ids == ["doc1", "doc2"]

    def test_used_doc_ids_default_empty_list(self):
        """기본값이 빈 리스트인지 확인"""
        payload = ChatTurnPayload(
            intent_main="POLICY_QA",
            route_type="RAG_INTERNAL",
            domain="POLICY",
            rag_used=False,
            latency_ms_total=100,
            pii_detected_input=False,
            pii_detected_output=False,
        )

        assert payload.used_doc_ids == []

    def test_used_doc_ids_serialization_camelcase(self):
        """camelCase 직렬화 확인 (usedDocIds)"""
        payload = ChatTurnPayload(
            intent_main="POLICY_QA",
            route_type="RAG_INTERNAL",
            domain="POLICY",
            rag_used=True,
            latency_ms_total=100,
            pii_detected_input=False,
            pii_detected_output=False,
            used_doc_ids=["doc1", "doc2"],
        )

        json_data = payload.model_dump(by_alias=True, exclude_none=True)

        assert "usedDocIds" in json_data
        assert json_data["usedDocIds"] == ["doc1", "doc2"]

    def test_used_doc_ids_with_rag_info(self):
        """RagInfo와 함께 사용할 때"""
        rag_info = RagInfo(
            retriever="milvus",
            top_k=3,
            min_score=0.5,
            max_score=0.9,
            avg_score=0.7,
            sources=[
                RagSource(doc_id="doc1", chunk_id=0, score=0.9),
                RagSource(doc_id="doc2", chunk_id=0, score=0.7),
            ],
        )

        payload = ChatTurnPayload(
            intent_main="POLICY_QA",
            route_type="RAG_INTERNAL",
            domain="POLICY",
            rag_used=True,
            latency_ms_total=100,
            pii_detected_input=False,
            pii_detected_output=False,
            rag=rag_info,
            used_doc_ids=["doc1", "doc2"],
        )

        # RagInfo.sources와 used_doc_ids가 일치하는지 확인
        rag_doc_ids = [s.doc_id for s in payload.rag.sources]
        assert payload.used_doc_ids == rag_doc_ids


class TestEmitChatTurnOnceUsedDocIds:
    """emit_chat_turn_once used_doc_ids 파라미터 테스트"""

    def setup_method(self):
        """각 테스트 전 중복 방지 가드 리셋"""
        from app.telemetry.emitters import reset_chat_turn_emitted
        reset_chat_turn_emitted()

    @patch("app.telemetry.emitters.get_request_context")
    @patch("app.telemetry.emitters.get_telemetry_publisher")
    def test_emit_with_used_doc_ids(self, mock_publisher, mock_ctx):
        """used_doc_ids가 포함된 이벤트 발행"""
        from app.telemetry.emitters import emit_chat_turn_once
        from app.telemetry.context import RequestContext

        # Mock 설정
        mock_ctx.return_value = RequestContext(
            trace_id="test-trace",
            user_id="test-user",
            dept_id="test-dept",
            conversation_id="test-conv",
            turn_id=1,
        )

        mock_pub_instance = MagicMock()
        mock_pub_instance.enqueue.return_value = True
        mock_publisher.return_value = mock_pub_instance

        # 실행
        result = emit_chat_turn_once(
            intent_main="POLICY_QA",
            route_type="RAG_INTERNAL",
            domain="POLICY",
            rag_used=True,
            latency_ms_total=100,
            pii_detected_input=False,
            pii_detected_output=False,
            used_doc_ids=["doc1", "doc2", "doc3"],
        )

        assert result is True

        # enqueue 호출 확인
        mock_pub_instance.enqueue.assert_called_once()
        event = mock_pub_instance.enqueue.call_args[0][0]

        # payload에 used_doc_ids 확인
        assert event.payload.used_doc_ids == ["doc1", "doc2", "doc3"]

    @patch("app.telemetry.emitters.get_request_context")
    @patch("app.telemetry.emitters.get_telemetry_publisher")
    def test_emit_with_none_used_doc_ids_defaults_to_empty(self, mock_publisher, mock_ctx):
        """used_doc_ids가 None이면 빈 리스트로 변환"""
        from app.telemetry.emitters import emit_chat_turn_once
        from app.telemetry.context import RequestContext

        mock_ctx.return_value = RequestContext(
            trace_id="test-trace",
            user_id="test-user",
            dept_id="test-dept",
            conversation_id="test-conv",
            turn_id=1,
        )

        mock_pub_instance = MagicMock()
        mock_pub_instance.enqueue.return_value = True
        mock_publisher.return_value = mock_pub_instance

        result = emit_chat_turn_once(
            intent_main="POLICY_QA",
            route_type="RAG_INTERNAL",
            domain="POLICY",
            rag_used=False,
            latency_ms_total=100,
            pii_detected_input=False,
            pii_detected_output=False,
            used_doc_ids=None,  # None 전달
        )

        assert result is True

        event = mock_pub_instance.enqueue.call_args[0][0]
        assert event.payload.used_doc_ids == []  # 빈 리스트로 변환됨


class TestDocIdExtraction:
    """doc_id 추출 로직 테스트"""

    def test_extract_doc_ids_from_sources(self):
        """sources 리스트에서 doc_id 추출"""
        from app.models.chat import ChatSource

        sources = [
            ChatSource(doc_id="doc1", title="문서1"),
            ChatSource(doc_id="doc2", title="문서2"),
            ChatSource(doc_id="doc3", title="문서3"),
        ]

        # Single Source of Truth: sources 확정 후 1회 계산
        used_doc_ids = [s.doc_id for s in sources] if sources else []

        assert used_doc_ids == ["doc1", "doc2", "doc3"]
        assert len(used_doc_ids) == len(sources)

    def test_extract_doc_ids_empty_sources(self):
        """빈 sources에서 추출"""
        sources = []

        used_doc_ids = [s.doc_id for s in sources] if sources else []

        assert used_doc_ids == []

    def test_extract_doc_ids_none_sources(self):
        """sources가 None인 경우"""
        sources = None

        used_doc_ids = [s.doc_id for s in sources] if sources else []

        assert used_doc_ids == []
