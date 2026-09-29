# scripts

기능 단위 API 스모크 체크 모음. 로컬에서 `docker compose up` 으로 스택을 띄운 뒤
실행 중인 백엔드(`http://localhost:8000`)를 HTTP 로 호출해 동작을 확인한다.

```bash
python scripts/check_auth.py
bash scripts/test_inbound_e2e.sh
```

- `check_*.py` — 인증·계정 보안, 알림 룰, 재시도 정책, 시크릿 만료, SLA, 토폴로지, AI 분석 등 기능별 확인
- `test_inbound_e2e.sh` — INBOUND(webhook) 인터페이스 수신 → 호출 로그 적재 흐름 E2E 확인

데모 계정(`admin` / `admin1234`)과 시드 데이터(`python -m app.scripts.seed_demo`)를 전제로 한다.
