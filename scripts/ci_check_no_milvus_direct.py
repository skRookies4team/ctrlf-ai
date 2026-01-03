#!/usr/bin/env python3
"""
Step 7: MilvusSearchClient 직접 호출 검사 스크립트 (AST 기반)

서비스 레이어(app/services/)에서 MilvusSearchClient를 직접 import/생성/호출하는
코드를 AST 정적 분석으로 탐지합니다.

허용된 호출 위치:
- app/services/chat/rag_handler.py (유일한 Milvus 접점)
- app/clients/milvus_client.py (클라이언트 정의)
- tests/** (테스트 코드)

금지된 패턴:
1. from app.clients.milvus_client import MilvusSearchClient
2. from app.clients.milvus_client import get_milvus_client
3. MilvusSearchClient(...) 직접 생성
4. *.search(...) / *.search_as_sources(...) 호출 (Milvus 관련 변수명)

Usage:
    python scripts/ci_check_no_milvus_direct.py

Exit codes:
    0: 위반 없음
    1: 위반 발견
"""

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set

# =============================================================================
# 설정
# =============================================================================

# 검사 대상 디렉토리
TARGET_DIR = Path("app/services")

# 허용된 파일 (Milvus 직접 사용 허용)
ALLOWED_FILES: Set[str] = {
    # 유일한 Milvus 접점 (다른 서비스는 이를 통해 검색)
    "app/services/chat/rag_handler.py",

    # TODO(Step7): 아래 파일들은 임시 예외. RagHandler 리팩토링 필요
    # - 비디오 생성 서비스: 금지질문 필터 적용 검토 필요
    "app/services/scene_based_script_generator.py",
    "app/services/source_set_orchestrator.py",
}

# 금지된 import 소스
FORBIDDEN_IMPORT_MODULE = "app.clients.milvus_client"

# 금지된 import 이름
FORBIDDEN_IMPORT_NAMES = {"MilvusSearchClient", "get_milvus_client"}

# Milvus 관련 변수명 패턴 (이 이름을 가진 변수에서 .search() 호출 시 위반)
MILVUS_VARIABLE_NAMES = {
    "milvus",
    "_milvus",
    "milvus_client",
    "_milvus_client",
}


# =============================================================================
# 데이터 클래스
# =============================================================================


@dataclass
class Violation:
    """위반 정보."""

    file_path: Path
    line: int
    col: int
    rule: str
    detail: str


# =============================================================================
# AST Visitor
# =============================================================================


