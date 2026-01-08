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

Phase 52 업데이트 (라우팅 정확도 개선):
- 정규화(공백 제거) 기반 키워드 매칭 도입 ("보안사고" == "보안 사고")
- 절차/대응 트리거 AND 조건 기반 RAG 우선 분류
- PROCEDURE_WORDS + SECURITY_HINTS → POLICY_QA
- PROCEDURE_WORDS + EDU_HINTS → EDUCATION_QA
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
# Phase 52: 정규화 유틸 (공백 제거 기반 키워드 매칭)
# =============================================================================

def normalize_for_matching(text: str) -> str:
    """
    키워드 매칭을 위한 텍스트 정규화 (공백 제거).

    "보안 사고" → "보안사고" 로 변환하여 공백 변형에 robust하게 매칭.

    Args:
        text: 원본 텍스트 (소문자 변환은 호출자가 수행)

    Returns:
        str: 공백이 제거된 정규화 텍스트
    """
    return re.sub(r"\s+", "", text)


def normalize_keyword_set(keywords: frozenset) -> frozenset:
    """
    키워드 세트를 정규화된 버전으로 변환합니다.

    Args:
        keywords: 원본 키워드 frozenset

    Returns:
        frozenset: 공백이 제거된 정규화 키워드 세트
    """
    return frozenset(normalize_for_matching(kw) for kw in keywords)


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
    "개인정보유출", "마스킹", "암호화", "보안사고", "보안 사고", "정보주체",
    # 사고/대응 절차 (RAG로 처리해야 함)
    "사고 대응", "사고대응", "대응 절차", "대응절차", "1차 대응", "초기 대응", "초기대응",
    "사고 발생", "사고발생", "보고 절차", "보고절차", "신고 절차", "신고절차",
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
    # 연차/휴가 사용 이력 패턴 (Q12 지원)
    "연차 사용", "연차 이력", "연차 내역", "연차 썼", "사용한 연차",
    "휴가 사용", "휴가 이력", "휴가 내역", "휴가 썼", "사용한 휴가",
    "언제 썼", "썼던 연차", "썼던 휴가",
    # 급여 패턴
    "급여", "월급", "봉급", "내 급여", "급여명세",
    # 근태 패턴
    "근태", "출근", "퇴근", "내 근태", "근태현황",
    # 복지/포인트 패턴 (Phase 50 확장)
    "복지", "복지포인트", "포인트 잔액", "내 포인트",
    "포인트 얼마", "포인트 조회", "포인트 확인",
    "식대", "식대 잔액", "식대 얼마", "식대 조회",
    # 복지/포인트 사용 내역 패턴 (Q15 지원)
    "포인트 사용", "포인트 이력", "포인트 내역", "포인트 썼",
    "복지 사용", "복지 이력", "복지 내역", "복지 썼",
    "어디서 썼", "뭐에 썼",
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
# OPEN_QUIZ 액션 트리거용 - 자연스러운 표현 포함
QUIZ_START_KEYWORDS = frozenset([
    # 기본 시작 패턴
    "퀴즈 시작", "퀴즈 시작해", "퀴즈 시작할", "퀴즈를 시작",
    "시험 시작", "테스트 시작",
    # 풀기 패턴
    "퀴즈 풀", "퀴즈 풀래", "퀴즈 풀어", "퀴즈 풀고",
    "시험 풀", "테스트 풀",
    # 보기/치기 패턴
    "퀴즈 치", "시험 치", "테스트 치",
    "퀴즈 볼", "퀴즈 볼래", "퀴즈 보고",
    "시험 볼", "시험 볼래", "시험 보고",
    # 하기 패턴
    "퀴즈 해", "퀴즈 해줘", "퀴즈 하고",
    "퀴즈 할래", "퀴즈 하자",
    # 응시 패턴
    "퀴즈 응시", "시험 응시", "테스트 응시",
])

