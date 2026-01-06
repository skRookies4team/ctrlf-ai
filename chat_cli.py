"""
채팅 CLI 테스트 도구

사용법: python chat_cli.py
"""

import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx
import json

API_URL = "http://localhost:8000/ai/chat/messages"


def chat(question: str) -> str:
    payload = {
        "session_id": "cli-test",
        "user_id": "tester",
        "user_role": "EMPLOYEE",
        "domain": "POLICY",
        "messages": [
            {"role": "user", "content": question}
        ],
    }

    try:
        resp = httpx.post(API_URL, json=payload, timeout=60)

        # ✅ 1. HTTP 상태 코드 먼저 확인
        if resp.status_code != 200:
            return (
                f"\n❌ HTTP 오류\n"
                f"STATUS: {resp.status_code}\n"
                f"RAW RESPONSE:\n{resp.text or '<EMPTY BODY>'}\n"
            )

        # ✅ 2. JSON 파싱 안전 처리
        try:
            data = resp.json()
        except json.JSONDecodeError:
            return (
                f"\n❌ JSON 파싱 실패\n"
                f"RAW RESPONSE:\n{resp.text or '<EMPTY BODY>'}\n"
            )

        # ✅ 3. 정상 응답 처리
        answer = data.get("answer", "응답 없음")
        meta = data.get("meta", {}) or {}
        sources = data.get("sources", []) or []

        result = f"\n{answer}\n"

        if sources:
            result += f"\n[참고: {len(sources)}개 문서]\n"

        result += f"({meta.get('route', '?')} | {meta.get('latency_ms', '?')}ms)"

        return result

    except httpx.RequestError as e:
        return f"\n❌ 요청 실패 (네트워크/서버 연결 문제)\n{e}\n"

    except Exception as e:
        return f"\n❌ 알 수 없는 오류\n{type(e).__name__}: {e}\n"


if __name__ == "__main__":
    print("=" * 50)
    print("CTRL+F AI 채팅 테스트 (종료: q 또는 Ctrl+C)")
    print("=" * 50)

    while True:
        try:
            q = input("\n질문> ").strip()
            if not q:
                continue
            if q.lower() in ("q", "quit", "exit"):
                print("종료합니다.")
                break

            print("응답 대기중...")
            print(chat(q))

        except KeyboardInterrupt:
            print("\n종료합니다.")
            break
