"""
FAQ Draft Service (Phase 18)

FAQ 후보 클러스터를 기반으로 FAQ 초안을 생성하는 서비스.
RAG + LLM을 사용하여 질문/답변/근거 문서를 생성합니다.
"""

import json
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from app.clients.llm_client import LLMClient
from app.clients.ragflow_client import RagflowClient
from app.core.logging import get_logger
from app.models.faq import (
    FaqDraft,
    FaqDraftGenerateRequest,
    FaqSourceDoc,
)
from app.models.rag import RagDocument

logger = get_logger(__name__)


# =============================================================================
# LLM 프롬프트 템플릿
# =============================================================================

SYSTEM_PROMPT = """너는 사내 보안/사규/교육 FAQ를 만드는 어시스턴트다.

주어진 대표 질문과 실제 직원 질문 예시, 정책/사규 문서 발췌(snippet)를 바탕으로 공식 FAQ 초안을 작성하라.

작성 원칙:
1. 질문(question)은 명확하고 간결하게 작성
2. 답변(answer_markdown)은 다음 구조로 작성:
   - 첫 줄: 핵심 내용 한 줄 요약
   - 이후: 상세 설명 (bullet 2~3개)
   - 필요 시 추가 참고사항
3. 문서에서 근거를 찾을 수 없으면 일반적인 가이드 제공
4. 정확한 정보만 제공하고, 확실하지 않은 내용은 포함하지 않음

중요: 반드시 아래 JSON 형식으로만 응답하라. 다른 텍스트 없이 JSON만 출력하라.

응답 JSON 형식:
{
  "question": "최종 FAQ 질문 문구",
  "answer_markdown": "**한 줄 요약**\\n\\n- 상세 설명 1\\n- 상세 설명 2\\n- 상세 설명 3",
  "summary": "한 줄 요약 텍스트",
  "source_doc_id": "근거 문서 ID (없으면 null)",
  "source_doc_version": "근거 문서 버전 (없으면 null)",
  "source_article_label": "근거 조항 라벨 (없으면 null)",
  "source_article_path": "근거 조항 경로 (없으면 null)",
  "answer_source": "AI_RAG",
  "ai_confidence": 0.85
}
"""

USER_PROMPT_TEMPLATE = """## 도메인
{domain}

## 대표 질문
{canonical_question}

## 실제 직원 질문 예시
{sample_questions_text}

## 참고 문서 발췌
{docs_text}

위 정보를 바탕으로 FAQ 초안을 JSON 형식으로 작성해 주세요.
"""


class FaqGenerationError(Exception):
    """FAQ 생성 중 발생한 에러"""

    pass


