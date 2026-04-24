import json, urllib.request, urllib.error
def login(u, p):
    req = urllib.request.Request(
        "http://localhost:8000/api/auth/login",
        data=json.dumps({"username": u, "password": p}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(req).read())["access_token"]


def patch(url, body, token):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="PATCH",
    )
    return urllib.request.urlopen(req)


def post(url, body, token):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(req).read())


def get(url, token):
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    return json.loads(urllib.request.urlopen(req).read())


t = login("admin", "admin1234")

# pick an interface that has a secret
ifs = get("http://localhost:8000/api/interfaces", t)
target = next((i for i in ifs if i.get("has_secret")), None)
if not target:
    print("no interface with secret found, picking first")
    target = ifs[0]
target_id = target["id"]
print(f"target #{target_id} {target['name']}\n")

print("=== ① 사유 없이 시크릿 변경 → 422 기대 ===")
try:
    patch(f"http://localhost:8000/api/interfaces/{target_id}", {"auth_secret": "newkey123"}, t)
    print("FAIL — 통과돼버림")
except urllib.error.HTTPError as e:
    print(f"OK — HTTP {e.code}")

print("\n=== ② 사유와 함께 변경 ===")
patch(
    f"http://localhost:8000/api/interfaces/{target_id}",
    {"auth_secret": "noahub:newpwd", "secret_change_reason": "KIDI 토큰 만료로 재발급"},
    t,
)
print("OK")

print("\n=== ③ 사유 없이 reveal → 422 기대 ===")
try:
    post(f"http://localhost:8000/api/interfaces/{target_id}/reveal-secret", {"reason": ""}, t)
    print("FAIL")
except urllib.error.HTTPError as e:
    print(f"OK — HTTP {e.code}")

print("\n=== ④ 사유 있는 reveal ===")
res = post(
    f"http://localhost:8000/api/interfaces/{target_id}/reveal-secret",
    {"reason": "운영 사고 분석을 위해 KIDI 시크릿 확인"},
    t,
)
print(f"interface={res['interface_name']} secret={res['secret']}")

print("\n=== ⑤ ADMIN 외 reveal → 403 기대 ===")
viewer = login("viewer", "view1234")
try:
    post(
        f"http://localhost:8000/api/interfaces/{target_id}/reveal-secret",
        {"reason": "확인용"},
        viewer,
    )
    print("FAIL")
except urllib.error.HTTPError as e:
    print(f"OK — HTTP {e.code}")