import io, json, sys, urllib.request, urllib.error

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def post(url, body=None, token=None):
    req = urllib.request.Request(url, method="POST")
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return json.loads(urllib.request.urlopen(req).read())


def get(url, token=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(req)


print("=== 로그인 테스트 ===")
for username, password in [
    ("admin", "admin1234"),
    ("operator", "op1234"),
    ("viewer", "view1234"),
    ("admin", "wrong"),
]:
    try:
        r = post("http://localhost:8000/api/auth/login", {"username": username, "password": password})
        print(f"  ✓ {username:9s} → role={r['user']['role']:9s} token={r['access_token'][:30]}…")
    except urllib.error.HTTPError as e:
        print(f"  ✗ {username:9s} → HTTP {e.code} (예상: wrong password 시 401)")

print("\n=== 인증 가드 테스트 ===")
admin = post("http://localhost:8000/api/auth/login", {"username": "admin", "password": "admin1234"})
viewer = post("http://localhost:8000/api/auth/login", {"username": "viewer", "password": "view1234"})

# admin token으로 인터페이스 조회 (성공해야)
res = get("http://localhost:8000/api/interfaces", token=admin["access_token"])
print(f"  admin GET /interfaces → {res.status}")

# 토큰 없이 인터페이스 조회 (401)
try:
    get("http://localhost:8000/api/interfaces")
except urllib.error.HTTPError as e:
    print(f"  no-token GET /interfaces → HTTP {e.code} (401 기대)")

# viewer token으로 인터페이스 실행 시도 (403 — OPERATOR 이상 필요)
try:
    post("http://localhost:8000/api/interfaces/15/execute", token=viewer["access_token"])
except urllib.error.HTTPError as e:
    print(f"  viewer POST /execute → HTTP {e.code} (403 기대)")

# admin token으로 실행 (성공)
exec_res = post("http://localhost:8000/api/interfaces/15/execute", token=admin["access_token"])
print(f"  admin POST /execute → status={exec_res['status']}")