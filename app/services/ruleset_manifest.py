# app/services/ruleset_manifest.py
"""
Step 6: Ruleset Manifest 검증 로더

기능:
- manifest.json에서 룰셋/임베딩 파일 체크섬 검증
- 파일 불일치 시 로딩 실패 또는 해당 기능 OFF
- 원자적 일관성 보장 (ruleset + embedding 버전 동기화)

manifest.json 형식:
{
    "version": "v2024.01.01",
    "profile": "A",
    "files": {
        "ruleset": {
            "path": "forbidden_ruleset.A.json",
            "sha256": "abc123..."
        },
        "embeddings": {
            "path": "forbidden_embeddings.A.npy",
            "sha256": "def456..."
        }
    }
}

사용법:
    from app.services.ruleset_manifest import RulesetManifest, load_manifest

    manifest = load_manifest(resources_dir, profile="A")
    if manifest.validate():
        # 로드 진행
    else:
        # 로딩 실패 처리
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# 데이터 클래스
# =============================================================================


@dataclass
class FileChecksum:
    """파일 체크섬 정보."""

    path: str
    sha256: str
    exists: bool = False
    actual_sha256: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        """체크섬 일치 여부."""
        return self.exists and self.sha256 == self.actual_sha256


@dataclass
class RulesetManifest:
    """룰셋 매니페스트."""

    version: str
    profile: str
    files: Dict[str, FileChecksum] = field(default_factory=dict)
    base_dir: Optional[Path] = None

    # 검증 결과
    is_valid: bool = False
    validation_errors: List[str] = field(default_factory=list)

    def validate(self) -> bool:
        """모든 파일 체크섬을 검증합니다.

        Returns:
            True if all files are valid
        """
        self.validation_errors = []
        all_valid = True

        for file_type, file_info in self.files.items():
            if not file_info.exists:
                self.validation_errors.append(
                    f"{file_type}: file not found ({file_info.path})"
                )
                all_valid = False
            elif not file_info.is_valid:
                self.validation_errors.append(
                    f"{file_type}: checksum mismatch "
                    f"(expected={file_info.sha256[:16]}..., "
                    f"actual={file_info.actual_sha256[:16] if file_info.actual_sha256 else 'N/A'}...)"
                )
                all_valid = False

        self.is_valid = all_valid

        if all_valid:
            logger.info(
                f"Manifest validation passed: version={self.version}, "
                f"profile={self.profile}, files={list(self.files.keys())}"
            )
        else:
            logger.warning(
                f"Manifest validation failed: version={self.version}, "
                f"errors={self.validation_errors}"
            )

        return all_valid

    def validate_file(self, file_type: str) -> bool:
        """특정 파일만 검증합니다.

        Args:
            file_type: 파일 타입 (ruleset, embeddings 등)

        Returns:
            True if file is valid
        """
        if file_type not in self.files:
            return True  # 매니페스트에 없으면 통과

        file_info = self.files[file_type]
        return file_info.is_valid

    def get_file_path(self, file_type: str) -> Optional[Path]:
        """파일 경로를 반환합니다.

        Args:
            file_type: 파일 타입

        Returns:
            절대 경로 또는 None
        """
        if file_type not in self.files:
            return None

        if self.base_dir is None:
            return None

        return self.base_dir / self.files[file_type].path


# =============================================================================
# 유틸리티 함수
# =============================================================================


def compute_sha256(file_path: Path) -> str:
    """파일의 SHA256 해시를 계산합니다."""
    sha256_hash = hashlib.sha256()

    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)

    return sha256_hash.hexdigest()


def load_manifest(
    resources_dir: Path,
    profile: str = "A",
    manifest_filename: str = "manifest.json",
    auto_validate: bool = True,
) -> Optional[RulesetManifest]:
    """매니페스트를 로드하고 검증합니다.

    Args:
        resources_dir: 리소스 디렉토리 경로
        profile: 프로필 (A 또는 B)
        manifest_filename: 매니페스트 파일명
        auto_validate: 자동 검증 수행 여부

    Returns:
        RulesetManifest 또는 None (파일 없을 시)
    """
    manifest_path = resources_dir / manifest_filename

    # 프로필별 매니페스트 먼저 확인
    profile_manifest_path = resources_dir / f"manifest.{profile}.json"
    if profile_manifest_path.exists():
        manifest_path = profile_manifest_path

    if not manifest_path.exists():
        logger.info(
            f"Manifest not found: path={manifest_path}, "
            f"skipping checksum validation"
        )
        return None

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 파일 정보 파싱
        files: Dict[str, FileChecksum] = {}
        for file_type, file_data in data.get("files", {}).items():
            file_path = file_data.get("path", "")
            expected_sha256 = file_data.get("sha256", "")

            full_path = resources_dir / file_path
            exists = full_path.exists()
            actual_sha256 = compute_sha256(full_path) if exists else None

            files[file_type] = FileChecksum(
                path=file_path,
                sha256=expected_sha256,
                exists=exists,
                actual_sha256=actual_sha256,
            )

        manifest = RulesetManifest(
            version=data.get("version", "unknown"),
            profile=data.get("profile", profile),
            files=files,
            base_dir=resources_dir,
        )

        if auto_validate:
            manifest.validate()

        return manifest

    except Exception as e:
        logger.error(f"Failed to load manifest: {e}")
        return None


def create_manifest(
    resources_dir: Path,
    profile: str,
    files: Dict[str, str],
    version: str = "v1.0.0",
) -> Dict[str, Any]:
    """매니페스트 JSON을 생성합니다.

    Args:
        resources_dir: 리소스 디렉토리 경로
        profile: 프로필
        files: 파일 타입 → 파일명 매핑
        version: 버전

    Returns:
        매니페스트 딕셔너리
    """
    file_entries = {}

    for file_type, filename in files.items():
        file_path = resources_dir / filename

        if file_path.exists():
            sha256 = compute_sha256(file_path)
            file_entries[file_type] = {
                "path": filename,
                "sha256": sha256,
            }
        else:
            logger.warning(f"File not found for manifest: {file_path}")

    return {
        "version": version,
        "profile": profile,
        "files": file_entries,
    }


def save_manifest(
    manifest_data: Dict[str, Any],
    output_path: Path,
) -> None:
    """매니페스트를 파일로 저장합니다."""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, ensure_ascii=False, indent=2)

    logger.info(f"Manifest saved: {output_path}")
