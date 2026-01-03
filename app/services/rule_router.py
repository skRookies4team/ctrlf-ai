"""
Phase 21: 규칙 기반 라우터 (Rule Router)

키워드 기반 1차 분기를 수행합니다.
강한 룰(개인화/현황/규정/교육 키워드)로 명확한 의도를 빠르게 분류하고,
애매한 경계에서는 needs_clarify=true를 설정합니다.

주요 기능:
1. 키워드 기반 Tier0Intent 분류
2. 애매한 경계 감지 및 되묻기 설정
3. 치명 액션(퀴즈 3종) 확인 게이트 설정
4. 높은 신뢰도(confidence=0.9+) 케이스에서 LLM Router 스킵 가능

경계 A: 교육 내용 설명 vs 내 이수현황/진도(개인화)
경계 B: 규정 질문 vs HR/근태/복지 개인화(내 정보 조회)

Phase 43 업데이트 (인텐트/라우팅 개선):
- POLICY_KEYWORDS 대폭 확장 (Q세트 5개 도메인 커버)
- 질문형 어미 감지 추가 (나요/하나요/인가요 등)
- 기본값 UNKNOWN → POLICY_QA로 변경 (RAG 우선)
- GENERAL_CHAT 조건 강화 (질문형은 제외)

Phase 49 업데이트 (도메인 라우팅 개선):
- POLICY 키워드 체크 우선순위를 EDU_CONTENT보다 앞으로 조정
- 연차/휴가/근태/징계/복무 등은 POLICY로 우선 분류
- 디버그 로깅에 ASCII-safe preview 적용 (Git Bash 파이프 한글 깨짐 방지)
"""

import random
import re
from typing import Optional, Tuple

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.router_types import (
    ClarifyTemplates,
    ConfirmationTemplates,
    CRITICAL_ACTION_SUB_INTENTS,
    RouterDebugInfo,
    RouterDomain,
    RouterResult,
    RouterRouteType,
    SubIntentId,
    Tier0Intent,
    get_default_route_for_intent,
)

logger = get_logger(__name__)


# =============================================================================
# Phase 49: ASCII-safe 로깅 유틸 (Git Bash 파이프 한글 깨짐 방지)
# =============================================================================


def ascii_safe_preview(text: str, max_len: int = 50) -> str:
    """
    로그 출력용 ASCII-safe 텍스트 미리보기를 생성합니다.
    Git Bash 파이프, Windows cp949, locale 문제로 인한 한글 깨짐(mojibake) 방지.

    Args:
        text: 원본 텍스트
        max_len: 최대 길이 (truncate)

    Returns:
        str: ASCII-safe 문자열 (예: '\\ud734\\uac00 \\uaddc\\uc815')
    """
    if not text:
        return ""
    truncated = text[:max_len]
    return truncated.encode("unicode_escape").decode("ascii")


# =============================================================================
# 키워드 정의
# =============================================================================

# 정책/규정 관련 키워드 (POLICY_QA) - Phase 43 대폭 확장
POLICY_KEYWORDS = frozenset([
    # 기본 규정 키워드
    "규정", "사규", "정책", "규칙", "지침", "매뉴얼", "가이드",
    "절차", "프로세스", "승인", "결재", "보안정책", "개인정보보호",
    "허용", "금지", "위반", "제재", "징계",
    "정보보호", "보안규정", "내부규정",
    # Q세트 도메인: 사규/복무/인사
    "근무시간", "휴게시간", "지각", "결근", "무단결근", "조퇴", "외출",
    "재택근무", "연차", "휴가", "반차", "병가", "경조사", "출산휴가",
    "육아휴직", "연장근로", "야근", "당직", "인사평가", "승진",
    "부서이동", "보직변경", "징계처분", "사규개정", "법령", "복무",
    "인사", "근로기준", "취업규칙", "휴일", "휴무", "초과근무",
    # Q세트 도메인: 개인정보보호 (PIP)
    "개인정보", "민감정보", "클라우드", "usb", "이메일", "외부전송",
    "개인정보유출", "마스킹", "암호화", "보안사고", "정보주체",
    "열람권", "정정권", "삭제권", "동의", "수집", "이용", "제공",
    "개인정보처리", "cctv", "영상정보", "익명처리", "가명처리",
    # Q세트 도메인: 성희롱 방지 (SHP)
    "성희롱", "성적", "언어적", "신체적", "시각적", "성적농담",
    "불쾌", "성적수치심", "성적굴욕감", "피해자", "가해자",
    "성희롱신고", "성희롱예방", "2차피해", "피해자보호",
    # Q세트 도메인: 직장내괴롭힘 (BHP)
    "괴롭힘", "직장내괴롭힘", "폭언", "폭행", "따돌림", "왕따",
    "업무배제", "업무외지시", "사적심부름", "인격모독",
    "괴롭힘신고", "괴롭힘예방", "우월적지위", "갑질",
    # Q세트 도메인: 장애인식 (DEP)
    "장애인", "장애", "장애인식", "합리적편의", "차별금지",
    "장애인차별", "장애유형", "편견", "고정관념",
    "장애인고용", "장애인채용", "보조기기", "편의제공",
    # Q세트 도메인: 직무별교육 (JOB) - Phase 49: 교육 특화 키워드는 EDU로 이동
    "소스코드", "오픈소스", "라이선스", "api", "로그", "데이터",
    "클라우드보안", "인사정보", "민감정보처리", "ai", "외부ai",
    "보안점검", "취약점", "사이버보안", "저작권", "초상권",
    "github", "코드", "개발자",
    # Note: "보안교육", "정보보호교육"은 EDU_CONTENT_KEYWORDS로 이동 (Phase 49)
])

