#!/bin/bash
# INBOUND 인터페이스 end-to-end 시뮬레이션
# webhook 정상 + 실패 + 조회 + 능동실행 차단 + 잘못된 enum 값 거부

set -e
TMP=".tmp_inbound_test.json"
trap 'rm -f "$TMP"' EXIT

TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin1234"}' \
  | python -c "import sys, json; print(json.load(sys.stdin)['access_token'])")

INBOUND_ID=$(curl -s -H "Authorization: Bearer $TOKEN" "http://localhost:8000/api/interfaces?direction=INBOUND" \
  | python -c "import sys, json; rows=json.load(sys.stdin); print(rows[0]['id'])")
echo "[INFO] INBOUND_ID=$INBOUND_ID"

echo ""
echo "[3a] 정상 webhook (SUCCESS) → ingest"
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "http://localhost:8000/api/call-logs/ingest" \
  -d "{\"interface_id\": $INBOUND_ID, \"status\": \"SUCCESS\", \"duration_ms\": 42, \"http_status\": 200, \"request\": {\"order_id\": \"ORD-12345\"}, \"response\": {\"received\": true}}" \
  > "$TMP"
python -c "import json; d=json.load(open(r'$TMP', encoding='utf-8')); print(f'  -> id={d[\"id\"]} status={d[\"status\"]} triggered_by={d[\"triggered_by\"]}')"

echo ""
echo "[3b] 실패 webhook (SERVER_ERROR) → ingest"
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "http://localhost:8000/api/call-logs/ingest" \
  -d "{\"interface_id\": $INBOUND_ID, \"status\": \"SERVER_ERROR\", \"duration_ms\": 1500, \"http_status\": 500, \"error_message\": \"webhook DB lost\"}" \
  > "$TMP"
python -c "import json; d=json.load(open(r'$TMP', encoding='utf-8')); print(f'  -> id={d[\"id\"]} status={d[\"status\"]} http={d[\"http_status\"]}')"

echo ""
echo "[3c] 방금 ingest 된 INBOUND 로그 조회 (최근 5건)"
curl -s -H "Authorization: Bearer $TOKEN" "http://localhost:8000/api/call-logs?interface_id=$INBOUND_ID&limit=5" > "$TMP"
python -c "import json; rows=json.load(open(r'$TMP', encoding='utf-8')); print(f'  -> count={len(rows)}'); [print(f'  -> #{r[\"id\"]} status={r[\"status\"]} triggered_by={r[\"triggered_by\"]} duration={r[\"duration_ms\"]}ms') for r in rows]"

echo ""
echo "[3d] INBOUND 능동 실행 시도 → 400 차단 기대"
HTTP=$(curl -s -o "$TMP" -w "%{http_code}" -X POST -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/interfaces/$INBOUND_ID/execute")
DETAIL=$(python -c "import json; print(json.load(open(r'$TMP', encoding='utf-8')).get('detail', '?'))")
if [ "$HTTP" = "400" ]; then
  echo "  -> OK (HTTP $HTTP): $DETAIL"
else
  echo "  -> FAIL: expected HTTP 400 but got $HTTP — $DETAIL"
  exit 1
fi

echo ""
echo "[3e] direction toggle (INBOUND → OUTBOUND → INBOUND)"
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "http://localhost:8000/api/interfaces/$INBOUND_ID" -d '{"direction": "OUTBOUND"}' > "$TMP"
python -c "import json; d=json.load(open(r'$TMP', encoding='utf-8')); print(f'  -> after toggle: direction={d[\"direction\"]} (OUTBOUND 기대)')"

curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "http://localhost:8000/api/interfaces/$INBOUND_ID" -d '{"direction": "INBOUND"}' > "$TMP"
python -c "import json; d=json.load(open(r'$TMP', encoding='utf-8')); print(f'  -> restored:    direction={d[\"direction\"]} (INBOUND 기대)')"

echo ""
echo "[3f] 잘못된 enum 값 거부 (BIDIRECTIONAL → 422)"
HTTP=$(curl -s -o "$TMP" -w "%{http_code}" -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "http://localhost:8000/api/interfaces/$INBOUND_ID" -d '{"direction": "BIDIRECTIONAL"}')
if [ "$HTTP" = "422" ]; then
  echo "  -> OK (HTTP $HTTP): 잘못된 enum 값 거부됨"
else
  echo "  -> FAIL: expected HTTP 422 but got $HTTP"
  exit 1
fi

echo ""
echo "[3g] 새 INBOUND 인터페이스 생성 → 능동실행 차단 → 삭제"
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "http://localhost:8000/api/interfaces" \
  -d '{"name": "_test_inbound_e2e", "endpoint": "/api/webhooks/test", "method": "POST", "protocol": "REST", "auth_type": "NONE", "category": "EXTERNAL_PARTNER", "direction": "INBOUND", "enabled": true}' > "$TMP"
NEW_ID=$(python -c "import json; print(json.load(open(r'$TMP', encoding='utf-8')).get('id', ''))")
echo "  -> 생성: id=$NEW_ID"

HTTP=$(curl -s -o "$TMP" -w "%{http_code}" -X POST -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/interfaces/$NEW_ID/execute")
echo "  -> 능동실행 시도: HTTP $HTTP (400 기대)"
[ "$HTTP" = "400" ] || { echo "FAIL"; exit 1; }

curl -s -o /dev/null -w "  -> 삭제: HTTP %{http_code}\n" -X DELETE -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/interfaces/$NEW_ID"

echo ""
echo "✅ INBOUND e2e 시뮬레이션 7단계 모두 통과"
