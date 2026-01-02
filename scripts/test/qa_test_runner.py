# -*- coding: utf-8 -*-
"""
질문리스트 기반 챗봇 성능 평가 스크립트
각 질문을 챗봇 API에 전송하고 결과를 수집합니다.
"""

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import aiohttp
import pandas as pd
from tqdm import tqdm

# 설정
API_URL = "http://localhost:8000/ai/chat/messages"
INPUT_FILE = "질문리스트.xlsx"
OUTPUT_FILE = "질답리스트.xlsx"
MAX_CONCURRENT = 5  # 동시 요청 수 (서버 부하 고려)
TIMEOUT = 120  # 요청 타임아웃 (초)


async def send_question(session: aiohttp.ClientSession, row: dict, semaphore: asyncio.Semaphore) -> dict:
    """단일 질문을 API에 전송하고 결과를 반환합니다."""
    async with semaphore:
        question_id = row.get("ID", "")
        question = row.get("질문", "")
        persona = row.get("페르소나", "EMPLOYEE")
        domain = row.get("domain", None)
        category = row.get("카테고리", "")

        # 요청 본문 구성
        payload = {
            "session_id": f"qa-test-{question_id}",
            "user_id": f"test-{question_id}",
            "user_role": persona if persona else "EMPLOYEE",
            "domain": domain if domain and pd.notna(domain) else None,
            "channel": "WEB",
            "messages": [
                {"role": "user", "content": question}
            ]
        }

        result = {
            "ID": question_id,
            "페르소나": persona,
            "카테고리": category,
            "domain": domain,
            "질문": question,
            "답변": "",
            "출처_문서": "",
            "출처_개수": 0,
            "사용_모델": "",
            "라우트": "",
            "의도": "",
            "RAG_사용": "",
            "응답시간_ms": 0,
            "RAG_시간_ms": 0,
            "LLM_시간_ms": 0,
            "에러": "",
            "테스트_시간": datetime.now().isoformat()
        }

        try:
            start_time = time.time()
            async with session.post(API_URL, json=payload, timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as response:
                elapsed_ms = int((time.time() - start_time) * 1000)

                if response.status == 200:
                    data = await response.json()

                    # 답변 추출
                    result["답변"] = data.get("answer", "")

                    # 출처 정보 추출
                    sources = data.get("sources", [])
                    result["출처_개수"] = len(sources)
                    if sources:
                        source_titles = [s.get("title", "") for s in sources[:3]]  # 상위 3개만
                        result["출처_문서"] = " | ".join(source_titles)

                    # 메타 정보 추출
                    meta = data.get("meta", {})
                    result["사용_모델"] = meta.get("used_model", "")
                    result["라우트"] = meta.get("route", "")
                    result["의도"] = meta.get("intent", "")
                    result["RAG_사용"] = "Y" if meta.get("rag_used", False) else "N"
                    result["응답시간_ms"] = meta.get("latency_ms", elapsed_ms)
                    result["RAG_시간_ms"] = meta.get("rag_latency_ms", 0) or 0
                    result["LLM_시간_ms"] = meta.get("llm_latency_ms", 0) or 0

                else:
                    result["에러"] = f"HTTP {response.status}: {await response.text()}"

        except asyncio.TimeoutError:
            result["에러"] = "Timeout"
        except Exception as e:
            result["에러"] = str(e)

        return result


async def run_all_questions(df: pd.DataFrame) -> list:
    """모든 질문을 비동기로 처리합니다."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    results = []

    async with aiohttp.ClientSession() as session:
        tasks = []
        for _, row in df.iterrows():
            tasks.append(send_question(session, row.to_dict(), semaphore))

        # tqdm으로 진행률 표시
        for future in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="질문 처리 중"):
            result = await future
            results.append(result)

            # 중간 저장 (10개마다)
            if len(results) % 10 == 0:
                save_results(results, f"질답리스트_중간저장_{len(results)}.xlsx")

    return results


def save_results(results: list, filename: str):
    """결과를 Excel 파일로 저장합니다."""
    df_results = pd.DataFrame(results)

    # 컬럼 순서 정리
    columns = [
        "ID", "페르소나", "카테고리", "domain", "질문", "답변",
        "출처_문서", "출처_개수", "사용_모델", "라우트", "의도",
        "RAG_사용", "응답시간_ms", "RAG_시간_ms", "LLM_시간_ms",
        "에러", "테스트_시간"
    ]
    df_results = df_results[columns]

    # Excel 저장
    df_results.to_excel(filename, index=False, engine='openpyxl')
    print(f"\n결과 저장 완료: {filename}")


def main():
    """메인 함수"""
    print("=" * 60)
    print("CTRL+F AI 챗봇 성능 평가 스크립트")
    print("=" * 60)

    # 입력 파일 읽기
    print(f"\n1. 질문 리스트 로딩: {INPUT_FILE}")
    df = pd.read_excel(INPUT_FILE, engine='openpyxl')
    total_questions = len(df)
    print(f"   - 총 {total_questions}개 질문 로드됨")

    # 서버 상태 확인
    print("\n2. 챗봇 서버 상태 확인...")
    import requests
    try:
        health = requests.get("http://localhost:8000/health", timeout=5)
        print(f"   - 서버 상태: {health.json()}")
    except Exception as e:
        print(f"   - 서버 연결 실패: {e}")
        print("   - 서버를 먼저 시작해주세요!")
        return

    # 질문 처리
    print(f"\n3. 질문 처리 시작 (동시 처리: {MAX_CONCURRENT}개)")
    print(f"   - 예상 소요 시간: 약 {total_questions * 4 // MAX_CONCURRENT // 60}분")

    start_time = time.time()
    results = asyncio.run(run_all_questions(df))
    elapsed = time.time() - start_time

    # ID 기준으로 정렬
    results.sort(key=lambda x: x.get("ID", ""))

    # 최종 결과 저장
    print(f"\n4. 최종 결과 저장")
    save_results(results, OUTPUT_FILE)

    # 통계 출력
    print(f"\n{'=' * 60}")
    print("처리 완료 요약")
    print(f"{'=' * 60}")
    print(f"총 질문 수: {total_questions}")
    print(f"성공: {sum(1 for r in results if not r['에러'])}")
    print(f"실패: {sum(1 for r in results if r['에러'])}")
    print(f"총 소요 시간: {elapsed:.1f}초 ({elapsed/60:.1f}분)")
    print(f"평균 응답 시간: {sum(r['응답시간_ms'] for r in results if not r['에러']) / max(1, sum(1 for r in results if not r['에러'])):.0f}ms")
    print(f"\n결과 파일: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
