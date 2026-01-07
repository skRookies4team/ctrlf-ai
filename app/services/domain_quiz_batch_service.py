from app.services.quiz_generate_service import QuizGenerateService
from app.repositories.milvus_quiz_repository import MilvusQuizRepository
from app.models.quiz_generate import QuizGenerateRequest

class DomainQuizBatchService:
    def __init__(
        self,
        milvus_repo: MilvusQuizRepository,
        quiz_service: QuizGenerateService,
    ):
        self.milvus_repo = milvus_repo
        self.quiz_service = quiz_service

    async def generate_domain_quiz(
        self,
        dataset_id: str,
        num_questions: int = 20,
    ):
        blocks = self.milvus_repo.fetch_blocks_by_domain(
            dataset_id=dataset_id,
            limit=min(num_questions * 2, 60),
        )

        if not blocks:
            raise ValueError(f"No blocks found for dataset: {dataset_id}")

        request = QuizGenerateRequest(
            language="ko",
            num_questions=num_questions,
            max_options=4,
            quiz_candidate_blocks=blocks,
            exclude_previous_questions=[],
        )

        return await self.quiz_service.generate_quiz(request)
