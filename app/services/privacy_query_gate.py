# -*- coding: utf-8 -*-
"""
Privacy Query Gate - 개인정보성 명단 요청 차단 게이트

"직원(타인) + 명단화 행위 + 민감 속성(교육/점수/평가)" 조합을 감지하여
개인 식별 가능한 인사정보 요청을 RAG/LLM 호출 전에 차단합니다.

설계 원칙:
1. 조합 규칙 기반 (하드코딩된 문장 매칭 X)
2. 1인칭(내/저) 개인화 요청은 허용
3. 점수 기반 판정으로 유지보수 용이
4. PII 마스킹 후, Intent 분류 전에 실행
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional, Set, List
from enum import Enum

logger = logging.getLogger(__name__)


class PrivacyGateDecision(str, Enum):
    """Privacy Query Gate 판정 결과"""
    ALLOW = "ALLOW"           # 허용 (파이프라인 계속 진행)
    BLOCK_PII_LIST = "BLOCK_PII_LIST"  # 개인정보 명단 요청 차단


@dataclass
class PrivacyGateResult:
    """Privacy Query Gate 판정 결과"""
    decision: PrivacyGateDecision = PrivacyGateDecision.ALLOW
    blocked: bool = False
    reason: Optional[str] = None

    # 점수 상세 (디버깅/로깅용)
    score_total: int = 0
    score_target: int = 0      # 대상(사람) 점수
    score_action: int = 0      # 명단화 행위 점수
    score_sensitive: int = 0   # 민감 속성 점수

    # 매칭된 키워드 (디버깅용)
    matched_target_terms: List[str] = field(default_factory=list)
    matched_action_terms: List[str] = field(default_factory=list)
    matched_sensitive_terms: List[str] = field(default_factory=list)

    # 1인칭 감지
    is_first_person: bool = False

    # 차단 시 반환할 응답
    block_response: Optional[str] = None


# =============================================================================
# 키워드 사전 정의
# =============================================================================

# 대상(사람/직원 집합) 지시어 - 타인을 지칭하는 표현
TARGET_PEOPLE_TERMS: Set[str] = {
    # 직원/사원 관련
    "직원", "사원", "팀원", "부서원", "동료", "인원",
    "담당자", "실무자", "근무자", "재직자",
    # 집합 표현
    "누가", "누구", "사람들", "인력", "구성원",
    # 팀/부서 표현
    "팀", "부서", "파트", "본부", "센터", "그룹",
    # 특정 그룹
    "미이수자", "수료자", "대상자", "해당자",
    "저성과자", "고성과자", "위험군",
    # 역할/직책 (다른 사람 지칭 가능)
    "관리자", "매니저", "팀장", "부장", "과장", "대리",
    "사장", "임원", "경영진", "CEO", "CTO", "CFO",
}

# 명단화/추출 행위 동사
LIST_ACTION_TERMS: Set[str] = {
    # 명단/리스트
    "리스트", "명단", "목록", "현황", "리스트업",
    # 추출/조회 동사
    "뽑아", "추출", "정리", "나열", "알려",
    "조회", "확인", "보여", "출력", "표시",
    # 랭킹/순위
    "랭킹", "순위", "상위", "하위", "최저", "최고",
    "top", "bottom", "worst", "best",
    # 분류/필터
    "분류", "필터", "골라", "선별", "찾아",
    # 질문형 표현 (암시적 명단 요청)
    "누구", "누가", "어떤", "몇 명", "몇명",
    "어느", "어디", "뭐야", "뭔지", "무엇",
}

# =============================================================================
# Phase 60: 민감 속성 레벨 분리
# =============================================================================

# 레벨 1: 직접적 개인정보 (이름만 있어도 차단)
# - "홍길동 이메일" → Action 없이도 명백한 개인정보 요청
DIRECT_PII_TERMS: Set[str] = {
    # 연락처 정보
    "이메일", "전화번호", "연락처", "휴대폰", "핸드폰", "폰번호",
    # 주소/위치 정보
    "주소", "거주지", "집주소", "자택",
    # 급여/금전 정보
    "급여", "연봉", "월급", "보너스", "인센티브", "수당",
    # 식별 정보
    "주민번호", "주민등록번호", "사번", "사원번호",
}

# 레벨 2: 상태/성과 정보 (이름 + Action 있을 때만 차단)
# - "홍길동 담당 교육 뭐야?" → 허용 (업무 질문)
# - "홍길동 교육 이수 현황 알려줘" → 차단 (개인정보 요청)
STATUS_INFO_TERMS: Set[str] = {
    # 교육 관련
    "교육", "이수", "미이수", "수료", "미수료", "진도", "수강",
    "시청률", "영상", "강의", "학습", "훈련",
    # 퀴즈/점수 관련
    "퀴즈", "점수", "성적", "오답", "정답", "테스트", "시험",
    "평가", "결과", "합격", "불합격", "탈락",
    # 성과/인사 관련
    "성과", "실적", "등급", "고과", "KPI",
    "징계", "경고", "주의", "불이익",
    # 기본 인사 정보
    "직급", "직책", "부서", "입사일", "근속",
    "id", "아이디", "이름", "성명",
}

# 전체 민감 속성 (기존 호환성 유지)
SENSITIVE_ATTRIBUTE_TERMS: Set[str] = DIRECT_PII_TERMS | STATUS_INFO_TERMS

# 업무 맥락 키워드 (이 키워드가 있으면 개인정보 요청이 아닌 업무 질문으로 간주)
# "홍길동 팀장님 담당 교육이 뭐야?" → 업무 질문, 허용
WORK_CONTEXT_TERMS: Set[str] = {
    "담당", "진행", "맡은", "책임", "관리",
    "주관", "주최", "기획", "운영", "개설",
    "만든", "작성", "올린", "등록",
}

# 1인칭 표현 (본인 요청은 허용)
FIRST_PERSON_TERMS: Set[str] = {
    "내", "나의", "제", "저의", "본인",
}

# 3인칭/타인 표현 (명시적 타인 지칭)
THIRD_PERSON_TERMS: Set[str] = {
    "다른", "타", "그", "저", "해당", "특정",
    "전체", "모든", "각", "개별",
}

# 한국 성씨 목록 (한글 이름 감지용)
# 이름 감지는 성씨로 시작하는 패턴만 이름으로 인식
KOREAN_SURNAMES: Set[str] = {
    "김", "이", "박", "최", "정", "강", "조", "윤", "장", "임",
    "한", "오", "서", "신", "권", "황", "안", "송", "류", "전",
    "홍", "고", "문", "양", "손", "배", "백", "허", "유", "남",
    "심", "노", "하", "곽", "성", "차", "주", "우", "구", "민",
    "진", "나", "원", "천", "방", "공", "현", "함", "변", "염",
}

# 성씨로 시작하지만 이름이 아닌 흔한 단어들
# 이 단어들은 성씨 검사에서 제외됨
NON_NAME_WORDS: Set[str] = {
    # 교육/업무 관련
    "교육", "현황", "정보", "성과", "문서", "문제", "방법", "방침",
    "고객", "고과", "배포", "배치", "원본", "원칙", "공개", "공유",
    "심사", "심각", "변경", "변수", "함수", "함께", "진행", "진도",
    "정책", "정리", "정답", "정보보안", "정보보호", "개인정보", "개인정보보호",
    # 이수/수료 관련 (이(성씨)+수 조합)
    "이수", "미이수", "이수자", "미이수자", "이메일",
    # 기술 관련
    "서버", "서비스", "문의", "조회", "조치", "진단", "손실",
    # 상태/수치 관련
    "최고", "최저", "최신", "최근", "고성과", "저성과", "하위", "상위",
    # 기타 자주 사용되는 단어
    "전체", "전화", "전문", "전략", "백업", "백서", "유지", "유출",
    "남은", "남용", "차단", "주요", "주의", "우선", "구현", "구성",
    "민감", "민원", "양식", "양성", "황폐", "황당",
    # 기타 복합어
    "내용", "내역", "나의", "나열",
    # Phase 60: 한(韓) 성씨로 시작하는 흔한 단어들 (이름 오인식 방지)
    "한번", "한달", "한해", "한시간", "한주", "한쪽", "한국", "한글",
    "한계", "한도", "한정", "한마디", "한눈", "한편", "한동안",
    # Phase 60: 이(李) 성씨로 시작하는 흔한 단어들
    "이번", "이번주", "이번달", "이후", "이전", "이상", "이하", "이유",
    "이용", "이론", "이해", "이력", "이날", "이때", "이렇게", "이런",
    # Phase 60: 오(吳) 성씨로 시작하는 흔한 단어들
    "오전", "오후", "오늘", "오류", "오랜", "오래", "오히려",
    # Phase 60: 강(姜) 성씨로 시작하는 흔한 단어들
    "강의", "강좌", "강화", "강조", "강력", "강제",
    # Phase 60: 성(成) 성씨로 시작하는 흔한 단어들
    "성희롱", "성과", "성적", "성공", "성능", "성립",
    # Phase 60: 장(張) 성씨로 시작하는 흔한 단어들
    "장애인", "장애", "장기", "장비", "장점", "장소", "장치",
    # Phase 60: 기타 성씨로 시작하는 흔한 단어들
    "고용", "고객", "고려", "고장", "고정",
    "임원", "임시", "임금", "임대",
    "권한", "권리", "권고",
    "황당", "황폐",
    "안내", "안전", "안정",
    "유형", "유지", "유출", "유효",
    "배포", "배경", "배치", "배워",
}


# =============================================================================
# 표준 차단 문구
# =============================================================================

PRIVACY_BLOCK_RESPONSE = """요청하신 내용은 특정 직원의 교육 이수 여부나 퀴즈 점수처럼 **개인 식별이 가능한 인사·교육 정보**를 포함할 수 있어 제공할 수 없습니다.

