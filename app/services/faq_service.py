"""
FAQ Draft Service (Phase 18 + Phase 19-AI-2)

FAQ 후보 클러스터를 기반으로 FAQ 초안을 생성하는 서비스.
RAG + LLM을 사용하여 질문/답변/근거 문서를 생성합니다.

Phase 19-AI-2 업데이트:
- RagflowSearchClient 연동 (/v1/chunk/search)
- NO_DOCS_FOUND 에러 처리
- RAGFlow 검색 결과 기반 컨텍스트 구성
"""

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from app.clients.llm_client import LLMClient
from app.clients.ragflow_search_client import (
    RagflowSearchClient,
    RagflowSearchError,
    RagflowConfigError,
)
from app.core.logging import get_logger
from app.models.faq import (
    FaqDraft,
    FaqDraftGenerateRequest,
    FaqSourceDoc,
)

logger = get_logger(__name__)


# =============================================================================
# Phase 19-AI-2: RAGFlow 검색 결과 데이터 클래스
# =============================================================================


@dataclass
class RagSearchResult:
    """RAGFlow /v1/chunk/search 응답의 개별 결과."""
    title: Optional[str]
    page: Optional[int]
    score: float
    snippet: str

    @classmethod
    def from_chunk(cls, chunk: Dict[str, Any]) -> "RagSearchResult":
        title = chunk.get("document_name") or chunk.get("doc_name") or chunk.get("title")
        page = chunk.get("page_num") or chunk.get("page")
        if page is not None:
            try:
                page = int(page)
            except (TypeError, ValueError):
                page = None
        score = chunk.get("similarity") or chunk.get("score") or 0.0
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.0
        snippet = chunk.get("content") or chunk.get("text") or ""
        if len(snippet) > 500:
            snippet = snippet[:500]
        return cls(title=title, page=page, score=score, snippet=snippet)


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


# Phase 19-AI-2: 문서 컨텍스트 타입
DocContext = Union[List[FaqSourceDoc], List[RagSearchResult]]


