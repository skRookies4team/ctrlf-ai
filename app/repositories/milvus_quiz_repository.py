from typing import List
from pymilvus import Collection

from app.models.quiz_generate import QuizCandidateBlock

class MilvusQuizRepository:
    def __init__(self, collection_name: str):
        self.collection = Collection(collection_name)

    def fetch_blocks_by_domain(
        self,
        dataset_id: str,
        limit: int = 200,
    ) -> List[QuizCandidateBlock]:
        """
        특정 도메인(dataset_id)에서 퀴즈 후보 블록 조회
        (직무 제외)
        """
        expr = f'dataset_id == "{dataset_id}" and department != "직무"'

        results = self.collection.query(
            expr=expr,
            output_fields=[
                "doc_id",
                "chunk_id",
                "text",
                "section",
                "section_path",
                "document_title",
            ],
            limit=limit,
        )

        blocks: List[QuizCandidateBlock] = []

        for r in results:
            blocks.append(
                QuizCandidateBlock(
                    block_id=f'{r["doc_id"]}:{r["chunk_id"]}',
                    doc_id=r["doc_id"],
                    doc_version=None,
                    chapter_id=r.get("section"),
                    article_path=r.get("section_path"),
                    learning_objective_id=None,
                    tags=[dataset_id],
                    text=r["text"],
                )
            )

        return blocks
