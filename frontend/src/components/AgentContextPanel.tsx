import { Brain, ChevronRight, Home, Plus, Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { apiRequest } from "../api/client";
import type { AgentMemory } from "../api/contracts";
import { useOptionalAuth } from "../auth/AuthContext";

const memoryLabels: Record<AgentMemory["memory_type"], string> = {
  PREFERENCE: "服务偏好",
  COMMUNICATION: "联系偏好",
  ACCESSIBILITY: "无障碍需求",
  SERVICE_NOTE: "服务备注",
};

export function AgentContextPanel() {
  const auth = useOptionalAuth();
  const currentHouse = auth?.session?.houses.find((house) => house.id === auth.session?.current_house_id)?.label;
  const [memories, setMemories] = useState<AgentMemory[]>([]);
  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState("");

  const load = () => apiRequest<AgentMemory[]>("/api/agent/memories")
    .then(setMemories)
    .catch(() => setMemories([]));

  useEffect(() => { void load(); }, []);

  async function addMemory(event: FormEvent) {
    event.preventDefault();
    const value = content.trim();
    if (!value) return;
    await apiRequest<AgentMemory>("/api/agent/memories", {
      method: "POST",
      body: { memory_type: "PREFERENCE", content: value },
    });
    setContent("");
    setEditing(false);
    await load();
  }

  async function removeMemory(item: AgentMemory) {
    await apiRequest(`/api/agent/memories/${item.id}`, {
      method: "DELETE",
      body: { expected_version: item.version },
    });
    await load();
  }

  return <aside className="agent-context-panel" aria-label="Agent 当前上下文">
    <section>
      <div className="context-title"><Home size={15} /><b>当前服务范围</b></div>
      <p>{currentHouse ?? "尚未选择房屋"}</p>
      <small>身份、社区和房屋权限每轮由服务端重新验证。</small>
    </section>
    <section>
      <div className="context-title"><Brain size={15} /><b>Agent 记忆</b><button type="button" aria-label="添加记忆" onClick={() => setEditing((value) => !value)}><Plus size={15} /></button></div>
      {editing && <form className="memory-form" onSubmit={addMemory}>
        <textarea aria-label="要让 Agent 记住的偏好" value={content} onChange={(event) => setContent(event.target.value)} placeholder="例如：上门前请先通过站内消息联系" maxLength={500} />
        <button className="button primary" type="submit">确认记住</button>
      </form>}
      <div className="memory-list">
        {memories.length === 0 && <p>暂未保存长期记忆。只有你确认的内容才会保留。</p>}
        {memories.map((item) => <article key={item.id}>
          <span>{memoryLabels[item.memory_type]}</span>
          <p>{item.content}</p>
          <button type="button" aria-label={`删除记忆：${item.content}`} onClick={() => void removeMemory(item)}><Trash2 size={13} /></button>
        </article>)}
      </div>
    </section>
    <section>
      <div className="context-title"><b>业务记录</b></div>
      <div className="context-links">
        <Link to="/repairs">报修与进度<ChevronRight size={14} /></Link>
        <Link to="/billing">账单与费用<ChevronRight size={14} /></Link>
        <Link to="/announcements">社区公告<ChevronRight size={14} /></Link>
      </div>
    </section>
  </aside>;
}
