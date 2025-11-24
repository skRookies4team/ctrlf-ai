"""
청킹 품질 평가기 - ChunkingReport 생성
"""
import logging
from typing import List
from datetime import datetime, timezone

from core.models import ChunkingReport

logger = logging.getLogger(__name__)


def evaluate_chunking(
    ingest_id: str,
    file_name: str,
    file_path: str,
    raw_text: str,
    cleaned_text: str,
    chunks: List[str],
    chunk_strategy: str = "character_window",
    max_chars: int = 1000,
    overlap_chars: int = 200,
    min_chunks: int = 1,
    max_chunks: int = 300,
    min_chunk_len: int = 200,
    max_chunk_len: int = 1500,
) -> ChunkingReport:
    """
    청킹 품질 평가 및 ChunkingReport 생성

    Args:
        ingest_id: Ingest ID
        file_name: 파일명
        file_path: 파일 경로
        raw_text: 원본 텍스트
        cleaned_text: 클리닝된 텍스트
        chunks: 청크 리스트
        chunk_strategy: 청킹 전략
        max_chars: 청크 최대 문자 수
        overlap_chars: 청크 간 겹침
        min_chunks: 최소 청크 개수
        max_chunks: 최대 청크 개수
        min_chunk_len: 최소 청크 길이
        max_chunk_len: 최대 청크 길이

    Returns:
        ChunkingReport: 평가 리포트
    """
    logger.info(f"Evaluating chunking for {file_name}")

    # 기본 상태
    status = "OK"
    reasons = []

    # 텍스트 길이
    raw_text_len = len(raw_text)
    cleaned_text_len = len(cleaned_text)
    num_chunks = len(chunks)
    chunk_lengths = [len(chunk) for chunk in chunks]

    # 평가 규칙 1: cleaned_text_len < 200 → FAIL
    if cleaned_text_len < 200:
        status = "FAIL"
        reasons.append("TEXT_TOO_SHORT")

    # 평가 규칙 2: num_chunks == 0 → FAIL
    if num_chunks == 0:
        status = "FAIL"
        reasons.append("NO_CHUNKS")

    # 평가 규칙 3: 0 < num_chunks < min_chunks → WARN (FAIL이 아닌 경우만)
    if 0 < num_chunks < min_chunks:
        if status != "FAIL":
            status = "WARN"
        reasons.append("TOO_FEW_CHUNKS")

    # 평가 규칙 4: num_chunks > max_chunks → WARN (FAIL이 아닌 경우만)
    if num_chunks > max_chunks:
        if status != "FAIL":
            status = "WARN"
        reasons.append("TOO_MANY_CHUNKS")

    # 평가 규칙 5: max(chunk_lengths) > max_chunk_len → WARN
    if chunk_lengths and max(chunk_lengths) > max_chunk_len:
        if status != "FAIL":
            status = "WARN"
        reasons.append("CHUNK_TOO_LONG")

    # 평가 규칙 6: min(chunk_lengths) < min_chunk_len → WARN
    if chunk_lengths and min(chunk_lengths) < min_chunk_len:
        if status != "FAIL":
            status = "WARN"
        reasons.append("CHUNK_TOO_SHORT")

    # 모든 검사 통과 시
    if not reasons:
        reasons.append("All checks passed")

    # created_at: UTC 기준 ISO 형식
    created_at = datetime.now(timezone.utc).isoformat()

    report = ChunkingReport(
        ingest_id=ingest_id,
        file_name=file_name,
        file_path=file_path,
        raw_text_len=raw_text_len,
        cleaned_text_len=cleaned_text_len,
        num_chunks=num_chunks,
        chunk_lengths=chunk_lengths,
        status=status,
        reasons=reasons,
        chunk_strategy=chunk_strategy,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
        created_at=created_at
    )

    logger.info(f"Evaluation completed. Status: {status}, Reasons: {reasons}")
    return report
