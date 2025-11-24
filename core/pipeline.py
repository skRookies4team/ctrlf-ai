"""
Ingestion Pipeline - 전체 파이프라인 오케스트레이션 (모니터링 포함)
Parser → Cleaner → Structure → Chunker → Evaluator → Embedder → VectorStore
"""
import logging
import os
from uuid import uuid4
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime, timezone

from core.models import ChunkingReport
from core.parser import extract_text_from_file, get_page_count
from core.cleaner import clean_text
from core.structure import normalize_structure, apply_structure, split_paragraphs, detect_headings
from core.chunker import chunk_text, chunk_by_paragraphs, chunk_by_headings
from core.evaluator import evaluate_chunking
from core.embedder import embed_texts
from core.vector_store import get_vector_store
from core.monitoring import (
    IngestMonitoring,
    FileMetrics,
    ParseMetrics,
    CleaningMetrics,
    StructureMetrics,
    ChunkingMetrics,
    EmbeddingMetrics,
    EvaluationMetrics,
)

logger = logging.getLogger(__name__)


def _calculate_stats(values: List[int]) -> Tuple[Optional[int], Optional[int], Optional[float], Optional[float]]:
    """리스트의 통계값 계산"""
    if not values:
        return None, None, None, None

    min_val = min(values)
    max_val = max(values)
    avg_val = sum(values) / len(values)

    # 표준편차 계산
    if len(values) > 1:
        variance = sum((x - avg_val) ** 2 for x in values) / len(values)
        std_val = variance ** 0.5
    else:
        std_val = 0.0

    return min_val, max_val, avg_val, std_val


