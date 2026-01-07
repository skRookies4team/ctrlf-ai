"""
Log Sync Service

Elasticsearch의 ai_log를 주기적으로 Backend로 동기화하는 서비스.

자동 실행:
- 서버 시작 시 백그라운드 태스크로 시작
- 설정된 주기(기본 1시간)마다 실행
- Elasticsearch에서 ai_log 조회 → Backend DTO로 정제 → Backend API로 전송
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class LogSyncService:
    """로그 동기화 서비스."""

    def __init__(self):
        """초기화."""
        self._settings = get_settings()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
        # 설정값
        self._es_url = str(self._settings.ELASTICSEARCH_URL) if self._settings.ELASTICSEARCH_URL else "http://localhost:9200"
        self._es_index = self._settings.ELASTICSEARCH_INDEX
        self._backend_url = (
            str(self._settings.BACKEND_INFRA_URL).rstrip("/")
            if self._settings.BACKEND_INFRA_URL
            else "http://localhost:9003"
        )
        self._internal_token = self._settings.BACKEND_INTERNAL_TOKEN
        self._sync_interval_seconds = self._settings.LOG_SYNC_INTERVAL_SECONDS
        self._fetch_days = self._settings.LOG_FETCH_RANGE
        self._fetch_limit = self._settings.LOG_FETCH_LIMIT

    def is_enabled(self) -> bool:
        """로그 동기화가 활성화되어 있는지 확인."""
        return bool(
            self._es_url
            and self._backend_url
            and self._internal_token
            and self._sync_interval_seconds > 0
        )

    async def start(self) -> None:
        """로그 동기화 서비스 시작."""
        if not self.is_enabled():
            logger.info(
                "LogSyncService disabled: "
                f"ES_URL={bool(self._es_url)}, "
                f"BACKEND_URL={bool(self._backend_url)}, "
                f"INTERNAL_TOKEN={bool(self._internal_token)}, "
                f"INTERVAL={self._sync_interval_seconds}s"
            )
            return

        if self._running:
            logger.warning("LogSyncService is already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_periodically())
        logger.info(
            f"LogSyncService started: "
            f"interval={self._sync_interval_seconds}s, "
            f"fetch_days={self._fetch_days}, "
            f"fetch_limit={self._fetch_limit}"
        )

    async def stop(self) -> None:
        """로그 동기화 서비스 중지."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("LogSyncService stopped")

    async def _run_periodically(self) -> None:
        """주기적으로 로그 동기화 실행."""
        # 서버 시작 시 즉시 실행하지 않고 첫 실행을 약간 지연 (30초)
        # Elasticsearch가 아직 준비되지 않았을 수 있음
        await asyncio.sleep(30)
        
        while self._running:
            try:
                await self.sync_logs()
            except Exception as e:
                # 연결 실패 등 에러는 경고로 처리하고 계속 진행
                # (Elasticsearch가 일시적으로 다운되었을 수 있음)
                logger.warning(
                    f"Log sync failed (will retry later): {type(e).__name__}: {e}"
                )

            # 다음 실행까지 대기
            try:
                await asyncio.sleep(self._sync_interval_seconds)
            except asyncio.CancelledError:
                break

    async def sync_logs(self) -> None:
        """로그 동기화 실행 (수동 호출 가능)."""
        logger.info("=== AI LOG SYNC START ===")

        # 1. Elasticsearch에서 ai_log 조회
        try:
            hits = await self._fetch_ai_logs()
            logger.info(f"Fetched {len(hits)} ai_log records from Elasticsearch")
        except Exception as e:
            logger.warning(f"Failed to fetch logs from Elasticsearch: {e}")
            logger.info("=== AI LOG SYNC DONE (skipped) ===")
            return

        if not hits:
            logger.info("No logs to sync")
            logger.info("=== AI LOG SYNC DONE ===")
            return

        # 2. Backend DTO 스키마에 맞게 정제
        refined_logs = self._refine_logs(hits)
        logger.info(f"Refined logs count: {len(refined_logs)}")

        if not refined_logs:
            logger.info("No valid logs to push")
            logger.info("=== AI LOG SYNC DONE ===")
            return

        # 3. Backend로 전송
        try:
            result = await self._push_to_backend(refined_logs)
            saved = result.get("saved", 0)
            skipped = result.get("skipped", 0)
            failed = result.get("failed", 0)
            logger.info(
                f"Log sync completed: "
                f"sent={len(refined_logs)}, saved={saved}, skipped={skipped}, failed={failed}"
            )
        except Exception as e:
            logger.warning(f"Failed to push logs to Backend: {e}")
            logger.info("=== AI LOG SYNC DONE (failed) ===")
            return

        logger.info("=== AI LOG SYNC DONE ===")

    async def _fetch_ai_logs(self) -> List[Dict[str, Any]]:
        """Elasticsearch에서 ai_log 조회."""
        query = {
            "size": self._fetch_limit,
            "sort": [{"@timestamp": {"order": "desc"}}],
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"log_type": "ai_log"}},
                        {"range": {"@timestamp": {"gte": f"now-{self._fetch_days}"}}},
                    ]
                }
            },
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self._es_url}/{self._es_index}/_search",
                json=query,
            )
            resp.raise_for_status()

        hits = resp.json().get("hits", {}).get("hits", [])
        return hits

    def _refine_log(self, hit: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """로그를 Backend DTO 스키마에 맞게 정제."""
        src = hit.get("_source", {})

        created_at = src.get("@timestamp")
        user_id = src.get("user_id")

        if not created_at or not user_id:
            return None

        return {
            # 필수
            "createdAt": created_at,
            "userId": user_id,
            # 선택
            "userRole": src.get("user_role"),
            "department": src.get("department"),
            "domain": src.get("domain"),
            "route": src.get("route"),
            "modelName": src.get("model_name"),
            "hasPiiInput": src.get("has_pii_input", False),
            "hasPiiOutput": src.get("has_pii_output", False),
            "ragUsed": src.get("rag_used", False),
            "ragSourceCount": src.get("rag_source_count", 0),
            "latencyMsTotal": src.get("latency_ms"),
            "errorCode": src.get("error_code"),
            # Trace
            "traceId": src.get("trace_id"),
            "conversationId": src.get("session_id"),
            "turnId": src.get("turn_index"),
        }

    def _refine_logs(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """로그 목록 정제."""
        refined: List[Dict[str, Any]] = []

        for h in hits:
            try:
                refined_log = self._refine_log(h)
                if refined_log:
                    refined.append(refined_log)
            except Exception as e:
                logger.warning(f"Skip invalid log: {e}")

        return refined

    async def _push_to_backend(self, logs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Backend로 로그 전송.
        
        백엔드 처리 흐름:
        1. 필수 필드 검증
        2. 중복 체크 (traceId + conversationId + turnId)
        3. Bulk insert (saveAll)
        4. 응답 반환 (received, saved, skipped, failed)
        
        Returns:
            백엔드 응답 (received, saved, skipped, failed)
        """
        payload = {"logs": logs}

        headers = {
            "Content-Type": "application/json",
            "X-Internal-Token": self._internal_token,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{self._backend_url}/internal/ai/logs/bulk",
                json=payload,
                headers=headers,
            )

            if resp.status_code != 200:
                raise RuntimeError(
                    f"Backend push failed: {resp.status_code} {resp.text}"
                )

            # 백엔드 응답 파싱
            response_data = resp.json()
            received = response_data.get("received", len(logs))
            saved = response_data.get("saved", 0)
            skipped = response_data.get("skipped", 0)
            failed = response_data.get("failed", 0)

            logger.info(
                f"Backend bulk insert result: "
                f"received={received}, saved={saved}, skipped={skipped}, failed={failed}"
            )

            return response_data


# 싱글톤 인스턴스
_log_sync_service: Optional[LogSyncService] = None


def get_log_sync_service() -> LogSyncService:
    """LogSyncService 싱글톤 인스턴스 반환."""
    global _log_sync_service
    if _log_sync_service is None:
        _log_sync_service = LogSyncService()
    return _log_sync_service