class FaqDraftService:
    """
    FAQ 초안 생성 서비스.

    백엔드에서 전달한 FAQ 후보 클러스터 정보를 기반으로
    RAG + LLM을 사용하여 FAQ 초안을 생성합니다.

    Attributes:
        _rag_client: RAGFlow 클라이언트
        _llm: LLM 클라이언트

    Example:
        service = FaqDraftService()
        draft = await service.generate_faq_draft(request)
    """

    def __init__(
        self,
        rag_client: Optional[RagflowClient] = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        """
        FaqDraftService 초기화.

        Args:
            rag_client: RAGFlow 클라이언트. None이면 새로 생성.
            llm_client: LLM 클라이언트. None이면 새로 생성.
        """
        self._rag_client = rag_client or RagflowClient()
        self._llm = llm_client or LLMClient()

    async def generate_faq_draft(
        self,
        req: FaqDraftGenerateRequest,
    ) -> FaqDraft:
        """
        FAQ 초안을 생성합니다.

        Args:
            req: FAQ 초안 생성 요청

        Returns:
            FaqDraft: 생성된 FAQ 초안

        Raises:
            FaqGenerationError: FAQ 생성 실패 시
        """
        logger.info(
            f"Generating FAQ draft: domain={req.domain}, "
            f"cluster_id={req.cluster_id}, question='{req.canonical_question[:50]}...'"
        )

        # 1. RAG 문서 확보
        source_docs = await self._get_source_docs(req)
        logger.info(f"Got {len(source_docs)} source documents")

        # 2. LLM 메시지 구성
        messages = self._build_llm_messages(req, source_docs)

        # 3. LLM 호출
        try:
            llm_response = await self._llm.generate_chat_completion(
                messages=messages,
                model=None,
                temperature=0.3,
                max_tokens=2048,
            )
            logger.debug(f"LLM response: {llm_response[:500]}...")

        except Exception as e:
            logger.exception(f"LLM call failed: {e}")
            raise FaqGenerationError(f"LLM 호출 실패: {type(e).__name__}: {str(e)}")

        # 4. 응답 파싱
        try:
            parsed = self._parse_llm_response(llm_response)
        except Exception as e:
            logger.exception(f"LLM response parsing failed: {e}")
            raise FaqGenerationError(f"LLM 응답 파싱 실패: {str(e)}")

        # 5. FaqDraft 생성
        draft = FaqDraft(
            faq_draft_id=f"FAQ-{req.cluster_id}-{uuid.uuid4().hex[:8]}",
            domain=req.domain,
            cluster_id=req.cluster_id,
            question=parsed.get("question", req.canonical_question),
            answer_markdown=parsed.get("answer_markdown", ""),
            summary=parsed.get("summary"),
            source_doc_id=parsed.get("source_doc_id"),
            source_doc_version=parsed.get("source_doc_version"),
            source_article_label=parsed.get("source_article_label"),
            source_article_path=parsed.get("source_article_path"),
            answer_source=self._normalize_answer_source(
                parsed.get("answer_source", "AI_RAG")
            ),
            ai_confidence=self._normalize_confidence(parsed.get("ai_confidence")),
            created_at=datetime.now(timezone.utc),
        )

        logger.info(
            f"FAQ draft generated: id={draft.faq_draft_id}, "
            f"source={draft.answer_source}, confidence={draft.ai_confidence}"
        )

        return draft

    async def _get_source_docs(
        self,
        req: FaqDraftGenerateRequest,
    ) -> List[FaqSourceDoc]:
        """
        FAQ 생성에 사용할 소스 문서를 확보합니다.

        Args:
            req: FAQ 초안 생성 요청

        Returns:
            List[FaqSourceDoc]: 소스 문서 리스트
        """
        # req.top_docs가 있으면 그대로 사용
        if req.top_docs:
            logger.info(f"Using {len(req.top_docs)} provided top_docs")
            return req.top_docs

        # 없으면 RAG 검색
        logger.info(f"Searching RAG for: '{req.canonical_question[:50]}...'")
        try:
            rag_docs = await self._rag_client.search(
                query=req.canonical_question,
                top_k=5,
                dataset=req.domain,
            )
            return [self._rag_doc_to_faq_source(doc) for doc in rag_docs]

        except Exception as e:
            logger.warning(f"RAG search failed, continuing without docs: {e}")
            return []

    def _rag_doc_to_faq_source(self, doc: RagDocument) -> FaqSourceDoc:
        """RagDocument를 FaqSourceDoc으로 변환합니다."""
        return FaqSourceDoc(
            doc_id=doc.doc_id,
            doc_version=None,
            title=doc.title,
            snippet=doc.snippet,
            article_label=doc.section_label,
            article_path=doc.section_path,
        )

    def _build_llm_messages(
        self,
        req: FaqDraftGenerateRequest,
        source_docs: List[FaqSourceDoc],
    ) -> List[dict]:
        """
        LLM 호출용 메시지를 구성합니다.

        Args:
            req: FAQ 초안 생성 요청
            source_docs: 소스 문서 리스트

        Returns:
            LLM 메시지 목록
        """
        # 샘플 질문 포맷
        if req.sample_questions:
            sample_questions_text = "\n".join(
                f"- {q}" for q in req.sample_questions[:5]
            )
        else:
            sample_questions_text = "(없음)"

        # 문서 발췌 포맷
        if source_docs:
            docs_lines = []
            for i, doc in enumerate(source_docs[:3], start=1):
                doc_info = f"### 문서 {i}: {doc.title or '제목 없음'}"
                if doc.article_label:
                    doc_info += f" ({doc.article_label})"
                if doc.snippet:
                    doc_info += f"\n{doc.snippet[:500]}"
                docs_lines.append(doc_info)
            docs_text = "\n\n".join(docs_lines)
        else:
            docs_text = "(관련 문서를 찾지 못했습니다. 일반적인 가이드를 제공해 주세요.)"

        # User 메시지 생성
        user_message = USER_PROMPT_TEMPLATE.format(
            domain=req.domain,
            canonical_question=req.canonical_question,
            sample_questions_text=sample_questions_text,
            docs_text=docs_text,
        )

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

    def _parse_llm_response(self, llm_response: str) -> dict:
        """
        LLM 응답을 파싱합니다.

        Args:
            llm_response: LLM 응답 텍스트

        Returns:
            파싱된 딕셔너리

        Raises:
            FaqGenerationError: 파싱 실패 시
        """
        # JSON 추출
        json_str = self._extract_json_from_response(llm_response)

        if not json_str:
            raise FaqGenerationError("LLM 응답에서 JSON을 찾을 수 없습니다")

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise FaqGenerationError(f"JSON 파싱 실패: {e}")

        # 필수 필드 검증
        if not data.get("question"):
            raise FaqGenerationError("필수 필드 'question'이 없습니다")
        if not data.get("answer_markdown"):
            raise FaqGenerationError("필수 필드 'answer_markdown'이 없습니다")

        return data

    def _extract_json_from_response(self, response: str) -> Optional[str]:
        """
        LLM 응답에서 JSON 문자열을 추출합니다.

        Args:
            response: LLM 응답 텍스트

        Returns:
            JSON 문자열 또는 None
        """
        # 방법 1: ```json ... ``` 블록에서 추출
        json_block_pattern = r"```(?:json)?\s*\n?([\s\S]*?)\n?```"
        match = re.search(json_block_pattern, response)
        if match:
            return match.group(1).strip()

        # 방법 2: { ... } 패턴에서 추출
        brace_pattern = r"\{[\s\S]*\}"
        match = re.search(brace_pattern, response)
        if match:
            return match.group(0).strip()

        return None

    def _normalize_answer_source(
        self, source: Optional[str]
    ) -> str:
        """answer_source를 정규화합니다."""
        valid_sources = {"AI_RAG", "LOG_REUSE", "MIXED"}
        if source and source.upper() in valid_sources:
            return source.upper()
        return "AI_RAG"

    def _normalize_confidence(
        self, confidence: Optional[float]
    ) -> Optional[float]:
        """ai_confidence를 정규화합니다."""
        if confidence is None:
            return None
        try:
            val = float(confidence)
            return max(0.0, min(1.0, val))
        except (TypeError, ValueError):
            return None
