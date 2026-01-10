#!/bin/bash
# =============================================================================
# Elasticsearch 초기 설정 스크립트
# (1) ILM 정책 생성 (2) 인덱스 템플릿 적용 (3) Rollover alias 초기 인덱스 생성
# =============================================================================
#
# 사용법:
#   ./elk/setup-elasticsearch.sh
#   또는
#   cd elk && ./setup-elasticsearch.sh
#
# 사전 조건:
#   - Elasticsearch가 실행 중이어야 함 (docker compose -f elk/docker-compose.elk.yml up -d)
#   - curl 설치 필요
#
# =============================================================================

set -e  # 에러 발생 시 즉시 종료

# 스크립트 위치 기준 경로 (어디서 실행해도 동작)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ES 호스트 (http:// scheme 포함)
ES_HOST="${ES_HOST:-http://localhost:9200}"
INDEX_ALIAS="ctrlf-ai"
INITIAL_INDEX="ctrlf-ai-000001"
CHAT_LOG_INDEX="chat_log"

echo "=== Elasticsearch 초기 설정 ==="
echo "ES_HOST: $ES_HOST"
echo "SCRIPT_DIR: $SCRIPT_DIR"
echo "INDEX_ALIAS: $INDEX_ALIAS"
echo "INITIAL_INDEX: $INITIAL_INDEX"
echo "CHAT_LOG_INDEX: $CHAT_LOG_INDEX"
echo ""

# 1. ES 연결 확인
echo "[1/7] Elasticsearch 연결 확인..."
if ! curl -s -f "$ES_HOST/_cluster/health" > /dev/null; then
    echo "ERROR: Elasticsearch에 연결할 수 없습니다. 실행 중인지 확인하세요."
    exit 1
fi
echo "OK: Elasticsearch 연결됨"
echo ""

# 2. ILM 정책 생성
echo "[2/7] ILM 정책 생성..."
curl -s -X PUT "$ES_HOST/_ilm/policy/ctrlf-ai-ilm-policy" \
    -H "Content-Type: application/json" \
    --data-binary @"${SCRIPT_DIR}/elasticsearch/ilm-policy.json"
echo ""
echo "OK: ILM 정책 생성됨"
echo ""

# 3. ctrlf-ai 인덱스 템플릿 생성
echo "[3/7] ctrlf-ai 인덱스 템플릿 생성..."
curl -s -X PUT "$ES_HOST/_index_template/ctrlf-ai-template" \
    -H "Content-Type: application/json" \
    --data-binary @"${SCRIPT_DIR}/elasticsearch/index-template.json"
echo ""
echo "OK: ctrlf-ai 인덱스 템플릿 생성됨"
echo ""

# 4. chat_log 인덱스 템플릿 생성
echo "[4/7] chat_log 인덱스 템플릿 생성..."
curl -s -X PUT "$ES_HOST/_index_template/chat-log-template" \
    -H "Content-Type: application/json" \
    --data-binary @"${SCRIPT_DIR}/elasticsearch/chat-log-template.json"
echo ""
echo "OK: chat_log 인덱스 템플릿 생성됨"
echo ""

# 5. ctrlf-faq-log 인덱스 템플릿 생성
echo "[5/7] ctrlf-faq-log 인덱스 템플릿 생성..."
curl -s -X PUT "$ES_HOST/_index_template/faq-log-template" \
    -H "Content-Type: application/json" \
    --data-binary @"${SCRIPT_DIR}/elasticsearch/faq-log-template.json"
echo ""
echo "OK: ctrlf-faq-log 인덱스 템플릿 생성됨 (날짜별 인덱스 자동 생성)"
echo ""

# 6. Rollover alias 초기 인덱스 생성
echo "[6/7] Rollover alias 초기 인덱스 생성..."

# 이미 존재하는지 확인
if curl -s -f "$ES_HOST/$INITIAL_INDEX" > /dev/null 2>&1; then
    echo "SKIP: 초기 인덱스가 이미 존재합니다 ($INITIAL_INDEX)"
else
    # 초기 인덱스 생성 (is_write_index: true로 alias 설정)
    curl -s -X PUT "$ES_HOST/$INITIAL_INDEX" \
        -H "Content-Type: application/json" \
        -d "{
            \"aliases\": {
                \"$INDEX_ALIAS\": {
                    \"is_write_index\": true
                }
            }
        }"
    echo ""
    echo "OK: 초기 인덱스 생성됨 ($INITIAL_INDEX with alias $INDEX_ALIAS)"
fi
echo ""

# 7. chat_log 인덱스 생성
echo "[7/7] chat_log 인덱스 생성..."

# 이미 존재하는지 확인
if curl -s -f "$ES_HOST/$CHAT_LOG_INDEX" > /dev/null 2>&1; then
    echo "SKIP: chat_log 인덱스가 이미 존재합니다 ($CHAT_LOG_INDEX)"
else
    # chat_log 인덱스 생성 (템플릿 설정이 자동 적용됨)
    curl -s -X PUT "$ES_HOST/$CHAT_LOG_INDEX" \
        -H "Content-Type: application/json" \
        -d '{}'
    echo ""
    echo "OK: chat_log 인덱스 생성됨 ($CHAT_LOG_INDEX)"
fi
echo ""

echo "=== 설정 완료 ==="
echo ""
echo "검증 명령어:"
echo "  curl '$ES_HOST/_ilm/policy/ctrlf-ai-ilm-policy?pretty'"
echo "  curl '$ES_HOST/_index_template/ctrlf-ai-template?pretty'"
echo "  curl '$ES_HOST/_index_template/chat-log-template?pretty'"
echo "  curl '$ES_HOST/_index_template/faq-log-template?pretty'"
echo "  curl '$ES_HOST/_alias/$INDEX_ALIAS?pretty'"
echo "  curl '$ES_HOST/_cat/indices/ctrlf-ai-*?v'"
echo "  curl '$ES_HOST/_cat/indices/chat_log?v'"
echo "  curl '$ES_HOST/_cat/indices/ctrlf-faq-log-*?v'"
echo ""
echo "Kibana 접속: http://localhost:5601"
echo ""
echo "Data View 생성:"
echo "  1. Kibana → Stack Management → Data Views"
echo "  2. Create data view → Name: ctrlf-ai, Index pattern: ctrlf-ai-*"
echo "  3. Timestamp field: @timestamp"
echo ""
echo "  chat_log Data View:"
echo "  1. Create data view → Name: chat_log, Index pattern: chat_log*"
echo "  2. Timestamp field: @timestamp 또는 createdAt"
echo ""
echo "  ctrlf-faq-log Data View:"
echo "  1. Create data view → Name: faq-log, Index pattern: ctrlf-faq-log-*"
echo "  2. Timestamp field: @timestamp 또는 createdAt"
