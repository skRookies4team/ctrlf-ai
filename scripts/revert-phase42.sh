#!/bin/bash
# Phase 42 Revert Script
#
# 사용 전 팀과 합의 필수!
# 이 스크립트는 Phase 42에서 삭제된 Direct Milvus 인덱싱 코드를 복구합니다.
#
# 실행 방법:
#   chmod +x scripts/revert-phase42.sh
#   ./scripts/revert-phase42.sh

set -e

echo "=============================================="
echo "Phase 42 Revert Script"
echo "=============================================="
echo ""
echo "이 스크립트는 다음 커밋들을 revert합니다:"
echo "  - bc0bcda: KB 인덱싱 서비스 및 테스트 제거"
echo "  - 700178d: MilvusSearchClient 읽기 전용 변환"
echo "  - 3afc150: Deprecated internal RAG 테스트 제거"
echo "  - 5f6a79d: Direct Milvus 인덱싱 제거"
echo ""
echo "복구되는 파일:"
echo "  - app/services/document_processor.py (550줄)"
echo "  - app/services/indexing_service.py (388줄)"
echo "  - app/services/job_service.py (336줄)"
echo "  - app/services/kb_index_service.py (665줄)"
echo "  - app/clients/milvus_client.py (upsert/delete 메서드)"
echo "  - tests/unit/test_phase28_kb_indexing.py"
echo "  - tests/unit/test_phase29_kb_e2e.py"
echo "  - tests/unit/test_internal_rag.py"
echo "  - tests/unit/test_phase30_internal_rag.py"
echo ""

read -p "계속하시겠습니까? (y/N): " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "취소되었습니다."
    exit 0
fi

echo ""
echo "새 브랜치 생성: revert/phase42"
git checkout -b revert/phase42

echo ""
echo "Phase 42 커밋 revert 중..."

# 역순으로 revert (최신 커밋부터)
git revert --no-commit bc0bcda
git revert --no-commit 700178d
git revert --no-commit 3afc150
git revert --no-commit 5f6a79d

echo ""
echo "커밋 생성 중..."
git commit -m "revert: Phase 42 롤백 (Direct Milvus 인덱싱 복구)

Phase 42에서 제거된 Direct Milvus 인덱싱 기능 복구:
- DocumentProcessor, IndexingService, JobService 복구
- KB 인덱싱 서비스 복구
- MilvusSearchClient upsert/delete 메서드 복구
- 관련 테스트 복구

Reverted commits:
- bc0bcda: KB 인덱싱 서비스 및 테스트 제거
- 700178d: MilvusSearchClient 읽기 전용 변환
- 3afc150: Deprecated internal RAG 테스트 제거
- 5f6a79d: Direct Milvus 인덱싱 제거

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"

echo ""
echo "=============================================="
echo "Revert 완료!"
echo "=============================================="
echo ""
echo "다음 단계:"
echo "  1. git log로 revert 커밋 확인"
echo "  2. pytest tests/unit/ -v로 테스트 통과 확인"
echo "  3. git push -u origin revert/phase42로 원격 푸시"
echo "  4. GitHub에서 PR 생성"
echo ""
echo "main으로 돌아가려면: git checkout main"
