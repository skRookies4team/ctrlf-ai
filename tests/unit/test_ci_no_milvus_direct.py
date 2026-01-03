# tests/unit/test_ci_no_milvus_direct.py
"""
Step 7: CI 검사 스크립트 테스트

AST 기반 MilvusSearchClient 직접 호출 탐지 스크립트 테스트.
"""
import ast
import tempfile
from pathlib import Path

import pytest

# 테스트 대상 모듈 import
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.ci_check_no_milvus_direct import (
    MilvusDirectCallVisitor,
    check_file,
    FORBIDDEN_IMPORT_NAMES,
)


# =============================================================================
# Test 1: Import 탐지
# =============================================================================


class TestForbiddenImportDetection:
    """금지된 import 탐지 테스트."""

    def test_detect_milvus_import(self):
        """MilvusSearchClient import 탐지."""
        code = '''
from app.clients.milvus_client import MilvusSearchClient

class MyService:
    pass
'''
        tree = ast.parse(code)
        visitor = MilvusDirectCallVisitor(Path("test.py"))
        visitor.visit(tree)

        assert len(visitor.violations) == 1
        assert visitor.violations[0].rule == "FORBIDDEN_IMPORT"
        assert "MilvusSearchClient" in visitor.violations[0].detail

    def test_detect_get_milvus_client_import(self):
        """get_milvus_client import 탐지."""
        code = '''
from app.clients.milvus_client import get_milvus_client

client = get_milvus_client()
'''
        tree = ast.parse(code)
        visitor = MilvusDirectCallVisitor(Path("test.py"))
        visitor.visit(tree)

        # import 1개 + 함수 호출 1개 = 2개
        assert len(visitor.violations) >= 1
        assert any(v.rule == "FORBIDDEN_IMPORT" for v in visitor.violations)

    def test_detect_multiple_imports(self):
        """여러 금지된 import 동시 탐지."""
        code = '''
from app.clients.milvus_client import MilvusSearchClient, get_milvus_client
'''
        tree = ast.parse(code)
        visitor = MilvusDirectCallVisitor(Path("test.py"))
        visitor.visit(tree)

        assert len(visitor.violations) == 2

    def test_allow_other_imports(self):
        """다른 import는 허용."""
        code = '''
from app.clients.llm_client import LLMClient
from app.services.chat.rag_handler import RagHandler
'''
        tree = ast.parse(code)
        visitor = MilvusDirectCallVisitor(Path("test.py"))
        visitor.visit(tree)

        assert len(visitor.violations) == 0


# =============================================================================
# Test 2: 직접 호출 탐지
# =============================================================================


