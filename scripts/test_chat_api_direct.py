#!/usr/bin/env python3
"""
AI 서버 Chat API 직접 호출 테스트

실제 API를 호출해서 개인화 흐름이 작동하는지 확인합니다.
"""

import asyncio
import httpx
import json


AI_SERVER_URL = "http://localhost:8000"  # AI 서버 URL (필요시 수정)


async def test_chat_api(query: str, user_id: str = "test-user-123"):
    """Chat API 직접 호출 테스트"""

    endpoint = f"{AI_SERVER_URL}/ai/chat/messages"

    payload = {
        "session_id": "test-session-001",
        "user_id": user_id,
        "user_role": "EMPLOYEE",
        "department": "개발팀",
        "channel": "WEB",
        "messages": [
            {"role": "user", "content": query}
        ]
    }

    print(f"\n{'='*60}")
    print(f"질문: {query}")
    print(f"{'='*60}")
    print(f"요청 URL: {endpoint}")
    print(f"페이로드: {json.dumps(payload, ensure_ascii=False, indent=2)}")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                endpoint,
                json=payload,
                headers={"Content-Type": "application/json"}
            )

            print(f"\n응답 상태: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                print(f"\n응답 데이터:")
                print(f"  answer: {data.get('answer', '')[:200]}...")

                meta = data.get('meta', {})
                print(f"\n메타 정보:")
                print(f"  route: {meta.get('route')}")
                print(f"  intent: {meta.get('intent')}")
                print(f"  domain: {meta.get('domain')}")
                print(f"  personalization_q: {meta.get('personalization_q')}")
                print(f"  user_role: {meta.get('user_role')}")

                # 개인화 여부 판단
                if meta.get('personalization_q'):
                    print(f"\n✅ 개인화 응답 (Q={meta.get('personalization_q')})")
                elif meta.get('route') == 'BACKEND_API':
                    print(f"\n⚠️ BACKEND_API 라우트지만 personalization_q 없음")
                else:
                    print(f"\n❌ 개인화 아님 (route={meta.get('route')})")
            else:
                print(f"에러 응답: {response.text}")

    except httpx.ConnectError as e:
        print(f"\n❌ 연결 실패: {e}")
        print(f"AI 서버가 {AI_SERVER_URL}에서 실행 중인지 확인하세요.")
    except Exception as e:
        print(f"\n❌ 에러: {e}")


async def main():
    print("=" * 60)
    print("AI 서버 Chat API 직접 호출 테스트")
    print("=" * 60)

    test_queries = [
        "내 연차 몇 개 남았어?",
        "내 성희롱 교육 퀴즈 점수 몇점이야?",
        "이번 주 해야할 교육 뭐야?",
        "복지포인트 잔액 알려줘",
    ]

    for query in test_queries:
        await test_chat_api(query)
        print("\n")

    print("=" * 60)
    print("테스트 완료")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
