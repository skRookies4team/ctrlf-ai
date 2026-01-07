"""
RAG Quality Regression Test

Phase 57: Golden Query 회귀 테스트
- 고정된 쿼리 세트로 RAG 품질 회귀 방지
- Sources=0 금지 가드레일 적용
- 품질 지표 저장 및 비교

실행:
    # 단위 테스트 (mock)
    pytest tests/regression/test_rag_quality_regression.py -v

    # 실서버 통합 테스트
    pytest tests/regression/test_rag_quality_regression.py -v -m real_regression --real-server

    # 결과 저장
    python tests/regression/test_rag_quality_regression.py --save-baseline
"""

import json
import os
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import MagicMock, AsyncMock

import pytest

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# =============================================================================
# 데이터 모델
# =============================================================================

@dataclass
class QueryResult:
    """단일 쿼리 테스트 결과"""
    query_id: str
    query: str
    domain: str
    category: str

    # Phase 57 지표
    rewrite_used: bool = False
    expanded_query: Optional[str] = None
    rrf_used: bool = False

    # 검색 결과 지표
    sources_count: int = 0
    top_k_doc_ids: List[str] = field(default_factory=list)
    top_k_chunk_ids: List[str] = field(default_factory=list)

    # 품질 지표
    min_distance: Optional[float] = None
    avg_distance: Optional[float] = None
    max_distance: Optional[float] = None

    # 메타
    passed: bool = False
    failure_reason: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class RegressionReport:
    """회귀 테스트 전체 보고서"""
    phase: int = 57
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    total_queries: int = 0
    passed_queries: int = 0
    failed_queries: int = 0
    critical_failures: int = 0
    results: List[QueryResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return self.passed_queries / self.total_queries * 100

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "timestamp": self.timestamp,
            "total_queries": self.total_queries,
            "passed_queries": self.passed_queries,
            "failed_queries": self.failed_queries,
            "critical_failures": self.critical_failures,
            "pass_rate": f"{self.pass_rate:.1f}%",
            "results": [asdict(r) for r in self.results]
        }


# =============================================================================
# Golden Query 로더
# =============================================================================

GOLDEN_QUERIES_PATH = Path(__file__).parent / "golden_queries.json"


