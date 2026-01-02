"""
질문리스트 배치 테스트 스크립트
LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct 모델 성능 평가용
"""

import asyncio
import aiohttp
import pandas as pd
from datetime import datetime
import json
import time
import sys
from pathlib import Path

# Configuration
AI_API_URL = "http://localhost:8000/ai/chat/messages"
CONCURRENT_REQUESTS = 3  # 동시 요청 수
TIMEOUT_SECONDS = 120  # 요청 타임아웃
OUTPUT_DIR = Path(__file__).parent / "docs"


async def send_chat_request(session: aiohttp.ClientSession, question_data: dict, semaphore: asyncio.Semaphore) -> dict:
    """단일 질문을 AI 챗봇에 전송하고 응답을 받음"""
    async with semaphore:
        question_id = question_data["ID"]
        question = question_data["질문"]
        domain = question_data.get("domain", "POLICY")
        persona = question_data.get("페르소나", "EMPLOYEE")

        payload = {
            "session_id": f"batch-test-{question_id}",
            "user_id": f"test-user-{persona.lower()}",
            "user_role": persona,
            "department": "테스트팀",
            "domain": domain,
            "channel": "BATCH_TEST",
            "messages": [{"role": "user", "content": question}]
        }

        start_time = time.time()
        result = {
            "ID": question_id,
            "페르소나": persona,
            "카테고리": question_data.get("카테고리", ""),
            "질문": question,
            "domain": domain,
            "intent": question_data.get("intent", ""),
            "답변": "",
            "모델": "",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "latency_ms": 0,
            "route": "",
            "rag_used": False,
            "rag_source_count": 0,
            "error": ""
        }

        try:
            async with session.post(
                AI_API_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
            ) as response:
                elapsed_ms = int((time.time() - start_time) * 1000)

                if response.status == 200:
                    data = await response.json()
                    result["답변"] = data.get("answer", "")
                    result["모델"] = data.get("model", "")
                    result["prompt_tokens"] = data.get("prompt_tokens", 0)
                    result["completion_tokens"] = data.get("completion_tokens", 0)
                    result["latency_ms"] = elapsed_ms

                    meta = data.get("meta", {})
                    result["route"] = meta.get("route", "")
                    result["rag_used"] = meta.get("rag_used", False)
                    result["rag_source_count"] = meta.get("rag_source_count", 0)
                else:
                    error_text = await response.text()
                    result["error"] = f"HTTP {response.status}: {error_text[:200]}"
                    result["latency_ms"] = elapsed_ms

        except asyncio.TimeoutError:
            result["error"] = "TIMEOUT"
            result["latency_ms"] = TIMEOUT_SECONDS * 1000
        except Exception as e:
            result["error"] = str(e)[:200]
            result["latency_ms"] = int((time.time() - start_time) * 1000)

        return result


async def process_batch(questions: list[dict], progress_callback=None) -> list[dict]:
    """모든 질문을 배치로 처리"""
    semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)
    results = []

    connector = aiohttp.TCPConnector(limit=CONCURRENT_REQUESTS)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for q in questions:
            task = send_chat_request(session, q, semaphore)
            tasks.append(task)

        # 진행 상황 추적
        completed = 0
        total = len(tasks)

        for coro in asyncio.as_completed(tasks):
            result = await coro
            results.append(result)
            completed += 1

            if progress_callback:
                progress_callback(completed, total, result)

    return results


def print_progress(completed: int, total: int, result: dict):
    """진행 상황 출력"""
    pct = (completed / total) * 100
    status = "OK" if not result["error"] else f"ERR: {result['error'][:30]}"
    print(f"\r[{completed}/{total}] ({pct:.1f}%) {result['ID']}: {status}".ljust(80), end="", flush=True)


def main():
    print("=" * 60)
    print("EXAONE 모델 질답리스트 생성 스크립트")
    print("=" * 60)

    # 질문리스트 읽기
    input_file = Path(__file__).parent / "질문리스트.xlsx"
    print(f"\n1. 질문리스트 로딩: {input_file}")

    df = pd.read_excel(input_file, engine="openpyxl")
    questions = df.to_dict("records")
    print(f"   -> 총 {len(questions)}개 질문 로드됨")

    # AI 서버 연결 테스트
    print("\n2. AI 서버 연결 테스트...")
    import requests
    try:
        test_resp = requests.post(
            AI_API_URL,
            json={
                "session_id": "connection-test",
                "user_id": "test",
                "user_role": "EMPLOYEE",
                "channel": "TEST",
                "messages": [{"role": "user", "content": "연결 테스트"}]
            },
            timeout=30
        )
        if test_resp.status_code == 200:
            data = test_resp.json()
            print(f"   -> 연결 성공! 모델: {data.get('model', 'N/A')}")
        else:
            print(f"   -> 연결 실패: {test_resp.status_code}")
            sys.exit(1)
    except Exception as e:
        print(f"   -> 연결 오류: {e}")
        sys.exit(1)

    # 배치 처리
    print(f"\n3. 배치 처리 시작 (동시 요청: {CONCURRENT_REQUESTS}개)")
    print("-" * 60)

    start_time = time.time()
    results = asyncio.run(process_batch(questions, print_progress))
    elapsed = time.time() - start_time

    print(f"\n\n4. 처리 완료!")
    print(f"   -> 총 소요 시간: {elapsed:.1f}초 ({elapsed/60:.1f}분)")
    print(f"   -> 평균 응답 시간: {sum(r['latency_ms'] for r in results) / len(results):.0f}ms")

    # 결과 정리
    success_count = sum(1 for r in results if not r["error"])
    error_count = len(results) - success_count
    print(f"   -> 성공: {success_count}, 실패: {error_count}")

    # 결과를 원래 순서로 정렬
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values("ID").reset_index(drop=True)

    # Excel 파일로 저장
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = OUTPUT_DIR / f"질답리스트_EXAONE_{timestamp}.xlsx"

    print(f"\n5. 결과 저장: {output_file}")
    results_df.to_excel(output_file, index=False, engine="openpyxl")
    print("   -> 저장 완료!")

    # 통계 요약
    print("\n" + "=" * 60)
    print("결과 요약")
    print("=" * 60)
    print(f"총 질문 수: {len(results)}")
    print(f"성공: {success_count} ({success_count/len(results)*100:.1f}%)")
    print(f"실패: {error_count} ({error_count/len(results)*100:.1f}%)")
    print(f"평균 latency: {results_df['latency_ms'].mean():.0f}ms")
    print(f"평균 prompt_tokens: {results_df['prompt_tokens'].mean():.0f}")
    print(f"평균 completion_tokens: {results_df['completion_tokens'].mean():.0f}")

    if error_count > 0:
        print(f"\n오류 유형:")
        error_types = results_df[results_df["error"] != ""]["error"].value_counts()
        for err_type, count in error_types.items():
            print(f"  - {err_type[:50]}: {count}")

    return output_file


if __name__ == "__main__":
    output = main()
    print(f"\n완료: {output}")
