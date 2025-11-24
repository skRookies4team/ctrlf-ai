"""
API integration tests using FastAPI TestClient
"""
import pytest
import tempfile
import os
from io import BytesIO
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.main import app

client = TestClient(app)


class TestRootEndpoints:
    """루트 엔드포인트 테스트"""

    def test_root_endpoint(self):
        """루트 엔드포인트 테스트"""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "service" in data
        assert "version" in data
        assert "endpoints" in data

    def test_health_endpoint(self):
        """전역 헬스체크 엔드포인트 테스트"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"


class TestIngestEndpoints:
    """Ingest API 엔드포인트 테스트"""

    def test_ingest_health(self):
        """Ingest 헬스체크 엔드포인트"""
        response = client.get("/api/v1/ingest/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    @patch('app.routers.ingest.process_pdf_file')
    def test_ingest_file_success(self, mock_process):
        """파일 업로드 성공 테스트"""
        from core.models import ChunkingReport

        # Mock ChunkingReport 생성
        mock_report = ChunkingReport(
            ingest_id="test123",
            file_name="test.pdf",
            file_path="/test/test.pdf",
            status="OK",
            reasons=[],
            raw_text_len=1000,
            cleaned_text_len=900,
            num_chunks=5,
            chunk_lengths=[200, 200, 200, 200, 100],
            chunk_strategy="character_window",
            max_chars=1000,
            overlap_chars=200,
            created_at="2025-11-19T12:00:00"
        )
        mock_process.return_value = mock_report

        # 가짜 PDF 파일 생성
        fake_pdf = BytesIO(b'%PDF-1.4 fake pdf content')
        files = {"file": ("test.pdf", fake_pdf, "application/pdf")}

        # API 호출
        response = client.post("/api/v1/ingest/file", files=files)

        # 검증
        assert response.status_code == 200
        data = response.json()
        assert "ingest_id" in data
        assert data["file_name"] == "test.pdf"
        assert data["status"] == "OK"
        assert data["num_chunks"] == 5
        assert "chunk_strategy" in data
        assert "created_at" in data

    def test_ingest_file_invalid_extension(self):
        """잘못된 파일 확장자 테스트"""
        fake_file = BytesIO(b'not a pdf')
        files = {"file": ("test.txt", fake_file, "text/plain")}

        response = client.post("/api/v1/ingest/file", files=files)

        # 400 에러 예상
        assert response.status_code == 400
        assert "PDF" in response.json()["detail"]

    @patch('app.routers.ingest.process_pdf_file')
    def test_ingest_with_chunking_strategy(self, mock_process):
        """청킹 전략 파라미터 테스트"""
        from core.models import ChunkingReport

        mock_report = ChunkingReport(
            ingest_id="test123",
            file_name="test.pdf",
            file_path="/test/test.pdf",
            status="OK",
            reasons=[],
            raw_text_len=1000,
            cleaned_text_len=900,
            num_chunks=3,
            chunk_lengths=[300, 300, 300],
            chunk_strategy="paragraph_based",
            max_chars=1000,
            overlap_chars=0,
            created_at="2025-11-19T12:00:00"
        )
        mock_process.return_value = mock_report

        fake_pdf = BytesIO(b'%PDF-1.4 fake pdf')
        files = {"file": ("test.pdf", fake_pdf, "application/pdf")}

        # paragraph_based 전략으로 요청
        response = client.post(
            "/api/v1/ingest/file",
            files=files,
            data={"chunk_strategy": "paragraph_based"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["chunk_strategy"] == "paragraph_based"


class TestReportsEndpoints:
    """Reports API 엔드포인트 테스트"""

    @patch('app.routers.reports.load_recent_reports')
    def test_get_reports_list(self, mock_load):
        """리포트 리스트 조회 테스트"""
        from core.models import ChunkingReport

        # Mock 리포트 리스트
        mock_reports = [
            ChunkingReport(
                ingest_id="test1",
                file_name="test1.pdf",
                file_path="/test/test1.pdf",
                status="OK",
                reasons=[],
                raw_text_len=1000,
                cleaned_text_len=900,
                num_chunks=5,
                chunk_lengths=[200] * 5,
                chunk_strategy="character_window",
                max_chars=1000,
                overlap_chars=200,
                created_at="2025-11-19T12:00:00"
            )
        ]
        mock_load.return_value = mock_reports

        response = client.get("/api/v1/ingest/reports")

        assert response.status_code == 200
        data = response.json()
        # API는 {"reports": [...], "total": N} 형식으로 반환
        assert "reports" in data
        assert isinstance(data["reports"], list)
        if len(data["reports"]) > 0:
            assert "ingest_id" in data["reports"][0]
            assert "file_name" in data["reports"][0]
            assert "status" in data["reports"][0]

    @patch('app.routers.reports.find_report_by_id')
    def test_get_report_by_id(self, mock_find):
        """특정 리포트 조회 테스트"""
        from core.models import ChunkingReport

        mock_report = ChunkingReport(
            ingest_id="test123",
            file_name="test.pdf",
            file_path="/test/test.pdf",
            status="OK",
            reasons=[],
            raw_text_len=1000,
            cleaned_text_len=900,
            num_chunks=5,
            chunk_lengths=[200] * 5,
            chunk_strategy="character_window",
            max_chars=1000,
            overlap_chars=200,
            created_at="2025-11-19T12:00:00"
        )
        mock_find.return_value = mock_report

        response = client.get("/api/v1/ingest/reports/test123")

        assert response.status_code == 200
        data = response.json()
        assert data["ingest_id"] == "test123"

    @patch('app.routers.reports.find_report_by_id')
    def test_get_report_not_found(self, mock_find):
        """존재하지 않는 리포트 조회"""
        mock_find.return_value = None

        response = client.get("/api/v1/ingest/reports/nonexistent")

        assert response.status_code == 404


class TestSearchEndpoints:
    """Search API 엔드포인트 테스트"""

    @patch('app.routers.search.search_similar_chunks')
    def test_search_query(self, mock_search):
        """검색 쿼리 테스트"""
        # Mock 검색 결과
        mock_search.return_value = [
            {
                "score": 0.5,
                "vector_id": 0,
                "ingest_id": "test123",
                "file_name": "test.pdf",
                "chunk_index": 0,
                "text": "Sample text",
                "strategy": "character_window"
            }
        ]

        request_data = {
            "query": "test query",
            "top_k": 5
        }

        response = client.post("/api/v1/search", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert isinstance(data["results"], list)
        assert data["query"] == "test query"
        assert data["top_k"] == 5

    @patch('app.routers.search.get_vector_store')
    def test_vector_store_stats(self, mock_get_vs):
        """벡터 스토어 통계 조회 테스트"""
        mock_vs = MagicMock()
        mock_vs.get_stats.return_value = {
            "dimension": 384,
            "total_vectors": 100,
            "vector_count": 100,
            "metadata_count": 100,
            "index_file_exists": True,
            "metadata_file_exists": True,
            "index_file_path": "/data/vector_store/faiss.index",
            "metadata_file_path": "/data/vector_store/metadata.jsonl"
        }
        mock_get_vs.return_value = mock_vs

        response = client.get("/api/v1/vector-store/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["dimension"] == 384
        assert data["total_vectors"] == 100


class TestRAGEndpoints:
    """RAG API 엔드포인트 테스트"""

    @patch('app.routers.rag.search_similar_chunks')
    def test_rag_query(self, mock_search):
        """RAG 쿼리 테스트"""
        mock_search.return_value = [
            {
                "score": 0.3,
                "vector_id": 1,
                "ingest_id": "test456",
                "file_name": "doc.pdf",
                "chunk_index": 2,
                "text": "RAG test content",
                "strategy": "heading_based"
            }
        ]

        request_data = {
            "query": "RAG test query",
            "top_k": 3,
            "include_context": True
        }

        response = client.post("/api/v1/rag/query", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert "retrieved_chunks" in data
        assert isinstance(data["retrieved_chunks"], list)
        assert data["query"] == "RAG test query"
        assert data["top_k"] == 3

    @patch('core.embedder.embed_texts')
    @patch('app.routers.rag.get_vector_store')
    def test_rag_health(self, mock_get_vs, mock_embed):
        """RAG 헬스체크 테스트"""
        # Mock 설정
        mock_vs = MagicMock()
        mock_vs.get_stats.return_value = {"total_vectors": 50}
        mock_get_vs.return_value = mock_vs
        mock_embed.return_value = [[0.1] * 384]

        response = client.get("/api/v1/rag/health")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "vector_store_available" in data
        assert "embedder_available" in data
        assert "total_vectors" in data
