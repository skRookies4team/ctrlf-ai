import os
import glob
import chromadb
from chromadb.config import Settings
from dotenv import load_dotenv
from openai import OpenAI
from langchain.text_splitter import RecursiveCharacterTextSplitter

# === Load .env ===
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

# === ChromaDB Local Storage ===
DB_DIR = "./vector_db"

chroma_client = chromadb.Client(
    Settings(chroma_db_impl="duckdb+parquet", persist_directory=DB_DIR)
)

collection = chroma_client.get_or_create_collection(
    name="company_docs",
    metadata={"hnsw:space": "cosine"}  # cosine similarity
)

# === Document loader ===
DOCS_PATH = "./docs/*.md"   # 생성한 문서 위치
files = glob.glob(DOCS_PATH)

if len(files) == 0:
    raise FileNotFoundError("❌ 문서(.md)가 없습니다. ./docs/ 경로를 확인해주세요.")

print(f"📄 Total documents found: {len(files)}")

# === Chunking ===
splitter = RecursiveCharacterTextSplitter(
    chunk_size=600,
    chunk_overlap=80,
    separators=["\n\n", "\n", " ", ""]
)

def embed_text(text: str):
    """OpenAI Embedding 호출"""
    res = client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return res.data[0].embedding

# === Main Process: 문서 -> chunk -> embedding -> vectorDB 저장 ===
doc_id = 0
for file_path in files:
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    chunks = splitter.split_text(content)
    print(f"📌 {os.path.basename(file_path)} → Chunks: {len(chunks)}")

    for chunk in chunks:
        embedding = embed_text(chunk)

        collection.add(
            ids=[f"chunk-{doc_id}"],
            documents=[chunk],
            embeddings=[embedding]
        )
        doc_id += 1

# === Save ChromaDB ===
chroma_client.persist()

print("\n🎉 ChromaDB 저장 완료!")
print(f"➡ 저장 위치: {DB_DIR}")
print(f"➡ 총 청크 개수: {doc_id}")