# 교육 내용/규정 관련 키워드 (EDUCATION_QA)
# Phase 49: 교육 특화 키워드 확장 (POLICY보다 우선 매칭)
EDU_CONTENT_KEYWORDS = frozenset([
    "교육내용", "교육자료", "교육규정", "학습내용",
    "강의내용", "교육과정", "커리큘럼",
    "4대교육", "법정교육", "의무교육",
    # 교육 특화 키워드 (Phase 49 확장)
    "정보보호교육", "보안교육", "컴플라이언스교육",
    "성희롱예방교육", "성희롱교육",
    "장애인식개선교육", "장애인식교육",
    "직장내괴롭힘예방교육", "괴롭힘예방교육",
    "개인정보보호교육", "개인정보교육",
    # 일반 교육 질문
    "교육이란", "교육이 뭐", "교육 설명",
    "무슨 교육", "어떤 교육",
])

# 교육 현황/개인화 키워드 (BACKEND_STATUS - EDU)
# Phase 50: 개인화 Q1/Q3/Q9 키워드와 동기화
EDU_STATUS_KEYWORDS = frozenset([
    # 이수/수료 상태 조회
    "수료", "이수", "미이수", "미수료", "수료율", "이수율",
    "진도", "진행률", "시청률", "완료율",
    # 내 교육 현황 조회
    "내 교육", "나의 교육", "내가 들은", "내가 수강",
    "교육현황", "수강현황", "학습현황",
    # Q1: 미이수 교육 패턴 (Phase 50)
    "안 들은", "안들은", "필수 미이수", "안한 교육", "안 한 교육",
    # Q3: 마감 임박 교육 패턴 (Phase 50)
    "데드라인", "마감", "곧 마감", "마감 임박",
    "이번 달", "이번달", "이달",
    "언제까지", "기한",
    # Q9: 이번 주 할 일 패턴 (Phase 50)
    "이번 주", "이번주", "금주", "이주",
    "할 일", "해야 할", "해야할", "해야 하는",
    # 진도 확인 패턴
    "어디까지", "몇 퍼센트", "얼마나 했",
])

# 교육 이어보기/재생 위치 조회 키워드 (EDU_RESUME_CHECK - 개인화)
# Phase 50: 보던/듣던/최근/마지막 패턴 확장
EDU_RESUME_KEYWORDS = frozenset([
    # 이어보기 패턴
    "이어서", "이어보기", "이어 보기", "계속 보기", "계속보기",
    "끊긴", "끊어진", "중단", "멈춘", "멈춰진",
    # 재생 위치 패턴
    "어디까지 봤", "어디서 끊", "마지막으로 본", "마지막 위치",
    "재생 위치", "시청 위치", "보던 거", "듣던 거",
    # 다시 보기/듣기 패턴
    "다시 재생", "다시 틀어", "이어 재생", "이어 틀어",
    # Phase 50: "보던/듣던 교육" 패턴 추가
    "보던 교육", "듣던 교육", "보던 강의", "듣던 강의",
    "다시 보고", "다시 듣고", "다시 보기", "다시 듣기",
    # Phase 50: 최근/마지막 시청 기록 패턴 추가
    "최근에 본", "최근에 보던", "최근에 듣던", "최근 본", "최근 보던",
    "마지막에 본", "마지막에 보던", "마지막에 듣던", "마지막 본", "마지막 보던",
    "마지막에 들은", "마지막에 듣던", "마지막 듣던",
])

