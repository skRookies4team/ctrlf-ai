import os
import chromadb
from chromadb.config import Settings
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

# === ChromaDB 로컬 storage ===
DB_DIR = "./vector_db"

chroma_client = chromadb.Client(
    Settings(chroma_db_impl="duckdb+parquet", persist_directory=DB_DIR)
)

collection = chroma_client.get_or_create_collection(
    name="company_docs",
    metadata={"hnsw:space": "cosine"}
)


# === 🔥  임베딩 함수  ===
def embed_text(text: str):
    """쿼리를 임베딩으로 변환"""
    res = client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return res.data[0].embedding


# === 🔥  Retriever: Similarity Search 함수 ===
def retrieve_context(query: str, top_k: int = 5):
    """
    입력 쿼리에 대해 ChromaDB에서 top-k 유사 문서 반환
    """
    query_emb = embed_text(query)

    results = collection.query(
        query_embeddings=[query_emb],
        n_results=top_k
    )

    documents = results["documents"][0]  # 2차원 배열 중 1번째
    scores = results["distances"][0]

    context = "\n---\n".join(documents)

    return {
        "context": context,
        "documents": documents,
        "scores": scores,
    }


# === 간단 테스트 ===
if __name__ == "__main__":
    query = "재택근무 신청 절차 알려줘"
    result = retrieve_context(query)

    print("\n📌 Top-K Retrieved Documents:")
    for i, (doc, score) in enumerate(zip(result["documents"], result["scores"])):
        print(f"\n[{i+1}] (distance={score:.4f})")
        print(doc)

    print("\n📌 Combined Context (RAG 입력용):\n")
    print(result["context"])
