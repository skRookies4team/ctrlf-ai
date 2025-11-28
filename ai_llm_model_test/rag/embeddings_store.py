import os
import glob
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma

DOCUMENT_PATH = "./ai_llm_model_test/rag/docs"
DB_PATH = "./ai_llm_model_test/rag/chroma_db"

def load_all_md_files():
    files = glob.glob(os.path.join(DOCUMENT_PATH, "*.md"))
    docs = []

    for file in files:
        with open(file, "r", encoding="utf-8") as f:
            docs.append(f.read())

    return docs

def store_embeddings():
    print("📌 문서 로딩 중...")
    texts = load_all_md_files()

    print(f"총 {len(texts)}개 문서 로드 완료")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=100
    )

    split_docs = splitter.split_text("\n\n".join(texts))

    print(f"총 {len(split_docs)}개 청크로 분할 완료")

    embedding_model = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-mpnet-base-v2"
    )

    print("🔄 Chroma DB 빌드 중...")

    db = Chroma.from_texts(
        texts=split_docs,
        embedding=embedding_model,
        persist_directory=DB_PATH
    )

    db.persist()
    print("✅ Chroma DB 저장 완료:", DB_PATH)

if __name__ == "__main__":
    store_embeddings()
