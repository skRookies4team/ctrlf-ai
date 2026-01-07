"""
백엔드가 사용해야 할 올바른 Elasticsearch 쿼리를 테스트하는 스크립트

백엔드에서 사용할 수 있는 정확한 쿼리 예시를 제공합니다.
"""

import os
import json
from datetime import datetime, timedelta

import httpx
from dotenv import load_dotenv

load_dotenv()

ES_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200").rstrip("/")
ES_INDEX_PATTERN = "ctrlf-logs-*"


def test_backend_query():
    """백엔드가 사용해야 할 올바른 쿼리 테스트"""
    
    print("=" * 80)
    print("백엔드용 Elasticsearch 쿼리 테스트")
    print("=" * 80)
    print()
    
    # 백엔드가 사용해야 할 올바른 쿼리
    query = {
        "size": 20,
        "sort": [{"@timestamp": {"order": "desc"}}],
        "query": {
            "bool": {
                "filter": [
                    {"term": {"log_type": "ai_log"}}
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
        ]
    }
    
    print("실행할 쿼리:")
    print(json.dumps(query, indent=2, ensure_ascii=False))
    print()
    
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
            
            print(f"[결과] 총 {total}개 로그, 반환된 {len(hits)}개")
            print()
            
            # 날짜별 분류
            dates = {}
            for hit in hits:
                src = hit.get("_source", {})
                ts = src.get("@timestamp", "")
                if ts:
                    date = ts[:10]  # YYYY-MM-DD
                    dates[date] = dates.get(date, 0) + 1
            
            print("[날짜별 분류]")
            for date in sorted(dates.keys(), reverse=True):
                print(f"  {date}: {dates[date]}개")
            print()
            
            # 최신 5개 표시
            print("[최신 5개 로그]")
            for i, hit in enumerate(hits[:5], 1):
                src = hit.get("_source", {})
                ts = src.get("@timestamp", "")
                user_id = src.get("user_id", "N/A")
                domain = src.get("domain", "N/A")
                question = (src.get("question_masked") or "")[:40]
                
                # 타임스탬프 포맷팅
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    formatted_ts = dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    formatted_ts = ts
                
                print(f"  [{i}] {formatted_ts} | {domain} | {user_id[:8]}... | {question}...")
            
            print()
            print("=" * 80)
            print("[결론]")
            print("=" * 80)
            
            if total > 6:
                print(f"✅ Elasticsearch에는 {total}개 이상의 로그가 있습니다.")
                print(f"   백엔드가 6개만 반환한다면 백엔드 쿼리에 문제가 있습니다.")
            else:
                print(f"⚠️ Elasticsearch에 {total}개의 로그만 있습니다.")
            
            # 1/7일 로그 확인
            today = datetime.now().strftime("%Y-%m-%d")
            today_count = dates.get(today, 0)
            
            if today_count > 0:
                print(f"✅ 오늘({today}) 로그: {today_count}개")
            else:
                print(f"⚠️ 오늘({today}) 로그가 없습니다.")
            
            print()
            print("[백엔드 확인 사항]")
            print("1. 인덱스 패턴이 'ctrlf-logs-*'인지 확인")
            print("2. 정렬이 '@timestamp desc'인지 확인")
            print("3. 날짜 필터가 최신 데이터를 제외하지 않는지 확인")
            print("4. 페이지네이션 size가 적절한지 확인 (6으로 고정되어 있지 않은지)")
            
    except Exception as e:
        print(f"[ERROR] 쿼리 실행 실패: {e}")


def test_wrong_query():
    """잘못된 쿼리 예시 (1/6일만 조회하는 경우)"""
    
    print()
    print("=" * 80)
    print("잘못된 쿼리 예시 테스트 (1/6일만 조회)")
    print("=" * 80)
    print()
    
    # 잘못된 쿼리: 특정 날짜 인덱스만 조회
    wrong_index = "ctrlf-logs-2026.01.06"
    
    query = {
        "size": 20,
        "sort": [{"@timestamp": {"order": "desc"}}],
        "query": {
            "bool": {
                "filter": [
                    {"term": {"log_type": "ai_log"}}
                ]
            }
        }
    }
    
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{ES_URL}/{wrong_index}/_search",
                json=query,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            
            hits = data.get("hits", {}).get("hits", [])
            total = data.get("hits", {}).get("total", {}).get("value", 0)
            
            print(f"[잘못된 쿼리 결과] 인덱스: {wrong_index}")
            print(f"  총 {total}개 로그 (1/6일만 조회됨)")
            print()
            print("⚠️ 이것이 백엔드의 문제일 수 있습니다!")
            print("   백엔드가 특정 날짜 인덱스만 조회하고 있을 가능성이 있습니다.")
            
    except Exception as e:
        print(f"[ERROR] 쿼리 실행 실패: {e}")


if __name__ == "__main__":
    test_backend_query()
    test_wrong_query()

