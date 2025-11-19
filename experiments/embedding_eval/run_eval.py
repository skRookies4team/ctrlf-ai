"""
임베딩 모델 평가 실행기

여러 임베딩 모델에 대해 동일한 질문을 실행하고 검색 성능 비교

평가 지표:
- Hit@k: 상위 k개 결과에 정답 문서가 포함되는 비율
- Recall@k: 상위 k개 결과에 포함된 정답 청크 비율
- MRR (Mean Reciprocal Rank): 정답의 평균 역순위

사용법:
    python experiments/embedding_eval/run_eval.py --providers dummy qwen_06b
    python experiments/embedding_eval/run_eval.py --top-k 5 --output results.json
"""

import argparse
import csv
import json
import logging
import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Tuple

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.embedder import embed_texts
from core.vector_store import FAISSVectorStore

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_eval_questions(csv_path: str) -> List[Dict[str, str]]:
    """
    평가 질문 CSV 로드

    Returns:
        List[Dict]: [{"question": ..., "expected_doc": ..., "expected_text": ...}, ...]
    """
    questions = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            questions.append(row)

    logger.info(f"Loaded {len(questions)} evaluation questions")
    return questions


def load_index(index_dir: str) -> FAISSVectorStore:
    """
    FAISS 인덱스 로드

    Args:
        index_dir: 인덱스 디렉토리 (faiss.index, metadata.jsonl 포함)

    Returns:
        FAISSVectorStore: 로드된 벡터 스토어
    """
    index_path = os.path.join(index_dir, "faiss.index")
    metadata_path = os.path.join(index_dir, "metadata.jsonl")

    if not os.path.exists(index_path):
        raise FileNotFoundError(f"Index not found: {index_path}")
    if not os.path.exists(metadata_path):
        raise FileNotFoundError(f"Metadata not found: {metadata_path}")

    vector_store = FAISSVectorStore(dim=384)
    vector_store.load_index(index_path)

    logger.info(f"Loaded index from {index_dir}")
    stats = vector_store.get_stats()
    logger.info(f"Total vectors: {stats['total_vectors']}")

    return vector_store


def search_query(
    query: str,
    vector_store: FAISSVectorStore,
    top_k: int = 5
) -> List[Dict[str, Any]]:
    """
    쿼리로 검색 실행

    Args:
        query: 검색 질문
        vector_store: FAISS 벡터 스토어
        top_k: 반환할 결과 개수

    Returns:
        List[Dict]: 검색 결과
    """
    # 쿼리 임베딩 생성
    query_vector = embed_texts([query])[0]

    # FAISS 검색
    results = vector_store.search(query_vector, top_k=top_k)

    return results


def calculate_hit_at_k(
    results: List[Dict[str, Any]],
    expected_doc: str,
    top_k: int
) -> bool:
    """
    Hit@k 계산: 상위 k개에 정답 문서가 있는지

    Args:
        results: 검색 결과
        expected_doc: 정답 문서명 (일부 포함 가능)
        top_k: k

    Returns:
        bool: Hit 여부
    """
    for i, result in enumerate(results[:top_k]):
        file_name = result.get("file_name", "")
        if expected_doc.lower() in file_name.lower():
            return True
    return False


def calculate_reciprocal_rank(
    results: List[Dict[str, Any]],
    expected_doc: str
) -> float:
    """
    Reciprocal Rank 계산: 정답의 역순위

    Args:
        results: 검색 결과
        expected_doc: 정답 문서명

    Returns:
        float: 역순위 (정답 없으면 0)
    """
    for i, result in enumerate(results):
        file_name = result.get("file_name", "")
        if expected_doc.lower() in file_name.lower():
            return 1.0 / (i + 1)
    return 0.0


