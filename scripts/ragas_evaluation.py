"""
RAGAS (Retrieval Augmented Generation Assessment) 평가 스크립트

이 스크립트는 CtrlF RAG 시스템의 품질을 RAGAS 프레임워크로 평가합니다.

사용법:
    # 전체 평가 실행
    python scripts/ragas_evaluation.py

    # 특정 카테고리만 평가
    python scripts/ragas_evaluation.py --category HR

    # API 호출 없이 기존 결과로 평가 (재평가)
    python scripts/ragas_evaluation.py --use-cached

필요 패키지:
    pip install ragas datasets langchain-openai

환경 변수:
    OPENAI_API_KEY: RAGAS 평가에 사용할 OpenAI API 키
    CTRLF_API_URL: CtrlF API 엔드포인트 (기본값: http://localhost:8000)
"""

import json
import os
import asyncio
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional

# RAGAS imports
try:
    from ragas import evaluate
    from ragas.metrics import (
        faithfulness,
        answer_relevancy,
        context_precision,
        context_recall,
    )
    from datasets import Dataset
    RAGAS_AVAILABLE = True
except ImportError:
    RAGAS_AVAILABLE = False
    print("Warning: ragas 패키지가 설치되지 않았습니다.")
    print("설치: pip install ragas datasets langchain-openai")

import httpx


# ============================================================================
# 설정
# ============================================================================

BASE_DIR = Path(__file__).parent.parent
GOLDEN_QUERIES_PATH = BASE_DIR / "tests" / "regression" / "golden_queries.json"
GROUND_TRUTH_PATH = BASE_DIR / "tests" / "regression" / "golden_queries_ground_truth.json"
RESULTS_DIR = BASE_DIR / "tests" / "regression" / "ragas_results"
CACHED_RESPONSES_PATH = RESULTS_DIR / "cached_responses.json"

API_BASE_URL = os.getenv("CTRLF_API_URL", "http://localhost:8000")
CHAT_ENDPOINT = f"{API_BASE_URL}/api/v1/chat"


# ============================================================================
# 데이터 로딩
# ============================================================================

def load_golden_queries() -> list[dict]:
    """Golden Query 파일 로드"""
    with open(GOLDEN_QUERIES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["queries"]


def load_ground_truth() -> dict[str, str]:
    """Ground Truth 데이터 로드 (있는 경우)"""
    if not GROUND_TRUTH_PATH.exists():
        return {}

    with open(GROUND_TRUTH_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    return {item["query"]: item["ground_truth"] for item in data.get("queries", [])}


def load_cached_responses() -> dict:
    """캐시된 API 응답 로드"""
    if not CACHED_RESPONSES_PATH.exists():
        return {}

    with open(CACHED_RESPONSES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cached_responses(responses: dict):
    """API 응답 캐시 저장"""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    with open(CACHED_RESPONSES_PATH, "w", encoding="utf-8") as f:
        json.dump(responses, f, ensure_ascii=False, indent=2)


# ============================================================================
# API 호출
# ============================================================================

async def call_chat_api(
    query: str,
    user_id: str = "test_user",
    session_id: str = "ragas_test_session",
) -> dict:
    """
    CtrlF Chat API 호출

    Returns:
        {
            "answer": str,
            "contexts": list[str],
            "sources": list[dict],
            "l2_distance": float
        }
    """
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(
                CHAT_ENDPOINT,
                json={
                    "message": query,
                    "session_id": session_id,
                },
                headers={
                    "X-User-Id": user_id,
                    "Content-Type": "application/json",
                }
            )
            response.raise_for_status()
            data = response.json()

            # 응답에서 필요한 정보 추출
            answer = data.get("answer", data.get("response", ""))
            sources = data.get("sources", [])

            # contexts 추출 (sources에서 content 필드)
            contexts = []
            for source in sources:
                if isinstance(source, dict):
                    content = source.get("content", source.get("text", ""))
                    if content:
                        contexts.append(content)
                elif isinstance(source, str):
                    contexts.append(source)

            return {
                "answer": answer,
                "contexts": contexts,
                "sources": sources,
                "l2_distance": data.get("min_l2_distance", data.get("l2_distance", None)),
            }

        except httpx.HTTPStatusError as e:
            print(f"API Error for query '{query}': {e.response.status_code}")
            return {
                "answer": "",
                "contexts": [],
                "sources": [],
                "l2_distance": None,
                "error": str(e),
            }
        except Exception as e:
            print(f"Error for query '{query}': {e}")
            return {
                "answer": "",
                "contexts": [],
                "sources": [],
                "l2_distance": None,
                "error": str(e),
            }


async def collect_responses(
    queries: list[dict],
    use_cached: bool = False,
) -> dict[str, dict]:
    """모든 쿼리에 대한 응답 수집"""

    if use_cached:
        cached = load_cached_responses()
        if cached:
            print(f"Using cached responses ({len(cached)} queries)")
            return cached

    responses = {}
    total = len(queries)

    print(f"Collecting responses for {total} queries...")

    for i, query_data in enumerate(queries, 1):
        query = query_data["query"]
        print(f"[{i}/{total}] {query}...")

        response = await call_chat_api(query)
        responses[query] = response

        # Rate limiting
        await asyncio.sleep(0.5)

    # 캐시 저장
    save_cached_responses(responses)
    print(f"Responses cached to {CACHED_RESPONSES_PATH}")

    return responses


# ============================================================================
# RAGAS 평가
# ============================================================================

def prepare_ragas_dataset(
    queries: list[dict],
    responses: dict[str, dict],
    ground_truth: dict[str, str],
) -> Dataset:
    """RAGAS 평가용 Dataset 준비"""

    data = {
        "question": [],
        "answer": [],
        "contexts": [],
        "ground_truth": [],
    }

    for query_data in queries:
        query = query_data["query"]
        response = responses.get(query, {})

        # 응답이 없거나 에러가 있으면 스킵
        if not response or response.get("error"):
            continue

        answer = response.get("answer", "")
        contexts = response.get("contexts", [])

        # contexts가 비어있으면 스킵 (RAGAS 에러 방지)
        if not contexts:
            contexts = ["정보를 찾을 수 없습니다."]

        # ground_truth가 없으면 빈 문자열 (context_recall 계산 불가)
        gt = ground_truth.get(query, "")

        data["question"].append(query)
        data["answer"].append(answer)
        data["contexts"].append(contexts)
        data["ground_truth"].append(gt)

    return Dataset.from_dict(data)


def run_ragas_evaluation(dataset: Dataset) -> dict:
    """RAGAS 평가 실행"""

    if not RAGAS_AVAILABLE:
        print("RAGAS가 설치되지 않아 평가를 실행할 수 없습니다.")
        return {}

    # ground_truth가 있는 경우에만 context_recall 포함
    has_ground_truth = any(gt for gt in dataset["ground_truth"])

    metrics = [faithfulness, answer_relevancy, context_precision]
    if has_ground_truth:
        metrics.append(context_recall)

    print(f"\nRunning RAGAS evaluation with metrics: {[m.name for m in metrics]}")
    print(f"Dataset size: {len(dataset)} queries")

    try:
        result = evaluate(dataset, metrics=metrics)
        return dict(result)
    except Exception as e:
        print(f"RAGAS evaluation error: {e}")
        return {"error": str(e)}


# ============================================================================
# 결과 저장 및 출력
# ============================================================================

def save_results(results: dict, responses: dict[str, dict]):
    """평가 결과 저장"""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # RAGAS 점수 저장
    scores_path = RESULTS_DIR / f"ragas_scores_{timestamp}.json"
    with open(scores_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": timestamp,
            "scores": results,
            "num_queries": len(responses),
        }, f, ensure_ascii=False, indent=2)

    print(f"\nResults saved to {scores_path}")

    # 최신 결과 링크
    latest_path = RESULTS_DIR / "ragas_scores_latest.json"
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": timestamp,
            "scores": results,
            "num_queries": len(responses),
        }, f, ensure_ascii=False, indent=2)