def process_file(
    file_path: str,
    file_name: str,
    chunk_strategy: str = "character_window",
    max_chars: int = 1000,
    overlap_chars: int = 200,
    use_ocr_fallback: bool = True
) -> Tuple[ChunkingReport, Optional[IngestMonitoring]]:
    """
    파일 처리 전체 파이프라인 실행 (모니터링 포함)

    지원 형식: PDF, HWP, DOCX, PPTX

    Returns:
        Tuple[ChunkingReport, Optional[IngestMonitoring]]: (리포트, 모니터링 데이터)
    """
    from core.ocr import run_ocr

    # ingest_id 생성
    ingest_id = uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()

    logger.info(f"Starting pipeline for {file_name} (ID: {ingest_id})")
    logger.info(f"Strategy: {chunk_strategy}, max_chars: {max_chars}, OCR fallback: {use_ocr_fallback}")

    # 모니터링 변수 초기화
    raw_text = ""
    cleaned = ""
    chunks = []
    used_ocr = False
    ocr_text_len = 0
    parse_error = None
    parse_success = False
    num_pages = None
    sections = []
    paragraph_count = 0
    heading_count = 0
    section_count = 0
    vectors = []
    vector_store_insert_success = False
    vector_store_error = None

    try:
        # === 1. 파일 정보 수집 ===
        try:
            size_bytes = os.path.getsize(file_path)
            extension = os.path.splitext(file_name)[1].lower()
        except Exception as e:
            logger.error(f"Error getting file info: {e}")
            size_bytes = 0
            extension = ".pdf"

        # === 2. 파일에서 텍스트 추출 (확장자 기반 라우팅) ===
        logger.info(f"Step 1: Extracting text from {extension}")
        try:
            raw_text = extract_text_from_file(file_path)
            parse_success = True if raw_text and len(raw_text.strip()) > 0 else False

            # 페이지 수 추출 시도 (PDF만 해당)
            if extension == ".pdf":
                num_pages = get_page_count(file_path)
            else:
                num_pages = None

        except Exception as e:
            logger.error(f"Error extracting text: {e}")
            parse_error = str(e)
            parse_success = False

        # 텍스트 추출 실패 시 OCR fallback
        if (not raw_text or len(raw_text.strip()) == 0) and use_ocr_fallback:
            logger.warning("No text extracted, trying OCR fallback")
            try:
                ocr_result = run_ocr(file_path)
                if ocr_result:
                    used_ocr = True
                    ocr_text_len = len(ocr_result)
                    raw_text = ocr_result
                    parse_success = True
                    logger.info(f"OCR succeeded: {len(raw_text)} characters extracted")
            except Exception as e:
                logger.error(f"OCR failed: {e}")

        # 일반 추출 실패 (OCR 미사용 또는 실패)
        if not raw_text or len(raw_text.strip()) == 0:
            logger.warning("No text extracted from PDF")
            # 빈 결과로 평가 진행
            report = evaluate_chunking(
                ingest_id=ingest_id,
                file_name=file_name,
                file_path=file_path,
                raw_text="",
                cleaned_text="",
                chunks=[],
                chunk_strategy=chunk_strategy,
                max_chars=max_chars,
                overlap_chars=overlap_chars
            )

            # 모니터링 객체 생성
            monitoring = _create_monitoring(
                ingest_id, created_at, file_name, file_path, extension, size_bytes, num_pages,
                "", 0, parse_success, parse_error, used_ocr, ocr_text_len, num_pages,
                "", 0, chunk_strategy, max_chars, overlap_chars, [], sections,
                paragraph_count, heading_count, section_count, vectors,
                vector_store_insert_success, vector_store_error, report
            )

            return report, monitoring

        # === 3. 텍스트 클리닝 ===
        logger.info("Step 2: Cleaning text")
        cleaned = clean_text(raw_text)

        # === 4. 청킹 전략에 따라 처리 ===
        if chunk_strategy == "character_window":
            logger.info("Step 3: Normalizing structure (passthrough)")
            normalized = normalize_structure(cleaned)
            logger.info("Step 4: Chunking text (character_window)")
            chunks = chunk_text(normalized, max_chars=max_chars, overlap_chars=overlap_chars)

        elif chunk_strategy == "paragraph_based":
            logger.info("Step 3: Applying structure analysis")
            sections = apply_structure(cleaned)
            section_count = len(sections)

            # 문단 수 계산
            for sec in sections:
                paras = split_paragraphs(sec.get("content", ""))
                paragraph_count += len(paras)

            logger.info("Step 4: Chunking by paragraphs")
            chunks = chunk_by_paragraphs(sections, max_chars=max_chars)

        elif chunk_strategy == "heading_based":
            logger.info("Step 3: Applying structure analysis")
            sections = apply_structure(cleaned)
            section_count = len(sections)

            # 제목 수 계산
            headings = detect_headings(cleaned)
            heading_count = len(headings)

            logger.info("Step 4: Chunking by headings")
            chunks = chunk_by_headings(sections, max_chars=max_chars)

        else:
            logger.warning(f"Unknown chunk_strategy '{chunk_strategy}', using character_window")
            normalized = normalize_structure(cleaned)
            chunks = chunk_text(normalized, max_chars=max_chars, overlap_chars=overlap_chars)
            chunk_strategy = "character_window"

        # === 5. 평가 ===
        logger.info("Step 5: Evaluating chunking")
        report = evaluate_chunking(
            ingest_id=ingest_id,
            file_name=file_name,
            file_path=file_path,
            raw_text=raw_text,
            cleaned_text=cleaned,
            chunks=chunks,
            chunk_strategy=chunk_strategy,
            max_chars=max_chars,
            overlap_chars=overlap_chars
        )

        # === 6. 임베딩 + 벡터DB 저장 (status가 OK 또는 WARN일 때만) ===
        if report.status in ["OK", "WARN"]:
            try:
                logger.info("Step 6: Embedding chunks")
                vectors = embed_texts(chunks)
                logger.info(f"Successfully embedded {len(vectors)} chunks")

                # 7. FAISS에 벡터 추가
                logger.info("Step 7: Adding vectors to FAISS")

                # 메타데이터 구성
                metadatas = []
                for i, chunk in enumerate(chunks):
                    metadata = {
                        "ingest_id": ingest_id,
                        "file_name": file_name,
                        "chunk_index": i,
                        "text": chunk,
                        "strategy": chunk_strategy
                    }
                    metadatas.append(metadata)

                # VectorStore에 추가
                vector_store = get_vector_store(dim=384)
                vector_store.add_vectors(vectors, metadatas)
                vector_store_insert_success = True
                logger.info(f"Successfully added {len(vectors)} vectors to FAISS")

            except Exception as e:
                logger.error(f"Error during embedding/vector store: {e}", exc_info=True)
                vector_store_insert_success = False
                vector_store_error = str(e)
        else:
            logger.info(f"Skipping embedding/vector store due to status: {report.status}")

        # === 모니터링 객체 생성 ===
        monitoring = _create_monitoring(
            ingest_id, created_at, file_name, file_path, extension, size_bytes, num_pages,
            raw_text, len(raw_text.split('\n')), parse_success, parse_error, used_ocr, ocr_text_len, num_pages,
            cleaned, len(cleaned.split('\n')), chunk_strategy, max_chars, overlap_chars, chunks, sections,
            paragraph_count, heading_count, section_count, vectors,
            vector_store_insert_success, vector_store_error, report
        )

        logger.info(f"Pipeline completed for {file_name}. Status: {report.status}")
        return report, monitoring

    except Exception as e:
        logger.error(f"Unexpected pipeline error: {e}", exc_info=True)
        report = evaluate_chunking(
            ingest_id=ingest_id,
            file_name=file_name,
            file_path=file_path,
            raw_text="",
            cleaned_text="",
            chunks=[],
            chunk_strategy=chunk_strategy,
            max_chars=max_chars,
            overlap_chars=overlap_chars
        )

        monitoring = _create_monitoring(
            ingest_id, created_at, file_name, file_path, extension if 'extension' in locals() else ".pdf",
            size_bytes if 'size_bytes' in locals() else 0, num_pages,
            "", 0, False, str(e), used_ocr, ocr_text_len, num_pages,
            "", 0, chunk_strategy, max_chars, overlap_chars, [], sections,
            paragraph_count, heading_count, section_count, vectors,
            vector_store_insert_success, vector_store_error, report
        )

        return report, monitoring