# HR/근태/복지/연차 개인화 키워드 (BACKEND_STATUS - HR)
# Phase 50: 개인화 질문 패턴 대폭 확장 (연차/휴가/복지 조회 질문)
HR_PERSONAL_KEYWORDS = frozenset([
    # 연차 개인화 패턴 (Phase 50 확장)
    "내 연차", "나의 연차", "연차 잔여", "연차 남은", "남은 연차", "잔여 연차",
    "연차 며칠", "연차 얼마", "연차 몇", "연차 확인", "연차 조회",
    "연차가 며칠", "연차가 얼마", "연차가 몇",
    # 휴가 개인화 패턴 (Phase 50 확장)
    "휴가 잔여", "휴가 남은", "내 휴가", "남은 휴가", "잔여 휴가",
    "휴가 며칠", "휴가 얼마", "휴가 몇", "휴가 확인", "휴가 조회",
    "휴가가 며칠", "휴가가 얼마", "휴가가 몇",
    # 급여 패턴
    "급여", "월급", "봉급", "내 급여", "급여명세",
    # 근태 패턴
    "근태", "출근", "퇴근", "내 근태", "근태현황",
    # 복지/포인트 패턴 (Phase 50 확장)
    "복지", "복지포인트", "포인트 잔액", "내 포인트",
    "포인트 얼마", "포인트 조회", "포인트 확인",
    "식대", "식대 잔액", "식대 얼마", "식대 조회",
    # 일반 개인정보 조회 패턴
    "내 정보", "나의 정보", "내 현황", "나의 현황",
    "내가 얼마", "내 잔여", "나 몇 개",
])

# 연차/휴가 규정 키워드 (POLICY_QA - 규정 설명 요청)
LEAVE_POLICY_KEYWORDS = frozenset([
    "연차규정", "휴가규정", "연차제도", "휴가제도",
    "연차 이월", "휴가 이월", "연차 기준",
    "연차가 뭐", "휴가가 뭐", "연차란", "휴가란",
    "연차 어떻게 계산", "휴가 어떻게 계산",
    "연차 정책", "휴가 정책",
])

# 퀴즈 시작 키워드 (QUIZ_START)
QUIZ_START_KEYWORDS = frozenset([
    "퀴즈 시작", "퀴즈 시작해", "퀴즈 시작할", "퀴즈를 시작",
    "시험 시작", "테스트 시작", "퀴즈 풀",
    "퀴즈 치", "시험 치", "테스트 치",
])

# 퀴즈 제출 키워드 (QUIZ_SUBMIT)
# 주의: "완료"는 "미완료"에도 매칭되므로 더 구체적인 표현 사용
QUIZ_SUBMIT_KEYWORDS = frozenset([
    "퀴즈 제출", "답안 제출", "정답 제출",
    "채점해", "채점 해", "점수 확인",
    "제출할게", "제출합니다",
    "퀴즈 완료", "시험 완료", "테스트 완료",
])

# 퀴즈 생성 키워드 (QUIZ_GENERATION)
QUIZ_GENERATION_KEYWORDS = frozenset([
    "퀴즈 생성", "문제 생성", "문항 생성",
    "퀴즈 만들", "문제 만들", "시험 만들",
    "퀴즈 출제", "문제 출제",
])

# 퀴즈 점수/평균 비교 키워드 (QUIZ_SCORE_CHECK - Q5)
QUIZ_SCORE_KEYWORDS = frozenset([
    "평균 점수", "내 평균", "나의 평균", "평균 비교",
    "부서 평균", "전사 평균", "회사 평균", "팀 평균",
    "점수 비교", "성적 비교", "퀴즈 평균", "시험 평균",
    "내 점수 어때", "점수가 어때", "평균이 어때",
    "다른 부서", "다른 팀", "우리 부서 평균",
])

# 퀴즈 미완료/재응시 조회 키워드 (QUIZ_PENDING_CHECK - 개인화)
QUIZ_PENDING_KEYWORDS = frozenset([
    # 미완료/미응시 패턴
    "안 푼 퀴즈", "안푼 퀴즈", "미완료 퀴즈", "미응시 퀴즈",
    "남은 퀴즈", "남아있는 퀴즈", "안 본 시험", "안본 시험",
    # 재응시/다시 풀기 패턴
    "다시 풀어야", "재응시", "재시험", "다시 봐야",
    "풀어야 할 퀴즈", "봐야 할 시험", "응시해야 할",
    # 퀴즈 현황 조회 패턴
    "퀴즈 현황", "시험 현황", "내 퀴즈", "나의 퀴즈",
    "퀴즈 목록", "시험 목록", "퀴즈 있", "시험 있",
])

# 퀴즈 문맥 키워드 (치명 액션 판정 시 오탐 방지용)
# "채점해", "점수 확인" 같은 범용 키워드가 퀴즈 외 맥락에서 매칭되지 않도록
QUIZ_CONTEXT_KEYWORDS = frozenset(["퀴즈", "시험", "테스트"])

# HR 할 일/미완료 항목 키워드 (HR_TODO_CHECK - Q20)
# 주의: query가 lower()로 변환되므로 키워드도 소문자로 정의
# 범용 키워드("올해 할 일", "해야 할 일")는 EDU와 충돌하므로 제외
HR_TODO_KEYWORDS = frozenset([
    # HR 명시 키워드
    "hr 할 일", "인사 할 일", "hr 투두", "hr todo",
    "미완료 hr", "미완료 인사", "인사 업무",
    # HR 고유 업무 키워드
    "연말정산", "성과 평가", "인사 평가",
    "서류 제출", "인사 서류", "hr 업무",
])

