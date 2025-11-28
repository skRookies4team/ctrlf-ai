import os
import glob
from pathlib import Path
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
 
BASE_DIR = Path(__file__).parent
DOCUMENT_PATH = BASE_DIR / "docs"
DB_PATH = BASE_DIR / "chroma_db"

def load_all_md_files():
    files = list((DOCUMENT_PATH).glob("*.md"))
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
        collection_name="company_docs",
        persist_directory=str(DB_PATH)
    )

    db.persist()
    print("✅ Chroma DB 저장 완료:", str(DB_PATH))

if __name__ == "__main__":
    store_embeddings()
