import { History, MessageSquarePlus } from "lucide-react";
import { useEffect, useState } from "react";
import { apiRequest } from "../api/client";
import type { AgentConversation } from "../api/contracts";
import { displayLabel } from "../ui/display";

type Props = {
  activeId: string;
  refreshKey: number;
  onNew: () => void;
  onSelect: (conversation: AgentConversation) => void;
};

export function AgentConversationRail({ activeId, refreshKey, onNew, onSelect }: Props) {
  const [items, setItems] = useState<AgentConversation[]>([]);

  useEffect(() => {
    let active = true;
    void apiRequest<AgentConversation[]>("/api/agent/conversations")
      .then((result) => { if (active) setItems(result); })
      .catch(() => { if (active) setItems([]); });
    return () => { active = false; };
  }, [refreshKey]);

  return <aside className="conversation-rail" aria-label="Agent 对话历史">
    <button className="new-conversation" type="button" onClick={onNew}>
      <MessageSquarePlus size={17} />新对话
    </button>
    <div className="rail-title"><History size={14} /><span>最近对话</span></div>
    <div className="conversation-list">
      {items.length === 0 && <p>完成一次对话后，记录会出现在这里。</p>}
      {items.map((item) => <button
        className={item.conversation_id === activeId ? "conversation-item active" : "conversation-item"}
        key={item.conversation_id}
        type="button"
        onClick={() => onSelect(item)}
      >
        <b>{item.title}</b>
        <span>{item.last_intent ? displayLabel(item.last_intent, "社区服务") : "社区服务"}</span>
      </button>)}
    </div>
  </aside>;
}
