"""
Conversation State 모델

멀티턴 대화에서 맥락을 유지하기 위한 상태 슬롯 정의.
"최근 N턴 히스토리 + 구조화 상태 슬롯" 조합으로 토큰 효율성과 맥락 정확도 확보.

주요 구성:
- DocReference: 문서 참조 정보 (신뢰도 메타 포함)
- ConversationState: 대화 상태 슬롯
- TopicSwitchResult: 토픽 전환 감지 결과
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import ClassVar, Dict, List, Optional, Any
import json


class DocReferenceReason(Enum):
    """문서 참조가 저장된 사유 (갱신 규칙에 사용)"""

    USER_SELECTED = "user_selected"      # 사용자가 되묻기에서 명시적 선택
    RAG_TOP1_HIGH = "rag_top1_high"      # RAG top1이 고신뢰 (score >= threshold)
    RAG_TOP1_LOW = "rag_top1_low"        # RAG top1이 저신뢰 (보류 상태)
    FALLBACK_FILTER = "fallback_filter"  # fallback doc_id filter로 잡힘
    ANAPHORA_RESOLVED = "anaphora_resolved"  # 지시어 해소로 확정


@dataclass
class DocReference:
    """
    문서 참조 정보 (신뢰도 메타 포함)

    상태 슬롯에 "왜 이 값이 저장됐는지"를 함께 기록하여
    갱신 규칙 적용 및 디버깅에 활용.

    Attributes:
        doc_id: 문서 고유 ID
        title: 문서 제목
        domain: 도메인 (POLICY, EDUCATION 등)
        score: retrieval score (0.0 ~ 1.0)
        reason: 갱신 사유
        turn: 몇 번째 턴에서 확정됐는지
        citations: 인용된 조항 목록 (예: ["제10조 제2항"])
    """

    doc_id: str
    title: str
    domain: str
    score: float = 0.0
    reason: DocReferenceReason = DocReferenceReason.RAG_TOP1_LOW
    turn: int = 0
    citations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """직렬화용 딕셔너리 변환"""
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "domain": self.domain,
            "score": self.score,
            "reason": self.reason.value,
            "turn": self.turn,
            "citations": self.citations,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DocReference":
        """딕셔너리에서 복원"""
        return cls(
            doc_id=data["doc_id"],
            title=data["title"],
            domain=data["domain"],
            score=data.get("score", 0.0),
            reason=DocReferenceReason(data.get("reason", "rag_top1_low")),
            turn=data.get("turn", 0),
            citations=data.get("citations", []),
        )

    @property
    def is_high_confidence(self) -> bool:
        """고신뢰 문서인지 확인"""
        return self.reason in (
            DocReferenceReason.USER_SELECTED,
            DocReferenceReason.RAG_TOP1_HIGH,
        )


class TopicSwitchAction(Enum):
    """토픽 전환 시 수행할 액션"""

    NONE = "none"              # 전환 없음
    RESET_BOOST = "reset_boost"    # 부스팅 비활성화
    DECAY_BOOST = "decay_boost"    # 부스팅 약화
    RESET_STATE = "reset_state"    # 상태 초기화


@dataclass
class TopicSwitchResult:
    """토픽 전환 감지 결과"""

    switched: bool
    action: TopicSwitchAction = TopicSwitchAction.NONE
    reason: Optional[str] = None


@dataclass
class ConversationState:
    """
    대화 상태 슬롯

    토큰 효율적인 맥락 유지를 위한 구조화된 상태 저장소.
    "결정된 사실"을 짧고 결정적으로 보존.

    Attributes:
        user_id: 사용자 ID
        session_id: 세션 ID

        # 라우팅 결과 (Single Source of Truth)
        last_domain: 마지막 도메인 (POLICY, EDUCATION 등)
        last_intent: 마지막 인텐트 (POLICY_QA, EDUCATION_VIDEO 등)

        # 최근 문서 스택 (D: recent_docs)
        recent_docs: 최근 참조된 문서 목록 (최대 5개)

        # 엔티티/키워드
        last_entities: 마지막 추출된 엔티티 목록

        # 턴 관리
        turn_count: 현재 턴 번호
        created_at: 상태 생성 시간
        updated_at: 마지막 업데이트 시간

        # CAS (Compare-And-Swap) 버전 관리
        state_version: 동시성 제어용 버전 (멀티 인스턴스 충돌 방지)
    """

    # 식별자
    user_id: str = ""
    session_id: str = ""

    # 라우팅 결과 (C: Single Source of Truth)
    last_domain: Optional[str] = None
    last_intent: Optional[str] = None

    # 최근 문서 스택 (D: recent_docs)
    recent_docs: List[DocReference] = field(default_factory=list)

    # 엔티티/키워드
    last_entities: List[str] = field(default_factory=list)

    # 턴 관리
    turn_count: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    # CAS 버전 관리 (동시 요청 충돌 방지)
    state_version: int = 0

    # 상수
    RECENT_DOCS_MAX_SIZE: ClassVar[int] = 5

    # ==========================================================================
    # Recent Docs 관리 (D)
    # ==========================================================================

    def add_recent_doc(self, doc: DocReference) -> None:
        """
        recent_docs에 문서 추가

        - 중복 제거 (같은 doc_id면 갱신)
        - 최신이 앞에 위치
        - 크기 제한 (RECENT_DOCS_MAX_SIZE)

        Args:
            doc: 추가할 문서 참조
        """
        # 중복 제거
        self.recent_docs = [d for d in self.recent_docs if d.doc_id != doc.doc_id]

        # 앞에 추가 (최신이 앞)
        self.recent_docs.insert(0, doc)

        # 크기 제한
        if len(self.recent_docs) > self.RECENT_DOCS_MAX_SIZE:
            self.recent_docs = self.recent_docs[:self.RECENT_DOCS_MAX_SIZE]

        self.updated_at = datetime.now()

    def get_last_doc(self) -> Optional[DocReference]:
        """가장 최근 문서 참조 반환"""
        return self.recent_docs[0] if self.recent_docs else None

    def get_doc_by_id(self, doc_id: str) -> Optional[DocReference]:
        """doc_id로 문서 참조 조회"""
        return next((d for d in self.recent_docs if d.doc_id == doc_id), None)

    # ==========================================================================
    # 상태 갱신 규칙 (B)
    # ==========================================================================

    def should_update_doc(
        self,
        candidate: DocReference,
        high_score_threshold: float = 0.75,
    ) -> bool:
        """
        문서 상태 갱신 여부 판단 (B: 갱신 규칙)

        우선순위:
        1. USER_SELECTED: 항상 갱신
        2. FALLBACK_FILTER: 갱신 안 함
        3. 기존 상태 없음: 갱신
        4. 기존이 USER_SELECTED: HIGH 이상만 덮어쓰기 가능
        5. 신뢰도 비교: candidate.score > current.score

        Args:
            candidate: 갱신 후보 문서
            high_score_threshold: 고신뢰 판정 임계값

        Returns:
            bool: 갱신 여부
        """
        current = self.get_last_doc()

        # 1. USER_SELECTED는 항상 갱신
        if candidate.reason == DocReferenceReason.USER_SELECTED:
            return True

        # 2. FALLBACK_FILTER는 갱신 안 함 (오답 고착 방지)
        if candidate.reason == DocReferenceReason.FALLBACK_FILTER:
            return False

        # 3. 기존 상태 없으면 갱신
        if current is None:
            return True

        # 4. 기존이 USER_SELECTED면 HIGH 이상만 덮어쓰기 가능
        if current.reason == DocReferenceReason.USER_SELECTED:
            return candidate.reason in (
                DocReferenceReason.USER_SELECTED,
                DocReferenceReason.RAG_TOP1_HIGH,
            )

        # 5. 신뢰도 비교
        return candidate.score > current.score

    def update_from_routing(
        self,
        domain: str,
        intent: str,
    ) -> TopicSwitchResult:
        """
        라우팅 결과로 상태 업데이트 (C: Single Source of Truth)

        Args:
            domain: 현재 도메인
            intent: 현재 인텐트

        Returns:
            TopicSwitchResult: 토픽 전환 결과
        """
        result = self.detect_topic_switch(domain, intent)

        # 상태 업데이트
        self.last_domain = domain
        self.last_intent = intent
        self.updated_at = datetime.now()

        return result

    def detect_topic_switch(
        self,
        current_domain: str,
        current_intent: str,
    ) -> TopicSwitchResult:
        """
        토픽 전환 감지 (C: 라우터 결과 기반)

        Args:
            current_domain: 현재 턴의 도메인
            current_intent: 현재 턴의 인텐트

        Returns:
            TopicSwitchResult: 전환 여부 및 액션
        """
        if self.last_domain is None:
            return TopicSwitchResult(switched=False, action=TopicSwitchAction.NONE)

        # 도메인 전환
        if self.last_domain != current_domain:
            return TopicSwitchResult(
                switched=True,
                action=TopicSwitchAction.RESET_BOOST,
                reason=f"domain_change:{self.last_domain}→{current_domain}",
            )

        # 같은 도메인 내 주요 인텐트 변화
        if self._is_major_intent_change(self.last_intent, current_intent):
            return TopicSwitchResult(
                switched=True,
                action=TopicSwitchAction.DECAY_BOOST,
                reason=f"intent_change:{self.last_intent}→{current_intent}",
            )

        return TopicSwitchResult(switched=False, action=TopicSwitchAction.NONE)

    def _is_major_intent_change(
        self,
        prev_intent: Optional[str],
        current_intent: str,
    ) -> bool:
        """주요 인텐트 변화 여부 판단"""
        if prev_intent is None:
            return False

        # 같은 인텐트면 변화 없음
        if prev_intent == current_intent:
            return False

        # 특정 인텐트 그룹 내 전환은 주요 변화 아님
        # 예: POLICY_QA → POLICY_SUMMARY는 같은 맥락
        minor_change_groups = [
            {"POLICY_QA", "POLICY_SUMMARY", "POLICY_DETAIL"},
            {"EDUCATION_QA", "EDUCATION_VIDEO", "EDUCATION_SUMMARY"},
        ]

        for group in minor_change_groups:
            if prev_intent in group and current_intent in group:
                return False

        return True

    # ==========================================================================
    # 턴 관리
    # ==========================================================================

    def increment_turn(self) -> None:
        """턴 카운터 증가"""
        self.turn_count += 1
        self.state_version += 1  # CAS: 버전도 함께 증가
        self.updated_at = datetime.now()

    def increment_version(self) -> int:
        """버전만 증가 (CAS용)"""
        self.state_version += 1
        self.updated_at = datetime.now()
        return self.state_version

    def get_turn_distance(self, doc: DocReference) -> int:
        """특정 문서가 참조된 이후 경과 턴 수"""
        return self.turn_count - doc.turn

    # ==========================================================================
    # 프롬프트 컨텍스트 생성
    # ==========================================================================

    def to_prompt_context(self) -> str:
        """
        LLM 프롬프트에 주입할 구조화 컨텍스트

        Returns:
            str: 프롬프트용 컨텍스트 문자열
        """
        lines = []

        last_doc = self.get_last_doc()
        if last_doc:
            lines.append(f"[이전 참조 문서] {last_doc.title}")
            if last_doc.citations:
                lines.append(f"[인용 조항] {', '.join(last_doc.citations[:3])}")

        if self.last_domain:
            lines.append(f"[현재 도메인] {self.last_domain}")

        if self.last_entities:
            lines.append(f"[핵심 키워드] {', '.join(self.last_entities[:5])}")

        return "\n".join(lines) if lines else ""

    def get_recent_doc_titles(self) -> List[str]:
        """최근 문서 제목 목록 (되묻기 옵션용)"""
        return [doc.title for doc in self.recent_docs]

    def get_recent_doc_ids(self) -> List[str]:
        """최근 문서 ID 목록 (부스팅용)"""
        return [doc.doc_id for doc in self.recent_docs]

    # ==========================================================================
    # 직렬화/역직렬화 (Redis 저장용)
    # ==========================================================================

    def to_dict(self) -> Dict[str, Any]:
        """Redis 저장용 딕셔너리 변환"""
        return {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "last_domain": self.last_domain,
            "last_intent": self.last_intent,
            "recent_docs": [doc.to_dict() for doc in self.recent_docs],
            "last_entities": self.last_entities,
            "turn_count": self.turn_count,
            "state_version": self.state_version,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def to_json(self) -> str:
        """JSON 문자열 변환"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationState":
        """딕셔너리에서 복원"""
        return cls(
            user_id=data.get("user_id", ""),
            session_id=data.get("session_id", ""),
            last_domain=data.get("last_domain"),
            last_intent=data.get("last_intent"),
            recent_docs=[
                DocReference.from_dict(d) for d in data.get("recent_docs", [])
            ],
            last_entities=data.get("last_entities", []),
            turn_count=data.get("turn_count", 0),
            state_version=data.get("state_version", 0),
            created_at=datetime.fromisoformat(data["created_at"])
            if "created_at" in data
            else datetime.now(),
            updated_at=datetime.fromisoformat(data["updated_at"])
            if "updated_at" in data
            else datetime.now(),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "ConversationState":
        """JSON 문자열에서 복원"""
        return cls.from_dict(json.loads(json_str))

    # ==========================================================================
    # 유틸리티
    # ==========================================================================

    def reset(self) -> None:
        """상태 초기화 (토픽 전환 시 등)"""
        self.last_domain = None
        self.last_intent = None
        self.recent_docs = []
        self.last_entities = []
        # turn_count는 유지 (세션 내 연속성)
        self.updated_at = datetime.now()

    def __repr__(self) -> str:
        return (
            f"ConversationState("
            f"user_id={self.user_id!r}, "
            f"session_id={self.session_id!r}, "
            f"last_domain={self.last_domain!r}, "
            f"turn={self.turn_count}, "
            f"recent_docs={len(self.recent_docs)})"
        )