def load_golden_queries() -> dict:
    """golden_queries.json 로드"""
    with open(GOLDEN_QUERIES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_queries_by_category(category: str) -> List[dict]:
    """카테고리별 쿼리 필터링"""
    data = load_golden_queries()
    return [q for q in data["queries"] if q.get("category") == category]


def get_critical_queries() -> List[dict]:
    """critical=True인 쿼리만 반환"""
    data = load_golden_queries()
    return [q for q in data["queries"] if q.get("critical", False)]


# =============================================================================
# Mock 테스트 (CI용)
# =============================================================================

class TestGoldenQueriesStructure:
    """Golden Query JSON 구조 검증"""

    def test_golden_queries_file_exists(self):
        """golden_queries.json 파일 존재 확인"""
        assert GOLDEN_QUERIES_PATH.exists(), "golden_queries.json not found"

    def test_golden_queries_valid_json(self):
        """JSON 파싱 가능 확인"""
        data = load_golden_queries()
        assert "queries" in data
        assert "guardrails" in data

    def test_golden_queries_minimum_count(self):
        """최소 쿼리 수 확인 (30개 이상)"""
        data = load_golden_queries()
        assert len(data["queries"]) >= 30, f"Need at least 30 queries, got {len(data['queries'])}"

    def test_golden_queries_required_fields(self):
        """필수 필드 존재 확인"""
        data = load_golden_queries()
        required_fields = ["id", "domain", "category", "query", "min_sources"]

        for query in data["queries"]:
            for field in required_fields:
                assert field in query, f"Missing field '{field}' in query {query.get('id')}"

    def test_critical_queries_exist(self):
        """critical 쿼리가 최소 5개 이상 존재"""
        critical = get_critical_queries()
        assert len(critical) >= 5, f"Need at least 5 critical queries, got {len(critical)}"

    def test_domain_coverage(self):
        """주요 도메인이 모두 커버되는지 확인"""
        data = load_golden_queries()
        categories = set(q["category"] for q in data["queries"])

        required_categories = {"HR", "SECURITY", "EDU", "HARASSMENT"}
        missing = required_categories - categories
        assert not missing, f"Missing categories: {missing}"


class TestQueryExpansionUnit:
    """Query Expansion 단위 테스트"""

    def test_expansion_for_short_queries(self):
        """짧은 쿼리에 대한 확장 동작 확인"""
        from app.services.chat.query_rewriter import expand_query_sync

        short_queries = ["연차", "휴가", "급여", "보안", "비밀번호"]

        for query in short_queries:
            result = expand_query_sync(query, "POLICY")
            assert result.used, f"Expected expansion for '{query}'"
            assert len(result.rewritten) > len(query), f"Expanded query should be longer for '{query}'"

    def test_no_expansion_for_long_queries(self):
        """긴 쿼리는 확장하지 않음"""
        from app.services.chat.query_rewriter import expand_query_sync

        long_query = "이 문서는 굉장히 긴 질문이라서 확장이 필요하지 않습니다 정말로요"
        result = expand_query_sync(long_query, "POLICY")
        assert not result.used, "Long query should not be expanded"

    def test_expansion_preserves_original(self):
        """확장 시 원본 쿼리 포함"""
        from app.services.chat.query_rewriter import expand_query_sync

        query = "연차"
        result = expand_query_sync(query, "POLICY")

        if result.used:
            assert query in result.rewritten, "Expanded query should contain original"


class TestRRFFusionUnit:
    """RRF Fusion 단위 테스트"""

    def test_rrf_fuse_basic(self):
        """기본 RRF 융합 테스트"""
        from app.services.search_merger import rrf_fuse_with_sources
        from dataclasses import dataclass as dc
        from typing import Optional as Opt

        @dc
        class MockSource:
            doc_id: str
            title: str
            snippet: str
            score: float
            page: Opt[int] = None
            article_label: Opt[str] = None
            article_path: Opt[str] = None
            source_type: Opt[str] = None

        original = [
            MockSource("doc1", "Title 1", "Snippet 1", 0.9),
            MockSource("doc2", "Title 2", "Snippet 2", 0.8),
        ]
        expanded = [
            MockSource("doc2", "Title 2", "Snippet 2", 0.85),
            MockSource("doc3", "Title 3", "Snippet 3", 0.75),
        ]

        result = rrf_fuse_with_sources(original, expanded, k=60, top_n=5)

        assert result.fusion_applied, "Fusion should be applied"
        result_ids = [r.doc_id for r in result.results]
        assert "doc2" in result_ids, "Common doc should be in results"

    def test_rrf_no_fusion_when_empty(self):
        """빈 결과 시 융합 안 함"""
        from app.services.search_merger import rrf_fuse_with_sources

        result = rrf_fuse_with_sources([], [], k=60, top_n=5)
        assert not result.fusion_applied


# =============================================================================
# 통합 테스트 (실서버용)
# =============================================================================

@pytest.fixture
def mock_milvus_client():
    """Mock Milvus 클라이언트"""
    from dataclasses import dataclass as dc
    from typing import Optional as Opt

    @dc
    class MockSource:
        doc_id: str
        title: str
        snippet: str
        score: float
        page: Opt[int] = None
        article_label: Opt[str] = None
        article_path: Opt[str] = None
        source_type: Opt[str] = None

    client = MagicMock()
    client.search_as_sources = AsyncMock(return_value=[
        MockSource("mock_doc_1", "Mock Title", "Mock snippet content", 0.85),
        MockSource("mock_doc_2", "Mock Title 2", "More mock content", 0.75),
    ])
    return client


class TestRagQualityGuardrails:
    """RAG 품질 가드레일 테스트"""

    def test_sources_zero_forbidden_categories(self):
        """Sources=0 금지 카테고리 검증"""
        data = load_golden_queries()
        forbidden = data["guardrails"]["sources_zero_forbidden_categories"]

        assert "HR" in forbidden, "HR should be in forbidden categories"
        assert "SECURITY" in forbidden, "SECURITY should be in forbidden categories"
        assert "HARASSMENT" in forbidden, "HARASSMENT should be in forbidden categories"

    @pytest.mark.parametrize("query_data", get_critical_queries()[:5])
    def test_critical_queries_have_guardrails(self, query_data):
        """Critical 쿼리에 대한 가드레일 존재 확인"""
        assert query_data.get("min_sources", 0) >= 1, \
            f"Critical query {query_data['id']} must have min_sources >= 1"


# =============================================================================
# 실서버 회귀 테스트 (pytest -m real_regression)
# =============================================================================

@pytest.mark.real_regression
@pytest.mark.skipif(
    os.environ.get("RUN_REAL_REGRESSION") != "true",
    reason="Real server tests require RUN_REAL_REGRESSION=true"
)
class TestRealServerRegression:
    """
    실서버 회귀 테스트

    실행:
        RUN_REAL_REGRESSION=true pytest tests/regression/test_rag_quality_regression.py -v -m real_regression

    또는 서버 URL 지정:
        AI_GATEWAY_URL=http://localhost:8000 RUN_REAL_REGRESSION=true pytest ...
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        """테스트 환경 설정"""
        self.report = RegressionReport()
        self.golden_data = load_golden_queries()
        yield
        # 테스트 후 리포트 저장
        self._save_report()

    def _save_report(self):
        """테스트 결과 저장"""
        report_dir = Path(__file__).parent / "reports"
        report_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = report_dir / f"regression_report_{timestamp}.json"

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(self.report.to_dict(), f, ensure_ascii=False, indent=2)

        print(f"\nReport saved: {report_path}")

    @pytest.mark.asyncio
    async def test_all_golden_queries(self):
        """모든 Golden Query 테스트"""
        import httpx

        base_url = os.environ.get("AI_GATEWAY_URL", "http://localhost:8000")

        async with httpx.AsyncClient(timeout=30.0) as client:
            for query_data in self.golden_data["queries"]:
                result = await self._test_single_query(client, base_url, query_data)
                self.report.results.append(result)
                self.report.total_queries += 1

                if result.passed:
                    self.report.passed_queries += 1
                else:
                    self.report.failed_queries += 1
                    if query_data.get("critical", False):
                        self.report.critical_failures += 1

        # 가드레일 검증
        assert self.report.critical_failures == 0, \
            f"Critical query failures: {self.report.critical_failures}"

        # 전체 통과율 80% 이상
        assert self.report.pass_rate >= 80.0, \
            f"Pass rate too low: {self.report.pass_rate:.1f}%"

    async def _test_single_query(
        self,
        client,
        base_url: str,
        query_data: dict
    ) -> QueryResult:
        """단일 쿼리 테스트"""
        result = QueryResult(
            query_id=query_data["id"],
            query=query_data["query"],
            domain=query_data["domain"],
            category=query_data["category"],
        )

        try:
            # API 호출
            response = await client.post(
                f"{base_url}/v1/chat",
                json={
                    "user_id": "regression_test",
                    "session_id": f"regression_{query_data['id']}",
                    "query": query_data["query"],
                    "domain": query_data["domain"],
                },
                headers={"Content-Type": "application/json"}
            )

            if response.status_code != 200:
                result.failure_reason = f"HTTP {response.status_code}"
                return result

            data = response.json()

            # 결과 파싱
            sources = data.get("sources", [])
            result.sources_count = len(sources)
            result.top_k_doc_ids = [s.get("doc_id", "") for s in sources[:5]]

            # 거리 지표 (있는 경우)
            distances = [s.get("score", 0) for s in sources if s.get("score")]
            if distances:
                result.min_distance = min(distances)
                result.avg_distance = sum(distances) / len(distances)
                result.max_distance = max(distances)

            # 가드레일 검증
            min_sources = query_data.get("min_sources", 1)
            if result.sources_count < min_sources:
                result.failure_reason = f"sources={result.sources_count} < min={min_sources}"
                return result

            result.passed = True

        except Exception as e:
            result.failure_reason = str(e)

        return result


# =============================================================================
# CLI 실행
# =============================================================================

def main():
    """CLI 실행"""
    import argparse

    parser = argparse.ArgumentParser(description="RAG Quality Regression Test")
    parser.add_argument("--save-baseline", action="store_true", help="Save current results as baseline")
    parser.add_argument("--compare", type=str, help="Compare with baseline file")
    args = parser.parse_args()

    if args.save_baseline:
        print("Saving baseline...")
        # 베이스라인 저장 로직
    elif args.compare:
        print(f"Comparing with {args.compare}...")
        # 비교 로직
    else:
        # pytest 실행
        pytest.main([__file__, "-v"])


if __name__ == "__main__":
    main()
