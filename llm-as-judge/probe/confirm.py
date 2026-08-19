import json, uuid, httpx
c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=30)
login = c.post("/api/auth/login", json={"username":"zhangsan","password":"123456"}).json()
h = {"Authorization": f"Bearer {login['access_token']}"}
conv = f"probe-{uuid.uuid4().hex[:8]}"
body = {"text": "我家卫生间水管漏水了，挺严重的，帮忙报修", "house_id": login["current_house_id"]}
d = c.post(f"/api/agent/conversations/{conv}/messages", headers=h, json=body).json()["data"]
card = d.get("pending_confirmation")
print("card tool:", card and card["tool"])
r = c.post(f"/api/agent/conversations/{conv}/confirmations", headers=h,
           json={"confirmed": True, "action_hash": card["action_hash"]}).json()
d2 = r["data"]
print("after confirm reply:", d2.get("reply")[:400])
print("facts:", json.dumps(d2.get("facts"), ensure_ascii=False)[:300])
print("error:", d2.get("error"))