class MilvusDirectCallVisitor(ast.NodeVisitor):
    """Milvus 직접 호출을 탐지하는 AST Visitor."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.violations: List[Violation] = []

        # 추적: Milvus 관련 변수 (import 또는 함수 호출로 생성된)
        self._milvus_vars: Set[str] = set()

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """from X import Y 문을 검사합니다."""
        if node.module == FORBIDDEN_IMPORT_MODULE:
            for alias in node.names:
                name = alias.name
                if name in FORBIDDEN_IMPORT_NAMES:
                    self.violations.append(
                        Violation(
                            file_path=self.file_path,
                            line=node.lineno,
                            col=node.col_offset,
                            rule="FORBIDDEN_IMPORT",
                            detail=f"from {node.module} import {name}",
                        )
                    )

                    # 변수 추적: import된 이름 또는 alias
                    var_name = alias.asname or name
                    if name == "MilvusSearchClient":
                        self._milvus_vars.add(var_name)
                    elif name == "get_milvus_client":
                        self._milvus_vars.add(var_name)

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """함수 호출을 검사합니다."""
        # Case 1: MilvusSearchClient() 직접 생성
        if isinstance(node.func, ast.Name):
            if node.func.id == "MilvusSearchClient":
                self.violations.append(
                    Violation(
                        file_path=self.file_path,
                        line=node.lineno,
                        col=node.col_offset,
                        rule="DIRECT_INSTANTIATION",
                        detail="MilvusSearchClient(...)",
                    )
                )

            # get_milvus_client() 호출
            elif node.func.id == "get_milvus_client":
                self.violations.append(
                    Violation(
                        file_path=self.file_path,
                        line=node.lineno,
                        col=node.col_offset,
                        rule="FACTORY_CALL",
                        detail="get_milvus_client()",
                    )
                )

        # Case 2: *.search() 또는 *.search_as_sources() 호출
        elif isinstance(node.func, ast.Attribute):
            method_name = node.func.attr

            if method_name in ("search", "search_as_sources"):
                # 호출 대상 객체 이름 추출
                receiver_name = self._get_receiver_name(node.func.value)

                if receiver_name:
                    # Milvus 관련 변수명이면 위반
                    if receiver_name in MILVUS_VARIABLE_NAMES:
                        self.violations.append(
                            Violation(
                                file_path=self.file_path,
                                line=node.lineno,
                                col=node.col_offset,
                                rule="DIRECT_SEARCH_CALL",
                                detail=f"{receiver_name}.{method_name}()",
                            )
                        )

                    # 추적된 Milvus 변수면 위반
                    elif receiver_name in self._milvus_vars:
                        self.violations.append(
                            Violation(
                                file_path=self.file_path,
                                line=node.lineno,
                                col=node.col_offset,
                                rule="DIRECT_SEARCH_CALL",
                                detail=f"{receiver_name}.{method_name}() (tracked variable)",
                            )
                        )

        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        """할당문을 검사하여 Milvus 변수를 추적합니다."""
        # self._milvus_client = get_milvus_client() 패턴 추적
        if isinstance(node.value, ast.Call):
            func = node.value.func

            # get_milvus_client() 호출 결과 할당
            if isinstance(func, ast.Name) and func.id == "get_milvus_client":
                for target in node.targets:
                    var_name = self._get_var_name(target)
                    if var_name:
                        self._milvus_vars.add(var_name)

            # MilvusSearchClient() 생성 결과 할당
            elif isinstance(func, ast.Name) and func.id == "MilvusSearchClient":
                for target in node.targets:
                    var_name = self._get_var_name(target)
                    if var_name:
                        self._milvus_vars.add(var_name)

        self.generic_visit(node)

    def _get_receiver_name(self, node: ast.expr) -> Optional[str]:
        """호출 대상 객체의 이름을 추출합니다."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            # self._milvus_client 형태
            if isinstance(node.value, ast.Name) and node.value.id == "self":
                return node.attr
        return None

    def _get_var_name(self, node: ast.expr) -> Optional[str]:
        """할당 대상의 변수 이름을 추출합니다."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            # self._milvus_client 형태
            if isinstance(node.value, ast.Name) and node.value.id == "self":
                return node.attr
        return None


# =============================================================================
# 검사 로직
# =============================================================================


def check_file(file_path: Path) -> List[Violation]:
    """파일을 검사하여 위반 목록을 반환합니다."""
    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(file_path))
    except SyntaxError as e:
        print(f"  Warning: Syntax error in {file_path}: {e}")
        return []
    except Exception as e:
        print(f"  Warning: Cannot read {file_path}: {e}")
        return []

    visitor = MilvusDirectCallVisitor(file_path)
    visitor.visit(tree)

    return visitor.violations


def main() -> int:
    """메인 검사 로직."""
    print("=" * 70)
    print("Step 7: MilvusSearchClient 직접 호출 검사 (AST 기반)")
    print("=" * 70)
    print()

    # 검사 대상 디렉토리 확인
    if not TARGET_DIR.exists():
        print(f"Error: Target directory not found: {TARGET_DIR}")
        return 1

    all_violations: List[Violation] = []
    checked_files = 0

    # Python 파일 검사
    for py_file in TARGET_DIR.rglob("*.py"):
        # 상대 경로로 변환 (Windows/Unix 호환)
        rel_path = str(py_file).replace("\\", "/")

        # 허용된 파일 스킵
        if rel_path in ALLOWED_FILES:
            print(f"  [SKIP] {rel_path} (allowed)")
            continue

        # 검사 수행
        violations = check_file(py_file)
        if violations:
            all_violations.extend(violations)
            print(f"  [FAIL] {rel_path}: {len(violations)} violation(s)")
        else:
            print(f"  [OK]   {rel_path}")

        checked_files += 1

    print()
    print("-" * 70)

    # 결과 출력
    if all_violations:
        print(f"\n{len(all_violations)} VIOLATION(S) FOUND!\n")

        for v in all_violations:
            rel_path = str(v.file_path).replace("\\", "/")
            print(f"  {rel_path}:{v.line}:{v.col}")
            print(f"    Rule: {v.rule}")
            print(f"    Detail: {v.detail}")
            print()

        print("=" * 70)
        print("HOW TO FIX:")
        print("  1. Use RagHandler instead of MilvusSearchClient")
        print("  2. Import RagHandler from app.services.chat.rag_handler")
        print("  3. Call rag_handler.perform_search_with_fallback(...)")
        print()
        print("Example:")
        print("  # Before (FORBIDDEN)")
        print("  from app.clients.milvus_client import get_milvus_client")
        print("  results = await self._milvus.search(query, domain)")
        print()
        print("  # After (OK)")
        print("  from app.services.chat.rag_handler import RagHandler")
        print("  sources, _, _ = await self._rag_handler.perform_search_with_fallback(")
        print("      query=query, domain=domain, req=None, top_k=5")
        print("  )")
        print("=" * 70)

        return 1

    else:
        print(f"\nSUCCESS: No violations found ({checked_files} files checked)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
