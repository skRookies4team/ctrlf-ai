"""
FAQ 자동 생성 서비스 (Auto FAQ Generation Service)

질문 로그를 분석하여 FAQ 후보를 선정하고, FAQ 초안을 자동 생성하는 서비스입니다.

주요 기능:
- 질문 로그 분석 (백엔드 API 호출)
- 질문 클러스터링 (유사 질문 그룹화) - 임베딩 기반
- 빈도 분석 (여러 사용자 간의 질문 빈도)
- 후보 선정 및 점수 계산
- FAQ 초안 자동 생성
"""

import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.clients.backend_client import BackendClient, get_backend_client
from app.clients.milvus_client import get_milvus_client
from app.core.config import get_settings
from app.core.logging import get_logger
from app.clients.http_client import get_async_http_client
from app.models.faq import (
    FaqAutoGenerateRequest,
    FaqAutoGenerateResponse,
    FaqCandidate,
    FaqDomain,
    FaqDraft,
    FaqDraftGenerateRequest,
)
from app.services.faq_service import (
    FaqDraftService,
    FaqGenerationError,
    classify_faq_domain,
)

logger = get_logger(__name__)


class ChatMessage:
    """질문 로그 메시지 데이터 클래스"""

    def __init__(
        self,
        content: str,
        domain: Optional[str],
        user_id: str,
        session_id: str,
        created_at: datetime,
    ):
        self.content = content
        self.domain = domain
        self.user_id = user_id
        self.session_id = session_id
        self.created_at = created_at


