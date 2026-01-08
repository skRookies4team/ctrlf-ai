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

# 민감 속성 (교육/점수/평가/징계 등 인사성 정보)
SENSITIVE_ATTRIBUTE_TERMS: Set[str] = {
    # 교육 관련
    "교육", "이수", "미이수", "수료", "미수료", "진도", "수강",
    "시청률", "영상", "강의", "학습", "훈련",
    # 퀴즈/점수 관련
    "퀴즈", "점수", "성적", "오답", "정답", "테스트", "시험",
    "평가", "결과", "합격", "불합격", "탈락",
    # 성과/인사 관련
    "성과", "실적", "평가", "등급", "고과", "KPI",
    "징계", "경고", "주의", "불이익",
    # 기타 민감 정보
    "급여", "연봉", "보너스", "인센티브",
    # 개인 식별 정보
    "id", "아이디", "이름", "성명", "주소", "주민번호",
    "이메일", "전화번호", "연락처", "휴대폰",
    "직급", "직책", "부서", "입사일", "근속",
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
        
        # 한글 이름 패턴 감지 (2-4글자 한글 이름)
        # 예: "최기민", "홍길동", "김철수" 등
        # 주의: "부서", "직급" 같은 단어는 위에서 이미 처리되었으므로 여기서는 실제 이름만 감지
        korean_name_pattern = r'[가-힣]{2,4}(?=\s|$|[은는이가을를의])'
        has_korean_name = bool(re.search(korean_name_pattern, query))
        
        # 영문 이름 패턴 감지 (대문자로 시작하는 2-3단어)
        # 예: "John Smith", "Kim", "Lee" 등
        english_name_pattern = r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b'
        has_english_name = bool(re.search(english_name_pattern, query))
        
        # 이름이 있으면 1인칭이 아님 (다른 사람 질문)
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
        
        # 이름이 포함된 질문은 무조건 차단 (다른 사람의 개인정보 요청)
        # "한규화의", "최기민의" 같은 소유격 표현도 감지
        korean_name_pattern = r'[가-힣]{2,4}(?=\s|$|[은는이가을를의])'
        korean_name_with_possessive = r'[가-힣]{2,4}의'  # "한규화의", "최기민의" 등
        english_name_pattern = r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b'
        has_korean_name = bool(re.search(korean_name_pattern, query)) or bool(re.search(korean_name_with_possessive, query))
        has_english_name = bool(re.search(english_name_pattern, query))
        
        if (has_korean_name or has_english_name) and not result.is_first_person:
            # 이름이 있고 1인칭이 아니면 다른 사람의 개인정보 요청
            # 민감 속성(이메일, 부서, 직급 등)이 포함되어 있는지 확인
            matched_sensitive = self._find_matches(normalized_query, self._sensitive_pattern)
            if matched_sensitive:
                result.decision = PrivacyGateDecision.BLOCK_PII_LIST
                result.blocked = True
                result.reason = (
                    f"다른 사람의 개인정보 요청 감지: "
                    f"이름={has_korean_name or has_english_name}, "
                    f"민감속성={matched_sensitive}"
                )
                result.block_response = PRIVACY_BLOCK_RESPONSE
                logger.warning(
                    f"[PrivacyGate] BLOCKED - 다른 사람의 개인정보 요청: "
                    f"query_preview={query[:80]}..."
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
