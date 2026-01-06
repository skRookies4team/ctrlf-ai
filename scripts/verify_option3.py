"""
선택지 3 검증: Milvus에서 text 직접 조회 가능 여부

실행: python scripts/verify_option3.py

.env 파일에서 환경변수를 자동으로 로드합니다.
"""
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# .env 파일 로드
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

from pymilvus import connections, Collection, utility

# 설정 (.env에서 로드)
HOST = os.getenv("MILVUS_HOST", "localhost")
PORT = os.getenv("MILVUS_PORT", "19530")
COLLECTION = os.getenv("MILVUS_COLLECTION_NAME", os.getenv("MILVUS_COLLECTION", "ragflow_chunks_openai"))


def main():
    print("=" * 60)
    print("  선택지 3 검증: Milvus text 직접 조회")
    print("=" * 60)

    results = None
    field_names = []

    # 1. 연결
    print(f"\n[1] Milvus 연결: {HOST}:{PORT}")
    try:
        connections.connect("default", host=HOST, port=int(PORT))
        print("✅ 연결 성공")
    except Exception as e:
        print(f"❌ 연결 실패: {e}")
        return

    # 2. 컬렉션 목록
    collections = utility.list_collections()
    print(f"\n[2] 컬렉션 목록: {collections}")

    if COLLECTION not in collections:
        print(f"❌ '{COLLECTION}' 컬렉션이 없습니다!")
        if collections:
            print(f"   사용 가능한 컬렉션: {collections}")
        connections.disconnect("default")
        return

    # 3. 스키마 출력
    col = Collection(COLLECTION)
    col.load()
    print(f"\n[3] 스키마 ({COLLECTION}):")
    embedding_dim = None
    for field in col.schema.fields:
        info = f"   - {field.name}: {field.dtype.name}"
        if hasattr(field, 'dim') and field.dim:
            info += f" (dim={field.dim})"
            embedding_dim = field.dim
        if hasattr(field, 'max_length') and field.max_length:
            info += f" (max_length={field.max_length})"
        if field.is_primary:
            info += " [PK]"
        print(info)

    print(f"\n   총 엔티티: {col.num_entities}")
    if embedding_dim:
        print(f"   임베딩 차원: {embedding_dim}")

    # 4. 필수 필드 존재 여부
    field_names = [f.name for f in col.schema.fields]
    print(f"\n[4] 필수 필드 확인:")

    checks = {
        "text": "text 필드 (청크 원문)",
        "chunk_id": "chunk_id 필드 (순서 정보)",
        "doc_id": "doc_id 필드 (문서 ID)",
        "dataset_id": "dataset_id 필드 (도메인 필터)",
        "embedding": "embedding 필드 (벡터)",
    }

    for field, desc in checks.items():
        exists = field in field_names
        print(f"   {'✅' if exists else '❌'} {desc}: {'있음' if exists else '없음'}")

    # 5. 샘플 조회 (text 포함)
    print(f"\n[5] 샘플 데이터 조회 (text 포함):")
    try:
        # chunk_id 타입에 따라 expr 조정
        if 'chunk_id' in field_names:
            results = col.query(
                expr="chunk_id >= 0",
                output_fields=["dataset_id", "doc_id", "chunk_id", "text"],
                limit=3
            )
        else:
            # chunk_id가 없으면 pk로 조회
            results = col.query(
                expr="pk >= 0",
                output_fields=["dataset_id", "doc_id", "text"] if "text" in field_names else ["dataset_id", "doc_id"],
                limit=3
            )

        if not results:
            print("   ⚠️ 데이터가 없습니다")
        else:
            for i, r in enumerate(results, 1):
                text = r.get("text", "")
                print(f"\n   [{i}] doc_id={r.get('doc_id', 'N/A')}")
                print(f"       chunk_id={r.get('chunk_id', 'N/A')}")
                print(f"       dataset_id={r.get('dataset_id', 'N/A')}")
                print(f"       text 길이: {len(text)} chars")
                if text:
                    preview = text[:200].replace('\n', ' ')
                    print(f"       text 미리보기: {preview}...")
                else:
                    print(f"       text: [비어있음]")

    except Exception as e:
        print(f"   ❌ 조회 실패: {e}")
        import traceback
        traceback.print_exc()

    # 6. doc_id로 전체 청크 조회 (스크립트 생성용)
    if results and results[0].get("doc_id"):
        target_doc = results[0].get("doc_id")
        print(f"\n[6] doc_id='{target_doc[:30]}...' 전체 청크 조회 (스크립트 생성용):")
        try:
            chunks = col.query(
                expr=f'doc_id == "{target_doc}"',
                output_fields=["chunk_id", "text"] if "text" in field_names else ["chunk_id"],
                limit=100
            )
            print(f"   총 {len(chunks)}개 청크")

            if chunks and 'chunk_id' in chunks[0]:
                sorted_chunks = sorted(chunks, key=lambda x: x.get("chunk_id", 0))
                chunk_ids = [c.get('chunk_id') for c in sorted_chunks[:10]]
                print(f"   ✅ chunk_id 기반 정렬 가능: {chunk_ids}...")

                # text 길이 통계
                if 'text' in field_names:
                    text_lens = [len(c.get('text', '')) for c in chunks]
                    print(f"   text 길이 - 평균: {sum(text_lens)/len(text_lens):.0f}, 최소: {min(text_lens)}, 최대: {max(text_lens)}")
            else:
                print(f"   ⚠️ chunk_id가 없어 정렬 불가")

        except Exception as e:
            print(f"   ❌ 조회 실패: {e}")

    # 결론
    print("\n" + "=" * 60)
    print("  📋 선택지 3 검증 결과")
    print("=" * 60)

    text_exists = 'text' in field_names
    text_has_data = results and results[0].get("text") if results else False
    chunk_id_exists = 'chunk_id' in field_names

    print(f"\n   [필수1] text 필드 존재: {'✅' if text_exists else '❌'}")
    print(f"   [필수2] text에 데이터 있음: {'✅' if text_has_data else '❌'}")
    print(f"   [필수3] chunk_id 순서 필드: {'✅' if chunk_id_exists else '❌'}")

    if text_exists and text_has_data:
        print("\n   🎯 결론: 선택지 3 사용 가능!")
        print("   → Milvus에서 text 직접 조회 가능")
        print("   → Spring 읽기 API 불필요")
        if chunk_id_exists:
            print("   → 영상 스크립트 생성도 가능 (chunk_id로 순서 정렬)")
        else:
            print("   → 영상 스크립트는 retrieval 기반으로 제한")
    else:
        print("\n   ❌ 결론: 선택지 3 사용 불가")
        if not text_exists:
            print("   → text 필드가 스키마에 없음")
        elif not text_has_data:
            print("   → text 필드는 있으나 데이터가 비어있음")

    print("\n" + "=" * 60)

    connections.disconnect("default")


if __name__ == "__main__":
    main()
