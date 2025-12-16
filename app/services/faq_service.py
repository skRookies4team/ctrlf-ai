"""
FAQ Draft Service (Phase 18 + Phase 19-AI-2 + Phase 19-AI-3)

FAQ 후보 클러스터를 기반으로 FAQ 초안을 생성하는 서비스.
RAG + LLM을 사용하여 질문/답변/근거 문서를 생성합니다.

Phase 19-AI-2 업데이트:
- RagflowSearchClient 연동 (/v1/chunk/search)
- NO_DOCS_FOUND 에러 처리
- RAGFlow 검색 결과 기반 컨텍스트 구성

Phase 19-AI-3 업데이트:
- 프롬프트 템플릿 개선 (근거 기반 + 짧고 명확 + 마크다운)
- answer_source: TOP_DOCS / RAGFLOW 구분
- LOW_RELEVANCE_CONTEXT 실패 규칙 추가
- 필드별 텍스트 출력 파싱 지원
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
# Phase 19-AI-3: LLM 프롬프트 템플릿 (개선)
# =============================================================================

SYSTEM_PROMPT = """너는 기업 내부 FAQ 작성 보조자다.

## 핵심 원칙
1. 답변은 반드시 제공된 컨텍스트(context_docs) 범위에서만 작성한다.
2. 컨텍스트에 없는 내용은 추측하지 않는다.
3. 컨텍스트가 질문과 관련이 없다고 판단되면 status를 "LOW_RELEVANCE"로 설정한다.

## 출력 형식
아래 형식으로 정확히 출력하라. 각 필드는 레이블과 콜론으로 시작한다.

```
status: SUCCESS 또는 LOW_RELEVANCE
question: [canonical_question을 자연스러운 FAQ 질문으로 다듬되 의미 변경 금지]
summary: [1문장, 최대 120자]
answer_markdown: |
  [결론 1~2문장]

  - [핵심 규칙/절차 bullet 1]
  - [핵심 규칙/절차 bullet 2]
  - [핵심 규칙/절차 bullet 3]
  (3~6개 bullet)

  **참고**
  - [문서 타이틀 (p.페이지)]
ai_confidence: [0.00~1.00, 컨텍스트 적합도가 높을수록 높게]
```

## 주의사항
- summary는 반드시 120자 이내로 작성
- answer_markdown의 bullet은 3~6개
- ai_confidence는 컨텍스트와 질문의 연관성을 0.00~1.00으로 평가
- 컨텍스트가 질문과 관련 없으면: status: LOW_RELEVANCE, ai_confidence: 0.3 이하
"""

USER_PROMPT_TEMPLATE = """## 도메인
{domain}

## 대표 질문 (canonical_question)
{canonical_question}

## 실제 직원 질문 예시 (sample_questions)
{sample_questions_text}

## 컨텍스트 문서 (context_docs)
{docs_text}