def _create_monitoring(
    ingest_id: str, created_at: str, file_name: str, file_path: str, extension: str,
    size_bytes: int, num_pages: Optional[int],
    raw_text: str, raw_line_count: int, parse_success: bool, parse_error: Optional[str],
    used_ocr: bool, ocr_text_len: int, page_count: Optional[int],
    cleaned: str, cleaned_line_count: int,
    chunk_strategy: str, max_chars: int, overlap_chars: int, chunks: List[str],
    sections: List[Dict], paragraph_count: int, heading_count: int, section_count: int,
    vectors: List, vector_store_insert_success: bool, vector_store_error: Optional[str],
    report: ChunkingReport
) -> IngestMonitoring:
    """모니터링 객체 생성 헬퍼"""

    # FileMetrics
    file_metrics = FileMetrics(
        file_name=file_name,
        file_path=file_path,
        original_extension=extension,
        converted_extension=extension,
        mime_type="application/pdf",
        size_bytes=size_bytes,
        num_pages=num_pages,
        conversion_success=True,
        conversion_error=None
    )

    # ParseMetrics
    page_text_coverage = None
    if page_count and page_count > 0:
        page_text_coverage = len(raw_text) / page_count

    parse_metrics = ParseMetrics(
        parser_name="pypdf",
        raw_text_len=len(raw_text),
        raw_line_count=raw_line_count,
        parse_success=parse_success,
        parse_error=parse_error,
        used_ocr=used_ocr,
        ocr_text_len=ocr_text_len,
        page_text_coverage_ratio=page_text_coverage
    )

    # CleaningMetrics
    clean_ratio = None
    if len(raw_text) > 0:
        clean_ratio = len(cleaned) / len(raw_text)

    # 의심스러운 문자 비율 (간단히 공백 비율로 계산)
    suspicious_ratio = None
    if len(cleaned) > 0:
        space_count = cleaned.count(' ')
        suspicious_ratio = space_count / len(cleaned)

    cleaning_metrics = CleaningMetrics(
        cleaned_text_len=len(cleaned),
        cleaned_line_count=cleaned_line_count,
        clean_ratio=clean_ratio,
        removed_header_footer=False,
        suspicious_char_ratio=suspicious_ratio
    )

    # StructureMetrics
    structure_ok = paragraph_count > 5 if paragraph_count > 0 else True
    structure_warnings = []
    if heading_count == 0 and chunk_strategy in ["paragraph_based", "heading_based"]:
        structure_warnings.append("NO_HEADINGS_DETECTED")
    if paragraph_count < 5 and chunk_strategy == "paragraph_based":
        structure_warnings.append("TOO_FEW_PARAGRAPHS")

    structure_metrics = StructureMetrics(
        paragraph_count=paragraph_count,
        heading_count=heading_count,
        section_count=section_count,
        structure_ok=structure_ok,
        structure_warnings=structure_warnings
    )

    # ChunkingMetrics
    chunk_lengths = [len(c) for c in chunks]
    chunk_min, chunk_max, chunk_avg, chunk_std = _calculate_stats(chunk_lengths)

    # evaluator의 경고를 가져옴
    chunk_warnings = []
    for reason in report.reasons:
        if "CHUNK" in reason or "TOO_MANY" in reason or "TOO_FEW" in reason:
            chunk_warnings.append(reason)

    chunking_metrics = ChunkingMetrics(
        chunk_strategy=chunk_strategy,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
        num_chunks=len(chunks),
        chunk_lengths=chunk_lengths,
        chunk_len_min=chunk_min,
        chunk_len_max=chunk_max,
        chunk_len_avg=chunk_avg,
        chunk_len_std=chunk_std,
        chunk_warnings=chunk_warnings
    )

    # EmbeddingMetrics
    embedding_dim = len(vectors[0]) if vectors and len(vectors) > 0 else 384

    embedding_metrics = EmbeddingMetrics(
        embedding_model="blake2b-hash-embedding",
        embedding_dim=embedding_dim,
        embedding_count=len(vectors),
        vector_store_type="faiss",
        vector_store_collection=None,
        vector_store_insert_success=vector_store_insert_success,
        vector_store_error=vector_store_error
    )

    # EvaluationMetrics
    evaluation_metrics = EvaluationMetrics(
        status=report.status,
        reasons=report.reasons,
        notes=None
    )

    # IngestMonitoring
    monitoring = IngestMonitoring(
        ingest_id=ingest_id,
        created_at=created_at,
        file=file_metrics,
        parse=parse_metrics,
        cleaning=cleaning_metrics,
        structure=structure_metrics,
        chunking=chunking_metrics,
        embedding=embedding_metrics,
        evaluation=evaluation_metrics
    )

    return monitoring


