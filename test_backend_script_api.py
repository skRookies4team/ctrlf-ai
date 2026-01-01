"""
백-AI 스크립트 API End-to-End 테스트

이 스크립트는 다음을 테스트합니다:
1. Milvus에서 문서 조회
2. 스크립트 생성 (실제 LLM 호출)
3. 백엔드 콜백 시뮬레이션

사용법:
    python test_backend_script_api.py

주의: 실제 백엔드 없이 AI 서비스만 테스트합니다.
"""

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from app.clients.milvus_client import get_milvus_client
from app.models.source_set import (
    GeneratedScript,
    SourceSetDocument,
    SourceSetStartRequest,
)
from app.services.source_set_orchestrator import (
    ProcessingStatus,
    SourceSetOrchestrator,
)


async def mock_backend_get_documents(source_set_id: str) -> Dict[str, Any]:
    """백엔드 문서 목록 조회 Mock.

    실제 Milvus에 있는 문서를 반환합니다.
    """
    print(f"\n[Mock 백엔드] 문서 목록 조회: source_set_id={source_set_id}")

    # 실제 Milvus에서 가져올 문서 정보
    # source_url에서 doc_id를 추출하므로 파일명 형식으로 지정
    return {
        "documents": [
            {
                "documentId": "doc-001",
                "title": "사내 보안형 AI 챗봇 사용 안내",
                "sourceUrl": "https://example.com/전체공통_추가교육자료_사내 보안형 AI 챗봇 사용 안내.docx",
                "domain": "EDUCATION",
                "version": 1,
            }
        ]
    }


async def mock_backend_notify_complete(
    source_set_id: str,
    request: Dict[str, Any],
) -> None:
    """백엔드 완료 콜백 Mock.

    전달받은 스크립트를 출력합니다.
    """
    print(f"\n{'='*60}")
    print("[Mock 백엔드] 스크립트 전달 완료 콜백 수신!")
    print(f"{'='*60}")
    print(f"source_set_id: {source_set_id}")
    print(f"status: {request.get('status')}")
    print(f"source_set_status: {request.get('source_set_status')}")

    script = request.get("script")
    if script:
        print(f"\n--- 생성된 스크립트 ---")
        print(f"script_id: {script.get('script_id')}")
        print(f"title: {script.get('title')}")
        print(f"total_duration_sec: {script.get('total_duration_sec')}")
        print(f"chapters: {len(script.get('chapters', []))}개")

        for ch in script.get("chapters", []):
            print(f"\n  [챕터 {ch.get('chapter_index')}] {ch.get('title')}")
            for sc in ch.get("scenes", []):
                print(f"    - 씬 {sc.get('scene_index')}: {sc.get('narration', '')[:50]}...")
    else:
        print(f"\n[오류] 스크립트가 없습니다!")
        print(f"error_code: {request.get('error_code')}")
        print(f"error_message: {request.get('error_message')}")

    # JSON 파일로 저장
    output_file = "backend_callback_result.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(request, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[저장됨] {output_file}")


class MockBackendClient:
    """Mock 백엔드 클라이언트."""

    def __init__(self):
        self._callbacks_received = []

    async def get_source_set_documents(self, source_set_id: str):
        """문서 목록 조회 Mock."""
        data = await mock_backend_get_documents(source_set_id)

        # SourceSetDocument 리스트로 변환
        documents = []
        for doc_data in data.get("documents", []):
            doc = MagicMock()
            doc.document_id = doc_data["documentId"]
            doc.title = doc_data["title"]
            doc.source_url = doc_data["sourceUrl"]
            doc.domain = doc_data["domain"]
            doc.version = doc_data.get("version", 1)
            documents.append(doc)

        result = MagicMock()
        result.documents = documents
        return result

    async def notify_source_set_complete(self, source_set_id: str, request):
        """완료 콜백 Mock."""
        # request를 dict로 변환
        request_dict = {
            "video_id": request.video_id,
            "status": request.status,
            "source_set_status": request.source_set_status,
            "error_code": request.error_code,
            "error_message": request.error_message,
            "request_id": request.request_id,
            "trace_id": request.trace_id,
        }

        if request.script:
            request_dict["script"] = {
                "script_id": request.script.script_id,
                "title": request.script.title,
                "total_duration_sec": request.script.total_duration_sec,
                "chapters": [
                    {
                        "chapter_index": ch.chapter_index,
                        "title": ch.title,
                        "duration_sec": ch.duration_sec,
                        "scenes": [
                            {
                                "scene_index": sc.scene_index,
                                "purpose": sc.purpose,
                                "narration": sc.narration,
                                "caption": sc.caption,
                                "visual": sc.visual,
                                "visual_type": getattr(sc, 'visual_type', None),
                                "visual_text": getattr(sc, 'visual_text', None),
                                "visual_description": getattr(sc, 'visual_description', None),
                                "highlight_terms": getattr(sc, 'highlight_terms', []),
                                "transition": getattr(sc, 'transition', None),
                                "duration_sec": sc.duration_sec,
                                "confidence_score": getattr(sc, 'confidence_score', None),
                                "source_refs": [
                                    {"document_id": r.document_id, "chunk_index": r.chunk_index}
                                    for r in (sc.source_refs or [])
                                ] if hasattr(sc, 'source_refs') and sc.source_refs else [],
                            }
                            for sc in ch.scenes
                        ]
                    }
                    for ch in request.script.chapters
                ]
            }

        if request.documents:
            request_dict["documents"] = [
                {
                    "document_id": d.document_id,
                    "status": d.status,
                    "fail_reason": d.fail_reason,
                }
                for d in request.documents
            ]

        await mock_backend_notify_complete(source_set_id, request_dict)
        self._callbacks_received.append(request_dict)

    async def bulk_upsert_chunks(self, document_id: str, request):
        """청크 저장 Mock."""
        print(f"[Mock 백엔드] 청크 저장: doc_id={document_id}, chunks={len(request.chunks)}개")


