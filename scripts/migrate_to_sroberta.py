"""
ragflow_chunks → ragflow_chunks_sroberta 마이그레이션 스크립트

원본 컬렉션의 텍스트를 원격 임베딩 서버 (vLLM)를 통해 재임베딩하여 대상 컬렉션에 삽입합니다.

사용법:
    # Dry-run (실제 삽입 없이 확인만)
    python scripts/migrate_to_sroberta.py --dry-run

    # 실제 마이그레이션 실행
    python scripts/migrate_to_sroberta.py

    # 특정 dataset만 마이그레이션
    python scripts/migrate_to_sroberta.py --dataset "정보보안교육"
"""

import argparse
import hashlib
import os
import sys
import time
from typing import List, Dict, Any, Optional
import httpx

# 프로젝트 루트 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

from pymilvus import connections, Collection
import numpy as np


# 설정
SOURCE_COLLECTION = "ragflow_chunks"
TARGET_COLLECTION = "ragflow_chunks_sroberta"
MILVUS_HOST = os.getenv("MILVUS_HOST", "58.127.241.84")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19540")
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "http://58.127.241.84:1234")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "jhgan/ko-sroberta-multitask")
EMBEDDING_DIM = 768
BATCH_SIZE = 50  # 임베딩 배치 크기


def compute_chunk_hash(text: str) -> str:
    """텍스트 해시 계산."""
    return hashlib.md5(text.encode('utf-8')).hexdigest()[:16]


