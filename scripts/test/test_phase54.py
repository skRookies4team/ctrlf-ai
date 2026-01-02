# -*- coding: utf-8 -*-
"""Phase 54 Korean enforcement test"""
import asyncio
import aiohttp
import json
import sys

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

async def test_question(question):
    url = 'http://localhost:8000/ai/chat/messages'
    payload = {
        'session_id': 'test-phase54',
        'user_id': 'test-user',
        'user_role': 'EMPLOYEE',
        'domain': 'COMPLAINT',
        'channel': 'WEB',
        'messages': [
            {'role': 'user', 'content': question}
        ]
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    return f'Error: {resp.status} - {await resp.text()}'

                data = await resp.json()
                answer = data.get('answer', '')

                # Check if response starts with English
                english_starts = ["I'd", 'I would', 'I can', 'According', 'Based on', 'Sure', 'Of course', 'Thank', 'Let me']
                starts_english = any(answer.strip().startswith(e) for e in english_starts)

                return {
                    'starts_english': starts_english,
                    'route': data.get('meta', {}).get('route', ''),
                    'first_150': answer[:150]
                }
    except Exception as e:
        return f'Error: {str(e)}'

async def main():
    # Test questions that previously returned English
    questions = [
        '이번 분기 전체 신고 현황을 월별로 정리해줘',
        '전화, 이메일, 챗봇별로 신고가 어느 채널에서 가장 많이 들어오는지 알려줘',
        '익명 신고 내역에 접근할 수 있는 역할이 어디까지인지 알려줘'
    ]

    print('Testing Phase 54 Korean enforcement...')
    for i, q in enumerate(questions, 1):
        result = await test_question(q)
        print(f'\nQ{i}: {q[:40]}...')
        if isinstance(result, dict):
            status = 'ENGLISH' if result['starts_english'] else 'KOREAN'
            print(f'  Status: {status} | Route: {result["route"]}')
            print(f'  Response: {result["first_150"]}...')
        else:
            print(f'  {result}')

if __name__ == '__main__':
    asyncio.run(main())
