"""
refine_logs_and_push_to_backend.py

AI 서버에서 Elasticsearch에 적재된 ai_log를
정제하여 Backend 내부 API로 전달하는 배치 스크립트

Flow:
1. Elasticsearch에서 ai_log 조회
2. Backend DTO 스키마에 맞게 정제
3. Backend /internal/ai/logs/bulk API 호출
"""

import os
import asyncio
import logging
from typing import List, Dict, Any

import httpx
from dotenv import load_dotenv

# =========================================================
# ENV
# =========================================================
load_dotenv()

ES_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200").rstrip("/")
ES_INDEX = os.getenv("ELASTICSEARCH_INDEX", "ctrlf-logs-*")

BACKEND_INFRA_URL = os.getenv(
    "BACKEND_INFRA_URL",
    "http://localhost:9003"
).rstrip("/")
BACKEND_LOG_ENDPOINT = "/internal/ai/logs/bulk"

INTERNAL_TOKEN = os.getenv("BACKEND_INTERNAL_TOKEN")
if not INTERNAL_TOKEN:
    raise RuntimeError("BACKEND_INTERNAL_TOKEN is not set")

FETCH_DAYS = os.getenv("LOG_FETCH_RANGE", "7d")
FETCH_LIMIT = int(os.getenv("LOG_FETCH_LIMIT", "500"))

# =========================================================
# Logging
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger("log-refiner")

# =========================================================
# Step 1. Fetch from Elasticsearch
# =========================================================

async def fetch_ai_logs() -> List[Dict[str, Any]]:
    query = {
        "size": FETCH_LIMIT,
        "sort": [{"@timestamp": {"order": "desc"}}],
        "query": {
            "bool": {
                "filter": [
                    {"term": {"log_type": "ai_log"}},
                    {"range": {"@timestamp": {"gte": f"now-{FETCH_DAYS}"}}},
                ]
            }
        }
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{ES_URL}/{ES_INDEX}/_search",
            json=query,
        )
        resp.raise_for_status()

    hits = resp.json().get("hits", {}).get("hits", [])
    logger.info(f"Fetched {len(hits)} ai_log records from Elasticsearch")
    return hits


# =========================================================
# Step 2. Refine (Backend DTO 기준)
# =========================================================

def refine_log(hit: Dict[str, Any]) -> Dict[str, Any]:
    src = hit.get("_source", {})

    created_at = src.get("@timestamp")
    user_id = src.get("user_id")

    if not created_at or not user_id:
        raise ValueError("missing required fields (@timestamp, user_id)")

    return {
        # ===== 필수 =====
        "createdAt": created_at,      # ISO-8601 → Instant
        "userId": user_id,

        # ===== 선택 =====
        "userRole": src.get("user_role"),
        "department": src.get("department"),
        "domain": src.get("domain"),
        "route": src.get("route"),
        "modelName": src.get("model_name"),

        "hasPiiInput": src.get("has_pii_input", False),
        "hasPiiOutput": src.get("has_pii_output", False),

        "ragUsed": src.get("rag_used", False),
        "ragSourceCount": src.get("rag_source_count", 0),

        "latencyMsTotal": src.get("latency_ms"),
        "errorCode": src.get("error_code"),

        # ===== Trace =====
        "traceId": src.get("trace_id"),
        "conversationId": src.get("session_id"),
        "turnId": src.get("turn_index"),
    }


def refine_logs(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    refined: List[Dict[str, Any]] = []

    for h in hits:
        try:
            refined.append(refine_log(h))
        except Exception as e:
            logger.warning(f"Skip invalid log: {e}")

    return refined


# =========================================================
# Step 3. Push to Backend
# =========================================================

async def push_to_backend(logs: List[Dict[str, Any]]) -> None:
    if not logs:
        logger.info("No logs to push")
        return

    payload = {
        "logs": logs
    }

    headers = {
        "Content-Type": "application/json",
        "X-Internal-Token": INTERNAL_TOKEN,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{BACKEND_INFRA_URL}{BACKEND_LOG_ENDPOINT}",
            json=payload,
            headers=headers,
        )

        if resp.status_code != 200:
            raise RuntimeError(
                f"Backend push failed: {resp.status_code} {resp.text}"
            )

    logger.info(f"Pushed {len(logs)} logs to Backend")


# =========================================================
# Main
# =========================================================

async def main():
    logger.info("=== AI LOG REFINE & PUSH START ===")

    hits = await fetch_ai_logs()
    refined_logs = refine_logs(hits)

    logger.info(f"Refined logs count: {len(refined_logs)}")

    await push_to_backend(refined_logs)

    logger.info("=== DONE ===")


if __name__ == "__main__":
    asyncio.run(main())