# 교육 패널 열기 키워드 (EDU_PANEL_OPEN)
# OPEN_EDU_PANEL 액션 트리거용 - 교육 목록/패널 열기 요청
# 주의: "교육 조회", "교육 확인"은 EDU_STATUS_KEYWORDS와 겹치므로 제외
#       예: "미이수 교육 조회"는 교육 현황 조회이지 패널 열기가 아님
EDU_PANEL_KEYWORDS = frozenset([
    # 교육 목록/패널 보기 패턴
    "교육 목록", "교육목록", "교육 리스트", "교육리스트",
    "교육 보여", "교육 보여줘", "교육 열어", "교육 열어줘",
    "교육 패널", "교육패널", "교육 화면", "교육화면",
    # 내 교육 보기 패턴
    "내 교육 보여", "내 교육 열어", "내 교육 목록",
    # 수강/학습 목록 패턴
    "수강 목록", "수강목록", "학습 목록", "학습목록",
    "강의 목록", "강의목록", "강좌 목록", "강좌목록",
    # 교육 시작/들으러 가기 패턴
    "교육 들으러", "교육 보러", "교육 시작",
    "강의 보러", "강의 들으러",
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

# 퀴즈 점수/성적 조회 키워드 (QUIZ_SCORE_CHECK - 개인화 Q5, Q6)
QUIZ_SCORE_KEYWORDS = frozenset([
    # 평균 점수 패턴 (Q5)
    "평균 점수", "점수 평균", "퀴즈 평균", "시험 평균",
    "내 평균", "나의 평균", "평균점수", "점수평균",
    "퀴즈 점수", "시험 점수", "내 점수", "나의 점수",
    "성적 평균", "평균 성적",
    # 부서/전사 비교 패턴 (Q5)
    "부서 평균", "전사 평균", "회사 평균", "팀 평균",
    "다른 사람", "비교",
    # 낮은/높은 점수 패턴 (Q6)
    "낮은 점수", "점수가 낮은", "점수 낮은",
    "높은 점수", "점수가 높은", "점수 높은",
    "가장 낮", "가장 높", "제일 낮", "제일 높",
    "취약", "취약한", "약한 과목", "못한 과목",
    # 성적 조회 일반 패턴
    "성적 조회", "점수 조회", "성적 확인", "점수 확인",
    "성적 알려", "점수 알려",
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

# =============================================================================
# Phase 52.1: Smalltalk Gate (초단문/일상 패턴 → GENERAL_CHAT)
# =============================================================================
# "미분류=POLICY_QA" 전략 유지하되, 초단문/일상 패턴만 예외로 GENERAL_CHAT
# RAG가 억지로 근거 없는 정책 안내를 하는 것 방지

# Smalltalk 인사/반응 키워드
SMALLTALK_GREETINGS = frozenset([
    "안녕", "안녕하세요", "안녕요", "하이", "헬로", "hi", "hello",
    "반가워", "반갑습니다", "반가", "잘가", "바이", "bye",
    "고마워", "고맙", "감사", "감사해", "땡큐", "thanks",
    "수고", "수고해", "수고하세요",
    "ㅎㅎ", "ㅋㅋ", "ㅋㅋㅋ", "ㅎㅎㅎ", "ㅇㅇ", "ㄴㄴ", "ㅎㅇ", "ㅂㅇ",
    "네", "응", "ㅇㅋ", "ok", "오케이", "알겠어", "알았어",
    # Phase 52.1: 일상 대화 패턴 추가
    "뭐해", "머해", "뭐하세요", "뭐해요", "머해요",
    "뭐야", "머야", "뭐", "머",
])

# Smalltalk 상태/감정 키워드
SMALLTALK_EMOTIONS = frozenset([
    "심심", "심심해", "심심하다", "지루해", "지루하다",
    "피곤", "피곤해", "피곤하다", "졸려", "졸리다",
    "배고파", "배고프다", "배불러", "배부르다",
    "기분", "기분이", "화나", "화난다", "짜증", "짜증나",
    "좋아", "좋다", "싫어", "싫다", "재밌어", "재미없어",
])

# Smalltalk 희망/의지 접미사 패턴 (정규화 후 적용)
# "사고싶어", "먹고싶어", "가고싶어" 등
# Phase 52.1: 공손형(요, 습니다)도 포함
SMALLTALK_DESIRE_PATTERN = re.compile(
    r"(고싶어|고싶다|고싶네|고싶은데|고파|고프다|"
    r"고싶어요|고싶어여|고싶습니다|고싶네요|고싶은데요)$"
)

# 업무 도메인 힌트 키워드 (이것이 있으면 Smalltalk이 아님)
# 이 키워드가 하나라도 있으면 Smalltalk Gate 통과 불가
DOMAIN_HINT_KEYWORDS = frozenset([
    # 인사/근태/연차
    "연차", "휴가", "근태", "출근", "퇴근", "지각", "결근", "조퇴",
    "반차", "병가", "육아휴직", "출산휴가", "경조사",
    # 교육/퀴즈
    "교육", "수강", "이수", "수료", "퀴즈", "시험", "테스트", "강의",
    "진도", "진행률", "마감", "데드라인",
    # 보안/규정/신고
    "보안", "규정", "정책", "사규", "지침", "신고", "절차", "대응",
    "유출", "침해", "개인정보", "성희롱", "괴롭힘", "장애인",
    # HR/복지
    "급여", "월급", "봉급", "복지", "포인트", "식대",
    # 기타 업무
    "결재", "승인", "보고", "회의", "업무", "프로젝트",
])

# =============================================================================
# Phase 52: 절차/대응 트리거 키워드 (AND 조건 기반)
# =============================================================================

# 절차/단계/대응 관련 트리거 워드
PROCEDURE_WORDS = frozenset([
    "절차", "단계", "단계별", "단계별로",
    "보고", "신고", "대응", "처리",
    "어떻게해야", "뭘해야", "해야하는", "해야할",
    "누구에게", "어디로", "어디에",
    "1차", "2차", "초기", "즉시",
])

# 보안/사고 관련 힌트 (PROCEDURE_WORDS와 AND로 결합 → POLICY_QA)
SECURITY_INCIDENT_HINTS = frozenset([
    "보안", "사고", "유출", "침해", "반출",
    "usb", "메일", "외부전송", "악성코드", "랜섬웨어",
    "해킹", "피싱", "스팸", "바이러스",
    "개인정보유출", "정보유출", "데이터유출",
])

# 교육 관련 힌트 (PROCEDURE_WORDS와 AND로 결합 → EDUCATION_QA)
EDU_PROCEDURE_HINTS = frozenset([
    "교육", "수강", "이수", "수료", "강의",
    "학습", "과정", "커리큘럼",
])

# =============================================================================
# Phase 52: 정규화된 키워드셋 (모듈 초기화 시 1회 생성)
# =============================================================================

# 정규화된 키워드셋 (공백 제거된 버전)
POLICY_KEYWORDS_NORM = normalize_keyword_set(POLICY_KEYWORDS)
EDU_CONTENT_KEYWORDS_NORM = normalize_keyword_set(EDU_CONTENT_KEYWORDS)
EDU_STATUS_KEYWORDS_NORM = normalize_keyword_set(EDU_STATUS_KEYWORDS)
EDU_RESUME_KEYWORDS_NORM = normalize_keyword_set(EDU_RESUME_KEYWORDS)
HR_PERSONAL_KEYWORDS_NORM = normalize_keyword_set(HR_PERSONAL_KEYWORDS)
LEAVE_POLICY_KEYWORDS_NORM = normalize_keyword_set(LEAVE_POLICY_KEYWORDS)
QUIZ_START_KEYWORDS_NORM = normalize_keyword_set(QUIZ_START_KEYWORDS)
EDU_PANEL_KEYWORDS_NORM = normalize_keyword_set(EDU_PANEL_KEYWORDS)
QUIZ_SUBMIT_KEYWORDS_NORM = normalize_keyword_set(QUIZ_SUBMIT_KEYWORDS)
QUIZ_GENERATION_KEYWORDS_NORM = normalize_keyword_set(QUIZ_GENERATION_KEYWORDS)
QUIZ_PENDING_KEYWORDS_NORM = normalize_keyword_set(QUIZ_PENDING_KEYWORDS)
QUIZ_SCORE_KEYWORDS_NORM = normalize_keyword_set(QUIZ_SCORE_KEYWORDS)
QUIZ_CONTEXT_KEYWORDS_NORM = normalize_keyword_set(QUIZ_CONTEXT_KEYWORDS)
HR_TODO_KEYWORDS_NORM = normalize_keyword_set(HR_TODO_KEYWORDS)
GENERAL_CHAT_KEYWORDS_NORM = normalize_keyword_set(GENERAL_CHAT_KEYWORDS)
SYSTEM_HELP_KEYWORDS_NORM = normalize_keyword_set(SYSTEM_HELP_KEYWORDS)
PROCEDURE_WORDS_NORM = normalize_keyword_set(PROCEDURE_WORDS)
SECURITY_INCIDENT_HINTS_NORM = normalize_keyword_set(SECURITY_INCIDENT_HINTS)
EDU_PROCEDURE_HINTS_NORM = normalize_keyword_set(EDU_PROCEDURE_HINTS)

# Phase 52.1: Smalltalk Gate용 정규화 키워드셋
SMALLTALK_GREETINGS_NORM = normalize_keyword_set(SMALLTALK_GREETINGS)
SMALLTALK_EMOTIONS_NORM = normalize_keyword_set(SMALLTALK_EMOTIONS)
DOMAIN_HINT_KEYWORDS_NORM = normalize_keyword_set(DOMAIN_HINT_KEYWORDS)

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
        # Phase 52: 정규화된 텍스트 생성 (공백 제거)
        query_normalized = normalize_for_matching(query_lower)
        debug_info = RouterDebugInfo()

        # Phase 49: ASCII-safe 로깅
        query_safe = ascii_safe_preview(user_query, 50)

        # Phase 52.1: Step -1 - Smalltalk Gate (초단문/일상 패턴 → GENERAL_CHAT)
        # RAG가 억지로 근거 없는 정책 안내를 하는 것 방지
        smalltalk_result = self._check_smalltalk_gate(
            query_lower, query_normalized, debug_info
        )
        if smalltalk_result:
            logger.info(
                f"RuleRouter: Smalltalk gate triggered, "
                f"intent=GENERAL_CHAT, "
                f"rule_hits={debug_info.rule_hits}, "
                f"query='{query_safe}'"
            )
            return smalltalk_result

        # Phase 52: Step 0 - 절차/대응 트리거 AND 조건 체크 (최우선)
        # "절차/단계" + "보안/사고" → POLICY_QA (RAG)
        # "절차/단계" + "교육" → EDUCATION_QA (RAG)
        procedure_result = self._check_procedure_triggers(
            query_normalized, debug_info
        )
        if procedure_result:
            logger.info(
                f"RuleRouter: Procedure trigger detected (AND condition), "
                f"intent={procedure_result.tier0_intent.value}, "
                f"query='{query_safe}'"
            )
            return procedure_result

        # Step 1: 애매한 경계 체크
        clarify_result = self._check_ambiguous_boundaries(
            query_lower, query_normalized, debug_info
        )
        if clarify_result:
            logger.info(
                f"RuleRouter: Ambiguous boundary detected, needs_clarify=True, "
                f"query='{query_safe}'"
            )
            return clarify_result

        # Step 2: 치명 액션(퀴즈 3종) 체크
        critical_result = self._check_critical_actions(query_normalized, debug_info)
        if critical_result:
            logger.info(
                f"RuleRouter: Critical action detected, "
                f"sub_intent_id={critical_result.sub_intent_id}, "
                f"query='{query_safe}'"
            )
            return critical_result

        # Step 3: 명확한 키워드 매칭 (Phase 52: 정규화된 텍스트 사용)
        intent_result = self._classify_by_keywords(
            query_lower, query_normalized, user_query, debug_info
        )

        logger.info(
            f"RuleRouter: intent={intent_result.tier0_intent.value}, "
            f"domain={intent_result.domain.value}, "
            f"confidence={intent_result.confidence}, "
            f"rule_hits={debug_info.rule_hits}, "
            f"query='{query_safe}'"
        )

        return intent_result

    def _check_smalltalk_gate(
        self,
        query_lower: str,
        query_normalized: str,
        debug_info: RouterDebugInfo,
    ) -> Optional[RouterResult]:
        """Phase 52.1: Smalltalk Gate를 체크합니다.

        초단문/일상 패턴을 GENERAL_CHAT으로 보내서
        RAG가 억지로 근거 없는 정책 안내를 하는 것을 방지합니다.

        조건 (AND):
        1. 정규화 후 길이가 짧음 (2~10자)
        2. 업무 도메인 힌트가 없음
        3. 아래 패턴 중 하나:
           - 인사/반응 키워드
           - 상태/감정 키워드
           - *싶어 접미사 (정규화 후)

        Args:
            query_lower: 소문자로 변환된 질문
            query_normalized: 정규화된(공백 제거) 질문 텍스트
            debug_info: 디버그 정보 객체

        Returns:
            Optional[RouterResult]: Smalltalk이면 GENERAL_CHAT, 아니면 None
        """
        # Step 1: 길이 체크 (정규화 후 2~10자)
        # 너무 긴 문장은 Smalltalk이 아님
        query_len = len(query_normalized)
        if query_len < 2 or query_len > 10:
            return None

        # Step 2: 도메인 힌트 체크 (하나라도 있으면 Smalltalk 아님)
        if self._contains_any_normalized(query_normalized, DOMAIN_HINT_KEYWORDS_NORM):
            return None

        # Step 3: Smalltalk 패턴 체크
        is_smalltalk = False
        matched_pattern = None

        # 3-1: 인사/반응 키워드
        if self._contains_any_normalized(query_normalized, SMALLTALK_GREETINGS_NORM):
            is_smalltalk = True
            matched_pattern = "GREETING"

        # 3-2: 상태/감정 키워드
        elif self._contains_any_normalized(query_normalized, SMALLTALK_EMOTIONS_NORM):
            is_smalltalk = True
            matched_pattern = "EMOTION"

        # 3-3: *싶어 접미사 패턴 (정규화 후 적용)
        elif SMALLTALK_DESIRE_PATTERN.search(query_normalized):
            is_smalltalk = True
            matched_pattern = "DESIRE_SUFFIX"

        if not is_smalltalk:
            return None

        # Smalltalk으로 판정 → GENERAL_CHAT
        debug_info.rule_hits.append(f"SMALLTALK_GATE_{matched_pattern}")
        return RouterResult(
            tier0_intent=Tier0Intent.GENERAL_CHAT,
            domain=RouterDomain.GENERAL,
            route_type=RouterRouteType.LLM_ONLY,
            confidence=0.95,
            debug=debug_info,
        )

    def _check_procedure_triggers(
        self,
        query_normalized: str,
        debug_info: RouterDebugInfo,
    ) -> Optional[RouterResult]:
        """Phase 52: 절차/대응 트리거 AND 조건을 체크합니다.

        절차/단계 키워드 + 보안/사고 힌트 → POLICY_QA (RAG)
        절차/단계 키워드 + 교육 힌트 → EDUCATION_QA (RAG)

        이 체크는 개인화 키워드보다 먼저 수행되어,
        "보안 사고 대응 절차" 같은 질문이 개인화로 오분류되는 것을 방지합니다.

        Args:
            query_normalized: 정규화된(공백 제거) 질문 텍스트
            debug_info: 디버그 정보 객체

        Returns:
            Optional[RouterResult]: 트리거 발동 시 RouterResult, 아니면 None
        """
        has_procedure = self._contains_any_normalized(
            query_normalized, PROCEDURE_WORDS_NORM
        )
        if not has_procedure:
            return None

        # 절차 + 보안/사고 힌트 → POLICY_QA
        has_security_hint = self._contains_any_normalized(
            query_normalized, SECURITY_INCIDENT_HINTS_NORM
        )
        if has_security_hint:
            debug_info.rule_hits.append("PROCEDURE_AND_SECURITY")
            debug_info.keywords.extend([
                kw for kw in PROCEDURE_WORDS_NORM if kw in query_normalized
            ])
            debug_info.keywords.extend([
                kw for kw in SECURITY_INCIDENT_HINTS_NORM if kw in query_normalized
            ])
            return RouterResult(
                tier0_intent=Tier0Intent.POLICY_QA,
                domain=RouterDomain.POLICY,
                route_type=RouterRouteType.RAG_INTERNAL,
                confidence=0.92,
                debug=debug_info,
            )

        # 절차 + 교육 힌트 → EDUCATION_QA
        # 단, 개인화 키워드(이번 주, 할 일, 미이수 등)가 있으면 개인화로 분류하도록 스킵
        has_edu_hint = self._contains_any_normalized(
            query_normalized, EDU_PROCEDURE_HINTS_NORM
        )
        if has_edu_hint:
            # 개인화 키워드가 있으면 스킵 (EDU_STATUS_CHECK로 분류되도록)
            if self._contains_any_normalized(query_normalized, EDU_STATUS_KEYWORDS_NORM):
                debug_info.rule_hits.append("PROCEDURE_AND_EDU_SKIPPED_PERSONALIZATION")
                return None  # 개인화 흐름으로 진행
            debug_info.rule_hits.append("PROCEDURE_AND_EDU")
            debug_info.keywords.extend([
                kw for kw in PROCEDURE_WORDS_NORM if kw in query_normalized
            ])
            debug_info.keywords.extend([
                kw for kw in EDU_PROCEDURE_HINTS_NORM if kw in query_normalized
            ])
            return RouterResult(
                tier0_intent=Tier0Intent.EDUCATION_QA,
                domain=RouterDomain.EDU,
                route_type=RouterRouteType.RAG_INTERNAL,
                confidence=0.92,
                debug=debug_info,
            )

        return None

    def _check_ambiguous_boundaries(
        self,
        query_lower: str,
        query_normalized: str,
        debug_info: RouterDebugInfo,
    ) -> Optional[RouterResult]:
        """애매한 경계를 체크하고 되묻기 결과를 반환합니다.

        경계 A: 교육 내용 설명 vs 내 이수현황/진도
        경계 B: 규정 질문 vs HR/근태/복지 개인화

        Args:
            query_lower: 소문자로 변환된 질문
            query_normalized: 정규화된(공백 제거) 질문 텍스트
            debug_info: 디버그 정보 객체

        Returns:
            Optional[RouterResult]: 되묻기가 필요하면 RouterResult, 아니면 None
        """
        # 경계 A: 교육 관련 애매함 체크 (정규화 텍스트 사용)
        if self._is_boundary_a_ambiguous(query_lower, query_normalized):
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

        # 경계 B: 연차/휴가 관련 애매함 체크 (정규화 텍스트 사용)
        if self._is_boundary_b_ambiguous(query_lower, query_normalized):
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

    def _is_boundary_a_ambiguous(
        self, query_lower: str, query_normalized: str
    ) -> bool:
        """경계 A (교육 내용 vs 이수현황) 애매함을 체크합니다.

        애매한 패턴 예시:
        - "교육 알려줘" (내용? 현황?)
        - "교육 확인해줘" (내용? 진도?)
        - "교육 어떻게 되어있어?" (규정? 내 현황?)

        명확하지 않은 패턴:
        - 교육 키워드 + 애매한 동사
        - 단, EDU_CONTENT_KEYWORDS나 EDU_STATUS_KEYWORDS에 명확히 해당하면 제외

        Phase 50: EDU_RESUME_KEYWORDS, QUIZ 키워드도 명확한 개인화 패턴으로 인식
        Phase 52: 정규화된 텍스트로 매칭 (공백 변형 무시)
        """
        # 먼저 명확한 키워드가 있는지 체크 (정규화된 텍스트 사용)
        if self._contains_any_normalized(query_normalized, EDU_CONTENT_KEYWORDS_NORM):
            return False  # 명확히 교육 내용 질문
        if self._contains_any_normalized(query_normalized, EDU_STATUS_KEYWORDS_NORM):
            return False  # 명확히 교육 현황 질문
        # Phase 50: 이어보기/다시보기 패턴도 명확한 개인화 질문
        if self._contains_any_normalized(query_normalized, EDU_RESUME_KEYWORDS_NORM):
            return False  # 명확히 교육 이어보기/재생 위치 질문
        # Phase 50: 퀴즈 미완료 조회도 명확한 개인화 질문
        if self._contains_any_normalized(query_normalized, QUIZ_PENDING_KEYWORDS_NORM):
            return False  # 명확히 퀴즈 미완료 조회 질문
        # Phase 59: EDU_PANEL_OPEN 키워드는 명확한 UI 액션 요청
        if self._contains_any_normalized(query_normalized, EDU_PANEL_KEYWORDS_NORM):
            return False  # 명확히 교육 패널 열기 요청

        # 교육 키워드 + 애매한 동사 조합 체크 (원본 텍스트로 체크)
        has_edu_keyword = self._contains_any(query_lower, EDU_AMBIGUOUS_KEYWORDS)
        has_ambiguous_verb = self._contains_any(query_lower, EDU_AMBIGUOUS_VERBS)

        return has_edu_keyword and has_ambiguous_verb

    def _is_boundary_b_ambiguous(
        self, query_lower: str, query_normalized: str
    ) -> bool:
        """경계 B (규정 질문 vs HR 개인화) 애매함을 체크합니다.

        애매한 패턴 예시:
        - "연차 알려줘" (규정? 내 잔여?)
        - "휴가 확인해줘" (정책? 내 휴가?)
        - "연차 어떻게 되어있어?" (규정? 내 현황?)

        명확하지 않은 패턴:
        - 연차/휴가 키워드 + 애매한 동사
        - 단, LEAVE_POLICY_KEYWORDS나 HR_PERSONAL_KEYWORDS에 명확히 해당하면 제외

        Phase 49: "규정", "정책" 등이 있으면 명확히 정책 질문으로 판단
        Phase 52: 정규화된 텍스트로 매칭 (공백 변형 무시)
        """
        # 먼저 명확한 키워드가 있는지 체크 (정규화된 텍스트 사용)
        if self._contains_any_normalized(query_normalized, LEAVE_POLICY_KEYWORDS_NORM):
            return False  # 명확히 정책 질문
        if self._contains_any_normalized(query_normalized, HR_PERSONAL_KEYWORDS_NORM):
            return False  # 명확히 개인화 질문

        # Phase 49: "규정", "정책" 등이 있으면 명확히 정책 질문
        policy_clarifiers = {"규정", "정책", "규칙", "지침", "제도"}
        if self._contains_any(query_lower, policy_clarifiers):
            return False  # 명확히 정책 질문

        # 연차/휴가 키워드 + 애매한 동사 조합 체크 (원본 텍스트로 체크)
        has_leave_keyword = self._contains_any(query_lower, LEAVE_AMBIGUOUS_KEYWORDS)
        has_ambiguous_verb = self._contains_any(query_lower, LEAVE_AMBIGUOUS_VERBS)

        return has_leave_keyword and has_ambiguous_verb

    def _check_critical_actions(
        self,
        query_normalized: str,
        debug_info: RouterDebugInfo,
    ) -> Optional[RouterResult]:
        """치명 액션(퀴즈 3종)을 체크하고 확인 게이트를 설정합니다.

        Args:
            query_normalized: 정규화된(공백 제거) 질문 텍스트
            debug_info: 디버그 정보 객체

        Returns:
            Optional[RouterResult]: 치명 액션이면 RouterResult, 아니면 None

        Phase 52: 정규화된 텍스트로 매칭 (공백 변형 무시)
        """
        # EDU_PANEL_OPEN 체크 (정규화된 텍스트 사용)
        # OPEN_EDU_PANEL 액션으로 프론트엔드에서 교육 패널을 바로 열도록 함
        if self._contains_any_normalized(query_normalized, EDU_PANEL_KEYWORDS_NORM):
            debug_info.rule_hits.append("EDU_PANEL_OPEN")
            debug_info.keywords.extend(
                [kw for kw in EDU_PANEL_KEYWORDS_NORM if kw in query_normalized]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                domain=RouterDomain.EDU,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id=SubIntentId.EDU_PANEL_OPEN.value,
                confidence=0.95,
                requires_confirmation=False,  # confirmation 없이 바로 OPEN_EDU_PANEL 액션 반환
                debug=debug_info,
            )

        # QUIZ_START 체크 (정규화된 텍스트 사용)
        # OPEN_QUIZ 액션으로 프론트엔드에서 퀴즈 패널을 바로 열도록 함
        # confirmation 없이 바로 처리 (퀴즈 패널 내에서 실제 시작 버튼 클릭)
        if self._contains_any_normalized(query_normalized, QUIZ_START_KEYWORDS_NORM):
            debug_info.rule_hits.append("QUIZ_START_OPEN_PANEL")
            debug_info.keywords.extend(
                [kw for kw in QUIZ_START_KEYWORDS_NORM if kw in query_normalized]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                domain=RouterDomain.QUIZ,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id=SubIntentId.QUIZ_START.value,
                confidence=0.95,
                requires_confirmation=False,  # confirmation 없이 바로 OPEN_QUIZ 액션 반환
                debug=debug_info,
            )

        # QUIZ_SUBMIT 체크
        # 오탐 방지: "채점해", "점수 확인" 같은 범용 키워드는 퀴즈 문맥이 있어야만 매칭
        if self._contains_any_normalized(query_normalized, QUIZ_SUBMIT_KEYWORDS_NORM):
            # 퀴즈 문맥 확인 (키워드에 "퀴즈/시험/테스트"가 포함되어 있으면 자동 통과)
            has_quiz_context = self._contains_any_normalized(
                query_normalized, QUIZ_CONTEXT_KEYWORDS_NORM
            )
            if not has_quiz_context:
                # 퀴즈 문맥 없음 → 치명 액션으로 판정하지 않음 (다른 라우팅으로 진행)
                debug_info.rule_hits.append("QUIZ_SUBMIT_SKIPPED_NO_CONTEXT")
            else:
                debug_info.rule_hits.append("QUIZ_SUBMIT")
                debug_info.keywords.extend(
                    [kw for kw in QUIZ_SUBMIT_KEYWORDS_NORM if kw in query_normalized]
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
        if self._contains_any_normalized(query_normalized, QUIZ_GENERATION_KEYWORDS_NORM):
            debug_info.rule_hits.append("QUIZ_GENERATION")
            debug_info.keywords.extend(
                [kw for kw in QUIZ_GENERATION_KEYWORDS_NORM if kw in query_normalized]
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
        query_normalized: str,
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

        Phase 52 업데이트:
        - 정규화된 텍스트로 키워드 매칭 (공백 변형 무시)

        Args:
            query_lower: 소문자로 변환된 질문
            query_normalized: 정규화된(공백 제거) 질문 텍스트
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
        # 단, 퀴즈 점수/현황 또는 교육 현황/이어보기 개인화 키워드가 있으면 개인화로 분류
        if "교육" in query_normalized:
            # 퀴즈 점수/현황 키워드가 있으면 개인화로 분류 (EDU_CONTENT_PRIORITY 스킵)
            has_quiz_personalization = (
                self._contains_any_normalized(query_normalized, QUIZ_SCORE_KEYWORDS_NORM) or
                self._contains_any_normalized(query_normalized, QUIZ_PENDING_KEYWORDS_NORM)
            )
            # 교육 현황 개인화 키워드가 있으면 개인화로 분류 (EDU_CONTENT_PRIORITY 스킵)
            has_edu_personalization = self._contains_any_normalized(
                query_normalized, EDU_STATUS_KEYWORDS_NORM
            )
            # Phase 53: 교육 이어보기/재생위치 키워드가 있으면 개인화로 분류 (EDU_CONTENT_PRIORITY 스킵)
            has_edu_resume = self._contains_any_normalized(
                query_normalized, EDU_RESUME_KEYWORDS_NORM
            )
            if has_quiz_personalization or has_edu_personalization or has_edu_resume:
                debug_info.rule_hits.append("EDU_CONTENT_PRIORITY_SKIPPED_PERSONALIZATION")
                # 개인화 흐름으로 진행 (아래 분기에서 처리됨)
            elif self._contains_any_normalized(query_normalized, EDU_CONTENT_KEYWORDS_NORM):
                debug_info.rule_hits.append("EDU_CONTENT_PRIORITY")
                debug_info.keywords.extend(
                    [kw for kw in EDU_CONTENT_KEYWORDS_NORM if kw in query_normalized]
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
            if self._contains_any_normalized(query_normalized, POLICY_KEYWORDS_NORM) or \
               self._contains_any_normalized(query_normalized, LEAVE_POLICY_KEYWORDS_NORM) or \
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

        # 우선순위 순서대로 체크 (Phase 52: 정규화된 텍스트 사용)
        # Phase 49: POLICY를 EDU_CONTENT보다 앞으로 이동

        # 1. HR 개인화 (가장 명확한 개인화 패턴)
        if self._contains_any_normalized(query_normalized, HR_PERSONAL_KEYWORDS_NORM):
            debug_info.rule_hits.append("HR_PERSONAL")
            debug_info.keywords.extend(
                [kw for kw in HR_PERSONAL_KEYWORDS_NORM if kw in query_normalized]
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
        if self._contains_any_normalized(query_normalized, HR_TODO_KEYWORDS_NORM):
            debug_info.rule_hits.append("HR_TODO_CHECK")
            debug_info.keywords.extend(
                [kw for kw in HR_TODO_KEYWORDS_NORM if kw in query_normalized]
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
        if self._contains_any_normalized(query_normalized, EDU_STATUS_KEYWORDS_NORM):
            debug_info.rule_hits.append("EDU_STATUS")
            debug_info.keywords.extend(
                [kw for kw in EDU_STATUS_KEYWORDS_NORM if kw in query_normalized]
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
        if self._contains_any_normalized(query_normalized, EDU_RESUME_KEYWORDS_NORM):
            debug_info.rule_hits.append("EDU_RESUME_CHECK")
            debug_info.keywords.extend(
                [kw for kw in EDU_RESUME_KEYWORDS_NORM if kw in query_normalized]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                domain=RouterDomain.EDU,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id=SubIntentId.EDU_RESUME_CHECK.value,
                confidence=0.9,
                debug=debug_info,
            )

        # 2-2. 퀴즈 미완료/재응시 조회 (개인화)
        if self._contains_any_normalized(query_normalized, QUIZ_PENDING_KEYWORDS_NORM):
            debug_info.rule_hits.append("QUIZ_PENDING_CHECK")
            debug_info.keywords.extend(
                [kw for kw in QUIZ_PENDING_KEYWORDS_NORM if kw in query_normalized]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.BACKEND_STATUS,
                domain=RouterDomain.QUIZ,
                route_type=RouterRouteType.BACKEND_API,
                sub_intent_id=SubIntentId.QUIZ_PENDING_CHECK.value,
                confidence=0.9,
                debug=debug_info,
            )

        # 2-3. 퀴즈 점수/성적 조회 (개인화 Q5, Q6)
        # Phase 52: 오탐 방지 - "점수 확인" 같은 범용 키워드는 퀴즈 문맥이 있어야만 매칭
        if self._contains_any_normalized(query_normalized, QUIZ_SCORE_KEYWORDS_NORM):
            # 퀴즈 문맥 확인 (키워드에 "퀴즈/시험/테스트/평균" 포함 시 자동 통과)
            has_quiz_context = self._contains_any_normalized(
                query_normalized, QUIZ_CONTEXT_KEYWORDS_NORM
            )
            # 평균/비교/취약 같은 개인화 컨텍스트도 허용
            has_personalization_context = any(
                kw in query_normalized for kw in ["평균", "비교", "취약", "낮은", "높은"]
            )
            if not has_quiz_context and not has_personalization_context:
                # 퀴즈 문맥 없음 → QUIZ_SCORE로 판정하지 않음 (다른 라우팅으로 진행)
                debug_info.rule_hits.append("QUIZ_SCORE_SKIPPED_NO_CONTEXT")
            else:
                debug_info.rule_hits.append("QUIZ_SCORE_CHECK")
                debug_info.keywords.extend(
                    [kw for kw in QUIZ_SCORE_KEYWORDS_NORM if kw in query_normalized]
                )
                return RouterResult(
                    tier0_intent=Tier0Intent.BACKEND_STATUS,
                    domain=RouterDomain.QUIZ,
                    route_type=RouterRouteType.BACKEND_API,
                    sub_intent_id=SubIntentId.QUIZ_SCORE_CHECK.value,
                    confidence=0.9,
                    debug=debug_info,
                )

        # 3. 정책/규정 질문 (Phase 49: EDU_CONTENT보다 먼저 체크)
        if self._contains_any_normalized(query_normalized, POLICY_KEYWORDS_NORM):
            debug_info.rule_hits.append("POLICY")
            debug_info.keywords.extend(
                [kw for kw in POLICY_KEYWORDS_NORM if kw in query_normalized]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.POLICY_QA,
                domain=RouterDomain.POLICY,
                route_type=RouterRouteType.RAG_INTERNAL,
                confidence=0.85,
                debug=debug_info,
            )

        # 4. 연차/휴가 규정 질문
        if self._contains_any_normalized(query_normalized, LEAVE_POLICY_KEYWORDS_NORM):
            debug_info.rule_hits.append("LEAVE_POLICY")
            debug_info.keywords.extend(
                [kw for kw in LEAVE_POLICY_KEYWORDS_NORM if kw in query_normalized]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.POLICY_QA,
                domain=RouterDomain.POLICY,
                route_type=RouterRouteType.RAG_INTERNAL,
                confidence=0.85,
                debug=debug_info,
            )

        # 5. 교육 내용 질문 (Phase 49: POLICY보다 뒤로 이동)
        if self._contains_any_normalized(query_normalized, EDU_CONTENT_KEYWORDS_NORM):
            debug_info.rule_hits.append("EDU_CONTENT")
            debug_info.keywords.extend(
                [kw for kw in EDU_CONTENT_KEYWORDS_NORM if kw in query_normalized]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.EDUCATION_QA,
                domain=RouterDomain.EDU,
                route_type=RouterRouteType.RAG_INTERNAL,
                confidence=0.85,
                debug=debug_info,
            )

        # 6. 시스템 도움말
        if self._contains_any_normalized(query_normalized, SYSTEM_HELP_KEYWORDS_NORM):
            debug_info.rule_hits.append("SYSTEM_HELP")
            debug_info.keywords.extend(
                [kw for kw in SYSTEM_HELP_KEYWORDS_NORM if kw in query_normalized]
            )
            return RouterResult(
                tier0_intent=Tier0Intent.SYSTEM_HELP,
                domain=RouterDomain.GENERAL,
                route_type=RouterRouteType.ROUTE_SYSTEM_HELP,
                confidence=0.9,
                debug=debug_info,
            )

        # 7. 일반 잡담 (Phase 43: 질문형 문장은 제외)
        if self._contains_any_normalized(query_normalized, GENERAL_CHAT_KEYWORDS_NORM):
            # 질문형 문장이면 잡담으로 분류하지 않음
            if not self._is_question_format(query_original):
                debug_info.rule_hits.append("GENERAL_CHAT")
                debug_info.keywords.extend(
                    [kw for kw in GENERAL_CHAT_KEYWORDS_NORM if kw in query_normalized]
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

    def _contains_any_normalized(
        self, text_normalized: str, keywords_normalized: frozenset
    ) -> bool:
        """정규화된 텍스트에서 정규화된 키워드를 검색합니다.

        Phase 52: 공백 제거 기반 매칭으로 "보안사고" == "보안 사고" 문제 해결.

        Args:
            text_normalized: 정규화된(공백 제거) 텍스트
            keywords_normalized: 정규화된 키워드 집합

        Returns:
            bool: 키워드 포함 여부
        """
        return any(keyword in text_normalized for keyword in keywords_normalized)
