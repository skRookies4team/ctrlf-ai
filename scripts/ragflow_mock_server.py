"""
RAGFlow Mock Server for Local Testing

AI 서버 연결 테스트용 Mock RAGFlow 서버입니다.

실행:
    python scripts/ragflow_mock_server.py

엔드포인트:
    POST /v1/internal_ragflow/internal/ragflow/ingest - AI → RAGFlow ingest 요청 수신

테스트 흐름:
    1. 이 Mock 서버 실행 (포트 9380)
    2. AI 서버 실행 (포트 8000)
    3. curl로 AI 서버에 ingest 요청
    4. AI 서버 → Mock RAGFlow → 콜백 → AI 서버 흐름 확인
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# 환경변수 (기본값)
AI_TO_RAGFLOW_TOKEN = os.getenv("AI_TO_RAGFLOW_TOKEN", "your-backend-internal-token-here")
AI_CALLBACK_URL = os.getenv("AI_CALLBACK_URL", "http://localhost:8000/internal/ai/callbacks/ragflow/ingest")
RAGFLOW_TO_AI_TOKEN = os.getenv("RAGFLOW_TO_AI_TOKEN", "your-ragflow-callback-token-here")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.route("/v1/internal_ragflow/internal/ragflow/ingest", methods=["POST"])
def internal_ragflow_ingest():
    """
    AI → RAGFlow ingest 요청 수신

    Spec:
        Headers:
            Content-Type: application/json
            X-Internal-Token: <AI_TO_RAGFLOW_TOKEN>

        Body:
        {
            "datasetId": "사내규정",
            "docId": "POL-EDU-015",
            "version": 3,
            "fileUrl": "https://...pdf",
            "replace": true,
            "meta": {...}
        }

    Response: 202
        { "received": true, "ingestId": "...", "status": "QUEUED" }
    """
    print(f"\n{'='*60}")
    print(f"[{_now_iso()}] Received ingest request")
    print(f"{'='*60}")

    # 토큰 검증
    got_token = request.headers.get("X-Internal-Token", "")
    if AI_TO_RAGFLOW_TOKEN and got_token != AI_TO_RAGFLOW_TOKEN:
        print(f"[ERROR] Token mismatch: got='{got_token}', expected='{AI_TO_RAGFLOW_TOKEN}'")
        return jsonify({"error": "Unauthorized", "message": "Invalid token"}), 401

    # Content-Type 검증
    if not request.is_json:
        print("[ERROR] Content-Type must be application/json")
        return jsonify({"error": "Bad Request", "message": "Content-Type must be application/json"}), 415

    # Body 파싱
    body = request.get_json(silent=True) or {}
    print(f"[INFO] Request body:")
    print(json.dumps(body, ensure_ascii=False, indent=2))

    dataset_id = body.get("datasetId")
    doc_id = body.get("docId")
    file_url = body.get("fileUrl")
    version = body.get("version")
    replace = body.get("replace", False)
    meta = body.get("meta", {})

    # 필수 필드 검증
    if not dataset_id or not doc_id or not file_url:
        print("[ERROR] Missing required fields: datasetId, docId, fileUrl")
        return jsonify({
            "error": "Bad Request",
            "message": "datasetId, docId, fileUrl are required"
        }), 400

    # ingestId 생성
    ingest_id = str(uuid.uuid4())
    print(f"[INFO] Created ingestId: {ingest_id}")

    # 비동기로 처리 시뮬레이션 + 콜백
    def process_and_callback():
        print(f"\n[{_now_iso()}] Processing ingest job: {ingest_id}")

        # 처리 시뮬레이션 (2초 대기)
        time.sleep(2)

        # 콜백 호출
        if AI_CALLBACK_URL and RAGFLOW_TO_AI_TOKEN:
            callback_body = {
                "ingestId": ingest_id,
                "docId": doc_id,
                "version": version,
                "status": "COMPLETED",  # 성공 시뮬레이션
                "processedAt": _now_iso(),
                "failReason": None,
                "meta": meta,
                "stats": {"chunks": 10}  # 청크 수 시뮬레이션
            }

            print(f"\n[{_now_iso()}] Sending callback to: {AI_CALLBACK_URL}")
            print(f"[INFO] Callback body:")
            print(json.dumps(callback_body, ensure_ascii=False, indent=2))

            try:
                resp = requests.post(
                    AI_CALLBACK_URL,
                    json=callback_body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Internal-Token": RAGFLOW_TO_AI_TOKEN,
                    },
                    timeout=10,
                )
                print(f"[INFO] Callback response: {resp.status_code}")
                print(f"[INFO] Callback response body: {resp.text}")
            except Exception as e:
                print(f"[ERROR] Callback failed: {e}")
        else:
            print("[WARN] Callback skipped (AI_CALLBACK_URL or RAGFLOW_TO_AI_TOKEN not set)")

    # 백그라운드 스레드로 처리
    threading.Thread(target=process_and_callback, daemon=True).start()

    # 즉시 202 Accepted 반환
    response = {
        "received": True,
        "ingestId": ingest_id,
        "status": "QUEUED"
    }
    print(f"\n[INFO] Returning 202 Accepted:")
    print(json.dumps(response, ensure_ascii=False, indent=2))

    return jsonify(response), 202


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint"""
    return jsonify({"status": "ok", "service": "ragflow-mock"})


if __name__ == "__main__":
    print("=" * 60)
    print("RAGFlow Mock Server for Local Testing")
    print("=" * 60)
    print(f"AI_TO_RAGFLOW_TOKEN: {AI_TO_RAGFLOW_TOKEN}")
    print(f"AI_CALLBACK_URL: {AI_CALLBACK_URL}")
    print(f"RAGFLOW_TO_AI_TOKEN: {RAGFLOW_TO_AI_TOKEN}")
    print("=" * 60)
    print("Endpoints:")
    print("  POST /v1/internal_ragflow/internal/ragflow/ingest")
    print("  GET  /health")
    print("=" * 60)
    print("Starting server on http://localhost:9380")
    print("=" * 60 + "\n")

    app.run(host="0.0.0.0", port=9380, debug=True)
