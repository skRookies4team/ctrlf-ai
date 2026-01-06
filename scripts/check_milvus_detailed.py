"""
Milvus 상세 데이터 확인 스크립트
"""

import asyncio
import sys
import os
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymilvus import connections, Collection


def print_header(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


async def main():
    milvus_host = "58.127.241.84"
    milvus_port = 19540
    collection_name = "ragflow_chunks"

    connections.connect(alias="default", host=milvus_host, port=milvus_port)
    coll = Collection(collection_name)
    coll.load()

    # 1. 법령위반_사례과정 문서 전체 내용 확인
    print_header("1. '법령위반_사례과정_개인정보_보호법_위반사례.pdf' 전체 청크")
    try:
        results = coll.query(
            expr='doc_id like "%법령위반%"',
            output_fields=["text", "doc_id", "chunk_id"],
            limit=50
        )
        print(f"총 {len(results)}개 청크 발견")
        for r in sorted(results, key=lambda x: x.get('chunk_id', 0)):
            chunk_id = r.get('chunk_id', 'N/A')
            text = r.get('text', '')[:300].replace("\n", " ")
            print(f"\n[청크 {chunk_id}]")
            print(f"{text}...")
    except Exception as e:
        print(f"오류: {e}")

    # 2. 사내규정에서 접근권한 관련 내용
    print_header("2. '사내규정'에서 '접근' 또는 '비밀번호' 검색")
    try:
        results = coll.query(
            expr='dataset_id == "사내규정" and (text like "%접근%" or text like "%비밀번호%")',
            output_fields=["text", "doc_id", "chunk_id"],
            limit=20
        )
        print(f"총 {len(results)}개 청크 발견")
        for r in results:
            chunk_id = r.get('chunk_id', 'N/A')
            text = r.get('text', '')[:400].replace("\n", " ")
            print(f"\n[청크 {chunk_id}]")
            print(f"{text}...")
    except Exception as e:
        print(f"오류: {e}")

    # 3. INCIDENT 관련 검색
    print_header("3. 'INCIDENT' 또는 '사고' 관련 검색")
    try:
        results = coll.query(
            expr='text like "%사고%"',
            output_fields=["text", "doc_id", "dataset_id"],
            limit=10
        )
        print(f"'사고' 관련: {len(results)}개 발견")
        for r in results[:5]:
            text = r.get('text', '')[:200].replace("\n", " ")
            print(f"\n[{r.get('dataset_id')}] {r.get('doc_id', 'N/A')[:50]}...")
            print(f"{text}...")
    except Exception as e:
        print(f"오류: {e}")

    # 4. 위반 사례 관련 검색
    print_header("4. '위반' 관련 검색")
    try:
        results = coll.query(
            expr='text like "%위반%"',
            output_fields=["text", "doc_id", "dataset_id"],
            limit=15
        )
        print(f"'위반' 관련: {len(results)}개 발견")
        for r in results:
            text = r.get('text', '')[:200].replace("\n", " ")
            print(f"\n[{r.get('dataset_id')}] {r.get('doc_id', 'N/A')[:50]}...")
            print(f"{text}...")
    except Exception as e:
        print(f"오류: {e}")

    # 5. TOP 5 또는 통계 관련
    print_header("5. '통계' 또는 '순위' 또는 '빈도' 검색")
    try:
        results = coll.query(
            expr='text like "%통계%" or text like "%순위%" or text like "%빈도%"',
            output_fields=["text", "doc_id", "dataset_id"],
            limit=10
        )
        print(f"통계/순위/빈도 관련: {len(results)}개 발견")
        for r in results:
            text = r.get('text', '')[:200].replace("\n", " ")
            print(f"\n[{r.get('dataset_id')}] {r.get('doc_id', 'N/A')[:50]}...")
            print(f"{text}...")
    except Exception as e:
        print(f"오류: {e}")

    connections.disconnect("default")


if __name__ == "__main__":
    asyncio.run(main())