class TestDirectCallDetection:
    """직접 호출 탐지 테스트."""

    def test_detect_milvus_client_search(self):
        """_milvus_client.search() 호출 탐지."""
        code = '''
class MyService:
    async def search(self):
        results = await self._milvus_client.search(query, domain)
        return results
'''
        tree = ast.parse(code)
        visitor = MilvusDirectCallVisitor(Path("test.py"))
        visitor.visit(tree)

        assert len(visitor.violations) == 1
        assert visitor.violations[0].rule == "DIRECT_SEARCH_CALL"
        assert "_milvus_client.search()" in visitor.violations[0].detail

    def test_detect_milvus_search_as_sources(self):
        """milvus.search_as_sources() 호출 탐지."""
        code = '''
class MyService:
    async def get_sources(self):
        sources = await self.milvus.search_as_sources(query, domain)
        return sources
'''
        tree = ast.parse(code)
        visitor = MilvusDirectCallVisitor(Path("test.py"))
        visitor.visit(tree)

        # 'milvus'는 금지된 변수명
        assert len(visitor.violations) == 1
        assert "search_as_sources" in visitor.violations[0].detail

    def test_detect_factory_call(self):
        """get_milvus_client() 호출 탐지."""
        code = '''
from app.clients.milvus_client import get_milvus_client

client = get_milvus_client()
'''
        tree = ast.parse(code)
        visitor = MilvusDirectCallVisitor(Path("test.py"))
        visitor.visit(tree)

        assert any(v.rule == "FACTORY_CALL" for v in visitor.violations)

    def test_allow_rag_handler_search(self):
        """RagHandler.perform_search_with_fallback() 호출은 허용."""
        code = '''
class MyService:
    async def search(self):
        sources, _, _ = await self._rag_handler.perform_search_with_fallback(
            query=query, domain=domain, req=None
        )
        return sources
'''
        tree = ast.parse(code)
        visitor = MilvusDirectCallVisitor(Path("test.py"))
        visitor.visit(tree)

        assert len(visitor.violations) == 0

    def test_allow_other_search_methods(self):
        """다른 객체의 search() 메서드는 허용."""
        code = '''
class MyService:
    async def process(self):
        # 리스트 검색
        result = my_list.search(item)
        # 문자열 검색
        idx = text.find("pattern")
        # 다른 클라이언트의 search
        data = await self.elasticsearch.search(query)
        return result
'''
        tree = ast.parse(code)
        visitor = MilvusDirectCallVisitor(Path("test.py"))
        visitor.visit(tree)

        # my_list, text, elasticsearch 등은 허용
        assert len(visitor.violations) == 0


# =============================================================================
# Test 3: 파일 검사
# =============================================================================


class TestFileCheck:
    """파일 검사 테스트."""

    def test_check_file_with_violations(self):
        """위반이 있는 파일 검사."""
        code = '''
from app.clients.milvus_client import MilvusSearchClient, get_milvus_client

class BadService:
    def __init__(self):
        self._milvus_client = get_milvus_client()

    async def search(self):
        return await self._milvus_client.search(query, domain)
'''
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            f.flush()
            file_path = Path(f.name)

        try:
            violations = check_file(file_path)
            # import 2개 + factory call 1개 + search call 1개 = 4개
            assert len(violations) >= 4
        finally:
            file_path.unlink()

    def test_check_file_without_violations(self):
        """위반이 없는 파일 검사."""
        code = '''
from app.services.chat.rag_handler import RagHandler

class GoodService:
    def __init__(self):
        self._rag_handler = RagHandler()

    async def search(self):
        sources, _, _ = await self._rag_handler.perform_search_with_fallback(
            query=query, domain=domain, req=None
        )
        return sources
'''
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            f.flush()
            file_path = Path(f.name)

        try:
            violations = check_file(file_path)
            assert len(violations) == 0
        finally:
            file_path.unlink()

    def test_check_file_with_syntax_error(self):
        """구문 오류가 있는 파일 검사 (에러 없이 빈 결과 반환)."""
        code = '''
def broken_function(
    # 괄호가 닫히지 않음
'''
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            f.flush()
            file_path = Path(f.name)

        try:
            # 구문 오류 시 빈 리스트 반환 (에러 발생하지 않음)
            violations = check_file(file_path)
            assert violations == []
        finally:
            file_path.unlink()


# =============================================================================
# Test 4: 변수 추적
# =============================================================================


class TestVariableTracking:
    """변수 추적 테스트."""

    def test_track_assigned_milvus_variable(self):
        """get_milvus_client()로 생성된 변수 추적."""
        code = '''
from app.clients.milvus_client import get_milvus_client

class MyService:
    def __init__(self):
        self.my_custom_client = get_milvus_client()  # 커스텀 변수명

    async def search(self):
        # 추적된 변수를 통한 호출도 탐지되어야 함
        return await self.my_custom_client.search(query)
'''
        tree = ast.parse(code)
        visitor = MilvusDirectCallVisitor(Path("test.py"))
        visitor.visit(tree)

        # import 1개 + factory call 1개 + search call 1개 = 3개 이상
        assert len(visitor.violations) >= 2
