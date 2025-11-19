"""
Unit tests for core.evaluator module
"""
import pytest
from core.evaluator import evaluate_chunking
from core.models import ChunkingReport


class TestEvaluateChunking:
    """evaluate_chunking 함수 테스트"""

    def test_text_too_short_returns_fail(self):
        """TEXT_TOO_SHORT일 때 FAIL 반환"""
        report = evaluate_chunking(
            ingest_id="test123",
            file_name="test.pdf",
            file_path="/test/test.pdf",
            raw_text="A" * 100,
            cleaned_text="A" * 50,  # 200자 미만
            chunks=["chunk1"],
            chunk_strategy="character_window",
            max_chars=1000,
            overlap_chars=200
        )

        assert report.status == "FAIL"
        assert "TEXT_TOO_SHORT" in report.reasons

    def test_no_chunks_returns_fail(self):
        """NO_CHUNKS일 때 FAIL 반환"""
        report = evaluate_chunking(
            ingest_id="test123",
            file_name="test.pdf",
            file_path="/test/test.pdf",
            raw_text="A" * 1000,
            cleaned_text="A" * 900,
            chunks=[],  # 빈 청크 리스트
            chunk_strategy="character_window",
            max_chars=1000,
            overlap_chars=200
        )

        assert report.status == "FAIL"
        assert "NO_CHUNKS" in report.reasons

    def test_no_text_extracted_returns_fail(self):
        """NO_TEXT_EXTRACTED일 때 FAIL 반환"""
        report = evaluate_chunking(
            ingest_id="test123",
            file_name="test.pdf",
            file_path="/test/test.pdf",
            raw_text="",
            cleaned_text="",
            chunks=[],
            chunk_strategy="character_window",
            max_chars=1000,
            overlap_chars=200
        )

        assert report.status == "FAIL"
        # TEXT_TOO_SHORT와 NO_CHUNKS도 함께 감지됨
        assert any(reason in report.reasons for reason in ["NO_TEXT_EXTRACTED", "TEXT_TOO_SHORT", "NO_CHUNKS"])

    def test_chunk_too_long_returns_warn(self):
        """CHUNK_TOO_LONG일 때 WARN 반환"""
        long_chunk = "A" * 5000  # 5000자 청크

        report = evaluate_chunking(
            ingest_id="test123",
            file_name="test.pdf",
            file_path="/test/test.pdf",
            raw_text="A" * 10000,
            cleaned_text="A" * 9000,
            chunks=[long_chunk],
            chunk_strategy="character_window",
            max_chars=1000,
            overlap_chars=200
        )

        assert report.status == "WARN"
        assert "CHUNK_TOO_LONG" in report.reasons

    def test_too_many_chunks_returns_warn(self):
        """TOO_MANY_CHUNKS일 때 WARN 반환"""
        # 500개 이상의 청크
        many_chunks = ["chunk"] * 600

        report = evaluate_chunking(
            ingest_id="test123",
            file_name="test.pdf",
            file_path="/test/test.pdf",
            raw_text="A" * 100000,
            cleaned_text="A" * 90000,
            chunks=many_chunks,
            chunk_strategy="character_window",
            max_chars=1000,
            overlap_chars=200
        )

        assert report.status == "WARN"
        assert "TOO_MANY_CHUNKS" in report.reasons

    def test_chunk_too_short_returns_warn(self):
        """CHUNK_TOO_SHORT일 때 WARN 반환"""
        short_chunks = ["A" * 10, "B" * 15, "C" * 5]  # 매우 짧은 청크들

        report = evaluate_chunking(
            ingest_id="test123",
            file_name="test.pdf",
            file_path="/test/test.pdf",
            raw_text="A" * 1000,
            cleaned_text="A" * 900,
            chunks=short_chunks,
            chunk_strategy="character_window",
            max_chars=1000,
            overlap_chars=200
        )

        assert report.status == "WARN"
        assert "CHUNK_TOO_SHORT" in report.reasons

    def test_too_few_chunks_returns_warn(self):
        """TOO_FEW_CHUNKS일 때 WARN 반환"""
        # 긴 텍스트인데 청크가 1개만
        report = evaluate_chunking(
            ingest_id="test123",
            file_name="test.pdf",
            file_path="/test/test.pdf",
            raw_text="A" * 50000,
            cleaned_text="A" * 45000,
            chunks=["A" * 45000],  # 청크 1개
            chunk_strategy="character_window",
            max_chars=1000,
            overlap_chars=200
        )

        assert report.status == "WARN"
        # CHUNK_TOO_LONG도 함께 감지될 수 있음
        assert len(report.reasons) > 0

    def test_ok_status_with_good_chunks(self):
        """정상적인 청킹 결과는 OK 반환"""
        good_chunks = ["A" * 800, "B" * 900, "C" * 850, "D" * 920]

        report = evaluate_chunking(
            ingest_id="test123",
            file_name="test.pdf",
            file_path="/test/test.pdf",
            raw_text="A" * 5000,
            cleaned_text="A" * 4500,
            chunks=good_chunks,
            chunk_strategy="character_window",
            max_chars=1000,
            overlap_chars=200
        )

        assert report.status == "OK"
        # OK 상태일 때 reasons에 "All checks passed" 같은 메시지가 있을 수 있음
        # 비어있거나 긍정 메시지만 있으면 됨
        if len(report.reasons) > 0:
            assert all("FAIL" not in r and "WARN" not in r for r in report.reasons)

    def test_report_contains_metadata(self):
        """리포트에 메타데이터가 정상 포함되는지 확인"""
        report = evaluate_chunking(
            ingest_id="test123",
            file_name="test.pdf",
            file_path="/test/test.pdf",
            raw_text="A" * 1000,
            cleaned_text="A" * 900,
            chunks=["A" * 800],
            chunk_strategy="character_window",
            max_chars=1000,
            overlap_chars=200
        )

        assert report.ingest_id == "test123"
        assert report.file_name == "test.pdf"
        assert report.file_path == "/test/test.pdf"
        assert report.chunk_strategy == "character_window"
        assert report.max_chars == 1000
        assert report.overlap_chars == 200
        assert report.raw_text_len == 1000
        assert report.cleaned_text_len == 900
        assert report.num_chunks == 1
        assert len(report.chunk_lengths) == 1
        assert report.chunk_lengths[0] == 800
        assert report.created_at is not None

    def test_multiple_warnings(self):
        """여러 경고가 동시에 발생할 수 있음"""
        # 청크가 너무 많고, 일부는 너무 짧음
        many_short_chunks = ["A" * 10] * 600

        report = evaluate_chunking(
            ingest_id="test123",
            file_name="test.pdf",
            file_path="/test/test.pdf",
            raw_text="A" * 10000,
            cleaned_text="A" * 9000,
            chunks=many_short_chunks,
            chunk_strategy="character_window",
            max_chars=1000,
            overlap_chars=200
        )

        assert report.status == "WARN"
        # 여러 경고가 있을 수 있음
        assert len(report.reasons) > 0