def evaluate_provider(
    provider: str,
    index_dir: str,
    questions: List[Dict[str, str]],
    top_k: int = 5
) -> Dict[str, Any]:
    """
    특정 임베딩 제공자에 대해 평가 실행

    Args:
        provider: 제공자 이름
        index_dir: 인덱스 디렉토리
        questions: 평가 질문 리스트
        top_k: Top-K

    Returns:
        Dict: 평가 결과
    """
    logger.info(f"Evaluating provider: {provider}")

    # 인덱스 로드
    try:
        vector_store = load_index(index_dir)
    except Exception as e:
        logger.error(f"Failed to load index for {provider}: {e}")
        return {
            "provider": provider,
            "error": str(e),
            "hit_at_1": 0,
            "hit_at_3": 0,
            "hit_at_5": 0,
            "mrr": 0,
            "total_questions": len(questions)
        }

    # 평가 지표 초기화
    hit_at_1 = 0
    hit_at_3 = 0
    hit_at_5 = 0
    reciprocal_ranks = []

    # 각 질문에 대해 평가
    for q in questions:
        question = q["question"]
        expected_doc = q["expected_doc"]

        try:
            # 검색 실행
            results = search_query(question, vector_store, top_k=top_k)

            # Hit@k 계산
            if calculate_hit_at_k(results, expected_doc, top_k=1):
                hit_at_1 += 1
            if calculate_hit_at_k(results, expected_doc, top_k=3):
                hit_at_3 += 1
            if calculate_hit_at_k(results, expected_doc, top_k=5):
                hit_at_5 += 1

            # Reciprocal Rank 계산
            rr = calculate_reciprocal_rank(results, expected_doc)
            reciprocal_ranks.append(rr)

        except Exception as e:
            logger.error(f"Error evaluating question '{question}': {e}")
            reciprocal_ranks.append(0.0)

    # 평균 계산
    total = len(questions)
    mrr = sum(reciprocal_ranks) / total if total > 0 else 0

    results = {
        "provider": provider,
        "hit_at_1": hit_at_1 / total if total > 0 else 0,
        "hit_at_3": hit_at_3 / total if total > 0 else 0,
        "hit_at_5": hit_at_5 / total if total > 0 else 0,
        "mrr": mrr,
        "total_questions": total
    }

    logger.info(f"Results for {provider}:")
    logger.info(f"  Hit@1: {results['hit_at_1']:.2%}")
    logger.info(f"  Hit@3: {results['hit_at_3']:.2%}")
    logger.info(f"  Hit@5: {results['hit_at_5']:.2%}")
    logger.info(f"  MRR: {results['mrr']:.4f}")

    return results


def print_comparison_table(all_results: List[Dict[str, Any]]):
    """
    비교 표 출력

    Args:
        all_results: 모든 제공자의 평가 결과
    """
    print("\n" + "=" * 80)
    print("임베딩 모델 성능 비교")
    print("=" * 80)

    # 헤더
    print(f"{'Provider':<15} {'Hit@1':>10} {'Hit@3':>10} {'Hit@5':>10} {'MRR':>10}")
    print("-" * 80)

    # 각 제공자 결과
    for result in all_results:
        provider = result["provider"]
        hit1 = result["hit_at_1"]
        hit3 = result["hit_at_3"]
        hit5 = result["hit_at_5"]
        mrr = result["mrr"]

        print(f"{provider:<15} {hit1:>9.1%} {hit3:>9.1%} {hit5:>9.1%} {mrr:>10.4f}")

    print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Evaluate embedding models")
    parser.add_argument(
        "--providers",
        nargs="+",
        default=["dummy"],
        help="Embedding providers to evaluate"
    )
    parser.add_argument(
        "--questions",
        type=str,
        default="experiments/embedding_eval/eval_questions.csv",
        help="Path to evaluation questions CSV"
    )
    parser.add_argument(
        "--indexes-dir",
        type=str,
        default="experiments/embedding_eval/indexes",
        help="Directory containing provider indexes"
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Top-K for evaluation"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON file for results"
    )

    args = parser.parse_args()

    # 질문 로드
    questions_path = os.path.join(PROJECT_ROOT, args.questions)
    questions = load_eval_questions(questions_path)

    # 각 제공자에 대해 평가
    all_results = []
    for provider in args.providers:
        index_dir = os.path.join(PROJECT_ROOT, args.indexes_dir, provider)

        if not os.path.exists(index_dir):
            logger.warning(f"Index directory not found: {index_dir}")
            logger.warning(f"Skipping provider: {provider}")
            continue

        result = evaluate_provider(provider, index_dir, questions, top_k=args.top_k)
        all_results.append(result)

    # 비교 표 출력
    if all_results:
        print_comparison_table(all_results)

        # JSON 파일로 저장 (옵션)
        if args.output:
            output_path = os.path.join(PROJECT_ROOT, args.output)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(all_results, f, indent=2, ensure_ascii=False)
            logger.info(f"Results saved to: {output_path}")
    else:
        logger.warning("No results to display")


if __name__ == "__main__":
    main()
