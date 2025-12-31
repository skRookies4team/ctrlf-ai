"""
FAQ Draft Service (Phase 18+)

FAQ 후보 클러스터를 기반으로 FAQ 초안을 생성하는 서비스.
Milvus + LLM을 사용하여 질문/답변/근거 문서를 생성합니다.

Note:
    RAGFlow 클라이언트는 제거되었습니다. Milvus만 사용합니다.

주요 기능:
- MilvusSearchClient로 벡터 검색 + text 직접 조회
- NO_DOCS_FOUND 에러 처리
- 프롬프트 템플릿 (근거 기반 + 짧고 명확 + 마크다운)
- answer_source: TOP_DOCS / MILVUS 구분
- LOW_RELEVANCE_CONTEXT 실패 규칙
- PII 강차단: 입력/출력/컨텍스트에 PII 검출 시 즉시 실패
- 품질 모니터링 로그: ai_confidence 기반 경고 로그
"""

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from app.clients.llm_client import LLMClient
from app.clients.milvus_client import (
    MilvusSearchClient,
    MilvusSearchError,
    get_milvus_client,
)
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.faq import (
    FaqDraft,
    FaqDraftGenerateRequest,
    FaqSourceDoc,
)
from app.models.intent import MaskingStage
from app.services.pii_service import PiiService

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

