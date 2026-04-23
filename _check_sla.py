import io, json, sys, urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def get(u):
    return json.loads(urllib.request.urlopen(u).read())


print("=== /sla/trend (월별) ===")
trend = get("http://localhost:8000/api/sla/trend?bucket=month&months=6")
print(f"rows={len(trend)}")
periods = sorted(set(p["period"] for p in trend))
print(f"periods: {periods}")
sample = trend[:3]
for r in sample:
    print(f"  {r['period']} #{r['interface_id']} {r['interface_name'][:25]:25s}  uptime={r['uptime_pct']:.2f}% calls={r['total_calls']}")

print("\n=== /sla/calendar (일별, 30일) ===")
cal = get("http://localhost:8000/api/sla/calendar?days=30")
print(f"rows={len(cal)}")
dates = sorted(set(c["date"] for c in cal))
print(f"date range: {dates[0]} ~ {dates[-1]} ({len(dates)} days)")
miss = [c for c in cal if not c["meets"]]
print(f"미달 셀: {len(miss)}건 / 총 {len(cal)}건")

print("\n=== /sla/export.xlsx ===")
import urllib.request
req = urllib.request.Request("http://localhost:8000/api/sla/export.xlsx?days=30")
with urllib.request.urlopen(req) as resp:
    data = resp.read()
    ct = resp.headers.get("Content-Type")
    cd = resp.headers.get("Content-Disposition")
print(f"size: {len(data):,} bytes  content-type: {ct}")
print(f"disposition: {cd}")
print(f"valid xlsx? magic bytes={data[:4]} (PK = openxml zip)")