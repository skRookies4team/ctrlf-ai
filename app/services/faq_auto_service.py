"""
FAQ 자동 생성 서비스 (Auto FAQ Generation Service)

질문 로그를 분석하여 FAQ 후보를 선정하고, FAQ 초안을 자동 생성하는 서비스입니다.

주요 기능:
- 질문 로그 분석 (백엔드 API 호출)
- 질문 클러스터링 (유사 질문 그룹화)
- 빈도 분석 (여러 사용자 간의 질문 빈도)
- 후보 선정 및 점수 계산
- FAQ 초안 자동 생성
"""

import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.clients.backend_client import BackendClient, get_backend_client
from app.core.config import get_settings
from app.core.logging import get_logger
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
            f"Auto FAQ generation started: domain={request.domain}, "
            f"min_frequency={request.min_frequency}, days_back={request.days_back}"
        )

        try:
            # 1. 질문 로그 조회
            messages = await self._fetch_chat_messages(request)
            logger.info(f"Fetched {len(messages)} chat messages")

            if not messages:
                return FaqAutoGenerateResponse(
                    status="SUCCESS",
                    candidates_found=0,
                    drafts_generated=0,
                    drafts_failed=0,
                    candidates=[],
                    drafts=[],
                    error_message=None,
                )

            # 2. 후보 선정
            candidates = await self._select_candidates(messages, request)
            logger.info(
                f"Selected {len(candidates)} candidates "
                f"(from {len(messages)} messages, min_frequency={request.min_frequency})"
            )

            if not candidates:
                logger.warning(
                    f"FAQ 후보가 0개입니다. 다음을 확인하세요:\n"
                    f"1. 최근 {request.days_back}일 내 질문이 있는지\n"
                    f"2. 여러 사용자가 같은 질문을 했는지 (한 사용자가 여러 번은 제외)\n"
                    f"3. 질문이 {request.min_frequency}회 이상인지\n"
                    f"4. 클러스터링 로직이 너무 엄격한지"
                )
                return FaqAutoGenerateResponse(
                    status="SUCCESS",
                    candidates_found=0,
                    drafts_generated=0,
                    drafts_failed=0,
                    candidates=[],
                    drafts=[],
                    error_message=None,
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
        백엔드에서 질문 로그를 조회합니다.

        백엔드 API를 호출하여 질문 로그를 가져옵니다.
        API가 없거나 실패하는 경우 빈 리스트를 반환합니다.

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

        # 2. 질문 클러스터링 (간단한 유사도 기반)
        clusters = self._cluster_questions(messages)
        logger.debug(f"클러스터링 결과: {len(clusters)}개 클러스터 생성")

        # 3. 빈도 분석 및 필터링
        candidates: List[FaqCandidate] = []
        filtered_count = 0
        for cluster_id, cluster_messages in clusters.items():
            # 여러 사용자 간의 질문 빈도 계산
            unique_users = set(m.user_id for m in cluster_messages)
            user_count = len(unique_users)

            # min_frequency 이상인 것만 선정
            if user_count < request.min_frequency:
                filtered_count += 1
                logger.debug(
                    f"클러스터 {cluster_id[:20]}... 필터링됨: "
                    f"user_count={user_count} < min_frequency={request.min_frequency}"
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

        return candidates

    def _cluster_questions(
        self, messages: List[ChatMessage]
    ) -> Dict[str, List[ChatMessage]]:
        """
        유사한 질문들을 클러스터링합니다.

        현재는 간단한 키워드 기반 클러스터링을 사용합니다.
        나중에 더 정교한 임베딩 기반 클러스터링으로 개선할 수 있습니다.

        Args:
            messages: 질문 로그 메시지 목록

        Returns:
            Dict[str, List[ChatMessage]]: 클러스터 ID를 키로 하는 클러스터 딕셔너리
        """
        # 간단한 키워드 기반 클러스터링
        # TODO: 더 정교한 임베딩 기반 클러스터링으로 개선
        clusters: Dict[str, List[ChatMessage]] = defaultdict(list)

        for message in messages:
            # 질문 내용을 정규화 (소문자, 공백 제거 등)
            normalized = self._normalize_question(message.content)

            # 간단한 해시 기반 클러스터링 (실제로는 임베딩 기반이 필요)
            cluster_key = self._get_cluster_key(normalized)
            clusters[cluster_key].append(message)

        return dict(clusters)

    def _normalize_question(self, question: str) -> str:
        """질문을 정규화합니다."""
        # 소문자 변환, 공백 제거, 특수문자 제거 등
        normalized = question.lower().strip()
        # 간단한 정규화 (실제로는 더 정교한 전처리 필요)
        return normalized

    def _get_cluster_key(self, normalized_question: str) -> str:
        """정규화된 질문에서 클러스터 키를 생성합니다."""
        # 간단한 해시 기반 (실제로는 임베딩 기반 유사도 계산 필요)
        # 현재는 질문의 첫 50자를 키로 사용
        return normalized_question[:50] if len(normalized_question) > 50 else normalized_question

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