def print_results(results: dict):
    """결과 출력"""
    print("\n" + "=" * 60)
    print("RAGAS Evaluation Results")
    print("=" * 60)

    if "error" in results:
        print(f"Error: {results['error']}")
        return

    metrics_desc = {
        "faithfulness": "답변이 컨텍스트에만 기반하는지 (환각 방지)",
        "answer_relevancy": "답변이 질문에 관련있는지",
        "context_precision": "검색된 문서가 정확한지",
        "context_recall": "필요한 문서가 모두 검색되었는지",
    }

    for metric, score in results.items():
        if metric in metrics_desc:
            desc = metrics_desc[metric]
            score_str = f"{score:.4f}" if isinstance(score, float) else str(score)
            print(f"\n{metric}: {score_str}")
            print(f"  └─ {desc}")

    print("\n" + "=" * 60)

    # 요약
    if all(k in results for k in ["faithfulness", "answer_relevancy"]):
        avg = (results["faithfulness"] + results["answer_relevancy"]) / 2
        print(f"\n평균 점수 (Faithfulness + Relevancy): {avg:.4f}")

        if avg >= 0.9:
            print("평가: 우수 (Excellent)")
        elif avg >= 0.8:
            print("평가: 양호 (Good)")
        elif avg >= 0.7:
            print("평가: 보통 (Fair)")
        else:
            print("평가: 개선 필요 (Needs Improvement)")


# ============================================================================
# 메인
# ============================================================================

async def main(
    category: Optional[str] = None,
    use_cached: bool = False,
):
    """메인 실행 함수"""

    print("=" * 60)
    print("CtrlF RAGAS Evaluation")
    print("=" * 60)

    # 1. 데이터 로드
    print("\n[1/4] Loading data...")
    queries = load_golden_queries()
    ground_truth = load_ground_truth()

    print(f"  - Golden Queries: {len(queries)}")
    print(f"  - Ground Truth: {len(ground_truth)} available")

    # 카테고리 필터링
    if category:
        queries = [q for q in queries if q.get("category") == category]
        print(f"  - Filtered by category '{category}': {len(queries)} queries")

    if not queries:
        print("No queries to evaluate!")
        return

    # 2. API 응답 수집
    print("\n[2/4] Collecting API responses...")
    responses = await collect_responses(queries, use_cached=use_cached)

    # 3. RAGAS Dataset 준비
    print("\n[3/4] Preparing RAGAS dataset...")
    dataset = prepare_ragas_dataset(queries, responses, ground_truth)
    print(f"  - Dataset prepared with {len(dataset)} samples")

    # 4. RAGAS 평가 실행
    print("\n[4/4] Running RAGAS evaluation...")
    results = run_ragas_evaluation(dataset)

    # 결과 출력 및 저장
    print_results(results)
    save_results(results, responses)

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAGAS Evaluation for CtrlF")
    parser.add_argument(
        "--category",
        type=str,
        help="Filter queries by category (HR, SECURITY, EDU, etc.)",
    )
    parser.add_argument(
        "--use-cached",
        action="store_true",
        help="Use cached API responses instead of calling API",
    )

    args = parser.parse_args()

    asyncio.run(main(
        category=args.category,
        use_cached=args.use_cached,
    ))
