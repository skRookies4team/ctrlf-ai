"""
Mock RAGFlow + 스크립트 생성 테스트

테스트 목적:
- Mock RAGFlow API가 정상 작동하는지 확인
- SourceSetOrchestrator가 RAGFlow 클라이언트를 통해 문서를 처리하는지 확인
"""

import asyncio
import os
import sys

# 환경변수 설정 (Mock RAGFlow 사용)
os.environ["RAGFLOW_BASE_URL"] = "http://localhost:8090"
os.environ["MILVUS_ENABLED"] = "false"
os.environ["AI_ENV"] = "mock"

# 프로젝트 경로 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.clients.ragflow_client import RagflowClient, get_ragflow_client, clear_ragflow_client


async def test_ragflow_client():
    """RagflowClient 직접 테스트"""
    print("\n" + "=" * 60)
    print("1. RagflowClient 직접 테스트")
    print("=" * 60)

    # 싱글톤 초기화
    clear_ragflow_client()
    client = get_ragflow_client()

    print(f"RAGFlow configured: {client.is_configured}")
    print(f"Base URL: {client._base_url}")

    # 1. 문서 업로드
    print("\n[1] 문서 업로드...")
    upload_result = await client.upload_document(
        dataset_id="education-dataset",
        file_url="https://example.com/교육자료.pdf",
        file_name="장애인식개선교육.pdf",
    )
    doc_id = upload_result.get("id")
    print(f"   ✓ 업로드 완료: doc_id={doc_id}")

    # 2. 파싱 트리거
    print("\n[2] 파싱 트리거...")
    parse_result = await client.trigger_parsing(
        dataset_id="education-dataset",
        document_ids=[doc_id],
    )
    print(f"   ✓ 파싱 트리거 완료: {parse_result}")

    # 3. 상태 조회
    print("\n[3] 문서 상태 조회...")
    status = await client.get_document_status(
        dataset_id="education-dataset",
        document_id=doc_id,
    )
    print(f"   ✓ 상태: run={status.get('run')}, progress={status.get('progress')}, chunks={status.get('chunk_count')}")

    # 4. 청크 조회
    print("\n[4] 청크 조회...")
    chunks_result = await client.get_document_chunks(
        dataset_id="education-dataset",
        document_id=doc_id,
    )
    chunks = chunks_result.get("chunks", [])
    print(f"   ✓ 청크 수: {len(chunks)}")
    for i, chunk in enumerate(chunks[:3]):
        content = chunk.get("content", "")[:50]
        print(f"     - [{i}] {content}...")

    return doc_id, chunks


async def test_source_set_orchestrator():
    """SourceSetOrchestrator 테스트 (문서 처리 부분)"""
    print("\n" + "=" * 60)
    print("2. SourceSetOrchestrator 문서 처리 테스트")
    print("=" * 60)

    from app.services.source_set_orchestrator import (
        SourceSetOrchestrator,
        ProcessingJob,
        clear_source_set_orchestrator,
    )
    from app.models.source_set import SourceSetDocument

    # 싱글톤 초기화
    clear_source_set_orchestrator()

    # Mock 백엔드 클라이언트 없이 RAGFlow만 테스트
    from unittest.mock import AsyncMock, MagicMock

    mock_backend = MagicMock()
    mock_backend.get_source_set_documents = AsyncMock()
    mock_backend.bulk_upsert_chunks = AsyncMock()
    mock_backend.notify_source_set_complete = AsyncMock()

    orchestrator = SourceSetOrchestrator(backend_client=mock_backend)

    print(f"RAGFlow configured: {orchestrator._ragflow_client.is_configured}")

    # 테스트용 문서
    test_doc = SourceSetDocument(
        document_id="test-doc-001",
        title="장애인식개선교육 자료",
        source_url="https://example.com/장애인식개선교육.pdf",
        domain="EDUCATION",
    )

    # 테스트용 Job
    job = ProcessingJob(
        source_set_id="test-source-set-001",
        video_id="test-video-001",
        education_id="test-edu-001",
        request_id="test-req-001",
        trace_id="test-trace-001",
        script_policy_id=None,
        llm_model_hint=None,
    )

    print("\n[1] 문서 처리 시작...")
    result = await orchestrator._process_document(
        source_set_id="test-source-set-001",
        doc=test_doc,
        job=job,
    )

    print(f"\n[결과]")
    print(f"   ✓ 성공: {result.success}")
    print(f"   ✓ 청크 수: {result.chunks_count}")
    print(f"   ✓ 실패 이유: {result.fail_reason or 'N/A'}")

    if result.chunks:
        print(f"\n[생성된 청크 샘플]")
        for i, chunk in enumerate(result.chunks[:3]):
            text = chunk.get("chunk_text", "")[:50]
            print(f"     - [{i}] {text}...")

    return result


async def main():
    print("\n" + "=" * 60)
    print("Mock RAGFlow 스크립트 생성 테스트")
    print("=" * 60)
    print(f"RAGFLOW_BASE_URL: {os.environ.get('RAGFLOW_BASE_URL')}")

    try:
        # 1. RagflowClient 직접 테스트
        doc_id, chunks = await test_ragflow_client()

        # 2. SourceSetOrchestrator 테스트
        result = await test_source_set_orchestrator()

        print("\n" + "=" * 60)
        print("✓ 모든 테스트 통과!")
        print("=" * 60)

    except Exception as e:
        print(f"\n✗ 테스트 실패: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
