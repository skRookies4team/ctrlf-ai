"""
임베딩 인덱스 빌더

다양한 임베딩 모델로 동일한 문서를 처리하여 각각 별도 FAISS 인덱스 생성

사용법:
    python experiments/embedding_eval/build_indexes.py --provider dummy
    python experiments/embedding_eval/build_indexes.py --provider qwen_06b
    python experiments/embedding_eval/build_indexes.py --provider qwen_15b

출력:
    experiments/embedding_eval/indexes/{provider}/
        ├── faiss.index
        └── metadata.jsonl
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import List

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.parser import extract_text_from_file
from core.cleaner import clean_text
from core.structure import apply_structure
from core.chunker import chunk_text, chunk_by_paragraphs, chunk_by_headings
from core.embedder import embed_texts
from core.vector_store import FAISSVectorStore

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def process_document(
    file_path: str,
    chunk_strategy: str = "character_window",
    max_chars: int = 1000,
    overlap_chars: int = 200
) -> List[str]:
    """
    문서를 처리하여 청크 리스트 반환

    CTRLF-AI의 동일한 전처리/청킹 로직 사용
    """
    logger.info(f"Processing {file_path}")

    # 1. 텍스트 추출
    raw_text = extract_text_from_file(file_path)

    # 2. 클리닝
    cleaned = clean_text(raw_text)

    # 3. 청킹
    if chunk_strategy == "character_window":
        chunks = chunk_text(cleaned, max_chars=max_chars, overlap_chars=overlap_chars)
    elif chunk_strategy == "paragraph_based":
        sections = apply_structure(cleaned)
        chunks = chunk_by_paragraphs(sections, max_chars=max_chars, overlap_sections=1)
    elif chunk_strategy == "heading_based":
        sections = apply_structure(cleaned)
        chunks = chunk_by_headings(sections, max_chars=max_chars)
    else:
        raise ValueError(f"Unknown chunk_strategy: {chunk_strategy}")

    logger.info(f"Generated {len(chunks)} chunks from {file_path}")
    return chunks


def build_index(
    data_dir: str,
    output_dir: str,
    provider: str = "dummy",
    chunk_strategy: str = "character_window",
    max_chars: int = 1000,
    overlap_chars: int = 200
):
    """
    문서들을 처리하여 FAISS 인덱스 생성

    Args:
        data_dir: 문서가 있는 디렉토리
        output_dir: 인덱스를 저장할 디렉토리
        provider: 임베딩 제공자 (dummy, qwen_06b, qwen_15b 등)
        chunk_strategy: 청킹 전략
        max_chars: 최대 청크 크기
        overlap_chars: 청크 겹침
    """
    logger.info(f"Building index with provider: {provider}")
    logger.info(f"Data directory: {data_dir}")
    logger.info(f"Output directory: {output_dir}")

    # 출력 디렉토리 생성
    os.makedirs(output_dir, exist_ok=True)

    # 벡터 스토어 초기화
    vector_store = FAISSVectorStore(dim=384)

    # data_dir의 모든 파일 찾기
    data_path = Path(data_dir)
    files = list(data_path.glob("*.pdf")) + list(data_path.glob("*.hwp"))

    if not files:
        logger.warning(f"No files found in {data_dir}")
        return

    logger.info(f"Found {len(files)} files")

    # 각 파일 처리
    for file_path in files:
        try:
            file_name = file_path.name
            logger.info(f"Processing: {file_name}")

            # 청크 생성
            chunks = process_document(
                str(file_path),
                chunk_strategy=chunk_strategy,
                max_chars=max_chars,
                overlap_chars=overlap_chars
            )

            if not chunks:
                logger.warning(f"No chunks generated for {file_name}")
                continue

            # 임베딩 생성
            logger.info(f"Generating embeddings for {len(chunks)} chunks")
            vectors = embed_texts(chunks)

            # 메타데이터 생성
            metadatas = [
                {
                    "file_name": file_name,
                    "chunk_index": i,
                    "text": chunk,
                    "strategy": chunk_strategy,
                    "provider": provider
                }
                for i, chunk in enumerate(chunks)
            ]

            # 벡터 스토어에 추가
            vector_store.add_vectors(vectors, metadatas)
            logger.info(f"Added {len(vectors)} vectors from {file_name}")

        except Exception as e:
            logger.error(f"Error processing {file_path}: {e}", exc_info=True)
            continue

    # 인덱스 저장
    index_path = os.path.join(output_dir, "faiss.index")
    metadata_path = os.path.join(output_dir, "metadata.jsonl")

    vector_store.save_index(index_path)
    logger.info(f"Index saved to: {index_path}")
    logger.info(f"Metadata saved to: {metadata_path}")

    # 통계 출력
    stats = vector_store.get_stats()
    logger.info(f"Total vectors: {stats['total_vectors']}")
    logger.info(f"Index built successfully!")


def main():
    parser = argparse.ArgumentParser(description="Build FAISS index for embedding evaluation")
    parser.add_argument(
        "--provider",
        type=str,
        default="dummy",
        choices=["dummy", "qwen_06b", "qwen_15b"],
        help="Embedding provider to use"
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data/files",
        help="Directory containing documents"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory for index (default: experiments/embedding_eval/indexes/{provider})"
    )
    parser.add_argument(
        "--chunk-strategy",
        type=str,
        default="character_window",
        choices=["character_window", "paragraph_based", "heading_based"],
        help="Chunking strategy"
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=1000,
        help="Maximum chunk size"
    )
    parser.add_argument(
        "--overlap-chars",
        type=int,
        default=200,
        help="Chunk overlap"
    )

    args = parser.parse_args()

    # 기본 출력 디렉토리 설정
    if args.output_dir is None:
        args.output_dir = os.path.join(
            PROJECT_ROOT,
            "experiments",
            "embedding_eval",
            "indexes",
            args.provider
        )

    # 인덱스 빌드
    build_index(
        data_dir=os.path.join(PROJECT_ROOT, args.data_dir),
        output_dir=args.output_dir,
        provider=args.provider,
        chunk_strategy=args.chunk_strategy,
        max_chars=args.max_chars,
        overlap_chars=args.overlap_chars
    )


if __name__ == "__main__":
    main()