async def test_e2e_script_generation():
    """E2E 스크립트 생성 테스트."""
    print("\n" + "="*60)
    print("백-AI 스크립트 API E2E 테스트")
    print("="*60)

    # 1. Milvus 연결 확인
    print("\n[1] Milvus 연결 확인...")
    milvus = get_milvus_client()
    health = await milvus.health_check()
    if not health:
        print("Milvus 연결 실패!")
        return False
    print("Milvus 연결 성공!")

    # 2. Mock 백엔드 클라이언트 생성
    print("\n[2] Mock 백엔드 설정...")
    mock_backend = MockBackendClient()

    # 3. Orchestrator 생성 (Mock 백엔드 주입)
    print("\n[3] SourceSetOrchestrator 초기화...")
    orchestrator = SourceSetOrchestrator(
        backend_client=mock_backend,
        milvus_client=milvus,
    )

    # 내부 설정 강제 (Milvus 사용)
    orchestrator._use_milvus = True
    orchestrator._milvus_client = milvus

    # 4. 스크립트 생성 요청
    print("\n[4] 스크립트 생성 요청...")
    source_set_id = f"test-{uuid.uuid4().hex[:8]}"

    request = SourceSetStartRequest(
        video_id="video-001",
        education_id="edu-001",
        request_id=f"req-{uuid.uuid4().hex[:8]}",
        trace_id=f"trace-{uuid.uuid4().hex[:8]}",
        llm_model_hint="meta-llama/Meta-Llama-3-8B-Instruct",  # 실제 LLM 모델명
    )

    print(f"  source_set_id: {source_set_id}")
    print(f"  video_id: {request.video_id}")

    # 5. 처리 시작 (비동기)
    print("\n[5] 처리 시작 (비동기)...")
    response = await orchestrator.start(source_set_id, request)
    print(f"  응답: received={response.received}, status={response.status}")

    # 6. 처리 완료 대기
    print("\n[6] 처리 완료 대기 중...")
    for i in range(120):  # 최대 2분 대기
        await asyncio.sleep(1)
        job = orchestrator.get_job_status(source_set_id)

        if job:
            if job.status == ProcessingStatus.COMPLETED:
                print(f"\n처리 완료! ({i+1}초 소요)")
                break
            elif job.status == ProcessingStatus.FAILED:
                print(f"\n처리 실패! ({i+1}초 소요)")
                print(f"  error_code: {job.error_code}")
                print(f"  error_message: {job.error_message}")
                break

        if i % 10 == 9:
            print(f"  ... 대기 중 ({i+1}초)")

    # 7. 결과 확인
    print("\n" + "="*60)
    print("[결과]")
    print("="*60)

    job = orchestrator.get_job_status(source_set_id)
    if job:
        print(f"상태: {job.status.value}")
        print(f"문서 수: {len(job.documents)}")
        print(f"스크립트 생성: {'성공' if job.generated_script else '실패'}")

        if job.generated_script:
            script = job.generated_script
            print(f"\n--- 스크립트 요약 ---")
            print(f"제목: {script.title}")
            print(f"총 길이: {script.total_duration_sec:.0f}초 ({script.total_duration_sec/60:.1f}분)")
            print(f"챕터 수: {len(script.chapters)}")

            for ch in script.chapters:
                print(f"\n[챕터 {ch.chapter_index}] {ch.title}")
                for sc in ch.scenes:
                    print(f"  씬 {sc.scene_index}: {sc.narration[:40]}...")

            return True

    return False


async def main():
    """메인 함수."""
    try:
        success = await test_e2e_script_generation()

        if success:
            print("\n" + "="*60)
            print("테스트 성공!")
            print("="*60)
            print("\n생성된 파일:")
            print("  - backend_callback_result.json (백엔드 콜백 데이터)")
        else:
            print("\n" + "="*60)
            print("테스트 실패!")
            print("="*60)

    except Exception as e:
        print(f"\n오류 발생: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
