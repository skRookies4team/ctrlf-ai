"""
ES 로그 적재 테스트 스크립트

사용법:
    # ES만 테스트
    python scripts/test_es_log.py --es-only

    # 채팅 API + ES 로그 확인
    python scripts/test_es_log.py --full

    # ES URL 지정
    python scripts/test_es_log.py --es-url http://localhost:9200
"""

import argparse
import json
import sys
from datetime import datetime

try:
    import httpx
except ImportError:
    print("httpx 설치 필요: pip install httpx")
    sys.exit(1)


def check_es_health(es_url: str) -> bool:
    """ES 상태 확인"""
    print("\n" + "=" * 60)
    print("1. Elasticsearch 상태 확인")
    print("=" * 60)

    try:
        resp = httpx.get(f"{es_url}", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            print(f"✅ ES 연결 성공")
            print(f"   - Cluster: {data.get('cluster_name')}")
            print(f"   - Version: {data.get('version', {}).get('number')}")
            return True
        else:
            print(f"❌ ES 연결 실패: {resp.status_code}")
            return False
    except httpx.ConnectError:
        print(f"❌ ES 연결 실패: {es_url}에 연결할 수 없습니다")
        print("   Docker로 ES 실행: docker compose up elasticsearch -d")
        return False
    except Exception as e:
        print(f"❌ ES 확인 실패: {e}")
        return False


def list_indices(es_url: str) -> list:
    """인덱스 목록 조회"""
    print("\n" + "=" * 60)
    print("2. 인덱스 목록")
    print("=" * 60)

    try:
        resp = httpx.get(f"{es_url}/_cat/indices?format=json", timeout=5)
        if resp.status_code == 200:
            indices = resp.json()
            ctrlf_indices = [i for i in indices if i.get("index", "").startswith("ctrlf-")]

            if ctrlf_indices:
                print(f"✅ ctrlf 인덱스 {len(ctrlf_indices)}개 발견:")
                for idx in ctrlf_indices:
                    print(f"   - {idx['index']} (docs: {idx.get('docs.count', 0)})")
                return ctrlf_indices
            else:
                print("⚠️ ctrlf 인덱스 없음 (아직 로그가 적재되지 않음)")
                return []
        else:
            print(f"❌ 인덱스 조회 실패: {resp.status_code}")
            return []
    except Exception as e:
        print(f"❌ 인덱스 조회 실패: {e}")
        return []


def query_logs(es_url: str, index_pattern: str, size: int = 5) -> list:
    """로그 조회"""
    print(f"\n" + "=" * 60)
    print(f"3. 로그 조회: {index_pattern}")
    print("=" * 60)

    query = {
        "query": {"match_all": {}},
        "size": size,
        "sort": [{"@timestamp": "desc"}]
    }

    try:
        resp = httpx.post(
            f"{es_url}/{index_pattern}/_search",
            json=query,
            headers={"Content-Type": "application/json"},
            timeout=10
        )

        if resp.status_code == 200:
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            total = data.get("hits", {}).get("total", {}).get("value", 0)

            print(f"✅ 총 {total}개 로그, 최근 {len(hits)}개 조회:")

            for i, hit in enumerate(hits, 1):
                src = hit.get("_source", {})
                print(f"\n   [{i}] {src.get('@timestamp', 'N/A')}")
                print(f"       log_type: {src.get('log_type')}")
                print(f"       domain: {src.get('domain')}")
                print(f"       intent: {src.get('intent')}")
                print(f"       question: {(src.get('question_masked') or '')[:50]}...")

            return hits
        elif resp.status_code == 404:
            print(f"⚠️ 인덱스 없음: {index_pattern}")
            return []
        else:
            print(f"❌ 조회 실패: {resp.status_code}")
            return []
    except Exception as e:
        print(f"❌ 조회 실패: {e}")
        return []


def send_chat_request(server_url: str) -> dict:
    """채팅 API 호출"""
    print("\n" + "=" * 60)
    print("4. 채팅 API 호출 (로그 적재 트리거)")
    print("=" * 60)

    payload = {
        "session_id": f"es-test-{datetime.now().strftime('%H%M%S')}",
        "user_id": "es-test-user",
        "message": "연차 규정 알려줘",
        "channel": "WEB",
        "user_role": "EMPLOYEE",
        "department": "개발팀"
    }

    print(f"요청: POST {server_url}/ai/chat")
    print(f"Body: {json.dumps(payload, ensure_ascii=False, indent=2)}")

    try:
        resp = httpx.post(
            f"{server_url}/ai/chat",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=60
        )

        if resp.status_code == 200:
            data = resp.json()
            print(f"\n✅ 채팅 응답 성공:")
            print(f"   - route: {data.get('meta', {}).get('route')}")
            print(f"   - intent: {data.get('meta', {}).get('intent')}")
            print(f"   - answer: {data.get('answer', '')[:100]}...")
            return data
        else:
            print(f"❌ 채팅 실패: {resp.status_code}")
            print(f"   {resp.text}")
            return {}
    except httpx.ConnectError:
        print(f"❌ 서버 연결 실패: {server_url}")
        print("   서버 실행: python -m uvicorn app.main:app --port 8000")
        return {}
    except Exception as e:
        print(f"❌ 채팅 실패: {e}")
        return {}


def main():
    parser = argparse.ArgumentParser(description="ES 로그 적재 테스트")
    parser.add_argument("--es-url", default="http://localhost:9200", help="ES URL")
    parser.add_argument("--server-url", default="http://localhost:8000", help="API 서버 URL")
    parser.add_argument("--es-only", action="store_true", help="ES 상태만 확인")
    parser.add_argument("--full", action="store_true", help="채팅 API + ES 로그 확인")
    args = parser.parse_args()

    print("\n" + "#" * 60)
    print("# ES 로그 적재 테스트")
    print(f"# ES URL: {args.es_url}")
    print(f"# Server URL: {args.server_url}")
    print("#" * 60)

    # 1. ES 상태 확인
    if not check_es_health(args.es_url):
        print("\n⚠️ ES가 실행되지 않았습니다. Docker로 시작하세요:")
        print("   docker compose up elasticsearch kibana -d")
        return 1

    # 2. 인덱스 목록
    list_indices(args.es_url)

    # ES만 확인하는 경우
    if args.es_only:
        # 기존 로그 조회
        query_logs(args.es_url, "ctrlf-logs-*")
        query_logs(args.es_url, "ctrlf-faq-log-*")
        return 0

    # 전체 테스트 (채팅 + ES)
    if args.full:
        # 3. 채팅 API 호출
        send_chat_request(args.server_url)

        # 4. 2초 대기 후 로그 확인
        print("\n⏳ 2초 대기 후 로그 확인...")
        import time
        time.sleep(2)

        # 5. 로그 조회
        query_logs(args.es_url, "ctrlf-logs-*")
        query_logs(args.es_url, "ctrlf-faq-log-*")

    print("\n" + "=" * 60)
    print("테스트 완료!")
    print("=" * 60)
    print("\n📊 Kibana에서 시각적으로 확인: http://localhost:5601")

    return 0


if __name__ == "__main__":
    sys.exit(main())