# 일반 잡담 키워드 (GENERAL_CHAT)
GENERAL_CHAT_KEYWORDS = frozenset([
    "안녕", "ㅎㅎ", "ㅋㅋ", "날씨", "농담", "심심",
    "잘가", "반가워", "고마워", "감사", "수고",
    "뭐해", "머해", "하이", "헬로", "바이",
    "ㅇㅇ", "ㄴㄴ", "ㅎㅇ",
])

# Phase 49: 요약 인텐트 키워드 (SUMMARY_INTENT_ENABLED=True일 때만 사용)
SUMMARY_KEYWORDS = frozenset([
    "요약", "요약해", "요약해줘", "요약해주세요",
    "정리", "정리해", "정리해줘", "정리해주세요",
    "줄여", "줄여줘", "간단히", "핵심만",
    "한줄로", "한 줄로", "짧게",
])

# 시스템 도움말 키워드 (SYSTEM_HELP)
SYSTEM_HELP_KEYWORDS = frozenset([
    "사용법", "메뉴", "화면", "버튼", "기능",
    "어떻게 사용", "어디서", "어디에", "찾기",
    "검색하는 방법", "사용방법", "이용방법",
    "도움말", "헬프", "help",
])

# 애매한 경계 감지용 키워드 조합

# 경계 A 감지: "교육" + 애매한 동사/표현
EDU_AMBIGUOUS_KEYWORDS = frozenset([
    "교육", "강의", "수강", "학습",
])
EDU_AMBIGUOUS_VERBS = frozenset([
    "알려", "알고", "확인", "조회", "보여",
    "뭐야", "뭔가요", "어떻게", "어때",
])

# 경계 B 감지: "연차/휴가" + 애매한 표현
LEAVE_AMBIGUOUS_KEYWORDS = frozenset([
    "연차", "휴가", "휴일", "쉬는날",
])
LEAVE_AMBIGUOUS_VERBS = frozenset([
    "알려", "알고", "확인", "조회", "보여",
    "뭐야", "뭔가요", "어떻게", "어때", "있",
])

# Phase 43: 질문형 어미 패턴 (GENERAL_CHAT에서 제외할 조건)
QUESTION_ENDINGS = re.compile(
    r"(나요|하나요|인가요|ㄴ가요|는지|ㄹ까|을까|할까|됩니까|습니까|입니까|"
    r"어야|해야|될까|되나요|건가요|인지|요\?|까\?|니\?|가\?)$"
)


# =============================================================================
# RuleRouter 클래스
# =============================================================================


