"""
generate_faq_from_logs.py

AI 로그를 기반으로 FAQ 초안을 자동 생성하는 배치 스크립트

Flow:
1. Backend 로그 API(/api/ai-logs)에서 AI 로그 조회
2. FAQ 후보 질문 필터링
3. 질문 클러스터링
4. AI 서버 /ai/faq/generate 호출
"""

import os
import uuid
import asyncio
import logging
from collections import defaultdict
from typing import List, Dict, Any

import httpx
from dotenv import load_dotenv

# =========================================================
# ENV 로드
# =========================================================

load_dotenv()

BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:8085")
AI_BASE_URL = os.getenv("AI_BASE_URL", "http://localhost:8000")
BACKEND_ACCESS_TOKEN = os.getenv("BACKEND_ACCESS_TOKEN")

MIN_QUESTION_COUNT = int(os.getenv("FAQ_MIN_QUESTION_COUNT", "3"))
LOG_FETCH_LIMIT = 500

if not BACKEND_ACCESS_TOKEN:
    raise RuntimeError("BACKEND_ACCESS_TOKEN is required (.env 확인)")

# =========================================================
# 로깅
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger("faq-batch")

# =========================================================
# 데이터 모델
# =========================================================

class LogEntry:
    """
    Backend /api/ai-logs 로그 래퍼
    """

    def __init__(self, raw: Dict[str, Any]):
        self.domain: str | None = raw.get("domain")
        self.intent: str | None = raw.get("intent")
        self.route: str | None = raw.get("route")

        # FAQ 후보는 반드시 마스킹된 질문만 사용
        self.question: str | None = raw.get("question_masked")

# =========================================================
# Step 1. 로그 조회
# =========================================================

async def fetch_ai_logs() -> List[LogEntry]:
    url = f"{BACKEND_BASE_URL}/api/ai-logs"

    headers = {
        "Authorization": f"Bearer {BACKEND_ACCESS_TOKEN}"
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            url,
            params={"limit": LOG_FETCH_LIMIT},
            headers=headers,
        )
        
        if resp.status_code == 401:
            logger.error(
                "인증 실패 (401 Unauthorized)\n"
                "  - .env 파일의 BACKEND_ACCESS_TOKEN이 올바른지 확인하세요\n"
                "  - 토큰이 만료되었는지 확인하세요"
            )
            raise RuntimeError(
                "Backend API 인증 실패: BACKEND_ACCESS_TOKEN을 확인하세요"
            )
        
        if resp.status_code == 500:
            error_detail = ""
            try:
                error_body = resp.json()
                error_detail = error_body.get("message") or error_body.get("error") or str(error_body)
            except:
                error_detail = resp.text[:500] if resp.text else "(응답 본문 없음)"
            
            logger.error(
                "백엔드 서버 오류 (500 Internal Server Error)\n"
                f"  - URL: {url}\n"
                f"  - 백엔드 서버에 문제가 발생했습니다\n"
                f"  - 백엔드 로그를 확인하세요\n"
                f"  - 응답: {error_detail}"
            )
            raise RuntimeError(
                f"백엔드 서버 오류: 백엔드 팀에 문의하세요. (상세: {error_detail[:100]})"
            )
        
        resp.raise_for_status()

    body = resp.json()

    # Spring 응답 구조:
    # {
    #   "status": "ok",
    #   "total_count": 123,
    #   "returned_count": 50,
    #   "logs": [...]
    # }
    raw_logs = body.get("logs", [])

    logger.info(f"Fetched {len(raw_logs)} logs from backend")
    return [LogEntry(log) for log in raw_logs]

# =========================================================
# Step 2. FAQ 후보 필터링
# =========================================================

def filter_faq_candidates(logs: List[LogEntry]) -> List[LogEntry]:
    """
    FAQ 후보 조건:
    - domain == POLICY
    - intent == POLICY_QA
    - question_masked 존재
    """
    candidates = [
        log for log in logs
        if log.domain == "POLICY"
        and log.intent == "POLICY_QA"
        and log.question
    ]

    logger.info(f"FAQ candidate logs: {len(candidates)}")
    return candidates

# =========================================================
# Step 3. 질문 클러스터링 (단순)
# =========================================================

def cluster_questions(logs: List[LogEntry]) -> Dict[str, List[str]]:
    clusters: Dict[str, List[str]] = defaultdict(list)

    for log in logs:
        key = log.question.strip()
        clusters[key].append(log.question)

    filtered = {
        canonical: samples
        for canonical, samples in clusters.items()
        if len(samples) >= MIN_QUESTION_COUNT
    }

    logger.info(
        f"FAQ clusters after threshold({MIN_QUESTION_COUNT}): {len(filtered)}"
    )
    return filtered

# =========================================================
# Step 4. FAQ 생성 요청
# =========================================================

async def generate_faq(
    canonical_question: str,
    sample_questions: List[str],
) -> bool:
    """
    FAQ를 생성합니다.
    
    Returns:
        bool: 성공 여부 (True: 성공, False: 실패)
    """
    payload = {
        "domain": "POLICY",
        "cluster_id": f"auto-log-{uuid.uuid4().hex[:8]}",
        "canonical_question": canonical_question,
        "sample_questions": sample_questions[:5],
        "top_docs": [],
        "avg_intent_confidence": 0.8,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{AI_BASE_URL}/ai/faq/generate",
                json=payload,
            )
            resp.raise_for_status()

        data = resp.json()
        status = data.get("status")
        draft = data.get("faq_draft") or {}
        error_message = data.get("error_message")

        if status == "SUCCESS" and draft:
            logger.info(
                "FAQ generated | "
                f"question='{canonical_question[:50]}...' | "
                f"confidence={draft.get('ai_confidence', 'N/A')} | "
                f"id={draft.get('faq_draft_id', 'N/A')}"
            )
            return True
        else:
            logger.warning(
                f"FAQ generation failed | "
                f"question='{canonical_question[:50]}...' | "
                f"error={error_message or 'Unknown error'}"
            )
            return False

    except httpx.HTTPStatusError as e:
        logger.error(
            f"FAQ generation API error | "
            f"question='{canonical_question[:50]}...' | "
            f"status={e.response.status_code} | "
            f"response={e.response.text[:200]}"
        )
        return False
    
    except Exception as e:
        logger.error(
            f"FAQ generation unexpected error | "
            f"question='{canonical_question[:50]}...' | "
            f"error={type(e).__name__}: {str(e)}"
        )
        return False

# =========================================================
# Main
# =========================================================

async def main():
    logger.info("=== FAQ AUTO GENERATION START ===")

    logs = await fetch_ai_logs()
    candidates = filter_faq_candidates(logs)
    clusters = cluster_questions(candidates)

    if not clusters:
        logger.info("No FAQ candidates found. Exit.")
        logger.info("Note: 백엔드 팀에 더미 데이터 생성을 요청하세요.")
        return

    # 통계 수집
    total = len(clusters)
    success_count = 0
    failed_count = 0

    logger.info(f"Processing {total} FAQ clusters...")

    for idx, (canonical, questions) in enumerate(clusters.items(), 1):
        logger.info(f"[{idx}/{total}] Processing: {canonical[:50]}...")
        
        success = await generate_faq(
            canonical_question=canonical,
            sample_questions=questions,
        )
        
        if success:
            success_count += 1
        else:
            failed_count += 1

    logger.info("=== FAQ AUTO GENERATION END ===")
    logger.info(f"Summary: Total={total}, Success={success_count}, Failed={failed_count}")

if __name__ == "__main__":
    asyncio.run(main())
