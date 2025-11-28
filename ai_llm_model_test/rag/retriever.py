import chromadb
from pathlib import Path
from langchain_community.embeddings import HuggingFaceEmbeddings
 
# === ChromaDB 로컬 storage ===
BASE_DIR = Path(__file__).parent
DB_DIR = str(BASE_DIR / "chroma_db")
 
# Persistent Chroma client pointing to the same directory used by embeddings_store
chroma_client = chromadb.PersistentClient(path=DB_DIR)
 
# Must match the collection_name used in embeddings_store.Chroma.from_texts
collection = chroma_client.get_or_create_collection(
    name="company_docs",
    metadata={"hnsw:space": "cosine"}
)
 
# === 🔥  임베딩 함수 (HF 임베딩으로 인덱스와 동일한 모델을 사용) ===
_embedding_model = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-mpnet-base-v2"
)
 
def embed_text(text: str):
    """쿼리를 임베딩으로 변환 (index와 동일한 임베딩 모델 사용)"""
    return _embedding_model.embed_query(text)


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
