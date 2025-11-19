"""
Ingest API 라우터
"""
import logging
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
import shutil

from core.pipeline import process_file
from core.report_store import save_report

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

# 파일 저장 디렉토리
FILES_DIR = Path("data/files")
FILES_DIR.mkdir(parents=True, exist_ok=True)


@router.get("/health")
async def health_check():
    """헬스체크 엔드포인트"""
    return {
        "status": "healthy",
        "version": "1.0.0",
        "pipeline_ready": True
    }


@router.post("/file")
async def ingest_file(
    file: UploadFile = File(...),
    chunk_strategy: str = "character_window",
    max_chars: int = 1000,
    overlap_chars: int = 200,
    use_ocr_fallback: bool = True
):
    """
    파일 업로드 및 Ingestion 처리

    Args:
        file: 업로드할 파일 (PDF, HWP, DOCX, PPTX)
        chunk_strategy: 청킹 전략 (character_window, paragraph_based, heading_based)
        max_chars: 청크 최대 문자 수
        overlap_chars: 청크 간 겹침 문자 수 (character_window only)
        use_ocr_fallback: 텍스트 추출 실패 시 OCR 사용 여부 (PDF only)

    Returns:
        dict: 리포트 요약 정보
    """
    try:
        # 파일 확장자 확인
        supported_extensions = ['.pdf', '.hwp', '.docx', '.pptx']
        if not file.filename:
            raise HTTPException(
                status_code=400,
                detail="File name is required"
            )

        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in supported_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file format. Supported formats: {', '.join(supported_extensions)}"
            )

        # 청킹 전략 검증
        valid_strategies = ["character_window", "paragraph_based", "heading_based"]
        if chunk_strategy not in valid_strategies:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid chunk_strategy. Must be one of: {valid_strategies}"
            )

        # UploadFile을 /data/files/ 아래 임시 파일로 저장
        file_path = FILES_DIR / file.filename
        logger.info(f"Saving uploaded file: {file.filename}")

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # process_file 호출 (모니터링 포함)
        logger.info(f"Processing file: {file.filename} with strategy: {chunk_strategy}")
        report, monitoring = process_file(
            str(file_path),
            file.filename,
            chunk_strategy=chunk_strategy,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
            use_ocr_fallback=use_ocr_fallback
        )

        # save_report 호출 (모니터링 포함)
        logger.info(f"Saving report for: {report.ingest_id}")
        save_report(report, monitoring=monitoring)

        # 응답: 리포트 요약 정보를 JSON으로 반환
        response = {
            "ingest_id": report.ingest_id,
            "file_name": report.file_name,
            "status": report.status,
            "reasons": report.reasons,
            "raw_text_len": report.raw_text_len,
            "cleaned_text_len": report.cleaned_text_len,
            "num_chunks": report.num_chunks,
            "chunk_strategy": report.chunk_strategy,
            "max_chars": report.max_chars,
            "overlap_chars": report.overlap_chars,
            "created_at": report.created_at
        }

        logger.info(f"Ingest completed for {file.filename}: {report.status}")
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing file {file.filename}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )
