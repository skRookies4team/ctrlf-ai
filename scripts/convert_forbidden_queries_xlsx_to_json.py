# scripts/convert_forbidden_queries_xlsx_to_json.py
"""
금지질문리스트 엑셀(xlsx) → JSON 변환 스크립트

기능:
- A_, B_로 시작하는 시트를 자동 감지하여 프로필별 룰셋 생성
- 필수 컬럼 검증 및 rule_id 중복 체크
- forbidden_ruleset.A.json, forbidden_ruleset.B.json, forbidden_ruleset.all.json 생성
- SHA256 체크섬이 포함된 manifest.json 생성

사용법:
    # 검증만
    python scripts/convert_forbidden_queries_xlsx_to_json.py \
        --input "docs/금지질문리스트.xlsx" \
        --out-dir "app/resources/forbidden_queries" \
        --validate-only

    # 실제 생성
    python scripts/convert_forbidden_queries_xlsx_to_json.py \
        --input "docs/금지질문리스트.xlsx" \
        --out-dir "app/resources/forbidden_queries" \
        --version "v1.0.0"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List


REQUIRED_COLUMNS = [
    "ID",
    "질문",
    "판정",
    "사유",
    "서브사유",
    "권장 응답방식",
    "대체 응답(안내문구) 예시",
]


def sha256_file(path: str) -> str:
    """파일의 SHA256 해시를 계산합니다."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_text(s: str) -> str:
    """룰 매칭용 정규화: 소문자 + 공백 정리."""
    if s is None:
        return ""
    s = str(s).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def detect_profile_sheets(xlsx_path: str) -> Dict[str, str]:
    """
    xlsx에서 'A_' / 'B_'로 시작하는 첫 시트를 각각 프로필로 사용.
    예) A_엄격(...), B_실무(...)
    """
    import pandas as pd

    xls = pd.ExcelFile(xlsx_path)
    sheets = xls.sheet_names

    profile_map: Dict[str, str] = {}
    for sh in sheets:
        if sh.startswith("A_") and "A" not in profile_map:
            profile_map["A"] = sh
        if sh.startswith("B_") and "B" not in profile_map:
            profile_map["B"] = sh

    missing = [p for p in ("A", "B") if p not in profile_map]
    if missing:
        raise ValueError(f"프로필 시트를 찾지 못했습니다: missing={missing}, sheets={sheets}")

    return profile_map


def load_sheet_rules(xlsx_path: str, sheet_name: str, profile: str) -> List[Dict[str, Any]]:
    """시트에서 룰을 로드하고 검증합니다."""
    import pandas as pd

    df = pd.read_excel(xlsx_path, sheet_name=sheet_name)

    # 컬럼 검증
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ValueError(f"필수 컬럼 누락: sheet={sheet_name}, missing={missing_cols}")

    # 질문/판정 없는 행 제거
    df = df.dropna(subset=["질문", "판정"])

    rules: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        rule_id = str(row["ID"]).strip()
        question = str(row["질문"]).strip()

        rule = {
            "rule_id": rule_id,
            "profile": profile,  # "A" or "B"
            "sheet": sheet_name,
            "match": {
                "type": "exact_normalized",  # 런타임에서 question_norm으로 exact 매칭 권장
                "question": question,
                "question_norm": normalize_text(question),
            },
            "decision": str(row["판정"]).strip(),  # 예: FORBIDDEN_PII / RESTRICTED_SECURITY ...
            "reason": "" if pd.isna(row["사유"]) else str(row["사유"]).strip(),
            "sub_reason": "" if pd.isna(row["서브사유"]) else str(row["서브사유"]).strip(),
            "response_mode": "" if pd.isna(row["권장 응답방식"]) else str(row["권장 응답방식"]).strip(),
            "example_response": "" if pd.isna(row["대체 응답(안내문구) 예시"]) else str(row["대체 응답(안내문구) 예시"]).strip(),
        }
        rules.append(rule)

    # rule_id 중복 체크
    ids = [r["rule_id"] for r in rules]
    dup_ids = sorted({x for x in ids if ids.count(x) > 1})
    if dup_ids:
        raise ValueError(f"rule_id 중복 발견: sheet={sheet_name}, dup_ids={dup_ids[:10]}...")

    return rules


