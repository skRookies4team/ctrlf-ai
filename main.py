import os
from extractors import extract_text_from_pdf, extract_text_from_hwp
from nodes.preprocess_node import PreprocessNode
from nodes.embedding_node import EmbeddingNode
from nodes.similarity_node import SimilarityNode
from nodes.preprocess_node import log_step

DATA_DIR = "data"

def main():
    preprocess_node = PreprocessNode()
    embedding_node = EmbeddingNode()
    similarity_node = SimilarityNode()
    embeddings_data = []

    for filename in os.listdir(DATA_DIR):
        filepath = os.path.join(DATA_DIR, filename)
        ext = filename.lower().split(".")[-1]
        text = ""

        if ext == "pdf":
            text = extract_text_from_pdf(filepath)
        elif ext == "hwp":
            text = extract_text_from_hwp(filepath)
        elif ext == "txt":
            with open(filepath, "r", encoding="utf-8") as f:
                text = f.read()
        else:
            log_step("skipped_file", f"{filename} (지원되지 않는 형식)")
            continue

        if not text.strip():
            log_step("empty_file", filename)
            continue

        pre_text = preprocess_node.run(text)
        embedding = embedding_node.run(pre_text)
        embeddings_data.append({"file": filename, "text": pre_text, "embedding": embedding})

    similarity_node.run(embeddings_data)
    print(f"\n✅ 완료! 총 {len(embeddings_data)}개의 임베딩 생성 및 추적 완료.")

if __name__ == "__main__":
    main()