def search_similar_chunks(query_text: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """
    유사한 청크 검색 (RAG Retrieval용)

    1. embed_texts([query_text]) → query_vector
    2. FAISS search
    3. vector_id + metadata 반환

    Args:
        query_text: 검색할 텍스트
        top_k: 반환할 결과 개수

    Returns:
        List[Dict[str, Any]]: 검색 결과
    """

    logger.info(f"Searching for similar chunks: '{query_text[:50]}...'")

    # 1. 쿼리 텍스트 임베딩
    query_vector = embed_texts([query_text])[0]

    # 2. FAISS 검색
    vector_store = get_vector_store(dim=384)
    results = vector_store.search(query_vector, top_k=top_k)

    logger.info(f"Found {len(results)} similar chunks")
    return results


# ========================================
# 하위 호환성 (Backward Compatibility)
# ========================================

def process_pdf_file(
    file_path: str,
    file_name: str,
    chunk_strategy: str = "character_window",
    max_chars: int = 1000,
    overlap_chars: int = 200,
    use_ocr_fallback: bool = True
) -> Tuple[ChunkingReport, Optional[IngestMonitoring]]:
    """
    하위 호환성 유지를 위한 alias 함수
    
    ⚠️ Deprecated: process_file()을 사용하세요
    """
    logger.warning("process_pdf_file() is deprecated. Use process_file() instead.")
    return process_file(file_path, file_name, chunk_strategy, max_chars, overlap_chars, use_ocr_fallback)