대신 다음과 같은 방법으로 도움드릴 수 있어요:
- **본인 정보 조회**: 본인의 교육/퀴즈 현황은 조회해 드릴 수 있습니다.
- **익명화된 통계**: 조직 단위로는 부서 평균, 분포, 미이수 인원 수 등 집계 형태로만 안내 가능합니다.

본인 교육 현황이나 조직 통계가 필요하시면 다시 질문해 주세요."""


# =============================================================================
# Privacy Query Gate 서비스
# =============================================================================

class PrivacyQueryGate:
    """
    개인정보성 명단 요청을 차단하는 게이트

    차단 조건 (3개 동시 성립):
    1. 대상(사람/직원 집합) 지시 - score +2
    2. 명단화/추출 행위 - score +3
    3. 민감 속성(교육/점수/평가) - score +3

    총점 >= 6 이면 차단

    허용 조건:
    - 1인칭(내/저) 중심의 개인화 조회
    """

    def __init__(
        self,
        block_threshold: int = 6,
        target_score: int = 2,
        action_score: int = 3,
        sensitive_score: int = 3,
    ):
        self.block_threshold = block_threshold
        self.target_score = target_score
        self.action_score = action_score
        self.sensitive_score = sensitive_score

        # 정규식 패턴 컴파일 (성능 최적화)
        self._target_pattern = self._build_pattern(TARGET_PEOPLE_TERMS)
        self._action_pattern = self._build_pattern(LIST_ACTION_TERMS)
        self._sensitive_pattern = self._build_pattern(SENSITIVE_ATTRIBUTE_TERMS)
        self._first_person_pattern = self._build_pattern(FIRST_PERSON_TERMS)
        self._third_person_pattern = self._build_pattern(THIRD_PERSON_TERMS)
        # Phase 60: 민감도 레벨별 패턴
        self._direct_pii_pattern = self._build_pattern(DIRECT_PII_TERMS)
        self._status_info_pattern = self._build_pattern(STATUS_INFO_TERMS)
        self._work_context_pattern = self._build_pattern(WORK_CONTEXT_TERMS)

        # Phase 62: "000의 직급"처럼 마스킹된 특정인 + 소유격(의) + 인사/개인정보 요청 감지
        # - 기존 이름 감지는 성씨 기반이라 "000/OOO/XX/**" 같은 마스킹 토큰을 놓칠 수 있음
        self._masked_possessive_subject_pattern = re.compile(
            r"(?P<subject>(?:0{2,}|o{2,}|x{2,}|\*{2,}|[○●□■]{2,}|\d{2,}))\s*의",
            re.IGNORECASE,
        )
        # 조직/제도 표현(보안팀의/인사부의 등)은 사람으로 오인하지 않도록 제외
        self._non_person_possessive_pattern = re.compile(
            r"(?:[가-힣A-Za-z0-9]{1,20})(?:팀|부서|파트|본부|센터|그룹|조직|회사|프로젝트|인사부|재무부|총무부)\s*의"
        )

    def _build_pattern(self, terms: Set[str]) -> re.Pattern:
        """키워드 집합을 정규식 패턴으로 컴파일"""
        # 긴 패턴부터 매칭 (예: "미이수자" > "이수")
        sorted_terms = sorted(terms, key=len, reverse=True)
        escaped = [re.escape(t) for t in sorted_terms]
        pattern = r"(" + "|".join(escaped) + r")"
        return re.compile(pattern, re.IGNORECASE)

    def _find_matches(self, query: str, pattern: re.Pattern) -> List[str]:
        """쿼리에서 패턴에 매칭되는 모든 키워드 반환"""
        matches = pattern.findall(query)
        return [m.lower() for m in matches]

    def _has_masked_third_party_subject(self, query: str) -> bool:
        """
        Phase 62: '000의/OOO의/XX의/**의' 형태로 특정 개인을 지칭하는 마스킹 토큰 감지.
        단, '보안팀의/인사부의' 같은 조직 표현은 제외합니다.
        """
        if not query:
            return False
        if self._non_person_possessive_pattern.search(query):
            return False
        return bool(self._masked_possessive_subject_pattern.search(query))

    def _filter_korean_names(self, korean_matches: List[str]) -> List[str]:
        """
        Phase 60: 한글 매칭 결과에서 실제 이름만 필터링.

        조사가 붙은 형태("한달에", "한번도" 등)도 처리하기 위해
        조사를 제거한 원형과 NON_NAME_WORDS를 비교합니다.

        Args:
            korean_matches: 정규식으로 매칭된 한글 단어 목록

        Returns:
            List[str]: 실제 이름으로 판단된 단어 목록
        """
        # 한글 조사 목록 (긴 것부터 제거해야 함)
        particles = ['에서', '으로', '부터', '까지', '에게', '한테',
                     '은', '는', '이', '가', '을', '를', '의', '에',
                     '로', '와', '과', '도', '만', '요']

        actual_names = []
        for m in korean_matches:
            # 성씨로 시작하지 않으면 이름 아님
            if m[0] not in KOREAN_SURNAMES:
                continue

            # 원형 그대로 NON_NAME_WORDS에 있으면 이름 아님
            if m in NON_NAME_WORDS:
                continue

            # 조사 제거 후 원형 추출
            base_word = m
            for particle in sorted(particles, key=len, reverse=True):
                if m.endswith(particle) and len(m) > len(particle):
                    base_word = m[:-len(particle)]
                    break

            # 원형이 NON_NAME_WORDS에 있으면 이름 아님
            if base_word in NON_NAME_WORDS:
                continue

            # 위 조건을 모두 통과하면 이름으로 판단
            actual_names.append(m)

        return actual_names

    def _is_first_person_query(self, query: str) -> bool:
        """1인칭 개인화 요청인지 판단"""
        # 1인칭 개인정보 조회 패턴을 먼저 체크 (가장 우선순위)
        # "내 부서", "내 직급", "내 이메일" 등은 1인칭으로 처리
        # 공백 없이도 매칭되도록 하고, 조사(은/는/이/가/을/를)가 있어도 매칭되도록 함
        # "내 직급은?", "내 이메일이 궁금해", "내 이메일이 뭐야?" 같은 패턴도 매칭
        personal_info_keywords = ["부서", "직급", "직책", "이메일", "전화번호", "연락처", "입사일", "근속", "정보", "프로필"]
        first_person_terms = ["내", "나의", "제", "저의", "본인"]
        
        # 간단한 키워드 매칭으로 먼저 체크 (더 확실함)
        query_lower = query.lower()
        for first_term in first_person_terms:
            for keyword in personal_info_keywords:
                # "내 이메일", "내이메일", "내 이메일이", "내이메일이" 등 다양한 패턴
                if first_term in query_lower and keyword in query_lower:
                    # "내"와 키워드가 모두 포함되어 있고, 순서가 맞는지 확인
                    first_idx = query_lower.find(first_term)
                    keyword_idx = query_lower.find(keyword)
                    if first_idx < keyword_idx:  # "내"가 키워드보다 앞에 있어야 함
                        logger.info(f"[PrivacyGate] 1인칭 개인정보 패턴 매칭 (키워드): first_term={first_term}, keyword={keyword}, query={query[:80]}")
                        return True
        
        # 정규식 패턴으로도 체크 (더 정확한 매칭)
        for first_term in first_person_terms:
            for keyword in personal_info_keywords:
                # "내 직급", "내직급", "내 직급은", "내직급은", "내 이메일이", "내 이메일이 뭐야" 등 다양한 패턴 매칭
                # 조사(은/는/이/가/을/를)와 질문형 표현(뭐야/뭔지/어떻게/어디)이 있어도 매칭되도록 함
                # 패턴을 더 유연하게: 조사와 질문형 표현이 선택적이도록
                patterns = [
                    rf"{re.escape(first_term)}\s*{re.escape(keyword)}",  # "내 이메일"
                    rf"{re.escape(first_term)}{re.escape(keyword)}",  # "내이메일"
                    rf"{re.escape(first_term)}\s*{re.escape(keyword)}[은는이가을를]",  # "내 이메일이"
                    rf"{re.escape(first_term)}{re.escape(keyword)}[은는이가을를]",  # "내이메일이"
                    rf"{re.escape(first_term)}\s*{re.escape(keyword)}[은는이가을를]?\s*(뭐야|뭔지|어떻게|어디|궁금|알려|보여|확인|조회)",  # "내 이메일이 뭐야"
                    rf"{re.escape(first_term)}{re.escape(keyword)}[은는이가을를]?\s*(뭐야|뭔지|어떻게|어디|궁금|알려|보여|확인|조회)",  # "내이메일이 뭐야"
                ]
                for pattern in patterns:
                    if re.search(pattern, query, re.IGNORECASE):
                        logger.info(f"[PrivacyGate] 1인칭 개인정보 패턴 매칭 (정규식): pattern={pattern}, query={query[:80]}")
                        return True
        
        # 한글 이름 패턴 감지 (성씨로 시작하는 2-4글자)
        # 예: "최기민", "홍길동", "김철수" 등
        # 성씨로 시작하지 않거나 NON_NAME_WORDS에 포함된 단어는 이름으로 간주하지 않음
        korean_name_pattern = r'[가-힣]{2,4}(?=\s|$|[은는이가을를의])'
        korean_matches = re.findall(korean_name_pattern, query)
        # Phase 60: 조사가 붙은 형태도 처리 ("한달에" → "한달"로 비교)
        actual_korean_names = self._filter_korean_names(korean_matches)
        has_korean_name = len(actual_korean_names) > 0

        # 영문 이름 패턴 감지 (대문자로 시작하는 2-3단어)
        # 예: "John Smith", "Kim", "Lee" 등
        english_name_pattern = r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b'
        has_english_name = bool(re.search(english_name_pattern, query))

        # 실제 이름이 있으면 1인칭이 아님 (다른 사람 질문)
        if has_korean_name or has_english_name:
            return False

        # 1인칭 표현이 있고, 3인칭/타인 표현이 없으면 개인화 요청
        has_first = bool(self._first_person_pattern.search(query))
        has_third = bool(self._third_person_pattern.search(query))

        # "내 팀원" 같은 경우는 타인 요청으로 처리
        # 하지만 "내 부서", "내 직급" 같은 1인칭 개인정보 조회는 위에서 이미 처리됨
        has_target = bool(self._target_pattern.search(query))

        if has_first and not has_third and not has_target:
            return True

        # "내 교육 현황" 같은 명확한 개인화 패턴
        personal_patterns = [
            r"(내|나의|제|저의|본인)\s*(교육|퀴즈|점수|성적|현황|이수)",
            r"(내가|제가)\s*(들은|수강한|이수한|본)",
        ]
        for p in personal_patterns:
            if re.search(p, query):
                return True

        return False

    def check(self, query: str) -> PrivacyGateResult:
        """
        쿼리가 개인정보성 명단 요청인지 검사

        Args:
            query: 사용자 쿼리 (PII 마스킹된 상태)

        Returns:
            PrivacyGateResult: 판정 결과
        """
        result = PrivacyGateResult()

        # 쿼리 정규화
        normalized_query = query.lower().strip()

        # 1인칭 개인화 요청 체크 (먼저 확인)
        # 원본 쿼리와 정규화된 쿼리 모두 체크 (한글 패턴 매칭을 위해)
        result.is_first_person = self._is_first_person_query(query) or self._is_first_person_query(normalized_query)
        
        # 디버깅: 1인칭 체크 결과 로그
        logger.debug(
            f"[PrivacyGate] 1인칭 체크 결과: query={query[:80]}, "
            f"is_first_person={result.is_first_person}, "
            f"original_result={self._is_first_person_query(query)}, "
            f"normalized_result={self._is_first_person_query(normalized_query)}"
        )

        # ---------------------------------------------------------------------
        # Phase 62: 마스킹된 타인(000/OOO/XX/**) + 소유격(의) + 인사/개인화 속성 요청 차단
        # - 예: "000의 직급을 알려줘" 가 통과하면, 파이프라인이 로그인 사용자 facts로 답해버리는 사고가 발생함
        # - 여기서 조기에 차단해 프라이버시 정책 응답을 반환한다
        # ---------------------------------------------------------------------
        if not result.is_first_person and self._has_masked_third_party_subject(normalized_query):
            matched_direct_pii = self._find_matches(normalized_query, self._direct_pii_pattern)
            matched_status = self._find_matches(normalized_query, self._status_info_pattern)
            matched_work_context = self._find_matches(normalized_query, self._work_context_pattern)

            # 직접적 PII는 즉시 차단
            if matched_direct_pii:
                result.decision = PrivacyGateDecision.BLOCK_PII_LIST
                result.blocked = True
                result.reason = f"masked third-party possessive direct pii: {matched_direct_pii}"
                result.block_response = PRIVACY_BLOCK_RESPONSE
                result.matched_sensitive_terms = matched_direct_pii
                logger.warning(
                    f"[PrivacyGate] BLOCKED (MASKED_POSSESSIVE + DIRECT_PII) - "
                    f"pii={matched_direct_pii}, query_preview={query[:80]}..."
                )
                return result

            # 상태/인사 정보는 기본 차단, 단 업무 맥락(담당/진행 등)이 있으면 업무 질문으로 허용
            if matched_status and not matched_work_context:
                result.decision = PrivacyGateDecision.BLOCK_PII_LIST
                result.blocked = True
                result.reason = f"masked third-party possessive status info: {matched_status}"
                result.block_response = PRIVACY_BLOCK_RESPONSE
                result.matched_sensitive_terms = matched_status
                logger.warning(
                    f"[PrivacyGate] BLOCKED (MASKED_POSSESSIVE + STATUS_INFO) - "
                    f"status={matched_status}, query_preview={query[:80]}..."
                )
                return result
        
        # 이름이 포함된 질문은 무조건 차단 (다른 사람의 개인정보 요청)
        # "한규화의", "최기민의" 같은 소유격 표현도 감지
        # 성씨로 시작하지 않거나 NON_NAME_WORDS에 포함된 단어는 이름으로 간주하지 않음
        korean_name_pattern = r'[가-힣]{2,4}(?=\s|$|[은는이가을를의])'
        korean_name_with_possessive = r'([가-힣]{2,4})의'  # "한규화의", "최기민의" 등
        english_name_pattern = r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b'

        # Phase 60: 조사 붙은 형태도 처리하는 필터링 적용
        korean_matches = re.findall(korean_name_pattern, query)
        possessive_matches = re.findall(korean_name_with_possessive, query)
        all_korean_matches = korean_matches + possessive_matches
        actual_korean_names = self._filter_korean_names(all_korean_matches)
        has_korean_name = len(actual_korean_names) > 0
        has_english_name = bool(re.search(english_name_pattern, query))

        if (has_korean_name or has_english_name) and not result.is_first_person:
            # Phase 60: 민감도 레벨별 차단 로직
            # 이름이 있고 1인칭이 아닌 경우, 민감도에 따라 차단 여부 결정

            # 레벨 1: 직접적 PII (이메일, 전화번호, 급여 등) → Action 없이도 차단
            matched_direct_pii = self._find_matches(normalized_query, self._direct_pii_pattern)
            if matched_direct_pii:
                result.decision = PrivacyGateDecision.BLOCK_PII_LIST
                result.blocked = True
                result.reason = (
                    f"다른 사람의 직접적 개인정보 요청 감지: "
                    f"이름={actual_korean_names or 'English'}, "
                    f"직접PII={matched_direct_pii}"
                )
                result.block_response = PRIVACY_BLOCK_RESPONSE
                logger.warning(
                    f"[PrivacyGate] BLOCKED (DIRECT_PII) - "
                    f"pii={matched_direct_pii}, query_preview={query[:80]}..."
                )
                return result

            # 레벨 2: 상태/성과/인사 정보 (교육, 점수, 직급/부서 등)
            # Phase 62: "임성현의 직급은?" 처럼 Action 키워드 없이 묻는 타인 인사정보 질문도
            # 개인식별 가능한 정보 요청으로 간주하여 차단해야 함.
            # 단, 업무 맥락 키워드("담당", "진행" 등)가 있으면 업무 질문으로 간주하여 허용.
            matched_status = self._find_matches(normalized_query, self._status_info_pattern)
            matched_work_context = self._find_matches(normalized_query, self._work_context_pattern)

            if matched_status:
                # 업무 맥락이 있으면 허용
                if matched_work_context:
                    logger.debug(
                        f"[PrivacyGate] ALLOW (work context) - "
                        f"work_context={matched_work_context}, "
                        f"query_preview={query[:80]}..."
                    )
                else:
                    result.decision = PrivacyGateDecision.BLOCK_PII_LIST
                    result.blocked = True
                    result.reason = (
                        f"다른 사람의 상태/인사정보 요청 감지: "
                        f"이름={actual_korean_names or 'English'}, "
                        f"상태정보={matched_status}"
                    )
                    result.block_response = PRIVACY_BLOCK_RESPONSE
                    logger.warning(
                        f"[PrivacyGate] BLOCKED (STATUS_INFO) - "
                        f"status={matched_status}, query_preview={query[:80]}..."
                    )
                    return result
        
        if result.is_first_person:
            logger.info(f"[PrivacyGate] 1인칭 개인화 요청으로 허용: query={query[:80]}, is_first_person={result.is_first_person}")
            result.decision = PrivacyGateDecision.ALLOW
            return result
        
        # 1인칭이 아닌 경우에만 키워드 매칭 및 점수 계산 진행
        logger.debug(f"[PrivacyGate] 1인칭이 아님, 키워드 매칭 진행: query={query[:80]}, is_first_person={result.is_first_person}")

        # 키워드 매칭
        result.matched_target_terms = self._find_matches(normalized_query, self._target_pattern)
        result.matched_action_terms = self._find_matches(normalized_query, self._action_pattern)
        result.matched_sensitive_terms = self._find_matches(normalized_query, self._sensitive_pattern)
        
        # 디버깅: 1인칭이 아닌데 민감 속성이 포함된 경우 로그
        if result.matched_sensitive_terms and not result.is_first_person:
            logger.debug(
                f"[PrivacyGate] 1인칭이 아닌 민감 속성 감지: "
                f"query={query[:80]}, is_first_person={result.is_first_person}, "
                f"sensitive={result.matched_sensitive_terms}"
            )

        # 점수 계산
        if result.matched_target_terms:
            result.score_target = self.target_score
        if result.matched_action_terms:
            result.score_action = self.action_score
        if result.matched_sensitive_terms:
            result.score_sensitive = self.sensitive_score

        result.score_total = result.score_target + result.score_action + result.score_sensitive

        # 차단 판정
        # 설계 원칙: 3개 조건(대상+행위+민감속성) 동시 성립 시에만 차단
        # 대상(사람/직원 집합)이 없으면 개인화 요청으로 간주하여 허용
        has_target = result.score_target > 0
        has_action = result.score_action > 0
        has_sensitive = result.score_sensitive > 0

        # 대상이 없으면 (=타인 지칭 없음) 개인화 요청으로 간주
        if not has_target:
            result.decision = PrivacyGateDecision.ALLOW
            logger.debug(
                f"[PrivacyGate] ALLOW (no target person) - score={result.score_total}, "
                f"action={result.matched_action_terms}, "
                f"sensitive={result.matched_sensitive_terms}, "
                f"query_preview={query[:50]}..."
            )
        elif result.score_total >= self.block_threshold and has_target and has_action and has_sensitive:
            # 3개 조건 모두 성립 시 차단
            # Phase 60: 업무 맥락이 있으면 허용
            matched_work_context = self._find_matches(normalized_query, self._work_context_pattern)
            if matched_work_context:
                result.decision = PrivacyGateDecision.ALLOW
                logger.debug(
                    f"[PrivacyGate] ALLOW (work context in score-based) - "
                    f"work_context={matched_work_context}, score={result.score_total}, "
                    f"query_preview={query[:80]}..."
                )
            else:
                result.decision = PrivacyGateDecision.BLOCK_PII_LIST
                result.blocked = True
                result.reason = (
                    f"개인정보성 명단 요청 감지: "
                    f"대상={result.matched_target_terms}, "
                    f"행위={result.matched_action_terms}, "
                    f"속성={result.matched_sensitive_terms}"
                )
                result.block_response = PRIVACY_BLOCK_RESPONSE

                logger.warning(
                    f"[PrivacyGate] BLOCKED - score={result.score_total}, "
                    f"target={result.matched_target_terms}, "
                    f"action={result.matched_action_terms}, "
                    f"sensitive={result.matched_sensitive_terms}, "
                    f"query_preview={query[:80]}..."
                )
        else:
            result.decision = PrivacyGateDecision.ALLOW
            logger.debug(
                f"[PrivacyGate] ALLOW - score={result.score_total}, "
                f"query_preview={query[:50]}..."
            )

        return result


# =============================================================================
# 싱글톤 인스턴스
# =============================================================================

_privacy_gate_instance: Optional[PrivacyQueryGate] = None


def get_privacy_gate() -> PrivacyQueryGate:
    """PrivacyQueryGate 싱글톤 인스턴스 반환"""
    global _privacy_gate_instance
    if _privacy_gate_instance is None:
        _privacy_gate_instance = PrivacyQueryGate()
    return _privacy_gate_instance
