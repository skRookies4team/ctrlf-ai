"""
백엔드 데이터 컨텍스트 포맷터 (Backend Context Formatter)

BackendDataClient에서 받아온 JSON 데이터를 LLM 컨텍스트용 텍스트로 변환합니다.
BACKEND_API / MIXED_BACKEND_RAG 라우트에서 LLM 프롬프트에 포함될 컨텍스트를 생성합니다.

Phase 11 신규 추가:
- 교육 현황/통계 포맷팅
- 사고 현황/상세 포맷팅
- 신고 안내 포맷팅

⚠️ 현재 상태:
- 백엔드 API 스펙이 100% 확정되지 않아 필드명은 추정치 기반
- 실제 연동 후 필드명/구조 조정 필요

사용 방법:
    from app.services.backend_context_formatter import BackendContextFormatter

    formatter = BackendContextFormatter()

    # 교육 현황 데이터를 LLM 컨텍스트로 변환
    context = formatter.format_edu_status_for_llm(edu_data)
"""

from typing import Any, Dict, List, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


class BackendContextFormatter:
    """
    백엔드 데이터를 LLM 컨텍스트 텍스트로 변환하는 포맷터.

    BackendDataClient에서 조회한 JSON 데이터를 LLM이 이해할 수 있는
    구조화된 텍스트로 변환합니다.

    Usage:
        formatter = BackendContextFormatter()

        # 직원 교육 현황
        edu_context = formatter.format_edu_status_for_llm(edu_data)

        # 부서 교육 통계 (관리자용)
        stats_context = formatter.format_edu_stats_for_llm(stats_data)

        # 사고 현황 요약
        incident_context = formatter.format_incident_overview_for_llm(incident_data)
    """

    # =========================================================================
    # 교육(EDU) 도메인 포맷팅
    # =========================================================================

    def format_edu_status_for_llm(self, data: Dict[str, Any]) -> str:
        """
        직원 교육 현황 데이터를 LLM 컨텍스트로 변환합니다.

        Args:
            data: BackendDataClient.get_employee_edu_status() 응답 데이터

        Returns:
            str: LLM 컨텍스트용 텍스트

        Example output:
            [교육 수료 현황]
            - 총 필수 교육: 4개
            - 수료 완료: 3개
            - 미수료: 1개
            - 다음 마감일: 2025-12-31

            [수료 완료 교육]
            - 정보보호교육 (2025-03-15 수료)
            - 개인정보보호교육 (2025-04-20 수료)
            - 직장 내 괴롭힘 방지 (2025-05-10 수료)

            [미수료 교육]
            - 산업안전보건 (마감: 2025-12-31)
        """
        if not data:
            return "[교육 현황 정보 없음]"

        lines: List[str] = []

        # 요약 정보
        total = data.get("total_required", 0)
        completed = data.get("completed", 0)
        pending = data.get("pending", 0)
        next_deadline = data.get("next_deadline", "정보 없음")

        lines.append("[교육 수료 현황]")
        lines.append(f"- 총 필수 교육: {total}개")
        lines.append(f"- 수료 완료: {completed}개")
        lines.append(f"- 미수료: {pending}개")
        lines.append(f"- 다음 마감일: {next_deadline}")

        # 수료/미수료 목록
        courses = data.get("courses", [])
        completed_courses = [c for c in courses if c.get("status") == "completed"]
        pending_courses = [c for c in courses if c.get("status") == "pending"]

        if completed_courses:
            lines.append("")
            lines.append("[수료 완료 교육]")
            for course in completed_courses:
                name = course.get("name", "알 수 없음")
                completed_at = course.get("completed_at", "날짜 미상")
                lines.append(f"- {name} ({completed_at} 수료)")

        if pending_courses:
            lines.append("")
            lines.append("[미수료 교육]")
            for course in pending_courses:
                name = course.get("name", "알 수 없음")
                deadline = course.get("deadline", "마감일 미정")
                lines.append(f"- {name} (마감: {deadline})")

        return "\n".join(lines)

    def format_edu_stats_for_llm(self, data: Dict[str, Any]) -> str:
        """
        부서/전체 교육 통계 데이터를 LLM 컨텍스트로 변환합니다.

        Args:
            data: BackendDataClient.get_department_edu_stats() 응답 데이터

        Returns:
            str: LLM 컨텍스트용 텍스트

        Example output:
            [교육 이수 통계]
            - 대상: 개발팀 (50명)
            - 전체 이수율: 85.0%

            [교육별 현황]
            - 정보보호교육: 45/50명 수료 (미수료 5명)
            - 개인정보보호교육: 42/50명 수료 (미수료 8명)

            ※ 미수료 대상자: 15명
        """
        if not data:
            return "[교육 통계 정보 없음]"

        lines: List[str] = []

        # 요약 정보
        dept_name = data.get("department_name", "전체")
        total_employees = data.get("total_employees", 0)
        completion_rate = data.get("completion_rate", 0.0)

        lines.append("[교육 이수 통계]")
        lines.append(f"- 대상: {dept_name} ({total_employees}명)")
        lines.append(f"- 전체 이수율: {completion_rate:.1f}%")

        # 교육별 현황
        by_course = data.get("by_course", [])
        if by_course:
            lines.append("")
            lines.append("[교육별 현황]")
            for course in by_course:
                name = course.get("name", "알 수 없음")
                completed = course.get("completed", 0)
                pending = course.get("pending", 0)
                total = completed + pending
                lines.append(f"- {name}: {completed}/{total}명 수료 (미수료 {pending}명)")

        # 미수료 대상자 수
        pending_count = data.get("pending_count", 0)
        if pending_count > 0:
            lines.append("")
            lines.append(f"※ 미수료 대상자: {pending_count}명")

        return "\n".join(lines)

    # =========================================================================
    # 사고/위반(INCIDENT) 도메인 포맷팅
    # =========================================================================

    def format_incident_overview_for_llm(self, data: Dict[str, Any]) -> str:
        """
        사고 현황 요약 데이터를 LLM 컨텍스트로 변환합니다.

        Args:
            data: BackendDataClient.get_incident_overview() 응답 데이터

        Returns:
            str: LLM 컨텍스트용 텍스트

        Example output:
            [사고 현황 요약]
            - 기간: 2025-Q4
            - 총 건수: 15건

            [상태별 현황]
            - 처리 중: 8건 (open 3건, in_progress 5건)
            - 완료: 7건

            [유형별 현황]
            - 보안사고: 8건
            - 개인정보: 5건
            - 컴플라이언스: 2건

            [전분기 대비]
            - 전분기: 12건 → 현분기: 15건 (+25.0%)
        """
        if not data:
            return "[사고 현황 정보 없음]"

        lines: List[str] = []

        # 요약 정보
        period = data.get("period", "미상")
        total = data.get("total_incidents", 0)

        lines.append("[사고 현황 요약]")
        lines.append(f"- 기간: {period}")
        lines.append(f"- 총 건수: {total}건")

        # 상태별 현황
        by_status = data.get("by_status", {})
        if by_status:
            lines.append("")
            lines.append("[상태별 현황]")
            open_count = by_status.get("open", 0)
            in_progress = by_status.get("in_progress", 0)
            closed = by_status.get("closed", 0)
            lines.append(f"- 처리 중: {open_count + in_progress}건 (open {open_count}건, in_progress {in_progress}건)")
            lines.append(f"- 완료: {closed}건")

        # 유형별 현황
        by_type = data.get("by_type", {})
        if by_type:
            lines.append("")
            lines.append("[유형별 현황]")
            type_names = {
                "security": "보안사고",
                "privacy": "개인정보",
                "compliance": "컴플라이언스",
            }
            for type_key, count in by_type.items():
                type_name = type_names.get(type_key, type_key)
                lines.append(f"- {type_name}: {count}건")

        # 추이 정보
        trend = data.get("trend", {})
        if trend:
            prev = trend.get("previous_period", 0)
            change_rate = trend.get("change_rate", 0.0)
            sign = "+" if change_rate >= 0 else ""
            lines.append("")
            lines.append("[전분기 대비]")
            lines.append(f"- 전분기: {prev}건 → 현분기: {total}건 ({sign}{change_rate:.1f}%)")

        return "\n".join(lines)

    def format_incident_detail_for_llm(self, data: Dict[str, Any]) -> str:
        """
        사건 상세 데이터를 LLM 컨텍스트로 변환합니다.

        Args:
            data: BackendDataClient.get_incident_detail() 응답 데이터

        Returns:
            str: LLM 컨텍스트용 텍스트

        Note:
            - 실명/사번 등 민감 정보는 포함되지 않음
            - 징계 결과 등 확정되지 않은 정보도 포함되지 않음
        """
        if not data:
            return "[사건 상세 정보 없음]"

        lines: List[str] = []

        incident_id = data.get("incident_id", "미상")
        incident_type = data.get("type", "미분류")
        status = data.get("status", "미상")
        reported_at = data.get("reported_at", "미상")
        summary = data.get("summary", "요약 없음")
        severity = data.get("severity", "미분류")
        assigned_to = data.get("assigned_to", "미배정")

        type_names = {
            "security": "보안사고",
            "privacy": "개인정보 침해",
            "compliance": "컴플라이언스 위반",
        }
        status_names = {
            "open": "접수됨",
            "in_progress": "처리 중",
            "closed": "완료",
        }
        severity_names = {
            "low": "낮음",
            "medium": "보통",
            "high": "높음",
            "critical": "긴급",
        }

        lines.append(f"[사건 {incident_id}]")
        lines.append(f"- 유형: {type_names.get(incident_type, incident_type)}")
        lines.append(f"- 상태: {status_names.get(status, status)}")
        lines.append(f"- 심각도: {severity_names.get(severity, severity)}")
        lines.append(f"- 신고일시: {reported_at}")
        lines.append(f"- 담당: {assigned_to}")
        lines.append(f"- 개요: {summary}")

        # 관련 정책
        related_policies = data.get("related_policies", [])
        if related_policies:
            lines.append("")
            lines.append("[관련 정책/규정]")
            for policy in related_policies:
                lines.append(f"- {policy}")

        return "\n".join(lines)

    def format_report_guide_for_llm(self, data: Dict[str, Any]) -> str:
        """
        신고 안내 데이터를 LLM 컨텍스트로 변환합니다.

        Args:
            data: BackendDataClient.get_report_guide() 응답 데이터

        Returns:
            str: LLM 컨텍스트용 텍스트
        """
        if not data:
            return "[신고 안내 정보 없음]"

        lines: List[str] = []

        title = data.get("title", "사고 신고 안내")
        steps = data.get("steps", [])
        channels = data.get("official_channels", [])
        warnings = data.get("warnings", [])

        lines.append(f"[{title}]")

        if steps:
            lines.append("")
            lines.append("【신고 절차】")
            for step in steps:
                lines.append(step)

        if channels:
            lines.append("")
            lines.append("【공식 신고 채널】")
            for ch in channels:
                name = ch.get("name", "")
                contact = ch.get("contact", ch.get("url", ""))
                lines.append(f"- {name}: {contact}")

        if warnings:
            lines.append("")
            lines.append("【주의사항】")
            for warning in warnings:
                lines.append(f"⚠️ {warning}")

        return "\n".join(lines)

    # =========================================================================
    # MIXED_BACKEND_RAG용 통합 컨텍스트 포맷팅
    # =========================================================================

    def format_mixed_context(
        self,
        rag_context: str,
        backend_context: str,
        domain: str,
    ) -> str:
        """
        RAG 컨텍스트와 백엔드 데이터 컨텍스트를 통합합니다.

        MIXED_BACKEND_RAG 라우트에서 사용됩니다.

        Args:
            rag_context: RAG 검색 결과 텍스트
            backend_context: 백엔드 데이터 포맷팅 결과
            domain: 도메인 (POLICY, INCIDENT, EDU)

        Returns:
            str: 통합된 LLM 컨텍스트

        Example output:
            ═══════════════════════════════════════
            [정책/규정 근거]
            ───────────────────────────────────────
            1) [DOC-001] 정보보안정책 (p.5) [관련도: 0.85]
               발췌: 임직원은 보안교육을 연 1회 이상 이수해야 한다...

            ═══════════════════════════════════════
            [실제 현황/통계]
            ───────────────────────────────────────
            [교육 이수 통계]
            - 대상: 개발팀 (50명)
            - 전체 이수율: 85.0%
            ...
        """
        lines: List[str] = []

        domain_titles = {
            "POLICY": "정책/규정",
            "INCIDENT": "사고/위반 관련 정책",
            "EDU": "교육 관련 정책",
        }
        policy_title = domain_titles.get(domain, "정책/규정 근거")

        # 섹션 1: 정책/규정 근거 (RAG)
        lines.append("═" * 40)
        lines.append(f"[{policy_title} 근거]")
        lines.append("─" * 40)
        if rag_context.strip():
            lines.append(rag_context)
        else:
            lines.append("(관련 정책/규정 문서를 찾지 못했습니다)")

        # 섹션 2: 실제 현황/통계 (백엔드)
        lines.append("")
        lines.append("═" * 40)
        lines.append("[실제 현황/통계]")
        lines.append("─" * 40)
        if backend_context.strip():
            lines.append(backend_context)
        else:
            lines.append("(현황 데이터를 조회하지 못했습니다)")

        return "\n".join(lines)
