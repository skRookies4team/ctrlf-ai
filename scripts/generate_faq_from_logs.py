import os
import uuid
import asyncio
import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from collections import defaultdict, Counter
from datetime import datetime

import httpx
from dotenv import load_dotenv

# =========================================================
# ENV
# =========================================================
load_dotenv()

ES_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200").rstrip("/")
FAQ_LOG_INDEX = os.getenv("FAQ_LOG_INDEX", "ctrlf-faq-log-*")
FAQ_MASTER_INDEX = os.getenv("FAQ_MASTER_INDEX", "ctrlf-faq-master")

AI_BASE_URL = os.getenv("AI_BASE_URL", "http://localhost:8000").rstrip("/")

FETCH_DAYS = os.getenv("FAQ_LOG_FETCH_RANGE", "7d")
FETCH_LIMIT = int(os.getenv("FAQ_LOG_FETCH_LIMIT", "500"))

MIN_QUESTION_COUNT = int(os.getenv("FAQ_MIN_QUESTION_COUNT", "2"))
FAQ_DOMAIN_DEFAULT = os.getenv("FAQ_DOMAIN_DEFAULT", "EDU")
EMBED_THRESHOLD = float(os.getenv("FAQ_EMBED_THRESHOLD", "0.75"))

# =========================================================
# Logging
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger("faq-batch")

# =========================================================
# Data Model
# =========================================================
@dataclass
class FaqLogEntry:
    ts: str
    domain: str
    intent: Optional[str]
    question: str


# =========================================================
# Utils
# =========================================================
def normalize(text: str) -> str:
    return " ".join((text or "").strip().split())


# =========================================================
# ES: Fetch faq_log
# =========================================================
async def fetch_faq_logs() -> List[FaqLogEntry]:
    query = {
        "size": FETCH_LIMIT,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"log_type": "faq_log"}},
                    {"range": {"@timestamp": {"gte": f"now-{FETCH_DAYS}"}}},
                    {"exists": {"field": "question_masked"}},
                ]
            }
        },
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{ES_URL}/{FAQ_LOG_INDEX}/_search", json=query)
        resp.raise_for_status()

    hits = resp.json().get("hits", {}).get("hits", [])
    logs: List[FaqLogEntry] = []

    for h in hits:
        src = h["_source"]
        logs.append(
            FaqLogEntry(
                ts=src.get("@timestamp"),
                domain=src.get("domain") or FAQ_DOMAIN_DEFAULT,
                intent=src.get("intent"),
                question=normalize(src.get("question_masked")),
            )
        )

    logger.info(f"[ES] fetched faq_log={len(logs)}")
    return logs


# =========================================================
# 추천 TOP 질문
# =========================================================
def compute_top_questions(logs: List[FaqLogEntry], limit=10):
    counter = Counter([x.question for x in logs])
    logger.info("\n[RECOMMEND] TOP QUESTIONS")
    for q, c in counter.most_common(limit):
        logger.info(f"- ({c}) {q}")


# =========================================================
# Domain group
# =========================================================
def group_by_domain(logs: List[FaqLogEntry]):
    grouped = defaultdict(list)
    for l in logs:
        grouped[l.domain].append(l)
    return grouped


# =========================================================
# Semantic clustering (lazy import)
# =========================================================
def cluster_semantic(logs: List[FaqLogEntry]) -> List[Dict[str, Any]]:
    from sentence_transformers import SentenceTransformer
    import numpy as np

    model = SentenceTransformer(
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )

    texts = [l.question for l in logs]
    vectors = model.encode(texts, normalize_embeddings=True)

    clusters = []
    anchors = []

    for idx, log in enumerate(logs):
        v = vectors[idx]
        placed = False

        for i, a in enumerate(anchors):
            if float(np.dot(v, a)) >= EMBED_THRESHOLD:
                clusters[i].append(log)
                placed = True
                break

        if not placed:
            anchors.append(v)
            clusters.append([log])

    results = []
    for items in clusters:
        if len(items) < MIN_QUESTION_COUNT:
            continue

        counter = Counter([x.question for x in items])
        canonical = counter.most_common(1)[0][0]

        results.append(
            {
                "canonical": canonical,
                "items": items,
                "count": len(items),
            }
        )

    return results


# =========================================================
# FAQ MASTER 중복 조회 (404 안전)
# =========================================================
async def find_existing_faq(domain: str, question: str) -> Optional[str]:
    query = {
        "size": 1,
        "query": {
            "bool": {
                "filter": [{"term": {"domain": domain}}],
                "must": [
                    {
                        "match": {
                            "canonical_question": {
                                "query": question,
                                "fuzziness": "AUTO"
                            }
                        }
                    }
                ],
            }
        },
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{ES_URL}/{FAQ_MASTER_INDEX}/_search",
            json=query,
        )

        if resp.status_code == 404:
            logger.info("[FAQ_MASTER] index not found → treat as empty")
            return None

        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
        return hits[0]["_id"] if hits else None


# =========================================================
# FAQ 강화
# =========================================================
async def reinforce_faq(faq_id: str, count: int):
    script = {
        "script": {
            "source": """
                ctx._source.question_count += params.c;
                ctx._source.last_seen_at = params.now;
            """,
            "params": {
                "c": count,
                "now": datetime.utcnow().isoformat(),
            },
        }
    }

    async with httpx.AsyncClient() as client:
        await client.post(
            f"{ES_URL}/{FAQ_MASTER_INDEX}/_update/{faq_id}",
            json=script,
        )

    logger.info(f"[REINFORCE] faq_id={faq_id} +{count}")


# =========================================================
# FAQ 생성
# =========================================================
async def generate_faq(domain: str, canonical: str, samples: List[str]) -> bool:
    payload = {
        "domain": domain,
        "cluster_id": f"auto-{uuid.uuid4().hex[:8]}",
        "canonical_question": canonical,
        "sample_questions": samples[:5],
        "top_docs": [],
        "avg_intent_confidence": 0.8,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{AI_BASE_URL}/ai/faq/generate",
            json=payload,
        )

        if resp.status_code >= 400:
            logger.error(f"[FAQ] HTTP {resp.status_code} {resp.text[:300]}")
            return False

        if resp.json().get("status") == "SUCCESS":
            logger.info(f"[FAQ] CREATED | {domain} | {canonical}")
            return True

    return False


# =========================================================
# Main
# =========================================================
async def main():
    logger.info("=== FAQ AUTO GENERATION START ===")

    logs = await fetch_faq_logs()
    if not logs:
        logger.info("No logs found.")
        return

    compute_top_questions(logs)

    grouped = group_by_domain(logs)

    for domain, dlogs in grouped.items():
        logger.info(f"\n[DOMAIN] {domain} logs={len(dlogs)}")

        clusters = cluster_semantic(dlogs)
        if not clusters:
            logger.info("  no semantic clusters")
            continue

        for c in clusters:
            canonical = c["canonical"]
            items = c["items"]

            existing = await find_existing_faq(domain, canonical)
            if existing:
                await reinforce_faq(existing, len(items))
                continue

            await generate_faq(
                domain=domain,
                canonical=canonical,
                samples=[x.question for x in items],
            )

    logger.info("\n=== FAQ AUTO GENERATION END ===")


if __name__ == "__main__":
    asyncio.run(main())
