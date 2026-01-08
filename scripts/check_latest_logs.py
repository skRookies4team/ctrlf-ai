"""
Elasticsearch에서 최신 로그를 확인하는 스크립트

백엔드 대시보드에서 최신 로그가 반영되지 않는 문제를 진단하기 위한 도구입니다.
"""

import os
import sys
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any

import httpx
from dotenv import load_dotenv

load_dotenv()

ES_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200").rstrip("/")
ES_INDEX_PATTERN = "ctrlf-logs-*"


def format_timestamp(ts: str) -> str:
    """타임스탬프를 읽기 쉬운 형식으로 변환"""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return ts


def query_latest_logs(limit: int = 20) -> List[Dict[str, Any]]:
    """Elasticsearch에서 최신 로그 조회"""
    query = {
        "size": limit,
        "sort": [{"@timestamp": {"order": "desc"}}],
        "query": {
            "bool": {
                "filter": [
                    {"term": {"log_type": "ai_log"}},
                ]
            }
        },
        "_source": [
            "@timestamp",
            "user_id",
            "session_id",
            "domain",
            "intent",
            "route",
            "model_name",
            "latency_ms",
            "question_masked",
            "user_role",
            "department",
        ],
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{ES_URL}/{ES_INDEX_PATTERN}/_search",
                json=query,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            total = data.get("hits", {}).get("total", {}).get("value", 0)
            return hits, total
    except Exception as e:
        print(f"[ERROR] 조회 실패: {e}")
        return [], 0


def check_indices() -> List[str]:
    """사용 가능한 인덱스 목록 확인"""
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{ES_URL}/_cat/indices/{ES_INDEX_PATTERN}?format=json")
            resp.raise_for_status()
            indices = resp.json()
            return [idx["index"] for idx in indices]
    except Exception as e:
        print(f"[WARN] 인덱스 목록 조회 실패: {e}")
        return []


def main():
    print("=" * 80)
    print("Elasticsearch 최신 로그 확인")
    print("=" * 80)
    print(f"ES URL: {ES_URL}")
    print(f"인덱스 패턴: {ES_INDEX_PATTERN}")
    print()

    # 1. 인덱스 확인
    print("[1] 인덱스 확인")
    print("-" * 80)
    indices = check_indices()
    if indices:
        print(f"[OK] 발견된 인덱스 ({len(indices)}개):")
        for idx in sorted(indices):
            print(f"   - {idx}")
    else:
        print("[WARN] 인덱스를 찾을 수 없습니다.")
    print()

    # 2. 최신 로그 조회
    print("[2] 최신 로그 조회 (최근 20개)")
    print("-" * 80)
    hits, total = query_latest_logs(limit=20)

    if total == 0:
        print("[ERROR] 로그가 없습니다.")
        return

    print(f"[OK] 총 {total}개 로그 중 최근 {len(hits)}개:")
    print()

    for i, hit in enumerate(hits, 1):
        src = hit.get("_source", {})
        timestamp = format_timestamp(src.get("@timestamp", ""))
        user_id = src.get("user_id", "N/A")
        session_id = src.get("session_id", "N/A")[:8] + "..."
        domain = src.get("domain", "N/A")
        route = src.get("route", "N/A")
        question = (src.get("question_masked") or "")[:50]

        print(f"[{i}] {timestamp}")
        print(f"    user_id: {user_id}")
        print(f"    session_id: {session_id}")
        print(f"    domain: {domain} | route: {route}")
        print(f"    question: {question}...")
        print()

    # 3. 오늘 날짜 로그 확인
    print("[3] 오늘 날짜 로그 확인")
    print("-" * 80)
    today = datetime.now().strftime("%Y.%m.%d")
    today_index = f"ctrlf-logs-{today}"
    
    if today_index in indices:
        print(f"[OK] 오늘 인덱스 존재: {today_index}")
        
        query_today = {
            "size": 10,
            "sort": [{"@timestamp": {"order": "desc"}}],
            "query": {"match_all": {}},
        }
        
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    f"{ES_URL}/{today_index}/_search",
                    json=query_today,
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()
                today_hits = data.get("hits", {}).get("hits", [])
                today_total = data.get("hits", {}).get("total", {}).get("value", 0)
                
                print(f"   오늘 로그: {today_total}개")
                if today_hits:
                    latest = today_hits[0].get("_source", {})
                    latest_ts = format_timestamp(latest.get("@timestamp", ""))
                    print(f"   최신 로그 시간: {latest_ts}")
        except Exception as e:
            print(f"   [WARN] 오늘 인덱스 조회 실패: {e}")
    else:
        print(f"[WARN] 오늘 인덱스 없음: {today_index}")
        print("   (아직 오늘 로그가 저장되지 않았을 수 있습니다)")

    print()
    print("=" * 80)
    print("진단 완료")
    print("=" * 80)
    print()
    print("[TIP] 백엔드에서 최신 로그가 보이지 않는다면:")
    print("   1. 백엔드의 Elasticsearch 쿼리가 올바른 인덱스 패턴을 사용하는지 확인")
    print("   2. 정렬이 @timestamp desc로 되어 있는지 확인")
    print("   3. 시간 범위 필터가 최신 데이터를 제외하지 않는지 확인")
    print("   4. 캐싱 문제가 있는지 확인")


if __name__ == "__main__":
    main()