SYSTEM_PROMPT = """너는 기업 내부 한국어 FAQ 작성 보조자다.

## 핵심 원칙
1. 답변은 반드시 제공된 컨텍스트(context_docs) 범위에서만 작성한다.
2. 컨텍스트에 없는 내용은 추측하지 않는다.
3. 컨텍스트가 질문과 관련이 없다고 판단되면 status를 "LOW_RELEVANCE"로 설정한다.
4. 모든 질문과 답변은 반드시 한국어로 작성한다.

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

    Milvus 벡터 검색 + LLM을 사용하여 FAQ 초안을 생성합니다.

    Note:
        RAGFlow 클라이언트는 제거되었습니다. Milvus만 사용합니다.

    주요 기능:
    - 프롬프트 템플릿 (근거 기반)
    - answer_source: TOP_DOCS / MILVUS 구분
    - LOW_RELEVANCE_CONTEXT 실패 규칙
    - PII 강차단: 입력/출력에 PII 검출 시 즉시 실패
    """

    def __init__(
        self,
        milvus_client: Optional[MilvusSearchClient] = None,
        llm_client: Optional[LLMClient] = None,
        pii_service: Optional[PiiService] = None,
    ) -> None:
        """
        FaqDraftService 초기화.

        Note:
            RAGFlow 클라이언트는 제거되었습니다. Milvus만 사용합니다.
        """
        self._milvus_client = milvus_client or get_milvus_client()
        self._llm = llm_client or LLMClient()
        self._pii_service = pii_service or PiiService()

        logger.info("FaqDraftService: Milvus search enabled (RAGFlow removed)")

    async def generate_faq_draft(
        self,
        req: FaqDraftGenerateRequest,
    ) -> FaqDraft:
        """FAQ 초안을 생성합니다.

        Phase 19-AI-4: PII 강차단
        - 입력에 PII 검출 시: PII_DETECTED 에러
        - 출력에 PII 검출 시: PII_DETECTED_OUTPUT 에러
        """
        logger.info(
            f"Generating FAQ draft: domain={req.domain}, "
            f"cluster_id={req.cluster_id}, question='{req.canonical_question[:50]}...'"
        )

        # 0. Phase 19-AI-4: 입력 PII 검사
        await self._check_input_pii(req)

        # 1. 문서 컨텍스트 확보 (Phase 19-AI-2 + Option 3)
        context_docs, answer_source = await self._get_context_docs(req)
        logger.info(
            f"Got {len(context_docs)} context documents (source: {answer_source})"
        )

        # 2. LLM 메시지 구성
        messages = self._build_llm_messages(req, context_docs, answer_source)

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
            logger.warning(f"LLM parse failed, retrying once: {e}")

            llm_response = await self._llm.generate_chat_completion(
                messages=messages,
                model=None,
                temperature=0.1,  # 안정화
                max_tokens=2048,
            )
            parsed = self._parse_llm_response(llm_response)

        # 5. Phase 19-AI-4: 출력 PII 검사
        await self._check_output_pii(parsed)
        
        # ===============================
        # Phase 48: LLM 필드 보정
        # ===============================
        parsed.setdefault(
            "summary",
            (parsed.get("answer_markdown", "") or "")[:120]
        )

        try:
            parsed["ai_confidence"] = min(
                max(float(parsed.get("ai_confidence", 0.7)), 0.0), 1.0
            )
        except (TypeError, ValueError):
            parsed["ai_confidence"] = 0.7


        # 6. Phase 19-AI-3: LOW_RELEVANCE_CONTEXT 체크
        # 설정에 따라 선택적 검증
        settings = get_settings()
        status = parsed.get("status", "SUCCESS").upper()
        if status == "LOW_RELEVANCE":
            if settings.FAQ_LOW_RELEVANCE_BLOCK:
                # 차단 모드: 에러 발생
                logger.warning(f"Low relevance context for query: '{req.canonical_question[:50]}...'")
                raise FaqGenerationError("LOW_RELEVANCE_CONTEXT")
            else:
                # 경고 모드: 경고만 출력하고 계속 진행
                logger.warning(
                    f"Low relevance context detected but continuing "
                    f"(cluster_id={req.cluster_id}, question='{req.canonical_question[:50]}...')"
                )
                # status를 SUCCESS로 강제 변경하여 FAQ 생성 허용
                parsed["status"] = "SUCCESS"

        # 7. FaqDraft 생성 (Phase 19-AI-3 + Option 3)
        draft = self._create_faq_draft(req, parsed, context_docs, answer_source)

        # 8. Phase 20-AI-4: 품질 모니터링 로그
        self._log_quality_metrics(draft, context_docs, answer_source)

        logger.info(
            f"FAQ draft generated: id={draft.faq_draft_id}, "
            f"source={draft.answer_source}, confidence={draft.ai_confidence}"
        )

        return draft

    # =========================================================================
    # Phase 19-AI-2 + Option 3: 문서 컨텍스트 확보
    # =========================================================================

    async def _get_context_docs(
        self,
        req: FaqDraftGenerateRequest,
    ) -> Tuple[DocContext, str]:
        """
        FAQ 생성에 사용할 문서 컨텍스트를 확보합니다.

        우선순위:
        1. request.top_docs가 비어있지 않으면 그대로 사용
        2. MilvusSearchClient로 벡터 검색

        Note:
            RAGFlow 클라이언트는 제거되었습니다. Milvus만 사용합니다.

        Args:
            req: FAQ 초안 생성 요청

        Returns:
            Tuple[DocContext, str]: (문서 컨텍스트, 소스 타입)
                소스 타입: "TOP_DOCS", "MILVUS"

        Raises:
            FaqGenerationError: 검색 결과가 없는 경우 (NO_DOCS_FOUND)
        """
        # 1. top_docs가 있으면 그대로 사용
        if req.top_docs:
            logger.info(f"Using {len(req.top_docs)} provided top_docs")
            return req.top_docs, "TOP_DOCS"

        # 2. Milvus 검색
        return await self._search_milvus(req)

    async def _search_milvus(
        self,
        req: FaqDraftGenerateRequest,
    ) -> Tuple[DocContext, str]:
        """
        Milvus에서 직접 벡터 검색 + text 조회.

        Note:
            RAGFlow 클라이언트는 제거되었습니다. Milvus만 사용합니다.

        Args:
            req: FAQ 초안 생성 요청

        Returns:
            Tuple[DocContext, str]: (문서 컨텍스트, "MILVUS")

        Raises:
            FaqGenerationError: 검색 실패 또는 결과 없음
        """
        logger.info(f"Searching Milvus for: '{req.canonical_question[:50]}...'")

        # ===============================
        # Phase 48: Multi-query Milvus search
        # ===============================
        queries = [req.canonical_question]
        if req.sample_questions:
            queries.extend(req.sample_questions[:2])  # 최대 2개

        raw_results: List[dict] = []

        for q in queries:
            try:
                partial = await self._milvus_client.search(
                    query=q,
                    domain=req.domain,
                    top_k=3,
                )
                raw_results.extend(partial)
            except MilvusSearchError as e:
                logger.error(f"Milvus search error (query='{q[:30]}...'): {e}")
                continue

        if not raw_results:
            logger.warning("No documents found in Milvus (multi-query)")
            raise FaqGenerationError("NO_DOCS_FOUND")

        # ===============================
        # dedup + RagSearchResult 변환
        # ===============================
        merged: Dict[tuple, RagSearchResult] = {}

        for r in raw_results:
            key = (r.get("doc_id"), r.get("metadata", {}).get("chunk_id"))
            score = float(r.get("score", 0.0))

            candidate = RagSearchResult(
                title=r.get("doc_id", ""),
                page=r.get("metadata", {}).get("chunk_id"),
                score=score,
                snippet=(r.get("content") or "")[:500],
            )

            if key not in merged or merged[key].score < score:
                merged[key] = candidate

        context_docs = sorted(
            merged.values(), key=lambda x: x.score, reverse=True
        )[:5]

        # ===============================
        # 🔥 점수 기반 사전 차단 (LLM 호출 전)
        # ===============================
        settings = get_settings()
        min_score = getattr(settings, "FAQ_MIN_MILVUS_SCORE", 0.55)

        top_score = context_docs[0].score if context_docs else 0.0
        if top_score < min_score:
            logger.warning(
                f"LOW_RELEVANCE before LLM: top_score={top_score:.2f} < {min_score}"
            )
            raise FaqGenerationError("LOW_RELEVANCE_CONTEXT")

        # 컨텍스트 PII 검사
        await self._check_context_pii(context_docs, req.domain, req.cluster_id)

        return context_docs, "MILVUS"


    # =========================================================================
    # Phase 19-AI-3: LLM 메시지 구성
    # =========================================================================

    def _build_llm_messages(
        self,
        req: FaqDraftGenerateRequest,
        context_docs: DocContext,
        answer_source: str,
    ) -> List[dict]:
        """
        LLM 호출용 메시지를 구성합니다.

        Args:
            req: FAQ 초안 생성 요청
            context_docs: 문서 컨텍스트 (FaqSourceDoc 또는 RagSearchResult)
            answer_source: 소스 타입 ("TOP_DOCS", "MILVUS", "RAGFLOW")

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
        docs_text = self._format_docs_for_prompt(context_docs, answer_source)

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
        answer_source: str,
    ) -> str:
        """
        문서 컨텍스트를 LLM 프롬프트용 문자열로 포맷합니다.

        Args:
            context_docs: 문서 컨텍스트
            answer_source: 소스 타입 ("TOP_DOCS", "MILVUS", "RAGFLOW")

        Returns:
            포맷된 문서 문자열
        """
        if not context_docs:
            return "(컨텍스트 문서 없음)"

        docs_lines = []

        if answer_source == "TOP_DOCS":
            # FaqSourceDoc 포맷
            for i, doc in enumerate(context_docs[:5], start=1):
                doc_info = f"### 문서 {i}: {doc.title or '제목 없음'}"
                if doc.article_label:
                    doc_info += f" ({doc.article_label})"
                if doc.snippet:
                    doc_info += f"\n{doc.snippet[:500]}"
                docs_lines.append(doc_info)
        else:
            # RagSearchResult 포맷 (MILVUS / RAGFLOW)
            for i, doc in enumerate(context_docs[:5], start=1):
                doc_info = f"### 문서 {i}: {doc.title or '제목 없음'}"
                if doc.page is not None:
                    doc_info += f" (chunk #{doc.page})"
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
        answer_source: str,
    ) -> FaqDraft:
        """
        파싱된 LLM 응답으로 FaqDraft를 생성합니다.

        Phase 19-AI-3 + Option 3 스펙:
        - answer_source: TOP_DOCS, MILVUS, 또는 RAGFLOW
        - top_docs 사용 시: source_doc_id 등 그대로 사용
        - MILVUS/RAGFLOW 검색 시: source 필드는 null

        Args:
            req: FAQ 초안 생성 요청
            parsed: 파싱된 LLM 응답
            context_docs: 문서 컨텍스트
            answer_source: 소스 타입 ("TOP_DOCS", "MILVUS", "RAGFLOW")

        Returns:
            FaqDraft: 생성된 FAQ 초안
        """
        answer_markdown = parsed.get("answer_markdown", "")

        # source 필드 결정
        if answer_source == "TOP_DOCS" and context_docs:
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
    # Phase 19-AI-4: PII 강차단
    # =========================================================================

    async def _check_input_pii(self, req: FaqDraftGenerateRequest) -> None:
        """입력 데이터에서 PII를 검사합니다.

        검사 대상:
        - canonical_question
        - sample_questions (모든 항목)
        - top_docs.snippet (모든 항목)

        Args:
            req: FAQ 초안 생성 요청

        Raises:
            FaqGenerationError: PII 검출 시 "PII_DETECTED" 에러
        """
        # 검사할 텍스트 목록 수집
        texts_to_check: List[str] = []

        # 1. canonical_question
        if req.canonical_question:
            texts_to_check.append(req.canonical_question)

        # 2. sample_questions
        if req.sample_questions:
            texts_to_check.extend(req.sample_questions)

        # 3. top_docs.snippet
        if req.top_docs:
            for doc in req.top_docs:
                if doc.snippet:
                    texts_to_check.append(doc.snippet)

        # PII 검사 수행
        for text in texts_to_check:
            if not text or not text.strip():
                continue

            result = await self._pii_service.detect_and_mask(text, MaskingStage.INPUT)

            if result.has_pii:
                logger.warning(
                    f"PII detected in input: {len(result.tags)} entities found, "
                    f"labels={[tag.label for tag in result.tags]}"
                )
                raise FaqGenerationError("PII_DETECTED")

        logger.debug("Input PII check passed")

    async def _check_output_pii(self, parsed: Dict[str, Any]) -> None:
        """LLM 출력 데이터에서 PII를 검사합니다.

        검사 대상:
        - answer_markdown
        - summary

        Args:
            parsed: 파싱된 LLM 응답

        Raises:
            FaqGenerationError: PII 검출 시 "PII_DETECTED_OUTPUT" 에러
        """
        # 검사할 텍스트 목록 수집
        texts_to_check: List[str] = []

        # 1. answer_markdown
        answer_markdown = parsed.get("answer_markdown", "")
        if answer_markdown:
            texts_to_check.append(answer_markdown)

        # 2. summary
        summary = parsed.get("summary", "")
        if summary:
            texts_to_check.append(summary)

        # PII 검사 수행
        for text in texts_to_check:
            if not text or not text.strip():
                continue

            result = await self._pii_service.detect_and_mask(text, MaskingStage.OUTPUT)

            if result.has_pii:
                logger.warning(
                    f"PII detected in output: {len(result.tags)} entities found, "
                    f"labels={[tag.label for tag in result.tags]}"
                )
                raise FaqGenerationError("PII_DETECTED_OUTPUT")

        logger.debug("Output PII check passed")

    # =========================================================================
    # Phase 20-AI-3: 컨텍스트 PII 강차단
    # =========================================================================

    async def _check_context_pii(
        self,
        context_docs: List[RagSearchResult],
        domain: str,
        cluster_id: str,
    ) -> None:
        """RAGFlow 검색 결과 snippet에서 PII를 검사합니다.

        Phase 20-AI-3: RAGFlow 스니펫에 PII가 포함되면 강차단.
        - 검사 대상: 각 context_docs의 snippet
        - PII 발견 시: PII_DETECTED_CONTEXT 에러
        - 로그에 domain, cluster_id, 문서 제목 일부를 남기되 PII 원문은 로그 금지

        Args:
            context_docs: RAGFlow 검색 결과 (RagSearchResult 리스트)
            domain: FAQ 도메인
            cluster_id: FAQ 클러스터 ID

        Raises:
            FaqGenerationError: PII 검출 시 "PII_DETECTED_CONTEXT" 에러
        """
        for i, doc in enumerate(context_docs):
            if not doc.snippet or not doc.snippet.strip():
                continue

            result = await self._pii_service.detect_and_mask(
                doc.snippet, MaskingStage.INPUT
            )

            if result.has_pii:
                # PII 원문은 로그하지 않고, 문서 제목/인덱스만 로그
                doc_title_safe = (doc.title or "untitled")[:30]
                logger.warning(
                    f"PII detected in RAGFlow context: "
                    f"domain={domain}, cluster_id={cluster_id}, "
                    f"doc_index={i}, doc_title='{doc_title_safe}...', "
                    f"pii_count={len(result.tags)}, pii_labels={[tag.label for tag in result.tags]}",
                    extra={
                        "event": "pii_detected_context",
                        "domain": domain,
                        "cluster_id": cluster_id,
                        "doc_index": i,
                    }
                )
                raise FaqGenerationError("PII_DETECTED_CONTEXT")

        logger.debug(
            f"Context PII check passed: {len(context_docs)} docs checked"
        )

    # =========================================================================
    # Phase 20-AI-4: 품질 모니터링 로그
    # =========================================================================

    def _log_quality_metrics(
        self,
        draft: FaqDraft,
        context_docs: DocContext,
        answer_source: str,
    ) -> None:
        """FAQ 생성 품질 메트릭을 로그에 기록합니다.

        Phase 20-AI-4: ai_confidence 기반 경고 로그
        - status=SUCCESS인데 ai_confidence < threshold -> WARN 로그
        - 구조화 로그로 대시보드 집계 지원

        Args:
            draft: 생성된 FAQ 초안
            context_docs: 사용된 문서 컨텍스트
            answer_source: 소스 타입 ("TOP_DOCS", "MILVUS", "RAGFLOW")
        """
        settings = get_settings()
        threshold = settings.FAQ_CONFIDENCE_WARN_THRESHOLD

        # 검색 top score 추출 (MILVUS / RAGFLOW)
        search_top_score = None
        if answer_source in ("MILVUS", "RAGFLOW") and context_docs:
            first_doc = context_docs[0]
            if hasattr(first_doc, "score"):
                search_top_score = first_doc.score

        log_extra = {
            "event": "faq_quality",
            "cluster_id": draft.cluster_id,
            "domain": draft.domain,
            "answer_source": draft.answer_source,
            "ai_confidence": draft.ai_confidence,
            "status": "SUCCESS",
            "error_message": None,
            "search_top_score": search_top_score,
        }

        # ai_confidence가 낮으면 경고 로그
        if draft.ai_confidence is not None and draft.ai_confidence < threshold:
            logger.warning(
                f"Low confidence FAQ generated: "
                f"cluster_id={draft.cluster_id}, domain={draft.domain}, "
                f"ai_confidence={draft.ai_confidence:.2f} (threshold={threshold}), "
                f"answer_source={draft.answer_source}",
                extra=log_extra,
            )
        else:
            logger.info(
                f"FAQ quality metrics: "
                f"cluster_id={draft.cluster_id}, ai_confidence={draft.ai_confidence}, "
                f"answer_source={draft.answer_source}",
                extra=log_extra,
            )

    def _log_failed_quality_metrics(
        self,
        req: FaqDraftGenerateRequest,
        error_message: str,
    ) -> None:
        """FAQ 생성 실패 시 품질 메트릭을 로그에 기록합니다.

        Phase 20-AI-4: 실패 케이스 모니터링
        - LOW_RELEVANCE_CONTEXT, NO_DOCS_FOUND 등 실패 케이스 추적

        Args:
            req: FAQ 초안 생성 요청
            error_message: 에러 메시지
        """
        log_extra = {
            "event": "faq_quality",
            "cluster_id": req.cluster_id,
            "domain": req.domain,
            "answer_source": None,
            "ai_confidence": None,
            "status": "FAILED",
            "error_message": error_message,
            "ragflow_top_score": None,
        }

        # 특정 에러 코드에 대해 경고 레벨 로그
        if error_message in ("LOW_RELEVANCE_CONTEXT", "NO_DOCS_FOUND"):
            logger.warning(
                f"FAQ generation quality issue: "
                f"cluster_id={req.cluster_id}, domain={req.domain}, "
                f"error={error_message}",
                extra=log_extra,
            )
        else:
            logger.info(
                f"FAQ generation failed: "
                f"cluster_id={req.cluster_id}, domain={req.domain}, "
                f"error={error_message}",
                extra=log_extra,
            )

    # =========================================================================
    # 유틸리티 메서드
    # =========================================================================

    def _normalize_answer_source(
        self, source: Optional[str]
    ) -> str:
        """answer_source를 정규화합니다. (하위 호환용)"""
        valid_sources = {"TOP_DOCS", "MILVUS", "AI_RAG", "LOG_REUSE", "MIXED"}
        if source and source.upper() in valid_sources:
            return source.upper()
        return "MILVUS"

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