class RuleRouter:
    """규칙 기반 라우터.

    키워드 기반 1차 분기를 수행하여 명확한 의도를 빠르게 분류합니다.
    애매한 경계에서는 needs_clarify=true를 설정합니다.

    Usage:
        router = RuleRouter()
        result = router.route(user_query="연차 며칠 남았어?")
        if result.confidence >= 0.9:
            # LLM Router 스킵 가능
            return result
        else:
            # LLM Router로 추가 분류 필요
            llm_result = await llm_router.route(user_query)
    """

    def __init__(self) -> None:
        """RuleRouter 초기화."""
        pass

    def route(self, user_query: str) -> RouterResult:
        """사용자 질문을 규칙 기반으로 분류합니다.

        Args:
            user_query: 사용자 질문 텍스트

        Returns:
            RouterResult: 라우팅 결과

        Note:
            - confidence >= 0.9: 높은 신뢰도, LLM Router 스킵 권장
            - confidence < 0.9: LLM Router로 추가 분류 권장
            - needs_clarify=True: 되묻기 필요
        """
        query_lower = user_query.lower()
        debug_info = RouterDebugInfo()

        # Phase 49: ASCII-safe 로깅
        query_safe = ascii_safe_preview(user_query, 50)

        # Step 1: 애매한 경계 체크 (최우선)
        clarify_result = self._check_ambiguous_boundaries(query_lower, debug_info)
        if clarify_result:
            logger.info(
                f"RuleRouter: Ambiguous boundary detected, needs_clarify=True, "
                f"query='{query_safe}'"
            )
            return clarify_result

        # Step 2: 치명 액션(퀴즈 3종) 체크
        critical_result = self._check_critical_actions(query_lower, debug_info)
        if critical_result:
            logger.info(
                f"RuleRouter: Critical action detected, "
                f"sub_intent_id={critical_result.sub_intent_id}, "
                f"query='{query_safe}'"
            )
            return critical_result

        # Step 3: 명확한 키워드 매칭 (Phase 43: 원본 질문도 전달)
        intent_result = self._classify_by_keywords(query_lower, user_query, debug_info)

        logger.info(
            f"RuleRouter: intent={intent_result.tier0_intent.value}, "
            f"domain={intent_result.domain.value}, "
            f"confidence={intent_result.confidence}, "
            f"rule_hits={debug_info.rule_hits}, "
            f"query='{query_safe}'"
        )

        return intent_result

    def _check_ambiguous_boundaries(
        self,
        query_lower: str,
        debug_info: RouterDebugInfo,
    ) -> Optional[RouterResult]:
        """애매한 경계를 체크하고 되묻기 결과를 반환합니다.

        경계 A: 교육 내용 설명 vs 내 이수현황/진도
        경계 B: 규정 질문 vs HR/근태/복지 개인화

        Args:
            query_lower: 소문자로 변환된 질문
            debug_info: 디버그 정보 객체

        Returns:
            Optional[RouterResult]: 되묻기가 필요하면 RouterResult, 아니면 None
        """
        # 경계 A: 교육 관련 애매함 체크
        if self._is_boundary_a_ambiguous(query_lower):
            debug_info.rule_hits.append("BOUNDARY_A_AMBIGUOUS")
            return RouterResult(
                tier0_intent=Tier0Intent.UNKNOWN,
                domain=RouterDomain.EDU,
                route_type=RouterRouteType.ROUTE_UNKNOWN,
                confidence=0.3,
                needs_clarify=True,
                clarify_question=random.choice(ClarifyTemplates.EDUCATION_CONTENT_VS_STATUS),
                debug=debug_info,
            )

        # 경계 B: 연차/휴가 관련 애매함 체크
        if self._is_boundary_b_ambiguous(query_lower):
            debug_info.rule_hits.append("BOUNDARY_B_AMBIGUOUS")
            return RouterResult(
                tier0_intent=Tier0Intent.UNKNOWN,
                domain=RouterDomain.HR,
                route_type=RouterRouteType.ROUTE_UNKNOWN,
                confidence=0.3,
                needs_clarify=True,
                clarify_question=random.choice(ClarifyTemplates.POLICY_VS_HR_PERSONAL),
                debug=debug_info,
            )

        return None

    def _is_boundary_a_ambiguous(self, query_lower: str) -> bool:
        """경계 A (교육 내용 vs 이수현황) 애매함을 체크합니다.

        애매한 패턴 예시:
        - "교육 알려줘" (내용? 현황?)
        - "교육 확인해줘" (내용? 진도?)
        - "교육 어떻게 되어있어?" (규정? 내 현황?)

        명확하지 않은 패턴:
        - 교육 키워드 + 애매한 동사
        - 단, EDU_CONTENT_KEYWORDS나 EDU_STATUS_KEYWORDS에 명확히 해당하면 제외

        Phase 50: EDU_RESUME_KEYWORDS, QUIZ 키워드도 명확한 개인화 패턴으로 인식
        """
        # 먼저 명확한 키워드가 있는지 체크
        if self._contains_any(query_lower, EDU_CONTENT_KEYWORDS):
            return False  # 명확히 교육 내용 질문
        if self._contains_any(query_lower, EDU_STATUS_KEYWORDS):
            return False  # 명확히 교육 현황 질문
        # Phase 50: 이어보기/다시보기 패턴도 명확한 개인화 질문
        if self._contains_any(query_lower, EDU_RESUME_KEYWORDS):
            return False  # 명확히 교육 이어보기/재생 위치 질문
        # Phase 50: 퀴즈 점수/미완료 조회도 명확한 개인화 질문
        if self._contains_any(query_lower, QUIZ_SCORE_KEYWORDS):
            return False  # 명확히 퀴즈 점수 조회 질문
        if self._contains_any(query_lower, QUIZ_PENDING_KEYWORDS):
            return False  # 명확히 퀴즈 미완료 조회 질문

        # 교육 키워드 + 애매한 동사 조합 체크
        has_edu_keyword = self._contains_any(query_lower, EDU_AMBIGUOUS_KEYWORDS)
        has_ambiguous_verb = self._contains_any(query_lower, EDU_AMBIGUOUS_VERBS)

        return has_edu_keyword and has_ambiguous_verb

    def _is_boundary_b_ambiguous(self, query_lower: str) -> bool:
        """경계 B (규정 질문 vs HR 개인화) 애매함을 체크합니다.

        애매한 패턴 예시:
        - "연차 알려줘" (규정? 내 잔여?)
        - "휴가 확인해줘" (정책? 내 휴가?)
        - "연차 어떻게 되어있어?" (규정? 내 현황?)

        명확하지 않은 패턴:
        - 연차/휴가 키워드 + 애매한 동사
        - 단, LEAVE_POLICY_KEYWORDS나 HR_PERSONAL_KEYWORDS에 명확히 해당하면 제외

        Phase 49: "규정", "정책" 등이 있으면 명확히 정책 질문으로 판단
        """
        # 먼저 명확한 키워드가 있는지 체크
        if self._contains_any(query_lower, LEAVE_POLICY_KEYWORDS):
            return False  # 명확히 정책 질문
        if self._contains_any(query_lower, HR_PERSONAL_KEYWORDS):
            return False  # 명확히 개인화 질문

        # Phase 49: "규정", "정책" 등이 있으면 명확히 정책 질문
        policy_clarifiers = {"규정", "정책", "규칙", "지침", "제도"}
        if self._contains_any(query_lower, policy_clarifiers):
            return False  # 명확히 정책 질문

        # 연차/휴가 키워드 + 애매한 동사 조합 체크
        has_leave_keyword = self._contains_any(query_lower, LEAVE_AMBIGUOUS_KEYWORDS)
        has_ambiguous_verb = self._contains_any(query_lower, LEAVE_AMBIGUOUS_VERBS)

        return has_leave_keyword and has_ambiguous_verb

    def _check_critical_actions(
        self,
        query_lower: str,
        debug_info: RouterDebugInfo,
    ) -> Optional[RouterResult]:
        """치명 액션(퀴즈 3종)을 체크하고 확인 게이트를 설정합니다.

        Args:
            query_lower: 소문자로 변환된 질문
            debug_info: 디버그 정보 객체

        Returns:
            Optional[RouterResult]: 치명 액션이면 RouterResult, 아니면 None
        """
        # QUIZ_START 체크
        if self._contains_any(query_lower, QUIZ_START_KEYWORDS):
            debug_info.rule_hits.append("QUIZ_START")
            debug_info.keywords.extend(
                [kw for kw in QUIZ_START_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                domain=RouterDomain.QUIZ,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id=SubIntentId.QUIZ_START.value,
                confidence=0.95,
                requires_confirmation=True,
                confirmation_prompt=ConfirmationTemplates.QUIZ_START,
                debug=debug_info,
            )

        # QUIZ_SUBMIT 체크
        # 오탐 방지: "채점해", "점수 확인" 같은 범용 키워드는 퀴즈 문맥이 있어야만 매칭
        if self._contains_any(query_lower, QUIZ_SUBMIT_KEYWORDS):
            # 퀴즈 문맥 확인 (키워드에 "퀴즈/시험/테스트"가 포함되어 있으면 자동 통과)
            has_quiz_context = self._contains_any(query_lower, QUIZ_CONTEXT_KEYWORDS)
            if not has_quiz_context:
                # 퀴즈 문맥 없음 → 치명 액션으로 판정하지 않음 (다른 라우팅으로 진행)
                debug_info.rule_hits.append("QUIZ_SUBMIT_SKIPPED_NO_CONTEXT")
            else:
                debug_info.rule_hits.append("QUIZ_SUBMIT")
                debug_info.keywords.extend(
                    [kw for kw in QUIZ_SUBMIT_KEYWORDS if kw in query_lower]
                )
                return RouterResult(
                    tier0_intent=Tier0Intent.BACKEND_STATUS,
                    domain=RouterDomain.QUIZ,
                    route_type=RouterRouteType.BACKEND_API,
                    sub_intent_id=SubIntentId.QUIZ_SUBMIT.value,
                    confidence=0.95,
                    requires_confirmation=True,
                    confirmation_prompt=ConfirmationTemplates.QUIZ_SUBMIT,
                    debug=debug_info,
                )

        # QUIZ_GENERATION 체크
        if self._contains_any(query_lower, QUIZ_GENERATION_KEYWORDS):
            debug_info.rule_hits.append("QUIZ_GENERATION")
            debug_info.keywords.extend(
                [kw for kw in QUIZ_GENERATION_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                domain=RouterDomain.QUIZ,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id=SubIntentId.QUIZ_GENERATION.value,
                confidence=0.95,
                requires_confirmation=True,
                confirmation_prompt=ConfirmationTemplates.QUIZ_GENERATION,
                debug=debug_info,
            )

        return None

    def _is_question_format(self, query: str) -> bool:
        """질문형 문장인지 확인합니다.

        Phase 43: 질문형 어미가 있으면 True 반환
        이 경우 GENERAL_CHAT으로 분류하지 않음

        Args:
            query: 원본 질문 텍스트

        Returns:
            bool: 질문형 문장이면 True
        """
        # 물음표가 있으면 질문
        if "?" in query:
            return True

        # 질문형 어미 패턴 체크
        if QUESTION_ENDINGS.search(query):
            return True

        return False

    def _classify_by_keywords(
        self,
        query_lower: str,
        query_original: str,
        debug_info: RouterDebugInfo,
    ) -> RouterResult:
        """키워드 기반으로 의도를 분류합니다.

        Phase 43 업데이트:
        - 키워드 대폭 확장으로 매칭률 향상
        - 기본값을 POLICY_QA로 변경 (RAG 우선)
        - 질문형 문장은 GENERAL_CHAT에서 제외

        Phase 49 업데이트:
        - POLICY 키워드 체크를 EDU_CONTENT보다 앞으로 이동
        - 연차/휴가/근태/징계/복무 등은 POLICY로 우선 분류
        - 요약 인텐트 감지 (SUMMARY_INTENT_ENABLED=True일 때)

        Args:
            query_lower: 소문자로 변환된 질문
            query_original: 원본 질문 (질문형 판정용)
            debug_info: 디버그 정보 객체

        Returns:
            RouterResult: 분류 결과
        """
        # Phase 49: 요약 인텐트 감지 (피처 플래그로 보호)
        settings = get_settings()
        if getattr(settings, "SUMMARY_INTENT_ENABLED", False):
            if self._contains_any(query_lower, SUMMARY_KEYWORDS):
                matched_keywords = [kw for kw in SUMMARY_KEYWORDS if kw in query_lower]
                debug_info.rule_hits.append("SUMMARY_DETECTED")
                debug_info.keywords.extend(matched_keywords)
                query_safe = ascii_safe_preview(query_original, 50)
                logger.info(
                    f"RuleRouter: Summary intent detected | "
                    f"keywords={matched_keywords} | query='{query_safe}'"
                )
                # TODO: 향후 별도 SUMMARY_QA 인텐트로 분기 가능
                # 현재는 기존 로직 계속 진행

        # Phase 49: 복합 조건 - "교육"이 포함되면 EDU 우선 체크
        # "정보보호교육", "성희롱예방교육" 등은 EDU로 분류해야 함
        if "교육" in query_lower:
            if self._contains_any(query_lower, EDU_CONTENT_KEYWORDS):
                debug_info.rule_hits.append("EDU_CONTENT_PRIORITY")
                debug_info.keywords.extend(
                    [kw for kw in EDU_CONTENT_KEYWORDS if kw in query_lower]
                )
                return RouterResult(
                    tier0_intent=Tier0Intent.EDUCATION_QA,
                    domain=RouterDomain.EDU,
                    route_type=RouterRouteType.RAG_INTERNAL,
                    confidence=0.85,
                    debug=debug_info,
                )

        # Phase 49: 복합 조건 - "규정/정책/규칙" 포함 시 POLICY 우선 체크
        # "연차 규정", "근태 규정" 등은 POLICY로 분류해야 함
        policy_clarifiers = {"규정", "정책", "규칙", "지침", "제도"}
        if self._contains_any(query_lower, policy_clarifiers):
            if self._contains_any(query_lower, POLICY_KEYWORDS) or \
               self._contains_any(query_lower, LEAVE_POLICY_KEYWORDS) or \
               self._contains_any(query_lower, LEAVE_AMBIGUOUS_KEYWORDS):
                debug_info.rule_hits.append("POLICY_PRIORITY")
                debug_info.keywords.extend(
                    [kw for kw in policy_clarifiers if kw in query_lower]
                )
                return RouterResult(
                    tier0_intent=Tier0Intent.POLICY_QA,
                    domain=RouterDomain.POLICY,
                    route_type=RouterRouteType.RAG_INTERNAL,
                    confidence=0.85,
                    debug=debug_info,
                )

        # 우선순위 순서대로 체크
        # Phase 49: POLICY를 EDU_CONTENT보다 앞으로 이동

        # 1. HR 개인화 (가장 명확한 개인화 패턴)
        if self._contains_any(query_lower, HR_PERSONAL_KEYWORDS):
            debug_info.rule_hits.append("HR_PERSONAL")
            debug_info.keywords.extend(
                [kw for kw in HR_PERSONAL_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                domain=RouterDomain.HR,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id=SubIntentId.HR_LEAVE_CHECK.value,
                confidence=0.9,
                debug=debug_info,
            )

        # 1-1. HR 할 일/미완료 항목 조회 (Q20 개인화) - EDU_STATUS보다 먼저 체크
        if self._contains_any(query_lower, HR_TODO_KEYWORDS):
            debug_info.rule_hits.append("HR_TODO_CHECK")
            debug_info.keywords.extend(
                [kw for kw in HR_TODO_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                domain=RouterDomain.HR,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id=SubIntentId.HR_TODO_CHECK.value,
                confidence=0.9,
                debug=debug_info,
            )

        # 2. 교육 현황 조회 (개인화)
        if self._contains_any(query_lower, EDU_STATUS_KEYWORDS):
            debug_info.rule_hits.append("EDU_STATUS")
            debug_info.keywords.extend(
                [kw for kw in EDU_STATUS_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                domain=RouterDomain.EDU,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id=SubIntentId.EDU_STATUS_CHECK.value,
                confidence=0.9,
                debug=debug_info,
            )

        # 2-1. 교육 이어보기/재생 위치 조회 (개인화)
        if self._contains_any(query_lower, EDU_RESUME_KEYWORDS):
            debug_info.rule_hits.append("EDU_RESUME_CHECK")
            debug_info.keywords.extend(
                [kw for kw in EDU_RESUME_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                domain=RouterDomain.EDU,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id=SubIntentId.EDU_RESUME_CHECK.value,
                confidence=0.9,
                debug=debug_info,
            )

        # 2-2. 퀴즈 점수/평균 비교 조회 (Q5 개인화)
        if self._contains_any(query_lower, QUIZ_SCORE_KEYWORDS):
            debug_info.rule_hits.append("QUIZ_SCORE_CHECK")
            debug_info.keywords.extend(
                [kw for kw in QUIZ_SCORE_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                domain=RouterDomain.QUIZ,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id=SubIntentId.QUIZ_SCORE_CHECK.value,
                confidence=0.9,
                debug=debug_info,
            )

        # 2-3. 퀴즈 미완료/재응시 조회 (개인화)
        if self._contains_any(query_lower, QUIZ_PENDING_KEYWORDS):
            debug_info.rule_hits.append("QUIZ_PENDING_CHECK")
            debug_info.keywords.extend(
                [kw for kw in QUIZ_PENDING_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                domain=RouterDomain.QUIZ,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id=SubIntentId.QUIZ_PENDING_CHECK.value,
                confidence=0.9,
                debug=debug_info,
            )

        # 3. 정책/규정 질문 (Phase 49: EDU_CONTENT보다 먼저 체크)
        if self._contains_any(query_lower, POLICY_KEYWORDS):
            debug_info.rule_hits.append("POLICY")
            debug_info.keywords.extend(
                [kw for kw in POLICY_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.POLICY_QA,
                domain=RouterDomain.POLICY,
                route_type=RouterRouteType.RAG_INTERNAL,
                confidence=0.85,
                debug=debug_info,
            )

        # 4. 연차/휴가 규정 질문
        if self._contains_any(query_lower, LEAVE_POLICY_KEYWORDS):
            debug_info.rule_hits.append("LEAVE_POLICY")
            debug_info.keywords.extend(
                [kw for kw in LEAVE_POLICY_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.POLICY_QA,
                domain=RouterDomain.POLICY,
                route_type=RouterRouteType.RAG_INTERNAL,
                confidence=0.85,
                debug=debug_info,
            )

        # 5. 교육 내용 질문 (Phase 49: POLICY보다 뒤로 이동)
        if self._contains_any(query_lower, EDU_CONTENT_KEYWORDS):
            debug_info.rule_hits.append("EDU_CONTENT")
            debug_info.keywords.extend(
                [kw for kw in EDU_CONTENT_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.EDUCATION_QA,
                domain=RouterDomain.EDU,
                route_type=RouterRouteType.RAG_INTERNAL,
                confidence=0.85,
                debug=debug_info,
            )

        # 6. 시스템 도움말
        if self._contains_any(query_lower, SYSTEM_HELP_KEYWORDS):
            debug_info.rule_hits.append("SYSTEM_HELP")
            debug_info.keywords.extend(
                [kw for kw in SYSTEM_HELP_KEYWORDS if kw in query_lower]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.SYSTEM_HELP,
                domain=RouterDomain.GENERAL,
                route_type=RouterRouteType.ROUTE_SYSTEM_HELP,
                confidence=0.9,
                debug=debug_info,
            )

        # 7. 일반 잡담 (Phase 43: 질문형 문장은 제외)
        if self._contains_any(query_lower, GENERAL_CHAT_KEYWORDS):
            # 질문형 문장이면 잡담으로 분류하지 않음
            if not self._is_question_format(query_original):
                debug_info.rule_hits.append("GENERAL_CHAT")
                debug_info.keywords.extend(
                    [kw for kw in GENERAL_CHAT_KEYWORDS if kw in query_lower]
                )
                return RouterResult(
                    tier0_intent=Tier0Intent.GENERAL_CHAT,
                    domain=RouterDomain.GENERAL,
                    route_type=RouterRouteType.LLM_ONLY,
                    confidence=0.8,
                    debug=debug_info,
                )

        # 8. Phase 43: 기본값을 POLICY_QA로 변경 (RAG 우선)
        # 질문형 문장이거나 분류가 안 되면 우선 RAG를 타도록 함
        if self._is_question_format(query_original):
            debug_info.rule_hits.append("QUESTION_FORMAT_DEFAULT_POLICY")
            return RouterResult(
                tier0_intent=Tier0Intent.POLICY_QA,
                domain=RouterDomain.POLICY,
                route_type=RouterRouteType.RAG_INTERNAL,
                confidence=0.6,  # 기본값이지만 RAG는 타도록
                debug=debug_info,
            )

        # 9. 그 외: POLICY_QA로 분류 (RAG 우선)
        debug_info.rule_hits.append("DEFAULT_POLICY_QA")
        return RouterResult(
            tier0_intent=Tier0Intent.POLICY_QA,
            domain=RouterDomain.POLICY,
            route_type=RouterRouteType.RAG_INTERNAL,
            confidence=0.5,  # LLM Router로 추가 분류 권장
            debug=debug_info,
        )

    def _contains_any(self, text: str, keywords: frozenset) -> bool:
        """텍스트에 키워드 중 하나라도 포함되어 있는지 확인합니다.

        Args:
            text: 검사할 텍스트 (소문자로 변환된 상태)
            keywords: 검사할 키워드 집합

        Returns:
            bool: 키워드 포함 여부
        """
        return any(keyword in text for keyword in keywords)
