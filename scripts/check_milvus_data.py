"""
Milvus 데이터 확인 스크립트
사용자가 언급한 보안 규정 관련 데이터가 실제로 존재하는지 확인
"""

import asyncio
import sys
import os
import io

# Windows 콘솔 UTF-8 설정
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymilvus import connections, Collection, utility


def print_header(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


async def main():
    # Milvus 연결
    milvus_host = os.getenv("MILVUS_HOST", "58.127.241.84")
    milvus_port = int(os.getenv("MILVUS_PORT", "19540"))
    collection_name = os.getenv("MILVUS_COLLECTION_NAME", "ragflow_chunks")

    print(f"Milvus 연결: {milvus_host}:{milvus_port}")

    try:
        connections.connect(
            alias="default",
            host=milvus_host,
            port=milvus_port,
        )
        print("[OK] Milvus 연결 성공!")

        # 컬렉션 목록 확인
        collections = utility.list_collections()
        print(f"\n존재하는 컬렉션: {collections}")

        if collection_name not in collections:
            print(f"[ERROR] '{collection_name}' 컬렉션이 없습니다.")
            return

        coll = Collection(collection_name)
        coll.load()

        print(f"\n컬렉션: {collection_name}")
        print(f"총 문서 수: {coll.num_entities}")

        # 스키마 확인
        print_header("스키마 정보")
        for field in coll.schema.fields:
            print(f"  - {field.name}: {field.dtype.name}")

        # 1. 전체 dataset_id 목록 확인
        print_header("1. 모든 dataset_id 확인")
        try:
            results = coll.query(
                expr="doc_id != ''",
                output_fields=["dataset_id"],
                limit=10000
            )
            dataset_ids = set()
            for r in results:
                if "dataset_id" in r and r["dataset_id"]:
                    dataset_ids.add(r["dataset_id"])
            print(f"발견된 dataset_id 목록: {sorted(dataset_ids)}")
        except Exception as e:
            print(f"dataset_id 조회 실패: {e}")

        # 2. 사용자가 언급한 문서들 검색
        print_header("2. 특정 문서 검색")

        search_keywords = [
            "접근권한",
            "제5조",
            "비밀번호 정책",
            "외부 챗봇",
            "AI 챗봇 사용 안내",
            "법령위반",
            "개인정보_보호법_위반",
            "개인정보 유출",
            "전산망 해킹",
            "이메일 변조",
            "내부 정보 유출",
            "퇴직자",
            "INCIDENT",
            "TOP5",
            "위반된 보안 규정"
        ]

        for keyword in search_keywords:
            print(f"\n검색어: '{keyword}'")
            try:
                # text 필드에서 검색
                expr = f'text like "%{keyword}%"'
                results = coll.query(
                    expr=expr,
                    output_fields=["text", "doc_id", "dataset_id"],
                    limit=3
                )
                if results:
                    print(f"  [OK] {len(results)}건 발견")
                    for r in results[:2]:
                        text_preview = r.get("text", "")[:150].replace("\n", " ")
                        print(f"      dataset_id: {r.get('dataset_id', 'N/A')}")
                        print(f"      doc_id: {r.get('doc_id', 'N/A')[:50]}...")
                        print(f"      text: {text_preview}...")
                else:
                    print(f"  [X] 결과 없음")
            except Exception as e:
                print(f"  [ERROR] 검색 실패: {e}")

        # 3. 사내규정 데이터셋에서 샘플 확인
        print_header("3. '사내규정' 데이터셋 샘플")
        try:
            results = coll.query(
                expr='dataset_id == "사내규정"',
                output_fields=["text", "doc_id", "dataset_id"],
                limit=10
            )
            print(f"사내규정 데이터 수: {len(results)}건 (최대 10건 표시)")
            for i, r in enumerate(results, 1):
                text_preview = r.get("text", "")[:200].replace("\n", " ")
                print(f"\n[{i}] doc_id: {r.get('doc_id', 'N/A')[:60]}...")
                print(f"    text: {text_preview}...")
        except Exception as e:
            print(f"사내규정 조회 실패: {e}")

        # 4. 정보보안교육 데이터셋에서 샘플 확인
        print_header("4. '정보보안교육' 데이터셋 샘플")
        try:
            results = coll.query(
                expr='dataset_id == "정보보안교육"',
                output_fields=["text", "doc_id", "dataset_id"],
                limit=10
            )
            print(f"정보보안교육 데이터 수: {len(results)}건 (최대 10건 표시)")
            for i, r in enumerate(results, 1):
                text_preview = r.get("text", "")[:200].replace("\n", " ")
                print(f"\n[{i}] doc_id: {r.get('doc_id', 'N/A')[:60]}...")
                print(f"    text: {text_preview}...")
        except Exception as e:
            print(f"정보보안교육 조회 실패: {e}")

        # 5. 모든 고유 doc_id 목록 (제한적)
        print_header("5. 문서 목록 (doc_id)")
        try:
            results = coll.query(
                expr="doc_id != ''",
                output_fields=["doc_id", "dataset_id"],
                limit=10000
            )
            doc_ids = {}
            for r in results:
                doc_id = r.get("doc_id", "N/A")
                dataset_id = r.get("dataset_id", "N/A")
                if doc_id not in doc_ids:
                    doc_ids[doc_id] = dataset_id

            print(f"총 고유 문서 수: {len(doc_ids)}개")
            print("\n문서 목록:")
            for doc_id, dataset_id in list(doc_ids.items())[:30]:
                print(f"  [{dataset_id}] {doc_id[:80]}...")
        except Exception as e:
            print(f"doc_id 목록 조회 실패: {e}")

        connections.disconnect("default")

    except Exception as e:
        print(f"[FAIL] 오류 발생: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