def truncate_text(text: str, max_chars: int = 500) -> str:
    """텍스트를 최대 글자수로 자름 (512토큰 ≈ 500자 보수적)."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def embed_texts_via_api(texts: List[str], batch_size: int = 50) -> np.ndarray:
    """원격 임베딩 서버를 통해 텍스트 임베딩 생성."""
    url = f"{EMBEDDING_BASE_URL.rstrip('/')}/v1/embeddings"
    all_embeddings = []
    total = len(texts)

    print(f"[*] 임베딩 API: {url}")
    print(f"[*] 모델: {EMBEDDING_MODEL_NAME}")

    with httpx.Client(timeout=300.0) as client:
        for i in range(0, total, batch_size):
            # 텍스트 길이 제한 (512 토큰 ≈ 1000자, 보수적)
            batch = [truncate_text(t) for t in texts[i:i+batch_size]]

            payload = {
                "model": EMBEDDING_MODEL_NAME,
                "input": batch,
                "encoding_format": "float"
            }

            try:
                response = client.post(url, json=payload)
                response.raise_for_status()
                result = response.json()

                # 응답에서 임베딩 추출
                for item in result.get("data", []):
                    all_embeddings.append(item["embedding"])

            except httpx.HTTPStatusError as e:
                print(f"\n[ERROR] API 오류: {e.response.status_code}")
                print(f"[ERROR] 응답: {e.response.text[:500]}")
                raise
            except Exception as e:
                print(f"\n[ERROR] 요청 실패: {e}")
                raise

            progress = min(i + batch_size, total)
            print(f"\r[*] 임베딩 진행: {progress}/{total} ({100*progress/total:.1f}%)", end="")

    print()
    return np.array(all_embeddings)


def get_existing_hashes(collection: Collection) -> set:
    """대상 컬렉션의 기존 chunk_hash 조회 (페이지네이션)."""
    print("[*] 기존 데이터 해시 조회 중...")
    collection.load()

    hashes = set()
    offset = 0
    page_size = 10000

    while True:
        results = collection.query(
            expr="chunk_id >= 0",
            output_fields=["chunk_hash"],
            offset=offset,
            limit=page_size
        )
        if not results:
            break
        for r in results:
            if r.get("chunk_hash"):
                hashes.add(r["chunk_hash"])
        offset += len(results)
        if len(results) < page_size:
            break

    print(f"[*] 기존 해시: {len(hashes)}개")
    return hashes


def get_source_data(
    collection: Collection,
    existing_hashes: set,
    target_datasets: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """원본 컬렉션에서 마이그레이션할 데이터 조회 (페이지네이션)."""
    print("[*] 원본 데이터 조회 중...")
    collection.load()

    results = []
    offset = 0
    page_size = 10000

    while True:
        page = collection.query(
            expr="chunk_id >= 0",
            output_fields=["dataset_id", "doc_id", "chunk_id", "text", "chunk_hash"],
            offset=offset,
            limit=page_size
        )
        if not page:
            break
        results.extend(page)
        print(f"\r[*] 조회 진행: {len(results)}개", end="")
        offset += len(page)
        if len(page) < page_size:
            break
    print()

    # 필터링
    filtered = []
    skipped_existing = 0
    skipped_dataset = 0

    for r in results:
        # 이미 존재하는 데이터 스킵
        chunk_hash = r.get("chunk_hash") or compute_chunk_hash(r.get("text", ""))
        if chunk_hash in existing_hashes:
            skipped_existing += 1
            continue

        # 특정 dataset만 처리
        if target_datasets and r.get("dataset_id") not in target_datasets:
            skipped_dataset += 1
            continue

        # 텍스트가 없으면 스킵
        if not r.get("text") or not r["text"].strip():
            continue

        r["chunk_hash"] = chunk_hash
        filtered.append(r)

    print(f"[*] 원본 데이터: {len(results)}개")
    print(f"[*] 이미 존재 (스킵): {skipped_existing}개")
    if target_datasets:
        print(f"[*] 다른 dataset (스킵): {skipped_dataset}개")
    print(f"[*] 마이그레이션 대상: {len(filtered)}개")

    return filtered


def get_next_pk(collection: Collection) -> int:
    """다음 pk 값 조회 (페이지네이션)."""
    collection.load()

    max_pk = 0
    offset = 0
    page_size = 10000

    while True:
        results = collection.query(
            expr="pk >= 0",
            output_fields=["pk"],
            offset=offset,
            limit=page_size
        )
        if not results:
            break
        page_max = max(r["pk"] for r in results)
        max_pk = max(max_pk, page_max)
        offset += len(results)
        if len(results) < page_size:
            break

    return max_pk + 1 if max_pk > 0 else 1


def insert_data(
    collection: Collection,
    data: List[Dict[str, Any]],
    embeddings: np.ndarray,
    start_pk: int,
    dry_run: bool = False
) -> int:
    """대상 컬렉션에 데이터 삽입."""
    if dry_run:
        print(f"[DRY-RUN] {len(data)}개 삽입 예정 (pk: {start_pk} ~ {start_pk + len(data) - 1})")
        return len(data)

    # 배치 삽입 (column-based, 스키마 필드 순서: pk, dataset_id, doc_id, chunk_id, chunk_hash, text, embedding)
    batch_size = 500
    total = len(data)
    inserted = 0

    for i in range(0, total, batch_size):
        batch_data = data[i:i+batch_size]
        batch_emb = embeddings[i:i+batch_size]

        # 스키마 순서대로 리스트의 리스트 형식 (pk는 auto_id=True이므로 제외)
        insert_list = [
            [row["dataset_id"] for row in batch_data],                    # dataset_id
            [row["doc_id"] for row in batch_data],                        # doc_id
            [row["chunk_id"] for row in batch_data],                      # chunk_id
            [row["chunk_hash"] for row in batch_data],                    # chunk_hash
            [row["text"] for row in batch_data],                          # text
            [emb.tolist() if hasattr(emb, 'tolist') else emb for emb in batch_emb],  # embedding
        ]

        collection.insert(insert_list)
        inserted += len(batch_data)
        print(f"\r[*] 삽입 진행: {inserted}/{total} ({100*inserted/total:.1f}%)", end="")

    print()
    collection.flush()
    return inserted


def main():
    parser = argparse.ArgumentParser(description="Milvus 컬렉션 마이그레이션")
    parser.add_argument("--dry-run", action="store_true", help="실제 삽입 없이 확인만")
    parser.add_argument("--dataset", type=str, help="특정 dataset만 마이그레이션")
    parser.add_argument("--batch-size", type=int, default=50, help="임베딩 배치 크기")
    args = parser.parse_args()

    target_datasets = [args.dataset] if args.dataset else None

    print("=" * 60)
    print("  Milvus 컬렉션 마이그레이션 (원격 임베딩 API 사용)")
    print("=" * 60)
    print(f"  원본: {SOURCE_COLLECTION}")
    print(f"  대상: {TARGET_COLLECTION}")
    print(f"  Milvus: {MILVUS_HOST}:{MILVUS_PORT}")
    print(f"  임베딩 API: {EMBEDDING_BASE_URL}")
    print(f"  임베딩 모델: {EMBEDDING_MODEL_NAME}")
    print(f"  Dry-run: {args.dry_run}")
    if target_datasets:
        print(f"  대상 Dataset: {target_datasets}")
    print("=" * 60)

    # Milvus 연결
    print("\n[1/5] Milvus 연결")
    connections.connect(alias="default", host=MILVUS_HOST, port=MILVUS_PORT)
    print(f"[*] 연결 성공")

    source_col = Collection(SOURCE_COLLECTION)
    target_col = Collection(TARGET_COLLECTION)

    # 기존 데이터 확인
    print("\n[2/5] 기존 데이터 확인")
    existing_hashes = get_existing_hashes(target_col)

    # 마이그레이션 대상 데이터 조회
    print("\n[3/5] 마이그레이션 대상 조회")
    data = get_source_data(source_col, existing_hashes, target_datasets)

    if not data:
        print("\n[*] 마이그레이션할 데이터가 없습니다.")
        connections.disconnect("default")
        return

    # Dataset별 통계
    print("\n[*] Dataset별 통계:")
    stats = {}
    for row in data:
        ds = row["dataset_id"]
        if ds not in stats:
            stats[ds] = {"chunks": 0, "docs": set()}
        stats[ds]["chunks"] += 1
        stats[ds]["docs"].add(row["doc_id"])

    for ds, info in sorted(stats.items()):
        print(f"    {ds}: {len(info['docs'])}문서, {info['chunks']}청크")

    # 임베딩 생성
    print("\n[4/5] 임베딩 생성 (원격 API)")
    if args.dry_run:
        print(f"[DRY-RUN] {len(data)}개 텍스트 임베딩 예정")
        embeddings = None
    else:
        texts = [row["text"] for row in data]
        start_time = time.time()
        embeddings = embed_texts_via_api(texts, args.batch_size)
        elapsed = time.time() - start_time
        print(f"[*] 임베딩 완료: {len(embeddings)}개, {elapsed:.1f}초")

    # 데이터 삽입
    print("\n[5/5] 데이터 삽입")
    next_pk = get_next_pk(target_col)
    print(f"[*] 시작 pk: {next_pk}")

    if args.dry_run:
        inserted = insert_data(target_col, data, None, next_pk, dry_run=True)
    else:
        inserted = insert_data(target_col, data, embeddings, next_pk, dry_run=False)

    # 완료
    print("\n" + "=" * 60)
    print(f"  마이그레이션 {'예정' if args.dry_run else '완료'}: {inserted}개 청크")
    print("=" * 60)

    connections.disconnect("default")


if __name__ == "__main__":
    main()
