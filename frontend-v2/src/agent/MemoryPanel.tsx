import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError } from "../api/client";
import { useSession } from "../auth/useSession";
import { Button, Card, EmptyState, Field, InlineAlert, Input } from "../shared/ui";
import type { AgentMemory, MemoryType } from "./models";
import { useAgentKey, useAgentService } from "./hooks";
import styles from "../styles/agent-real.module.css";
import { useAgentRuntime } from "./runtimeDefinition";

const memoryLabels: Record<MemoryType, string> = {
  PREFERENCE: "偏好",
  COMMUNICATION: "沟通",
  ACCESSIBILITY: "无障碍",
  SERVICE_NOTE: "服务备注",
};

export function MemoryPanel({ conversationId }: { conversationId: string | null }) {
  const service = useAgentService();
  const runtime = useAgentRuntime();
  const queryClient = useQueryClient();
  const { session } = useSession();
  const key = useAgentKey("memories");
  const memories = useQuery({ queryKey: key, queryFn: ({ signal }) => service.listMemories(signal) });
  const [content, setContent] = useState("");
  const [type, setType] = useState<MemoryType>("PREFERENCE");
  const [houseId, setHouseId] = useState("");
  const [conflict, setConflict] = useState("");

  const refreshAfter = async () => queryClient.invalidateQueries({ queryKey: key });
  const scopedWrite = async <T,>(operation: (signal: AbortSignal) => Promise<T>) => {
    const controller = runtime.createController();
    try {
      return await operation(controller.signal);
    } finally {
      runtime.releaseController(controller);
    }
  };
  const handleConflict = async (error: Error) => {
    if (error instanceof ApiError && error.kind === "conflict") {
      setConflict("记忆已被其他操作更新，已重新加载；请基于最新内容重新操作。");
      await refreshAfter();
    }
  };
  const create = useMutation({
    mutationFn: () => scopedWrite((signal) => service.createMemory({
      memory_type: type,
      content: content.trim(),
      house_id: houseId || null,
      source_conversation_id: conversationId,
    }, signal)),
    onSuccess: async () => { setContent(""); setConflict(""); await refreshAfter(); },
  });
  const update = useMutation({
    mutationFn: ({ memory, next }: { memory: AgentMemory; next: string }) =>
      scopedWrite((signal) => service.updateMemory(memory.id, { content: next, expected_version: memory.version }, signal)),
    onSuccess: refreshAfter,
    onError: handleConflict,
  });
  const remove = useMutation({
    mutationFn: (memory: AgentMemory) => scopedWrite((signal) => service.deleteMemory(memory.id, { expected_version: memory.version }, signal)),
    onSuccess: refreshAfter,
    onError: handleConflict,
  });

  if (session.status !== "authenticated") return null;
  return <section className={styles.memory} aria-labelledby="memory-heading">
    <h2 id="memory-heading">长期记忆</h2>
    <p>这些内容由真实 Memory API 保存，不包含模型向量或内部检索信息。</p>
    {conflict ? <InlineAlert>{conflict}</InlineAlert> : null}
    <div className={styles.memoryForm}>
      <Field label="类型"><select value={type} onChange={(event) => setType(event.target.value as MemoryType)}>{Object.entries(memoryLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field>
      <Field label="房屋范围"><select value={houseId} onChange={(event) => setHouseId(event.target.value)}><option value="">不限房屋</option>{session.houses.map((house) => <option key={house.id} value={house.id}>{house.label}</option>)}</select></Field>
      <Field label="内容"><Input maxLength={500} value={content} onChange={(event) => setContent(event.target.value)} /></Field>
      <Button tone="primary" disabled={!content.trim() || create.isPending} onClick={() => create.mutate()}>保存记忆</Button>
    </div>
    {create.error ? <InlineAlert>记忆保存失败，请核对内容后重试。</InlineAlert> : null}
    <div className={styles.memoryList}>
      {memories.data?.map((memory) => <MemoryRow key={memory.id} memory={memory} busy={update.isPending || remove.isPending} onUpdate={(next) => update.mutate({ memory, next })} onDelete={() => remove.mutate(memory)} />)}
      {memories.data?.length === 0 ? <EmptyState title="暂无长期记忆" description="只有明确保存的真实记忆会显示在这里。" /> : null}
    </div>
  </section>;
}

function MemoryRow({ memory, busy, onUpdate, onDelete }: { memory: AgentMemory; busy: boolean; onUpdate(next: string): void; onDelete(): void }) {
  const [editing, setEditing] = useState(false);
  const [next, setNext] = useState(memory.content);
  return <Card><strong>{memoryLabels[memory.memoryType]}</strong><p>{memory.content}</p><small>版本 {memory.version} · {memory.houseId ? `房屋 ${memory.houseId.slice(0, 8)}` : "不限房屋"}{memory.sourceConversationId ? ` · 来源会话 ${memory.sourceConversationId}` : ""}{memory.expiresAt ? ` · 到期 ${memory.expiresAt}` : ""}</small><div className={styles.actions}>{editing ? <><Input maxLength={500} value={next} onChange={(event) => setNext(event.target.value)} /><Button disabled={busy || !next.trim()} onClick={() => { onUpdate(next.trim()); setEditing(false); }}>提交更新</Button></> : <Button disabled={busy} onClick={() => setEditing(true)}>编辑</Button>}<Button tone="danger" disabled={busy} onClick={onDelete}>删除</Button></div></Card>;
}
