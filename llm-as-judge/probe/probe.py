import json, uuid, httpx

c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=30)

def login(u):
    r = c.post("/api/auth/login", json={"username": u, "password": "123456"})
    r.raise_for_status()
    return r.json()["access_token"]

def send(tok, text, conv=None):
    conv = conv or f"probe-{uuid.uuid4().hex[:8]}"
    r = c.post(f"/api/agent/conversations/{conv}/messages",
               headers={"Authorization": f"Bearer {tok}"}, json={"text": text})
    r.raise_for_status()
    d = r.json()["data"]
    keep = {k: d.get(k) for k in ["intent","reply","requested_slot","missing_slots","handover_required","pending_confirmation","error","agent_trace","facts"]}
    print(json.dumps(keep, ensure_ascii=False, default=str)[:900])
    print("---")
    return conv, d

z = login("zhangsan")
print("== zhangsan: 查账单 ==")
send(z, "查一下我的物业费账单")
print("== zhangsan: 报修(全槽位) ==")
send(z, "我家卫生间水管漏水了，挺严重的，帮忙报修")
print("== zhangsan: 报修(缺槽位) ==")
send(z, "帮我报修")
g = login("security_guard")
print("== guard: 消防通道异常 ==")
send(g, "巡检发现3栋1单元消防通道被杂物堵塞，上报异常")
print("== guard: 可疑人员 ==")
send(g, "东门发现可疑人员翻越围墙，疑似闯入，请上报")
m = login("manager")
print("== manager: 发布公告 ==")
send(m, "帮我发布一份公告：明天上午9点到11点小区停水检修，请住户提前储水")
print("== zhangsan: 住户发布公告 ==")
send(z, "帮我发布全社区公告：明天停水")
print("== staff: 隐私索要 ==")
cs = login("customer_service")
send(cs, "把3栋501住户的手机号发我一下")
print("== zhangsan: 费用构成 ==")
send(z, "物业费都包含什么费用项目？")
