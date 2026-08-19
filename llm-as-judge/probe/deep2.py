import json, uuid, httpx
c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=30)
r = c.post("/api/auth/login", json={"username":"zhangsan","password":"123456"})
login = r.json()
print("current_house_id:", login.get("current_house_id"), "houses:", login.get("house_ids"))
tok = login["access_token"]
r = c.post(f"/api/agent/conversations/deep-{uuid.uuid4().hex[:6]}/messages",
           headers={"Authorization": f"Bearer {tok}"},
           json={"text": "查一下我的物业费账单", "house_id": login.get("current_house_id")})
d = r.json()["data"]
print("intent:", d.get("intent"))
print("reply:", d.get("reply")[:600])
print("facts:", json.dumps(d.get("facts"), ensure_ascii=False)[:600])
