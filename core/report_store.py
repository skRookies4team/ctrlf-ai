"""
리포트 저장/조회 - JSONL 기반 (모니터링 포함)
"""
import logging
import json
from pathlib import Path
from typing import List, Optional, Dict, Any

from core.models import ChunkingReport
from core.monitoring import IngestMonitoring

logger = logging.getLogger(__name__)

# JSONL 파일 경로
REPORTS_FILE_PATH = "data/reports/chunking_reports.jsonl"


def save_report(report: ChunkingReport, monitoring: Optional[IngestMonitoring] = None) -> None:
    """
    ChunkingReport를 JSONL 파일에 append 저장 (모니터링 포함)

    Args:
        report: ChunkingReport 객체
        monitoring: IngestMonitoring 객체 (선택사항)
    """
    try:
        # 디렉토리 생성
        report_file = Path(REPORTS_FILE_PATH)
        report_file.parent.mkdir(parents=True, exist_ok=True)

        # 리포트 dict 생성
        report_dict = report.to_dict()

        # 모니터링 데이터 추가
        if monitoring:
            report_dict["monitoring"] = monitoring.to_dict()

        # JSONL 형식으로 append
        with open(report_file, "a", encoding="utf-8") as f:
            json_line = json.dumps(report_dict, ensure_ascii=False)
            f.write(json_line + "\n")

        logger.info(f"Report saved: {report.ingest_id} (monitoring={'yes' if monitoring else 'no'})")

    except Exception as e:
        logger.error(f"Failed to save report: {e}", exc_info=True)
        raise


def load_all_reports() -> List[Dict[str, Any]]:
    """
    모든 리포트를 로드 (모니터링 포함)

    Returns:
        List[Dict[str, Any]]: 리포트 dict 리스트 (파일이 없으면 빈 리스트)
    """
    report_file = Path(REPORTS_FILE_PATH)

    if not report_file.exists():
        logger.info(f"Report file not found: {REPORTS_FILE_PATH}")
        return []

    reports = []

    try:
        with open(report_file, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                    # dict 그대로 반환 (monitoring 포함)
                    reports.append(data)
                except Exception as e:
                    logger.warning(f"Failed to parse line {line_num}: {e}")

        logger.info(f"Loaded {len(reports)} reports from {REPORTS_FILE_PATH}")
        return reports

    except Exception as e:
        logger.error(f"Failed to load reports: {e}", exc_info=True)
        return []


def find_report_by_id(ingest_id: str) -> Optional[Dict[str, Any]]:
    """
    ingest_id로 리포트 하나 검색 (모니터링 포함)

    Args:
        ingest_id: Ingest ID

    Returns:
        Optional[Dict[str, Any]]: 리포트 dict (없으면 None)
    """
    all_reports = load_all_reports()

    for report in all_reports:
        if report.get("ingest_id") == ingest_id:
            logger.info(f"Found report: {ingest_id}")
            return report

    logger.info(f"Report not found: {ingest_id}")
    return None


def load_recent_reports(limit: int = 50, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    최근 limit개 리포트를 역순으로 반환 (모니터링 포함)

    Args:
        limit: 반환할 리포트 수
        status: 상태 필터 (None이면 전체)

    Returns:
        List[Dict[str, Any]]: 리포트 dict 리스트
    """
    all_reports = load_all_reports()

    # 상태 필터링
    if status:
        all_reports = [r for r in all_reports if r.get("status") == status]

    # created_at 기준 역순 정렬 (최신순)
    all_reports.sort(key=lambda r: r.get("created_at", ""), reverse=True)

    # limit 적용
    result = all_reports[:limit]

    logger.info(f"Returning {len(result)} recent reports (status={status})")
    return result
