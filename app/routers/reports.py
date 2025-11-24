"""
Reports API 라우터
"""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from core.report_store import load_recent_reports, find_report_by_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ingest/reports", tags=["reports"])


@router.get("")
async def get_reports(
    limit: int = Query(50, ge=1, le=1000),
    status: Optional[str] = Query(None, pattern="^(OK|WARN|FAIL)$")
):
    """
    리포트 목록 조회

    Args:
        limit: 반환할 리포트 수 (기본: 50, 최대: 1000)
        status: 상태 필터 ("OK" | "WARN" | "FAIL", 선택적)

    Returns:
        dict: 리포트 목록
    """
    try:
        # load_recent_reports 호출 (Dict[str, Any] 리스트 반환)
        reports = load_recent_reports(limit=limit, status=status)

        # 응답 포맷
        response = {
            "total": len(reports),
            "reports": [
                {
                    "ingest_id": r.get("ingest_id"),
                    "file_name": r.get("file_name"),
                    "status": r.get("status"),
                    "num_chunks": r.get("num_chunks"),
                    "created_at": r.get("created_at"),
                    "monitoring": r.get("monitoring")  # monitoring 데이터 포함
                }
                for r in reports
            ]
        }

        return response

    except Exception as e:
        logger.error(f"Error fetching reports: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/{ingest_id}")
async def get_report(ingest_id: str):
    """
    특정 리포트 조회

    Args:
        ingest_id: Ingest ID

    Returns:
        dict: 리포트 상세 정보

    Raises:
        HTTPException: 리포트를 찾을 수 없는 경우 404
    """
    try:
        # find_report_by_id로 조회 (Dict[str, Any] 반환)
        report = find_report_by_id(ingest_id)

        # 없으면 404
        if not report:
            raise HTTPException(
                status_code=404,
                detail="Report not found"
            )

        # 응답 포맷
        response = {
            "ingest_id": report.get("ingest_id"),
            "file_name": report.get("file_name"),
            "file_path": report.get("file_path"),
            "raw_text_len": report.get("raw_text_len"),
            "cleaned_text_len": report.get("cleaned_text_len"),
            "num_chunks": report.get("num_chunks"),
            "chunk_lengths": report.get("chunk_lengths"),
            "status": report.get("status"),
            "reasons": report.get("reasons"),
            "chunk_strategy": report.get("chunk_strategy"),
            "max_chars": report.get("max_chars"),
            "overlap_chars": report.get("overlap_chars"),
            "created_at": report.get("created_at"),
            "monitoring": report.get("monitoring")  # monitoring 데이터 포함
        }

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching report {ingest_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )
