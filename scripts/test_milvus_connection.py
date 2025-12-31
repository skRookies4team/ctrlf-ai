"""
Milvus 연결 테스트 스크립트

실행: python scripts/test_milvus_connection.py
"""

import asyncio
import sys
import os
import io

# Windows 콘솔 UTF-8 설정
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 환경변수 설정 (테스트용) - 실제 사용 시 .env 파일에서 로드
# 환경변수가 이미 설정되어 있으면 그대로 사용
os.environ.setdefault("MILVUS_ENABLED", "true")
os.environ.setdefault("MILVUS_HOST", os.getenv("MILVUS_HOST", "localhost"))
os.environ.setdefault("MILVUS_PORT", os.getenv("MILVUS_PORT", "19530"))
os.environ.setdefault("LLM_BASE_URL", os.getenv("LLM_BASE_URL", "http://localhost:8001"))


def print_header(title: str):
    print("\n" + "=" * 50)
    print(f"  {title}")
    print("=" * 50)


async def test_connection():
    """Milvus 연결 및 검색 테스트."""

    from pymilvus import connections, utility

    print_header("1. Milvus 서버 연결 테스트")

    try:
        # 연결 (환경변수에서 읽음)
        milvus_host = os.getenv("MILVUS_HOST", "localhost")
        milvus_port = int(os.getenv("MILVUS_PORT", "19530"))

        connections.connect(
            alias="default",
            host=milvus_host,
            port=milvus_port,
        )
        print("[OK] Milvus 서버 연결 성공!")

        # 컬렉션 목록 확인
        collections = utility.list_collections()
        print(f"\n[INFO] 존재하는 컬렉션 목록:")
        for coll in collections:
            print(f"   - {coll}")

        if not collections:
            print("   (컬렉션이 없습니다)")
            return

        # 첫 번째 컬렉션 정보 확인
        print_header("2. 컬렉션 스키마 확인")

        from pymilvus import Collection

        target_collection = "ctrlf_documents"
        if target_collection not in collections:
            print(f"[WARN] '{target_collection}' 컬렉션이 없습니다.")
            print(f"   존재하는 컬렉션 중 하나를 선택하세요: {collections}")
            target_collection = collections[0]
            print(f"   -> '{target_collection}' 사용")

        coll = Collection(target_collection)
        coll.load()

        print(f"\n컬렉션: {target_collection}")
        print(f"문서 수: {coll.num_entities}")
        print(f"\n필드 목록:")
        for field in coll.schema.fields:
            print(f"   - {field.name}: {field.dtype.name}")

        connections.disconnect("default")

    except Exception as e:
        print(f"[FAIL] 연결 실패: {e}")
        return


async def test_embedding():
    """vLLM 임베딩 서버 테스트."""

    print_header("3. vLLM 임베딩 서버 테스트")

    import httpx

    llm_base_url = os.getenv("LLM_BASE_URL", "http://localhost:8001")
    url = f"{llm_base_url.rstrip('/')}/v1/embeddings"
    payload = {
        "input": "테스트 문장입니다",
        "model": "BAAI/bge-m3",
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=10.0)

            print(f"   응답 코드: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                print(f"   응답 키: {list(data.keys())}")

                # OpenAI 호환 형식 또는 다른 형식 처리
                if "data" in data:
                    embedding = data["data"][0]["embedding"]
                elif "embedding" in data:
                    embedding = data["embedding"]
                elif "embeddings" in data:
                    embedding = data["embeddings"][0]
                else:
                    print(f"   [WARN] 알 수 없는 응답 형식: {str(data)[:300]}")
                    return

                print(f"[OK] 임베딩 생성 성공!")
                print(f"   차원: {len(embedding)}")
                print(f"   샘플: [{embedding[0]:.4f}, {embedding[1]:.4f}, ...]")
            else:
                print(f"[FAIL] 임베딩 실패: {response.status_code}")
                print(f"   {response.text[:300]}")

    except Exception as e:
        print(f"[FAIL] 임베딩 서버 연결 실패: {e}")
        import traceback
        traceback.print_exc()


async def test_search():
    """실제 검색 테스트."""

    print_header("4. 벡터 검색 테스트")

    # settings 캐시 클리어
    from app.core.config import clear_settings_cache
    clear_settings_cache()

    from app.clients.milvus_client import MilvusSearchClient, clear_milvus_client

    clear_milvus_client()

    try:
        client = MilvusSearchClient()

        # 헬스체크
        is_healthy = await client.health_check()
        print(f"헬스체크: {'[OK] 정상' if is_healthy else '[FAIL] 실패'}")

        if not is_healthy:
            print("   컬렉션이 없거나 연결에 문제가 있습니다.")
            return

        # 검색 테스트
        print("\n[SEARCH] 검색 쿼리: '연차휴가 규정'")

        results = await client.search(
            query="연차휴가 규정",
            domain=None,  # 도메인 필터 없이 전체 검색
            top_k=3,
        )

        print(f"\n검색 결과: {len(results)}건")

        for i, r in enumerate(results, 1):
            print(f"\n[{i}] score: {r.get('score', 'N/A'):.4f}")
            print(f"    title: {r.get('title', 'N/A')}")
            print(f"    domain: {r.get('domain', 'N/A')}")
            content = r.get('content', '')[:100]
            print(f"    content: {content}...")

        client.disconnect()

    except Exception as e:
        print(f"[FAIL] 검색 실패: {e}")
        import traceback
        traceback.print_exc()


async def main():
    print("\n" + " Milvus 연결 테스트 시작 ".center(50, "="))

    await test_connection()
    await test_embedding()
    await test_search()

    print("\n" + " 테스트 완료 ".center(50, "=") + "\n")


if __name__ == "__main__":
    asyncio.run(main())
