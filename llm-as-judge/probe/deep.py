import json, uuid, httpx
c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=30)
r = c.post("/api/auth/login", json={"username":"zhangsan","password":"123456"})
tok = r.json()["access_token"]
r = c.post(f"/api/agent/conversations/deep-{uuid.uuid4().hex[:6]}/messages",
           headers={"Authorization": f"Bearer {tok}"}, json={"text": "查一下我的物业费账单"})
d = r.json()["data"]
trace = d.get("agent_trace") or {}
for e in trace.get("events", []):
    print(json.dumps(e, ensure_ascii=False)[:260])
print("status:", trace.get("status"), "finish:", trace.get("finish_reason"))
print("reply:", d.get("reply"))
print("facts:", json.dumps(d.get("facts"), ensure_ascii=False)[:400])