class FaqDraftService:
    """
    FAQ 초안 생성 서비스.

    Phase 19-AI-2 업데이트:
    - RagflowSearchClient 사용 (/v1/chunk/search)
    - NO_DOCS_FOUND 에러 처리
    - top_docs vs RAGFlow 검색 결과 구분 처리
    """

    def __init__(
        self,
        search_client: Optional[RagflowSearchClient] = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        self._search_client = search_client or RagflowSearchClient()
        self._llm = llm_client or LLMClient()

    async def generate_faq_draft(
        self,
        req: FaqDraftGenerateRequest,
    ) -> FaqDraft:
        """FAQ 초안을 생성합니다."""
        logger.info(
            f"Generating FAQ draft: domain={req.domain}, "
            f"cluster_id={req.cluster_id}, question='{req.canonical_question[:50]}...'"
        )

        # 1. 문서 컨텍스트 확보 (Phase 19-AI-2)
        context_docs, used_top_docs = await self._get_context_docs(req)
        logger.info(
            f"Got {len(context_docs)} context documents "
            f"(source: {'top_docs' if used_top_docs else 'ragflow_search'})"
        )

        # 2. LLM 메시지 구성
        messages = self._build_llm_messages(req, context_docs, used_top_docs)

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

        # 5. FaqDraft 생성 (Phase 19-AI-2)
        draft = self._create_faq_draft(req, parsed, context_docs, used_top_docs)

        logger.info(
            f"FAQ draft generated: id={draft.faq_draft_id}, "
            f"source={draft.answer_source}, confidence={draft.ai_confidence}"
        )

        return draft

    # =========================================================================
    # Phase 19-AI-2: 문서 컨텍스트 확보
    # =========================================================================

    async def _get_context_docs(
        self,
        req: FaqDraftGenerateRequest,
    ) -> Tuple[DocContext, bool]:
        """
        FAQ 생성에 사용할 문서 컨텍스트를 확보합니다.

        우선순위:
        1. request.top_docs가 비어있지 않으면 그대로 사용
        2. request.top_docs가 비어있으면 RagflowSearchClient.search_chunks() 호출

        Args:
            req: FAQ 초안 생성 요청

        Returns:
            Tuple[DocContext, bool]: (문서 컨텍스트, top_docs 사용 여부)

        Raises:
            FaqGenerationError: RAGFlow 검색 결과가 없는 경우 (NO_DOCS_FOUND)
        """
        # 1. top_docs가 있으면 그대로 사용
        if req.top_docs:
            logger.info(f"Using {len(req.top_docs)} provided top_docs")
            return req.top_docs, True

        # 2. RAGFlow 검색
        logger.info(f"Searching RAGFlow for: '{req.canonical_question[:50]}...'")
        try:
            results = await self._search_client.search_chunks(
                query=req.canonical_question,
                dataset=req.domain,
                top_k=5,
            )
        except RagflowConfigError as e:
            logger.error(f"RAGFlow config error: {e}")
            raise FaqGenerationError(f"RAGFlow 설정 오류: {str(e)}")
        except RagflowSearchError as e:
            logger.error(f"RAGFlow search error: {e}")
            raise FaqGenerationError(f"RAGFlow 검색 실패: {str(e)}")

        # 3. 결과가 없으면 NO_DOCS_FOUND 에러
        if not results:
            logger.warning(f"No documents found for query: '{req.canonical_question[:50]}...'")
            raise FaqGenerationError("NO_DOCS_FOUND")

        # 4. RagSearchResult로 변환
        context_docs = [RagSearchResult.from_chunk(chunk) for chunk in results]
        logger.info(f"Found {len(context_docs)} documents from RAGFlow search")

        return context_docs, False

    # =========================================================================
    # Phase 19-AI-2: LLM 메시지 구성
    # =========================================================================

    def _build_llm_messages(
        self,
        req: FaqDraftGenerateRequest,
        context_docs: DocContext,
        used_top_docs: bool,
    ) -> List[dict]:
        """
        LLM 호출용 메시지를 구성합니다.

        Args:
            req: FAQ 초안 생성 요청
            context_docs: 문서 컨텍스트 (FaqSourceDoc 또는 RagSearchResult)
            used_top_docs: top_docs 사용 여부

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
        docs_text = self._format_docs_for_prompt(context_docs, used_top_docs)

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

    def _format_docs_for_prompt(
        self,
        context_docs: DocContext,
        used_top_docs: bool,
    ) -> str:
        """
        문서 컨텍스트를 LLM 프롬프트용 문자열로 포맷합니다.

        Args:
            context_docs: 문서 컨텍스트
            used_top_docs: top_docs 사용 여부

        Returns:
            포맷된 문서 문자열
        """
        if not context_docs:
            return "(관련 문서를 찾지 못했습니다. 일반적인 가이드를 제공해 주세요.)"

        docs_lines = []

        if used_top_docs:
            # FaqSourceDoc 포맷
            for i, doc in enumerate(context_docs[:3], start=1):
                doc_info = f"### 문서 {i}: {doc.title or '제목 없음'}"
                if doc.article_label:
                    doc_info += f" ({doc.article_label})"
                if doc.snippet:
                    doc_info += f"\n{doc.snippet[:500]}"
                docs_lines.append(doc_info)
        else:
            # RagSearchResult 포맷 (Phase 19-AI-2)
            for i, doc in enumerate(context_docs[:3], start=1):
                doc_info = f"### 문서 {i}: {doc.title or '제목 없음'}"
                if doc.page is not None:
                    doc_info += f" (p.{doc.page})"
                doc_info += f" [유사도: {doc.score:.2f}]"
                if doc.snippet:
                    doc_info += f"\n{doc.snippet}"
                docs_lines.append(doc_info)

        return "\n\n".join(docs_lines)

    # =========================================================================
    # Phase 19-AI-2: FaqDraft 생성
    # =========================================================================

    def _create_faq_draft(
        self,
        req: FaqDraftGenerateRequest,
        parsed: dict,
        context_docs: DocContext,
        used_top_docs: bool,
    ) -> FaqDraft:
        """
        파싱된 LLM 응답으로 FaqDraft를 생성합니다.

        Phase 19-AI-2 스펙:
        - top_docs 사용 시: source_doc_id 등 그대로 사용
        - RAGFlow 검색 시: source 필드는 null, answer_markdown에 참고 문서 추가

        Args:
            req: FAQ 초안 생성 요청
            parsed: 파싱된 LLM 응답
            context_docs: 문서 컨텍스트
            used_top_docs: top_docs 사용 여부

        Returns:
            FaqDraft: 생성된 FAQ 초안
        """
        answer_markdown = parsed.get("answer_markdown", "")

        # RAGFlow 검색 결과 사용 시 참고 문서 정보 추가
        if not used_top_docs and context_docs:
            ref_lines = ["\n\n---\n**참고 문서:**"]
            for doc in context_docs[:3]:
                ref_line = f"- {doc.title or '제목 없음'}"
                if doc.page is not None:
                    ref_line += f" (p.{doc.page})"
                ref_lines.append(ref_line)
            answer_markdown += "\n".join(ref_lines)

        # source 필드 결정
        if used_top_docs and context_docs:
            first_doc = context_docs[0]
            source_doc_id = parsed.get("source_doc_id") or first_doc.doc_id
            source_doc_version = parsed.get("source_doc_version") or first_doc.doc_version
            source_article_label = parsed.get("source_article_label") or first_doc.article_label
            source_article_path = parsed.get("source_article_path") or first_doc.article_path
        else:
            # RAGFlow 검색 결과는 source 정보 없음
            source_doc_id = None
            source_doc_version = None
            source_article_label = None
            source_article_path = None

        return FaqDraft(
            faq_draft_id=str(uuid.uuid4()),
            cluster_id=req.cluster_id,
            domain=req.domain,
            question=parsed.get("question", req.canonical_question),
            answer_markdown=answer_markdown,
            summary=parsed.get("summary"),
            source_doc_id=source_doc_id,
            source_doc_version=source_doc_version,
            source_article_label=source_article_label,
            source_article_path=source_article_path,
            answer_source=self._normalize_answer_source(parsed.get("answer_source")),
            ai_confidence=self._normalize_confidence(parsed.get("ai_confidence")),
            created_at=datetime.now(timezone.utc),
        )

    # =========================================================================
    # LLM 응답 파싱
    # =========================================================================

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

    # =========================================================================
    # 유틸리티 메서드
    # =========================================================================

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