위 컨텍스트를 바탕으로 FAQ를 작성해 주세요. 컨텍스트에 없는 내용은 작성하지 마세요.
"""


class FaqGenerationError(Exception):
    """FAQ 생성 중 발생한 에러"""
    pass


# Phase 19-AI-2: 문서 컨텍스트 타입
DocContext = Union[List[FaqSourceDoc], List[RagSearchResult]]


class FaqDraftService:
    """
    FAQ 초안 생성 서비스.

    Phase 19-AI-3 업데이트:
    - 프롬프트 템플릿 개선 (근거 기반)
    - answer_source: TOP_DOCS / RAGFLOW 구분
    - LOW_RELEVANCE_CONTEXT 실패 규칙
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
            f"(source: {'TOP_DOCS' if used_top_docs else 'RAGFLOW'})"
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

        # 4. 응답 파싱 (Phase 19-AI-3: 필드별 텍스트 파싱)
        try:
            parsed = self._parse_llm_response(llm_response)
        except Exception as e:
            logger.exception(f"LLM response parsing failed: {e}")
            raise FaqGenerationError(f"LLM 응답 파싱 실패: {str(e)}")

        # 5. Phase 19-AI-3: LOW_RELEVANCE_CONTEXT 체크
        status = parsed.get("status", "SUCCESS").upper()
        if status == "LOW_RELEVANCE":
            logger.warning(f"Low relevance context for query: '{req.canonical_question[:50]}...'")
            raise FaqGenerationError("LOW_RELEVANCE_CONTEXT")

        # 6. FaqDraft 생성 (Phase 19-AI-3)
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
    # Phase 19-AI-3: LLM 메시지 구성
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
            return "(컨텍스트 문서 없음)"

        docs_lines = []

        if used_top_docs:
            # FaqSourceDoc 포맷
            for i, doc in enumerate(context_docs[:5], start=1):
                doc_info = f"### 문서 {i}: {doc.title or '제목 없음'}"
                if doc.article_label:
                    doc_info += f" ({doc.article_label})"
                if doc.snippet:
                    doc_info += f"\n{doc.snippet[:500]}"
                docs_lines.append(doc_info)
        else:
            # RagSearchResult 포맷 (Phase 19-AI-2)
            for i, doc in enumerate(context_docs[:5], start=1):
                doc_info = f"### 문서 {i}: {doc.title or '제목 없음'}"
                if doc.page is not None:
                    doc_info += f" (p.{doc.page})"
                doc_info += f" [유사도: {doc.score:.2f}]"
                if doc.snippet:
                    doc_info += f"\n{doc.snippet}"
                docs_lines.append(doc_info)

        return "\n\n".join(docs_lines)

    # =========================================================================
    # Phase 19-AI-3: FaqDraft 생성
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

        Phase 19-AI-3 스펙:
        - answer_source: TOP_DOCS 또는 RAGFLOW
        - top_docs 사용 시: source_doc_id 등 그대로 사용
        - RAGFlow 검색 시: source 필드는 null

        Args:
            req: FAQ 초안 생성 요청
            parsed: 파싱된 LLM 응답
            context_docs: 문서 컨텍스트
            used_top_docs: top_docs 사용 여부

        Returns:
            FaqDraft: 생성된 FAQ 초안
        """
        answer_markdown = parsed.get("answer_markdown", "")

        # Phase 19-AI-3: answer_source 결정
        answer_source = "TOP_DOCS" if used_top_docs else "RAGFLOW"

        # source 필드 결정
        if used_top_docs and context_docs:
            first_doc = context_docs[0]
            source_doc_id = first_doc.doc_id
            source_doc_version = first_doc.doc_version
            source_article_label = first_doc.article_label
            source_article_path = first_doc.article_path
        else:
            # RAGFlow 검색 결과는 source 정보 없음
            source_doc_id = None
            source_doc_version = None
            source_article_label = None
            source_article_path = None

        # summary 길이 검증 (Phase 19-AI-3: 최대 120자)
        summary = parsed.get("summary", "")
        if len(summary) > 120:
            summary = summary[:117] + "..."

        return FaqDraft(
            faq_draft_id=str(uuid.uuid4()),
            cluster_id=req.cluster_id,
            domain=req.domain,
            question=parsed.get("question", req.canonical_question),
            answer_markdown=answer_markdown,
            summary=summary,
            source_doc_id=source_doc_id,
            source_doc_version=source_doc_version,
            source_article_label=source_article_label,
            source_article_path=source_article_path,
            answer_source=answer_source,
            ai_confidence=self._normalize_confidence(parsed.get("ai_confidence")),
            created_at=datetime.now(timezone.utc),
        )

    # =========================================================================
    # Phase 19-AI-3: LLM 응답 파싱 (필드별 텍스트 + JSON 지원)
    # =========================================================================

    def _parse_llm_response(self, llm_response: str) -> dict:
        """
        LLM 응답을 파싱합니다.

        Phase 19-AI-3: 필드별 텍스트 형식과 JSON 형식 모두 지원

        Args:
            llm_response: LLM 응답 텍스트

        Returns:
            파싱된 딕셔너리

        Raises:
            FaqGenerationError: 파싱 실패 시
        """
        # 방법 1: 필드별 텍스트 형식 파싱 시도
        parsed = self._parse_field_text_format(llm_response)
        if parsed and parsed.get("question") and parsed.get("answer_markdown"):
            return parsed

        # 방법 2: JSON 형식 파싱 시도 (하위 호환)
        json_str = self._extract_json_from_response(llm_response)
        if json_str:
            try:
                data = json.loads(json_str)
                if data.get("question") and data.get("answer_markdown"):
                    return data
            except json.JSONDecodeError:
                pass

        # 파싱 실패
        raise FaqGenerationError("LLM 응답 파싱 실패: 필드별 텍스트 또는 JSON 형식을 찾을 수 없습니다")

    def _parse_field_text_format(self, response: str) -> Optional[dict]:
        """
        Phase 19-AI-3: 필드별 텍스트 형식을 파싱합니다.

        형식:
        status: SUCCESS
        question: 질문 내용
        summary: 요약 내용
        answer_markdown: |
          답변 내용
        ai_confidence: 0.85

        Args:
            response: LLM 응답 텍스트

        Returns:
            파싱된 딕셔너리 또는 None
        """
        result = {}

        # status 추출
        status_match = re.search(r'^status:\s*(.+)$', response, re.MULTILINE | re.IGNORECASE)
        if status_match:
            result["status"] = status_match.group(1).strip()

        # question 추출
        question_match = re.search(r'^question:\s*(.+)$', response, re.MULTILINE | re.IGNORECASE)
        if question_match:
            result["question"] = question_match.group(1).strip()

        # summary 추출
        summary_match = re.search(r'^summary:\s*(.+)$', response, re.MULTILINE | re.IGNORECASE)
        if summary_match:
            result["summary"] = summary_match.group(1).strip()

        # ai_confidence 추출
        confidence_match = re.search(r'^ai_confidence:\s*([\d.]+)', response, re.MULTILINE | re.IGNORECASE)
        if confidence_match:
            try:
                result["ai_confidence"] = float(confidence_match.group(1))
            except ValueError:
                pass

        # answer_markdown 추출 (멀티라인)
        # 패턴 1: answer_markdown: | 로 시작하는 YAML 스타일
        md_match = re.search(
            r'^answer_markdown:\s*\|?\s*\n((?:[ \t]+.+\n?)+)',
            response,
            re.MULTILINE | re.IGNORECASE
        )
        if md_match:
            # 들여쓰기 제거
            lines = md_match.group(1).split('\n')
            # 최소 들여쓰기 찾기
            min_indent = float('inf')
            for line in lines:
                if line.strip():
                    indent = len(line) - len(line.lstrip())
                    min_indent = min(min_indent, indent)
            if min_indent == float('inf'):
                min_indent = 0
            # 들여쓰기 제거하여 결합
            cleaned_lines = []
            for line in lines:
                if len(line) >= min_indent:
                    cleaned_lines.append(line[min_indent:])
                else:
                    cleaned_lines.append(line)
            result["answer_markdown"] = '\n'.join(cleaned_lines).strip()
        else:
            # 패턴 2: answer_markdown: 이후 다음 필드까지
            md_match2 = re.search(
                r'^answer_markdown:\s*(.+?)(?=^(?:ai_confidence|status|question|summary):|\Z)',
                response,
                re.MULTILINE | re.DOTALL | re.IGNORECASE
            )
            if md_match2:
                result["answer_markdown"] = md_match2.group(1).strip()

        return result if result else None

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
        """answer_source를 정규화합니다. (하위 호환용)"""
        valid_sources = {"TOP_DOCS", "RAGFLOW", "AI_RAG", "LOG_REUSE", "MIXED"}
        if source and source.upper() in valid_sources:
            return source.upper()
        return "RAGFLOW"

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