class FaqAutoGenerateService:
    """FAQ 자동 생성 서비스"""

    # 임베딩 기반 클러스터링 유사도 임계값 (0.80 이상이면 같은 클러스터)
    # 0.85에서 0.80으로 낮춰서 더 많은 유사 질문을 묶을 수 있도록 함
    CLUSTERING_SIMILARITY_THRESHOLD = 0.80

    def __init__(
        self,
        faq_service: Optional[FaqDraftService] = None,
        backend_client: Optional[BackendClient] = None,
    ):
        """
        FaqAutoGenerateService 초기화.

        Args:
            faq_service: FAQ 초안 생성 서비스 (None이면 새로 생성)
            backend_client: 백엔드 클라이언트 (None이면 새로 생성)
        """
        self._faq_service = faq_service or FaqDraftService()
        self._backend_client = backend_client or get_backend_client()
        self._milvus_client = None  # lazy initialization
        self._settings = get_settings()
        # Elasticsearch 설정
        if not self._settings.ELASTICSEARCH_URL:
            logger.warning("ELASTICSEARCH_URL이 설정되지 않았습니다. FAQ 로그 조회가 실패할 수 있습니다.")
        self._es_base_url = (self._settings.ELASTICSEARCH_URL or "").rstrip("/")
        # FAQ 생성은 chat_log 인덱스에서 직접 조회 (백엔드 변경사항 반영)
        # 백엔드가 채팅 메시지를 저장할 때 자동으로 chat_log 인덱스에 저장됨
        self._es_faq_log_index = "chat_log"  # FAQ 자동 생성용 로그 인덱스 (백엔드 chat_log 인덱스 사용)

    async def generate_auto_faq(
        self, request: FaqAutoGenerateRequest
    ) -> FaqAutoGenerateResponse:
        """
        자동 FAQ 생성 메인 메서드.

        Args:
            request: 자동 FAQ 생성 요청

        Returns:
            FaqAutoGenerateResponse: 생성 결과
        """
        logger.info(
            f"[FAQ_AUTO] 자동 FAQ 생성 시작: domain={request.domain}, "
            f"min_frequency={request.min_frequency}, days_back={request.days_back}, "
            f"max_candidates={request.max_candidates}, auto_generate_drafts={request.auto_generate_drafts}"
        )

        try:
            # 1. 질문 로그 조회
            messages = await self._fetch_chat_messages(request)
            logger.info(
                f"[FAQ_AUTO] 조회된 메시지: {len(messages)}개 "
                f"(domain={request.domain}, days_back={request.days_back})"
            )

            if not messages:
                error_msg = (
                    f"조건에 맞는 FAQ 후보가 없습니다. (발견된 후보: 0개)\n"
                    f"가능한 원인:\n"
                    f"1. 최근 {request.days_back}일 내 여러 사용자가 {request.min_frequency}회 이상 질문한 항목이 없는 경우\n"
                    f"2. Elasticsearch 인덱스({self._es_faq_log_index})가 없거나 데이터가 없는 경우\n"
                    f"3. 질문 후 클러스터링 처리 시간이 필요한 경우\n"
                    f"백엔드 로그에서 'index_not_found_exception' 또는 'faq_log' 관련 에러를 확인해주세요."
                )
                logger.warning(
                    f"⚠️ [FAQ_AUTO] 조회된 메시지가 0개입니다.\n"
                    f"  Elasticsearch 인덱스: {self._es_faq_log_index}\n"
                    f"  Elasticsearch URL: {self._es_base_url}\n"
                    f"  날짜 범위: 최근 {request.days_back}일\n"
                    f"  최소 빈도: {request.min_frequency}회 이상"
                )
                return FaqAutoGenerateResponse(
                    status="SUCCESS",
                    candidates_found=0,
                    drafts_generated=0,
                    drafts_failed=0,
                    candidates=[],
                    drafts=[],
                    error_message=error_msg,
                )

            # 2. 후보 선정
            candidates = await self._select_candidates(messages, request)
            logger.info(
                f"후보 선정 완료: {len(candidates)}개 선정됨 "
                f"(from {len(messages)} messages, min_frequency={request.min_frequency})"
            )

            if not candidates:
                # 상세한 디버깅 정보 출력
                error_msg = (
                    f"조건에 맞는 FAQ 후보가 없습니다. (발견된 후보: 0개)\n"
                    f"가능한 원인:\n"
                    f"1. 최근 {request.days_back}일 내 여러 사용자가 {request.min_frequency}회 이상 질문한 항목이 없는 경우\n"
                    f"2. Elasticsearch 인덱스({self._es_faq_log_index})가 없거나 데이터가 없는 경우\n"
                    f"3. 질문 후 클러스터링 처리 시간이 필요한 경우\n"
                    f"백엔드 로그에서 'index_not_found_exception' 또는 'faq_log' 관련 에러를 확인해주세요."
                )
                logger.warning(
                    f"⚠️ [FAQ_AUTO] 후보가 0개입니다. 확인 필요:\n"
                    f"  1. 최근 {request.days_back}일 내 질문이 있는지\n"
                    f"  2. 여러 사용자가 같은 질문을 했는지 (한 사용자가 여러 번은 제외)\n"
                    f"  3. 질문이 {request.min_frequency}회 이상인지\n"
                    f"  4. 클러스터링 로직이 너무 엄격한지 (유사도 임계값: {self.CLUSTERING_SIMILARITY_THRESHOLD})\n"
                    f"  5. 질문 후 시간이 지났는지 확인 (클러스터링 처리 시간 필요)\n"
                    f"  6. 백엔드 로그 확인: /admin/chat/logs 에서 최근 질문 확인\n"
                    f"  [디버깅] 현재 요청: domain={request.domain}, "
                    f"min_frequency={request.min_frequency}, "
                    f"days_back={request.days_back}, "
                    f"messages_count={len(messages)}, "
                    f"es_index={self._es_faq_log_index}"
                )
                return FaqAutoGenerateResponse(
                    status="SUCCESS",
                    candidates_found=0,
                    drafts_generated=0,
                    drafts_failed=0,
                    candidates=[],
                    drafts=[],
                    error_message=error_msg,
                )

            # 3. FAQ 초안 생성 (auto_generate_drafts가 true인 경우)
            drafts: List[FaqDraft] = []
            drafts_failed = 0

            if request.auto_generate_drafts:
                for candidate in candidates:
                    try:
                        draft = await self._generate_draft_from_candidate(
                            candidate, llm_model=request.llm_model
                        )
                        drafts.append(draft)
                    except Exception as e:
                        logger.warning(
                            f"Failed to generate draft for candidate {candidate.candidate_id}: {e}"
                        )
                        drafts_failed += 1

            # 4. 상태 결정
            if request.auto_generate_drafts:
                if drafts_failed == 0:
                    status = "SUCCESS"
                elif len(drafts) > 0:
                    status = "PARTIAL"
                else:
                    status = "FAILED"
            else:
                status = "SUCCESS"

            return FaqAutoGenerateResponse(
                status=status,
                candidates_found=len(candidates),
                drafts_generated=len(drafts),
                drafts_failed=drafts_failed,
                candidates=candidates,
                drafts=drafts,
                error_message=None,
            )

        except Exception as e:
            logger.exception(f"Auto FAQ generation failed: {e}")
            return FaqAutoGenerateResponse(
                status="FAILED",
                candidates_found=0,
                drafts_generated=0,
                drafts_failed=0,
                candidates=[],
                drafts=[],
                error_message=f"자동 FAQ 생성 실패: {str(e)}",
            )

    async def _fetch_chat_messages(
        self, request: FaqAutoGenerateRequest
    ) -> List[ChatMessage]:
        """
        Elasticsearch에서 FAQ 로그를 조회합니다.

        FAQ 자동 생성을 위해 Elasticsearch의 chat_log 인덱스에서 질문 로그를 가져옵니다.
        (백엔드가 관리하는 chat_log 인덱스에서 조회)

        Args:
            request: 자동 FAQ 생성 요청

        Returns:
            List[ChatMessage]: 질문 로그 메시지 목록
        """
        # Elasticsearch에서 chat_log 직접 조회
        messages = await self._fetch_faq_logs_from_elasticsearch(request)
        
        if messages:
            logger.info(f"Elasticsearch {self._es_faq_log_index} 인덱스에서 {len(messages)}개의 채팅 로그를 조회했습니다.")
            return messages
        
        # 폴백: 백엔드 API 사용 (하위 호환성)
        logger.warning("Elasticsearch에서 채팅 로그를 조회할 수 없어 백엔드 API를 사용합니다.")
        return await self._fetch_chat_messages_from_backend(request)
    
    async def _fetch_faq_logs_from_elasticsearch(
        self, request: FaqAutoGenerateRequest
    ) -> List[ChatMessage]:
        """
        Elasticsearch에서 FAQ 로그를 조회합니다.

        백엔드가 관리하는 chat_log 인덱스에서 조회합니다.
        (백엔드가 채팅 메시지를 저장할 때 자동으로 chat_log 인덱스에 저장됨)

        Args:
            request: 자동 FAQ 생성 요청

        Returns:
            List[ChatMessage]: 질문 로그 메시지 목록
        """
        if not self._es_base_url:
            logger.warning("ELASTICSEARCH_URL이 설정되지 않았습니다.")
            return []
        
        try:
            # 날짜 범위 계산
            end_date = datetime.now(timezone.utc)
            start_date = end_date - timedelta(days=request.days_back)
            
            logger.debug(
                f"[FAQ_LOG] Elasticsearch 조회 파라미터: "
                f"days_back={request.days_back}, "
                f"start_date={start_date.date()}, "
                f"end_date={end_date.date()}, "
                f"date_range_days={(end_date - start_date).days}"
            )
            
            # Elasticsearch 쿼리 생성
            # 백엔드가 관리하는 chat_log 인덱스에서 조회 (백엔드 변경사항 반영)
            # 백엔드가 createdAt 필드로 쿼리 필터를 사용하므로, createdAt 필드를 우선 사용
            query = {
                "size": 10000,  # 충분히 큰 값 (실제로는 스크롤 사용 권장)
                "sort": [{"createdAt": {"order": "desc", "missing": "_last"}}, {"@timestamp": {"order": "desc"}}],  # createdAt 우선, 없으면 @timestamp
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"role": "user"}},  # 사용자 질문만 조회 (백엔드 chat_log 인덱스 구조에 맞춤)
                            {
                                "range": {
                                    "createdAt": {  # 백엔드 쿼리 필터와 일치하도록 createdAt 사용
                                        "gte": start_date.isoformat() + "Z",
                                        "lte": end_date.isoformat() + "Z",
                                    }
                                }
                            },
                            # 질문 내용이 있는 것만 (백엔드 chat_log 인덱스는 content 필드에 질문 저장)
                            {"bool": {"should": [
                                {"exists": {"field": "content"}},
                                {"exists": {"field": "question_masked"}},
                                {"exists": {"field": "questionMasked"}},
                            ]}},
                        ]
                    }
                },
            }
            
            # 도메인 필터 추가 (요청에 도메인이 있는 경우)
            if request.domain:
                query["query"]["bool"]["filter"].append(
                    {"term": {"domain": request.domain}}
                )
            
            # Elasticsearch 조회: 백엔드 chat_log 인덱스에서 직접 조회
            client = get_async_http_client()
            es_url = f"{self._es_base_url}/{self._es_faq_log_index}/_search"
            logger.info(
                f"[FAQ_LOG] Elasticsearch 조회 시작: URL={es_url}, "
                f"날짜 범위={start_date.date()} ~ {end_date.date()}, "
                f"days_back={request.days_back}, "
                f"min_frequency={request.min_frequency}, "
                f"인덱스={self._es_faq_log_index}"
            )
            
            response = await client.post(
                es_url,
                json=query,
                timeout=30.0,
            )
            
            if response.status_code == 404:
                logger.warning(
                    f"⚠️ [FAQ_LOG] Elasticsearch 인덱스를 찾을 수 없습니다: {self._es_faq_log_index}\n"
                    f"  가능한 원인:\n"
                    f"  1. 인덱스가 아직 생성되지 않았습니다 (최근 채팅이 발생했는지 확인)\n"
                    f"  2. 인덱스 이름이 잘못되었습니다 (예상: chat_log)\n"
                    f"  3. Elasticsearch URL이 올바른지 확인: {self._es_base_url}\n"
                    f"  확인 방법: curl {self._es_base_url}/_cat/indices/chat_log?v"
                )
                return []
            elif response.status_code != 200:
                logger.warning(
                    f"⚠️ [FAQ_LOG] Elasticsearch FAQ 로그 조회 실패: HTTP {response.status_code}\n"
                    f"  URL: {es_url}\n"
                    f"  응답: {response.text[:500]}"
                )
                return []
            
            # 응답 파싱
            data = response.json()
            hits = data.get("hits", {}).get("hits", [])
            
            messages: List[ChatMessage] = []
            for hit in hits:
                try:
                    source = hit.get("_source", {})
                    # 백엔드 chat_log 인덱스 구조: content 필드에 질문 저장 (camelCase 필드명 사용)
                    # question_masked 또는 questionMasked가 있으면 우선 사용, 없으면 content 사용
                    content = source.get("question_masked") or source.get("questionMasked") or source.get("content")
                    if not content:
                        continue
                    
                    # createdAt 파싱 (백엔드 쿼리 필터와 일치하도록 createdAt 우선 사용)
                    # createdAt이 없으면 @timestamp 폴백
                    timestamp_str = source.get("createdAt") or source.get("@timestamp")
                    if timestamp_str:
                        # ISO 형식 파싱 (예: "2026-01-09T05:47:33.451Z")
                        try:
                            if isinstance(timestamp_str, str):
                                if timestamp_str.endswith("Z"):
                                    timestamp_str = timestamp_str[:-1] + "+00:00"
                                created_at = datetime.fromisoformat(timestamp_str)
                            else:
                                created_at = timestamp_str
                        except Exception:
                            created_at = datetime.now(timezone.utc)
                    else:
                        created_at = datetime.now(timezone.utc)
                    
                    # 백엔드 chat_log 인덱스 구조: camelCase 필드명 사용
                    # userId, sessionId 필드 사용 (user_id, session_id 폴백)
                    user_id = source.get("userId") or source.get("user_id") or ""
                    session_id = source.get("sessionId") or source.get("session_id") or ""
                    
                    # user_id가 없으면 경고 및 건너뜀 (클러스터링에서 필터링되므로)
                    if not user_id:
                        logger.warning(
                            f"⚠️ 채팅 로그에 user_id가 없습니다 (건너뜀): "
                            f"content='{content[:50]}...', domain={source.get('domain')}, "
                            f"userId={source.get('userId')}, user_id={source.get('user_id')}"
                        )
                        continue  # user_id가 없으면 건너뜀
                    
                    messages.append(
                        ChatMessage(
                            content=content,
                            domain=source.get("domain"),
                            user_id=user_id,
                            session_id=session_id,
                            created_at=created_at,
                        )
                    )
                except Exception as e:
                    logger.warning(f"FAQ 로그 파싱 실패 (건너뜀): {e}")
                    continue
            
            # 디버깅: 조회된 메시지 샘플 출력
            total_hits = data.get("hits", {}).get("total", {})
            total_count = total_hits.get("value", 0) if isinstance(total_hits, dict) else len(hits)
            
            logger.info(
                f"[FAQ_LOG] Elasticsearch 조회 성공: {len(messages)}개 메시지 파싱됨 "
                f"(총 {total_count}개 히트 중, 인덱스: {self._es_faq_log_index}, "
                f"날짜 범위: {start_date.date()} ~ {end_date.date()})"
            )
            
            # 디버깅: 조회된 메시지 샘플 (최대 5개)
            if messages:
                logger.info(
                    f"[FAQ_LOG] 메시지 샘플 (최대 5개): "
                    + " | ".join([
                        f"user_id={m.user_id[:8]}..., content='{m.content[:30]}...', domain={m.domain}"
                        for m in messages[:5]
                    ])
                )
                # user_id별 통계
                user_counts = {}
                for m in messages:
                    user_counts[m.user_id] = user_counts.get(m.user_id, 0) + 1
                logger.info(
                    f"[FAQ_LOG] 사용자별 통계: {len(user_counts)}명의 사용자, "
                    f"상위 5명: {dict(sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:5])}"
                )
            else:
                # 상세한 디버깅 정보 출력
                logger.warning(
                    f"⚠️ [FAQ_LOG] 조회된 메시지가 0개입니다. 디버깅 정보:\n"
                    f"  1. Elasticsearch 인덱스: {self._es_faq_log_index}\n"
                    f"  2. Elasticsearch URL: {self._es_base_url}\n"
                    f"  3. 날짜 범위: {start_date.date()} ~ {end_date.date()} ({request.days_back}일)\n"
                    f"  4. role 필터: user (사용자 질문만 조회)\n"
                    f"  5. 총 히트 수: {total_count}개\n"
                    f"  6. 파싱 실패 또는 user_id 누락으로 인한 필터링 가능성"
                )
                
                # 실제 히트가 있는데 파싱이 안 된 경우
                if len(hits) > 0:
                    logger.warning(
                        f"⚠️ [FAQ_LOG] 히트는 {len(hits)}개 있지만 파싱된 메시지가 0개입니다. "
                        f"샘플 히트 확인:"
                    )
                    for i, hit in enumerate(hits[:3], 1):
                        source = hit.get("_source", {})
                        logger.warning(
                            f"  샘플 {i}: userId={source.get('userId', '없음')}, "
                            f"sessionId={source.get('sessionId', '없음')}, "
                            f"content={bool(source.get('content'))}, "
                            f"question_masked={bool(source.get('question_masked'))}, "
                            f"questionMasked={bool(source.get('questionMasked'))}, "
                            f"role={source.get('role')}, "
                            f"domain={source.get('domain')}"
                        )
                else:
                    logger.warning(
                        f"⚠️ [FAQ_LOG] Elasticsearch에서 히트가 0개입니다. "
                        f"다음을 확인하세요:\n"
                        f"  - 인덱스가 존재하는지: curl {self._es_base_url}/_cat/indices/{self._es_faq_log_index}?v\n"
                        f"  - 최근 채팅이 발생했는지 확인\n"
                        f"  - 백엔드가 chat_log 인덱스에 정상적으로 저장하는지 확인"
                    )
            
            return messages
            
        except Exception as e:
            logger.exception(f"Elasticsearch 채팅 로그 조회 중 오류 발생: {e}")
            return []
    
    async def _fetch_chat_messages_from_backend(
        self, request: FaqAutoGenerateRequest
    ) -> List[ChatMessage]:
        """
        백엔드 API에서 질문 로그를 조회합니다 (폴백용).

        Args:
            request: 자동 FAQ 생성 요청

        Returns:
            List[ChatMessage]: 질문 로그 메시지 목록
        """
        if not self._backend_client.is_configured:
            logger.warning(
                "백엔드 API가 설정되지 않았습니다. BACKEND_BASE_URL 환경변수를 설정하세요."
            )
            return []

        try:
            # 백엔드 API 호출
            # 엔드포인트: GET /internal/chat/admin/messages (인증 불필요)
            # 파라미터: daysBack (camelCase), domain (선택), role은 백엔드에서 자동 필터링
            params: Dict[str, Any] = {
                "daysBack": request.days_back,  # camelCase
            }
            if request.domain:
                params["domain"] = request.domain

            # 백엔드 base URL 가져오기
            base_url = (
                self._backend_client._base_url
                if hasattr(self._backend_client, "_base_url")
                else ""
            )

            # 올바른 엔드포인트 경로 (백엔드 팀 답변에 따름)
            endpoint_path = "/internal/chat/admin/messages"
            
            # 백엔드 팀 답변: API Gateway(8085) 또는 Chat Service 직접(9005) 호출 가능
            # API Gateway를 통한 호출이 404인 경우 Chat Service 직접 호출 시도
            base_urls_to_try = []
            if base_url:
                base_urls_to_try.append(base_url)  # API Gateway (예: http://localhost:8085)
            # Chat Service 직접 호출 (포트 9005)
            # base_url이 http://localhost:8085인 경우 http://localhost:9005로 변경
            if base_url and "8085" in base_url:
                chat_service_url = base_url.replace("8085", "9005")
                base_urls_to_try.append(chat_service_url)
            elif not base_url:
                # base_url이 없으면 기본값으로 시도
                base_urls_to_try.append("http://localhost:9005")

            for try_base_url in base_urls_to_try:
                full_url = f"{try_base_url}{endpoint_path}"
                
                logger.info(
                    f"백엔드 API 호출 시도: {full_url} with params={params}"
                )

                # 인증 없이 호출 (백엔드 팀 답변: /internal/** 경로는 인증 불필요)
                response = await self._call_backend_without_auth(full_url, params)

                logger.info(
                    f"백엔드 API 응답: endpoint={endpoint_path}, base_url={try_base_url}, "
                    f"success={response.success}, "
                    f"error={response.error_message if not response.success else None}"
                )

                if response.success and response.data:
                    # 응답 파싱 및 ChatMessage 변환
                    # 백엔드 응답 형식: { "messages": [...], "totalCount": ... }
                    messages_data = response.data
                    if isinstance(messages_data, dict) and "messages" in messages_data:
                        logger.info(
                            f"백엔드 API 호출 성공: {full_url}, "
                            f"messages_count={len(messages_data.get('messages', []))}"
                        )
                        return self._parse_chat_messages(messages_data["messages"])
                    elif isinstance(messages_data, list):
                        # 리스트 형식도 지원 (하위 호환성)
                        logger.info(
                            f"백엔드 API 호출 성공: {full_url}, "
                            f"messages_count={len(messages_data)}"
                        )
                        return self._parse_chat_messages(messages_data)
                    else:
                        logger.warning(
                            f"예상하지 못한 응답 형식: {type(messages_data)}"
                        )
                        continue
                else:
                    # 실패한 경우 다음 URL 시도
                    logger.debug(
                        f"백엔드 API 호출 실패, 다음 URL 시도: {try_base_url}"
                    )
                    continue

            # 모든 URL 시도 실패
            logger.warning(
                f"모든 백엔드 URL 시도 실패. "
                f"시도한 URL: {base_urls_to_try}"
            )
            return []

        except Exception as e:
            logger.exception(f"질문 로그 조회 중 오류 발생: {e}")
            return []

    async def _call_backend_without_auth(
        self, full_url: str, params: Dict[str, Any]
    ) -> Any:
        """
        인증 없이 백엔드 API를 호출합니다.

        /internal/** 경로는 인증이 필요 없습니다 (백엔드 팀 답변).

        Args:
            full_url: 전체 URL
            params: 쿼리 파라미터

        Returns:
            BackendDataResponse: 백엔드 응답
        """
        from app.clients.http_client import get_async_http_client
        from app.clients.backend_client import BackendDataResponse

        try:
            client = get_async_http_client()
            # 인증 헤더 없이 호출 (Content-Type만 설정)
            headers = {"Content-Type": "application/json"}

            logger.debug(f"인증 없이 백엔드 API 호출: {full_url}")

            response = await client.get(
                full_url,
                params=params,
                headers=headers,
                timeout=self._backend_client._timeout,
            )

            if response.status_code == 200:
                try:
                    data = response.json()
                    logger.info(f"백엔드 API 호출 성공: {full_url}")
                    return BackendDataResponse(success=True, data=data)
                except Exception as e:
                    logger.debug(f"JSON 파싱 실패: {e}")
                    return BackendDataResponse(
                        success=False, error_message="Invalid JSON response"
                    )
            else:
                logger.warning(
                    f"백엔드 API 호출 실패: HTTP {response.status_code}, {full_url}, "
                    f"response={response.text[:200]}"
                )
                return BackendDataResponse(
                    success=False, error_message=f"HTTP {response.status_code}"
                )
        except Exception as e:
            logger.debug(f"백엔드 API 호출 예외: {e}")
            return BackendDataResponse(success=False, error_message=str(e))

    def _parse_chat_messages(self, data: List[Dict[str, Any]]) -> List[ChatMessage]:
        """
        백엔드 API 응답을 ChatMessage 리스트로 변환합니다.

        Args:
            data: 백엔드 API 응답 데이터 (리스트)

        Returns:
            List[ChatMessage]: 변환된 메시지 목록
        """
        messages: List[ChatMessage] = []

        for item in data:
            try:
                # 필수 필드 추출
                content = item.get("content") or item.get("message") or item.get("text")
                if not content:
                    continue

                user_id = item.get("user_id") or item.get("userId") or ""
                session_id = item.get("session_id") or item.get("sessionId") or ""
                domain = item.get("domain")

                # created_at 파싱
                created_at_str = item.get("created_at") or item.get("createdAt")
                if created_at_str:
                    try:
                        # ISO 8601 형식 파싱
                        if isinstance(created_at_str, str):
                            # 타임존 정보가 있으면 그대로 파싱, 없으면 UTC로 가정
                            if "T" in created_at_str:
                                if created_at_str.endswith("Z"):
                                    created_at = datetime.fromisoformat(
                                        created_at_str.replace("Z", "+00:00")
                                    )
                                elif "+" in created_at_str or created_at_str.count("-") >= 3:
                                    created_at = datetime.fromisoformat(created_at_str)
                                else:
                                    created_at = datetime.fromisoformat(
                                        created_at_str + "+00:00"
                                    )
                            else:
                                # 날짜만 있는 경우
                                created_at = datetime.fromisoformat(created_at_str + "T00:00:00+00:00")
                        else:
                            # 이미 datetime 객체인 경우
                            created_at = created_at_str
                    except Exception as e:
                        logger.debug(f"created_at 파싱 실패: {created_at_str}, {e}")
                        created_at = datetime.now(timezone.utc)
                else:
                    created_at = datetime.now(timezone.utc)

                # timezone 정보가 없으면 UTC로 설정
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)

                message = ChatMessage(
                    content=content,
                    domain=domain,
                    user_id=user_id,
                    session_id=session_id,
                    created_at=created_at,
                )
                messages.append(message)

            except Exception as e:
                logger.debug(f"메시지 파싱 실패: {item}, {e}")
                continue

        return messages

    async def _select_candidates(
        self, messages: List[ChatMessage], request: FaqAutoGenerateRequest
    ) -> List[FaqCandidate]:
        """
        질문 로그를 분석하여 FAQ 후보를 선정합니다.

        Args:
            messages: 질문 로그 메시지 목록
            request: 자동 FAQ 생성 요청

        Returns:
            List[FaqCandidate]: 선정된 FAQ 후보 목록
        """
        # 1. 도메인 필터링
        if request.domain:
            messages = [m for m in messages if m.domain == request.domain]

        # 2. 질문 클러스터링 (임베딩 기반 유사도)
        clusters = await self._cluster_questions_embedding(messages)
        logger.info(
            f"클러스터링 완료: {len(clusters)}개 클러스터 생성 "
            f"(입력 메시지: {len(messages)}개)"
        )

        # 3. 빈도 분석 및 필터링
        candidates: List[FaqCandidate] = []
        filtered_count = 0
        filtered_clusters_info = []  # 디버깅용
        
        for cluster_id, cluster_messages in clusters.items():
            # 여러 사용자 간의 질문 빈도 계산
            unique_users = set(m.user_id for m in cluster_messages if m.user_id)
            user_count = len(unique_users)
            
            # user_id가 없는 메시지 경고
            empty_user_count = sum(1 for m in cluster_messages if not m.user_id)
            if empty_user_count > 0:
                logger.warning(
                    f"⚠️ 클러스터 {cluster_id[:20]}...에 user_id가 없는 메시지 {empty_user_count}개 포함"
                )

            # min_frequency 이상인 것만 선정
            if user_count < request.min_frequency:
                filtered_count += 1
                filtered_clusters_info.append({
                    "cluster_id": cluster_id,
                    "user_count": user_count,
                    "message_count": len(cluster_messages),
                    "sample_question": cluster_messages[0].content[:50] if cluster_messages else "",
                    "unique_user_ids": list(unique_users)[:3]  # 샘플
                })
                logger.debug(
                    f"클러스터 {cluster_id[:20]}... 필터링됨: "
                    f"user_count={user_count} < min_frequency={request.min_frequency}, "
                    f"메시지 수={len(cluster_messages)}, "
                    f"질문 샘플='{cluster_messages[0].content[:50] if cluster_messages else ''}...'"
                )
                continue

            # 대표 질문 선택 (가장 많이 나온 질문)
            canonical_question = self._select_canonical_question(cluster_messages)

            # 샘플 질문 수집 (최대 5개)
            sample_questions = [
                m.content
                for m in cluster_messages[:5]
                if m.content != canonical_question
            ]

            # 점수 계산
            frequency_score = min(user_count / 10.0, 1.0)  # 최대 10명 기준
            recency_score = self._calculate_recency_score(cluster_messages)
            total_score = (frequency_score * 0.7) + (recency_score * 0.3)

            # 도메인 추출 및 자동 분류
            provided_domain = cluster_messages[0].domain
            # 질문 기반으로 도메인 자동 분류
            classified_domain = classify_faq_domain(
                question=canonical_question,
                provided_domain=provided_domain
            )
            
            if provided_domain and provided_domain.upper() != classified_domain:
                logger.debug(
                    f"FAQ domain auto-classified in candidate: "
                    f"{provided_domain} → {classified_domain}, "
                    f"question='{canonical_question[:50]}...'"
                )

            candidate = FaqCandidate(
                candidate_id=str(uuid.uuid4()),
                cluster_id=cluster_id,
                canonical_question=canonical_question,
                frequency_score=frequency_score,
                recency_score=recency_score,
                total_score=total_score,
                domain=classified_domain,  # 분류된 도메인 사용
                sample_questions=sample_questions,
                user_count=user_count,
            )
            candidates.append(candidate)

        # 4. 점수 순으로 정렬 및 제한
        candidates.sort(key=lambda x: x.total_score, reverse=True)
        candidates = candidates[: request.max_candidates]

        logger.info(
            f"후보 선정 완료: 총 {len(clusters)}개 클러스터 중 "
            f"{filtered_count}개 필터링됨, {len(candidates)}개 선정됨"
        )
        
        # 디버깅: 후보가 0개일 때 상세 정보 출력
        if len(candidates) == 0 and len(clusters) > 0:
            logger.warning(
                f"⚠️ 모든 클러스터가 필터링되었습니다 (min_frequency={request.min_frequency}). "
                f"필터링된 클러스터 상위 5개:"
            )
            for i, info in enumerate(filtered_clusters_info[:5], 1):
                logger.warning(
                    f"  {i}. 클러스터 {info['cluster_id'][:20]}...: "
                    f"user_count={info['user_count']} (필요: {request.min_frequency}), "
                    f"메시지 수={info['message_count']}, "
                    f"질문='{info['sample_question']}...', "
                    f"사용자 샘플={info['unique_user_ids']}"
                )
        elif len(clusters) == 0:
            logger.warning(
                f"⚠️ 클러스터가 0개입니다. 클러스터링이 실패했거나 입력 메시지가 없습니다."
            )

        return candidates

    async def _cluster_questions_embedding(
        self, messages: List[ChatMessage]
    ) -> Dict[str, List[ChatMessage]]:
        """
        임베딩 기반으로 유사한 질문들을 클러스터링합니다.

        각 질문의 임베딩 벡터를 생성하고, 유사도가 임계값 이상이면 같은 클러스터로 묶습니다.

        Args:
            messages: 질문 로그 메시지 목록

        Returns:
            Dict[str, List[ChatMessage]]: 클러스터 ID를 키로 하는 클러스터 딕셔너리
        """
        if not messages:
            return {}

        # Milvus 클라이언트 lazy initialization
        if self._milvus_client is None:
            try:
                self._milvus_client = get_milvus_client()
            except Exception as e:
                logger.warning(
                    f"Milvus 클라이언트 초기화 실패, 간단한 정규화 기반 클러스터링으로 fallback: {e}"
                )
                return self._cluster_questions_fallback(messages)

        try:
            # 1. 질문 텍스트 정규화 및 중복 제거
            normalized_questions: Dict[str, str] = {}  # normalized -> original
            for msg in messages:
                normalized = self._normalize_question(msg.content)
                if normalized and normalized not in normalized_questions:
                    normalized_questions[normalized] = msg.content

            if not normalized_questions:
                return {}

            # 2. 임베딩 생성 (배치 처리)
            question_texts = list(normalized_questions.keys())
            embeddings: List[List[float]] = []
            
            logger.info(f"임베딩 생성 시작: {len(question_texts)}개 질문")
            for text in question_texts:
                try:
                    embedding = await self._milvus_client.generate_embedding(text)
                    embeddings.append(embedding)
                except Exception as e:
                    logger.warning(f"임베딩 생성 실패: {text[:50]}..., error: {e}")
                    # 실패한 경우 fallback으로 처리
                    return self._cluster_questions_fallback(messages)

            if len(embeddings) != len(question_texts):
                logger.warning("일부 임베딩 생성 실패, fallback으로 전환")
                return self._cluster_questions_fallback(messages)

            # 3. 임베딩 기반 클러스터링
            embeddings_array = np.array(embeddings, dtype=np.float32)
            # L2 정규화
            norms = np.linalg.norm(embeddings_array, axis=1, keepdims=True)
            norms[norms == 0] = 1  # 0으로 나누기 방지
            embeddings_normalized = embeddings_array / norms

            clusters: Dict[str, List[ChatMessage]] = defaultdict(list)
            cluster_centers: List[np.ndarray] = []  # 각 클러스터의 대표 임베딩
            cluster_keys: List[str] = []  # 각 클러스터의 키

            for idx, (text, embedding) in enumerate(zip(question_texts, embeddings_normalized)):
                original_question = normalized_questions[text]
                # 해당 질문을 포함한 메시지 찾기
                matched_messages = [m for m in messages if self._normalize_question(m.content) == text]

                if not matched_messages:
                    continue

                # 기존 클러스터 중 유사도가 임계값 이상인 클러스터 찾기
                best_cluster_idx = None
                best_similarity = 0.0

                for cluster_idx, center in enumerate(cluster_centers):
                    # 코사인 유사도 계산
                    similarity = float(np.dot(embedding, center))
                    if similarity >= self.CLUSTERING_SIMILARITY_THRESHOLD and similarity > best_similarity:
                        best_similarity = similarity
                        best_cluster_idx = cluster_idx
                
                # 디버깅: 유사도 로깅 (임계값 근처의 케이스)
                if best_cluster_idx is None and cluster_centers:
                    # 임계값보다 낮지만 가장 높은 유사도 찾기
                    max_sim = max(float(np.dot(embedding, center)) for center in cluster_centers)
                    if max_sim >= 0.70:  # 임계값보다 낮지만 어느 정도 유사한 경우
                        logger.debug(
                            f"클러스터 매칭 실패 (유사도 부족): "
                            f"question='{text[:50]}...', max_similarity={max_sim:.3f} < threshold={self.CLUSTERING_SIMILARITY_THRESHOLD}"
                        )

                if best_cluster_idx is not None:
                    # 기존 클러스터에 추가
                    cluster_key = cluster_keys[best_cluster_idx]
                    old_message_count = len(clusters[cluster_key])
                    clusters[cluster_key].extend(matched_messages)
                    logger.debug(
                        f"클러스터 매칭 성공: question='{text[:50]}...' → cluster={cluster_key[:16]}... "
                        f"(similarity={best_similarity:.3f}, messages: {old_message_count} → {len(clusters[cluster_key])})"
                    )
                    
                    # 클러스터 중심 업데이트 (가중 평균 후 정규화)
                    # 현재 클러스터의 메시지 수 (새로 추가된 것 제외)
                    old_count = len(clusters[cluster_key]) - len(matched_messages)
                    new_count = len(clusters[cluster_key])
                    
                    if old_count > 0:
                        # 가중 평균: (기존 중심 * 기존 수 + 새 임베딩 * 새 수) / 총 수
                        new_center = (
                            cluster_centers[best_cluster_idx] * old_count +
                            embedding * len(matched_messages)
                        ) / new_count
                    else:
                        # 첫 번째 질문인 경우
                        new_center = embedding
                    
                    # L2 정규화
                    norm = np.linalg.norm(new_center)
                    if norm > 0:
                        new_center = new_center / norm
                    cluster_centers[best_cluster_idx] = new_center
                else:
                    # 새로운 클러스터 생성
                    cluster_key = f"cluster-{uuid.uuid4().hex[:8]}"
                    clusters[cluster_key] = matched_messages
                    cluster_centers.append(embedding.copy())
                    cluster_keys.append(cluster_key)

            # 클러스터별 통계 로깅 (디버깅용)
            cluster_stats = []
            for cluster_key, cluster_msgs in clusters.items():
                unique_questions = set(m.content for m in cluster_msgs)
                unique_users = set(m.user_id for m in cluster_msgs)
                cluster_stats.append({
                    "cluster_id": cluster_key[:16],
                    "message_count": len(cluster_msgs),
                    "user_count": len(unique_users),
                    "unique_questions": len(unique_questions),
                    "sample_questions": list(unique_questions)[:3],  # 최대 3개 샘플
                })
            
            # 사용자 수가 많은 클러스터부터 정렬하여 로깅
            cluster_stats.sort(key=lambda x: x["user_count"], reverse=True)
            top_clusters = cluster_stats[:5]  # 상위 5개만 로깅
            
            logger.info(
                f"임베딩 기반 클러스터링 완료: {len(messages)}개 메시지 → {len(clusters)}개 클러스터 | "
                f"상위 클러스터: {[(c['cluster_id'], c['user_count'], c['message_count']) for c in top_clusters]}"
            )
            
            # 디버깅: 사용자 수가 3명 이상인 클러스터 상세 로깅
            for stat in cluster_stats:
                if stat["user_count"] >= 3:
                    logger.debug(
                        f"다중 사용자 클러스터: cluster_id={stat['cluster_id']}, "
                        f"users={stat['user_count']}, messages={stat['message_count']}, "
                        f"questions={stat['unique_questions']}, "
                        f"samples={[q[:30] for q in stat['sample_questions']]}"
                    )
            
            return dict(clusters)

        except Exception as e:
            logger.exception(f"임베딩 기반 클러스터링 실패, fallback으로 전환: {e}")
            return self._cluster_questions_fallback(messages)

    def _cluster_questions_fallback(
        self, messages: List[ChatMessage]
    ) -> Dict[str, List[ChatMessage]]:
        """
        Fallback 클러스터링: 정규화된 질문 텍스트를 키로 사용.

        임베딩 기반 클러스터링이 실패했을 때 사용됩니다.

        Args:
            messages: 질문 로그 메시지 목록

        Returns:
            Dict[str, List[ChatMessage]]: 클러스터 딕셔너리
        """
        logger.info("Fallback 클러스터링 사용 (정규화 기반)")
        clusters: Dict[str, List[ChatMessage]] = defaultdict(list)

        for message in messages:
            normalized = self._normalize_question(message.content)
            if normalized:
                cluster_key = normalized
                clusters[cluster_key].append(message)

        return dict(clusters)

    def _normalize_question(self, question: str) -> str:
        """
        질문을 정규화합니다.

        정규화 단계:
        1. 소문자 변환
        2. 연속 공백 제거
        3. 특수문자 제거 (한글, 영문, 숫자만 유지)
        4. 앞뒤 공백 제거

        Args:
            question: 원본 질문

        Returns:
            정규화된 질문
        """
        if not question:
            return ""

        # 1. 소문자 변환
        normalized = question.lower().strip()

        # 2. 연속 공백을 단일 공백으로 변환
        normalized = re.sub(r'\s+', ' ', normalized)

        # 3. 특수문자 제거 (한글, 영문, 숫자, 공백만 유지)
        # 단, 물음표는 유지 (질문 의미 파악에 중요)
        normalized = re.sub(r'[^\w\s가-힣?]', '', normalized)

        # 4. 앞뒤 공백 제거
        normalized = normalized.strip()

        return normalized

    def _select_canonical_question(
        self, cluster_messages: List[ChatMessage]
    ) -> str:
        """클러스터에서 대표 질문을 선택합니다."""
        # 가장 많이 나온 질문을 대표 질문으로 선택
        question_counts: Dict[str, int] = defaultdict(int)
        for message in cluster_messages:
            question_counts[message.content] += 1

        if question_counts:
            return max(question_counts.items(), key=lambda x: x[1])[0]
        return cluster_messages[0].content if cluster_messages else ""

    def _calculate_recency_score(self, messages: List[ChatMessage]) -> float:
        """최근성 점수를 계산합니다."""
        if not messages:
            return 0.0

        # 가장 최근 메시지의 시간
        latest_time = max(m.created_at for m in messages)
        now = datetime.now(timezone.utc)

        # 최근 7일 이내면 1.0, 30일 이내면 0.5, 그 외는 0.0
        days_diff = (now - latest_time).days
        if days_diff <= 7:
            return 1.0
        elif days_diff <= 30:
            return 0.5
        else:
            return 0.0

    async def _generate_draft_from_candidate(
        self, candidate: FaqCandidate, llm_model: Optional[str] = None
    ) -> FaqDraft:
        """
        후보로부터 FAQ 초안을 생성합니다.

        Args:
            candidate: FAQ 후보
            llm_model: LLM 프로바이더 ("exaone" | "openai" | None)

        Returns:
            FaqDraft: 생성된 FAQ 초안
        """
        # FaqDraftGenerateRequest 생성
        # candidate.domain은 이미 classify_faq_domain으로 분류된 값
        generate_request = FaqDraftGenerateRequest(
            domain=candidate.domain or FaqDomain.ETC.value,  # ETC를 기본값으로 사용
            cluster_id=candidate.cluster_id,
            canonical_question=candidate.canonical_question,
            sample_questions=candidate.sample_questions,
            top_docs=[],  # 백엔드에서 제공하지 않으면 빈 리스트
            avg_intent_confidence=None,  # 나중에 계산 가능하면 추가
            llm_model=llm_model,  # LLM 프로바이더 전달
        )

        # FAQ 초안 생성
        draft = await self._faq_service.generate_faq_draft(generate_request)
        return draft

