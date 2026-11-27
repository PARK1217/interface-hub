"""에이전트 전환 전후 비교용 평가셋 실행기.

실행 중인 백엔드(기본 http://127.0.0.1:8000)에 평가셋 질문을 보내고
경로·결과·응답 시간·토큰·비용을 표로 출력합니다.

    python eval/run_agent_eval.py --phase before        # 전환 전 (only_after 항목 건너뜀)
    python eval/run_agent_eval.py --phase after --json out.json

토큰·비용은 응답에 run_id 가 있고 /api/agent-runs/{run_id} 가 있을 때만 채웁니다
(전환 전에는 LLM 응답의 usage 를 버리고 있어 "기록 없음").
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import httpx
import yaml

HERE = Path(__file__).resolve().parent


def _login(client: httpx.Client, base: str, username: str, password: str) -> str:
    r = client.post(f"{base}/auth/login", json={"username": username, "password": password})
    r.raise_for_status()
    return r.json()["access_token"]


def _expected(value) -> list:
    return value if isinstance(value, list) else [value]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000/api")
    ap.add_argument("--phase", choices=["before", "after"], required=True)
    ap.add_argument("--set", default=str(HERE / "agent_eval_set.yaml"))
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="admin1234")
    ap.add_argument("--json", default=None, help="결과를 JSON 파일로도 저장")
    args = ap.parse_args()

    items = yaml.safe_load(Path(args.set).read_text(encoding="utf-8"))["items"]
    rows: list[dict] = []
    with httpx.Client(timeout=120.0) as client:
        token = _login(client, args.base, args.user, args.password)
        headers = {"Authorization": f"Bearer {token}"}
        for it in items:
            if it.get("only_after") and args.phase == "before":
                continue
            t0 = time.perf_counter()
            if it.get("incident_id"):
                r = client.post(f"{args.base}/ai/rca", json={"incident_id": it["incident_id"]}, headers=headers)
            else:
                r = client.post(f"{args.base}/ai/ask", json={"question": it["question"]}, headers=headers)
            ms = int((time.perf_counter() - t0) * 1000)
            if r.status_code != 200:
                rows.append({"id": it["id"], "error": f"HTTP {r.status_code}", "latency_ms": ms})
                continue
            body = r.json()
            row = {
                "id": it["id"],
                "question": it["question"],
                "intent": body.get("intent"),
                "route": body.get("route"),
                "outcome": body.get("outcome"),
                "mode": body.get("mode"),
                "cached": body.get("cached"),
                "cases": len(body.get("similar_cases") or []),
                "latency_ms": ms,
                "intent_ok": body.get("intent") in _expected(it["expected_intent"]),
                "route_ok": (body.get("route") in _expected(it["expected_route"])) if args.phase == "after" else None,
                "outcome_ok": body.get("outcome") in _expected(it["expected_outcome"]),
                "input_tokens": None,
                "output_tokens": None,
                "cost_krw": None,
                "answer_head": (body.get("answer") or "").split("\n")[0][:60],
            }
            run_id = body.get("run_id")
            if run_id:
                d = client.get(f"{args.base}/agent-runs/{run_id}", headers=headers)
                if d.status_code == 200:
                    run = d.json()["run"]
                    row["input_tokens"] = run.get("total_input_tokens")
                    row["output_tokens"] = run.get("total_output_tokens")
                    row["cost_krw"] = run.get("total_cost_krw")
            rows.append(row)

    ok_rows = [r for r in rows if "error" not in r]
    print("| id | intent | route | outcome | mode | 시간(ms) | 토큰(입/출) | 판정 |")
    print("|---|---|---|---|---|---:|---|---|")
    for r in rows:
        if "error" in r:
            print(f"| {r['id']} | - | - | {r['error']} | - | {r['latency_ms']} | - | ✗ |")
            continue
        tok = "기록 없음" if r["input_tokens"] is None else f"{r['input_tokens']}/{r['output_tokens']}"
        ok = r["intent_ok"] and r["outcome_ok"] and (r["route_ok"] is not False)
        print(f"| {r['id']} | {r['intent']} | {r['route'] or '-'} | {r['outcome']} | {r['mode']} | "
              f"{r['latency_ms']} | {tok} | {'✓' if ok else '✗'} |")

    def rate(key: str) -> str:
        vals = [r[key] for r in ok_rows if r.get(key) is not None]
        return f"{sum(vals)}/{len(vals)}" if vals else "-"

    lat = [r["latency_ms"] for r in ok_rows]
    print()
    print(f"- 문항 {len(rows)}개 (HTTP 오류 {len(rows) - len(ok_rows)}개)")
    print(f"- intent 적중 {rate('intent_ok')} · route 적중 {rate('route_ok')} · outcome 적중 {rate('outcome_ok')}")
    if lat:
        print(f"- 응답 시간 평균 {statistics.mean(lat):.0f}ms · 중앙값 {statistics.median(lat):.0f}ms")
    toks = [r["input_tokens"] + r["output_tokens"] for r in ok_rows if r["input_tokens"] is not None]
    print(f"- 요청당 평균 토큰 {statistics.mean(toks):.0f}" if toks else "- 요청당 평균 토큰: 기록 없음")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all("error" not in r for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