def write_json(path: str, obj: Any) -> None:
    """JSON 파일을 UTF-8로 저장합니다."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="금지질문리스트 엑셀(xlsx) → JSON 변환 스크립트"
    )
    parser.add_argument("--input", required=True, help="금지질문리스트 xlsx 경로")
    parser.add_argument("--out-dir", required=True, help="출력 디렉토리 (예: app/resources/forbidden_queries)")
    parser.add_argument("--version", default="", help="ruleset 버전 (미지정 시 날짜 기반 자동 생성)")
    parser.add_argument("--validate-only", action="store_true", help="검증만 하고 파일은 쓰지 않음")
    args = parser.parse_args()

    xlsx_path = args.input
    out_dir = args.out_dir

    if not os.path.exists(xlsx_path):
        raise FileNotFoundError(f"input xlsx not found: {xlsx_path}")

    profile_sheets = detect_profile_sheets(xlsx_path)

    now = datetime.now(timezone.utc).isoformat()
    version = args.version.strip() or f"v{datetime.now(timezone.utc).strftime('%Y.%m.%d')}"

    source_sha = sha256_file(xlsx_path)

    rules_by_profile: Dict[str, List[Dict[str, Any]]] = {}
    for profile, sheet in profile_sheets.items():
        rules_by_profile[profile] = load_sheet_rules(xlsx_path, sheet, profile)

    # 통합 ruleset
    ruleset_all = {
        "schema_version": "1.0",
        "version": version,
        "generated_at": now,
        "source": {
            "file": os.path.basename(xlsx_path),
            "sha256": source_sha,
            "profiles": profile_sheets,  # {"A": "A_...", "B": "B_..."}
        },
        "profiles": {
            "A": {
                "mode": "strict",
                "rules_count": len(rules_by_profile["A"]),
                "rules": rules_by_profile["A"],
            },
            "B": {
                "mode": "practical",
                "rules_count": len(rules_by_profile["B"]),
                "rules": rules_by_profile["B"],
            },
        },
    }

    # 프로필 단일 파일도 같이 생성 (런타임에서 더 단순하게 로드 가능)
    ruleset_A = {
        "schema_version": "1.0",
        "version": version,
        "generated_at": now,
        "source": {"file": os.path.basename(xlsx_path), "sha256": source_sha, "profile_sheet": profile_sheets["A"]},
        "profile": "A",
        "mode": "strict",
        "rules_count": len(rules_by_profile["A"]),
        "rules": rules_by_profile["A"],
    }
    ruleset_B = {
        "schema_version": "1.0",
        "version": version,
        "generated_at": now,
        "source": {"file": os.path.basename(xlsx_path), "sha256": source_sha, "profile_sheet": profile_sheets["B"]},
        "profile": "B",
        "mode": "practical",
        "rules_count": len(rules_by_profile["B"]),
        "rules": rules_by_profile["B"],
    }

    # 출력 경로
    out_all = os.path.join(out_dir, "forbidden_ruleset.all.json")
    out_A = os.path.join(out_dir, "forbidden_ruleset.A.json")
    out_B = os.path.join(out_dir, "forbidden_ruleset.B.json")

    # validate-only면 요약만
    if args.validate_only:
        print("OK validate-only")
        print(f"- input: {xlsx_path}")
        print(f"- source_sha256: {source_sha}")
        print(f"- A sheet: {profile_sheets['A']} rules={len(rules_by_profile['A'])}")
        print(f"- B sheet: {profile_sheets['B']} rules={len(rules_by_profile['B'])}")
        return

    write_json(out_all, ruleset_all)
    write_json(out_A, ruleset_A)
    write_json(out_B, ruleset_B)

    # manifest (결과물 체크섬)
    manifest = {
        "schema_version": "1.0",
        "version": version,
        "generated_at": now,
        "source_xlsx": {"path": xlsx_path, "sha256": source_sha},
        "outputs": {
            "forbidden_ruleset.all.json": {"path": out_all, "sha256": sha256_file(out_all)},
            "forbidden_ruleset.A.json": {"path": out_A, "sha256": sha256_file(out_A)},
            "forbidden_ruleset.B.json": {"path": out_B, "sha256": sha256_file(out_B)},
        },
    }
    out_manifest = os.path.join(out_dir, "forbidden_ruleset.manifest.json")
    write_json(out_manifest, manifest)

    print("DONE")
    print(f"- out: {out_dir}")
    print(f"- version: {version}")
    print(f"- A rules: {ruleset_A['rules_count']}")
    print(f"- B rules: {ruleset_B['rules_count']}")


if __name__ == "__main__":
    main()
