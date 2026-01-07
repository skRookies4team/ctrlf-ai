from typing import List
from pymilvus import Collection

from app.models.quiz_generate import QuizCandidateBlock


class MilvusQuizRepository:
    def __init__(self, collection_name: str):
        self.collection_name = collection_name
        self.collection = Collection(collection_name)

        field_names = {f.name for f in self.collection.schema.fields}
        self._has_department_field = "department" in field_names

    def fetch_blocks_by_domain(
        self,
        dataset_id: str,
        limit: int = 100,
    ) -> List[QuizCandidateBlock]:
        # 기본 필터
        expr = f'dataset_id == "{dataset_id}"'

        # department 필드가 있을 때만 제외
        if self._has_department_field:
            expr += ' and department != "직무"'

        results = self.collection.query(
            expr=expr,
            output_fields=[
                "doc_id",
                "chunk_id",
                "text",
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
                    chapter_id=None,          # ❌ section 제거
                    article_path=None,        # ❌ section_path 제거
                    learning_objective_id=None,
                    tags=[dataset_id],
                    text=r["text"],
                )
            )

        return blocks
