"""
채팅 CLI 테스트 도구

사용법: python chat_cli.py
"""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import httpx

API_URL = "http://localhost:8000/ai/chat/messages"

def chat(question: str) -> str:
    payload = {
        "session_id": "cli-test",
        "user_id": "tester",
        "user_role": "EMPLOYEE",
        "domain": "POLICY",
        "messages": [{"role": "user", "content": question}]
    }

    try:
        resp = httpx.post(API_URL, json=payload, timeout=60)
        data = resp.json()

        answer = data.get("answer", "응답 없음")
        meta = data.get("meta", {})
        sources = data.get("sources", [])

        result = f"\n{answer}\n"
        if sources:
            result += f"\n[참고: {len(sources)}개 문서]\n"
        result += f"({meta.get('route', '?')} | {meta.get('latency_ms', '?')}ms)"

        return result
    except Exception as e:
        return f"오류: {e}"

if __name__ == "__main__":
    print("=" * 50)
    print("CTRL+F AI 채팅 테스트 (종료: q 또는 Ctrl+C)")
    print("=" * 50)

    while True:
        try:
            q = input("\n질문> ").strip()
            if not q:
                continue
            if q.lower() in ('q', 'quit', 'exit'):
                print("종료합니다.")
                break

            print("응답 대기중...")
            print(chat(q))

        except KeyboardInterrupt:
            print("\n종료합니다.")
            break