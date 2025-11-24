"""
Unit tests for core.pipeline module
"""
import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from core.pipeline import process_pdf_file, search_similar_chunks
from core.models import ChunkingReport


class TestProcessPdfFile:
    """process_pdf_file 함수 테스트"""

    @patch('core.pipeline.extract_text_from_pdf')
    @patch('core.pipeline.embed_texts')
    @patch('core.pipeline.get_vector_store')
    def test_successful_processing(self, mock_vector_store, mock_embed, mock_extract):
        """정상적인 PDF 처리 테스트"""
        # Mock 설정
        mock_extract.return_value = "This is a test PDF content with sufficient length to pass validation checks and create multiple chunks."
        mock_embed.return_value = [[0.1] * 384, [0.2] * 384]  # 2개 청크에 대한 임베딩
        mock_vs = MagicMock()
        mock_vector_store.return_value = mock_vs

        # 임시 PDF 파일 생성
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(b'fake pdf content')
            tmp_path = tmp.name

        try:
            # 실행
            report = process_pdf_file(
                file_path=tmp_path,
                file_name="test.pdf",
                chunk_strategy="character_window",
                max_chars=50,
                overlap_chars=10
            )

            # 검증
            assert isinstance(report, ChunkingReport)
            assert report.file_name == "test.pdf"
            assert report.ingest_id is not None
            assert report.status in ["OK", "WARN", "FAIL"]
            assert report.raw_text_len > 0
            assert report.chunk_strategy == "character_window"

        finally:
            # 임시 파일 삭제
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @patch('core.ocr.run_ocr')
    @patch('core.pipeline.extract_text_from_pdf')
    def test_ocr_fallback_when_no_text(self, mock_extract, mock_ocr):
        """텍스트 추출 실패 시 OCR fallback 테스트"""
        # Mock 설정: 일반 추출 실패, OCR 성공
        mock_extract.return_value = ""
        mock_ocr.return_value = "OCR extracted text with sufficient length for processing and validation."

        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(b'fake pdf content')
            tmp_path = tmp.name

        try:
            report = process_pdf_file(
                file_path=tmp_path,
                file_name="test.pdf",
                use_ocr_fallback=True
            )

            # OCR이 호출되었는지 확인
            mock_ocr.assert_called_once()

            # 리포트 검증
            assert isinstance(report, ChunkingReport)

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @patch('core.pipeline.extract_text_from_pdf')
    def test_no_text_extracted_returns_fail(self, mock_extract):
        """텍스트 추출 실패 시 FAIL 반환"""
        mock_extract.return_value = ""

        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(b'fake pdf content')
            tmp_path = tmp.name

        try:
            report = process_pdf_file(
                file_path=tmp_path,
                file_name="test.pdf",
                use_ocr_fallback=False  # OCR 비활성화
            )

            assert report.status == "FAIL"
            # TEXT_TOO_SHORT, NO_CHUNKS 등도 함께 감지될 수 있음
            assert len(report.reasons) > 0

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @patch('core.pipeline.extract_text_from_pdf')
    @patch('core.pipeline.embed_texts')
    @patch('core.pipeline.get_vector_store')
    def test_fail_status_skips_embedding(self, mock_vector_store, mock_embed, mock_extract):
        """FAIL 상태일 때 임베딩/벡터 저장을 건너뜀"""
        mock_extract.return_value = "Short"  # 너무 짧아서 FAIL 예상

        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(b'fake pdf content')
            tmp_path = tmp.name

        try:
            report = process_pdf_file(
                file_path=tmp_path,
                file_name="test.pdf"
            )

            if report.status == "FAIL":
                # FAIL이면 임베딩이 호출되지 않아야 함
                mock_embed.assert_not_called()
                mock_vector_store().add_vectors.assert_not_called()

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @patch('core.pipeline.extract_text_from_pdf')
    def test_different_chunking_strategies(self, mock_extract):
        """다양한 청킹 전략 테스트"""
        mock_extract.return_value = """
        제1장 총칙

        제1조 목적
        이 규정은 정보보안에 관한 사항을 규정함을 목적으로 한다.

        제2장 정의

        제2조 용어의 정의
        이 규정에서 사용하는 용어의 정의는 다음과 같다.
        """

        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(b'fake pdf content')
            tmp_path = tmp.name

        try:
            # character_window
            report1 = process_pdf_file(
                file_path=tmp_path,
                file_name="test.pdf",
                chunk_strategy="character_window"
            )
            assert report1.chunk_strategy == "character_window"

            # paragraph_based
            report2 = process_pdf_file(
                file_path=tmp_path,
                file_name="test.pdf",
                chunk_strategy="paragraph_based"
            )
            assert report2.chunk_strategy == "paragraph_based"

            # heading_based
            report3 = process_pdf_file(
                file_path=tmp_path,
                file_name="test.pdf",
                chunk_strategy="heading_based"
            )
            assert report3.chunk_strategy == "heading_based"

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @patch('core.pipeline.extract_text_from_pdf')
    def test_invalid_strategy_falls_back(self, mock_extract):
        """잘못된 전략은 character_window로 fallback"""
        mock_extract.return_value = "A" * 500

        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(b'fake pdf content')
            tmp_path = tmp.name

        try:
            report = process_pdf_file(
                file_path=tmp_path,
                file_name="test.pdf",
                chunk_strategy="invalid_strategy"
            )

            # character_window으로 fallback되어야 함
            assert report.chunk_strategy == "character_window"

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


class TestSearchSimilarChunks:
    """search_similar_chunks 함수 테스트"""

    @patch('core.pipeline.embed_texts')
    @patch('core.pipeline.get_vector_store')
    def test_search_returns_results(self, mock_vector_store, mock_embed):
        """검색 결과가 정상 반환되는지 테스트"""
        # Mock 설정
        mock_embed.return_value = [[0.1] * 384]
        mock_vs = MagicMock()
        mock_vs.search.return_value = [
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
        mock_vector_store.return_value = mock_vs

        # 실행
        results = search_similar_chunks("test query", top_k=5)

        # 검증
        assert len(results) > 0
        assert results[0]["score"] == 0.5
        assert results[0]["file_name"] == "test.pdf"
        mock_embed.assert_called_once_with(["test query"])
        mock_vs.search.assert_called_once()

    @patch('core.pipeline.embed_texts')
    @patch('core.pipeline.get_vector_store')
    def test_search_respects_top_k(self, mock_vector_store, mock_embed):
        """top_k 파라미터가 정상 동작하는지 테스트"""
        mock_embed.return_value = [[0.1] * 384]
        mock_vs = MagicMock()
        mock_vs.search.return_value = []
        mock_vector_store.return_value = mock_vs

        # top_k=3으로 검색
        search_similar_chunks("test query", top_k=3)

        # search가 top_k=3으로 호출되었는지 확인
        args, kwargs = mock_vs.search.call_args
        assert kwargs.get('top_k') == 3 or args[1] == 3
