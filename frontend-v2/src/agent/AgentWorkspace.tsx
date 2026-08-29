import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Menu, PanelRight, Plus, Send, Square, X } from "lucide-react";
import { useEffect, useReducer, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { hasCapability } from "../auth/capabilities";
import { useSession } from "../auth/useSession";
import { Drawer } from "../shared/overlays";
import { Button, Card, EmptyState, ErrorState, InlineAlert, LoadingState, Textarea } from "../shared/ui";
import { AgentFacts } from "./facts";
import { useAgentKey, useAgentService } from "./hooks";
import { MemoryPanel } from "./MemoryPanel";
import { initialTurnState, turnReducer } from "./reducer";
import { reconcileTrustedFacts } from "./reconcile";
import { useAgentRuntime } from "./runtimeDefinition";
import { ConfirmationCard, HumanHandoffCard } from "./sharedCards";
import type { ConversationSummary } from "./models";
import styles from "../styles/agent-real.module.css";

function safeError(error: unknown): string {
  if (error instanceof ApiError) {
    const request = error.requestId ? `（请求 ${error.requestId}）` : "";
    if (error.kind === "forbidden") return `当前账号无权使用此 Agent 操作，登录仍然有效。${request}`;
    if (error.kind === "rate-limited") return `Agent 当前繁忙，请稍后再试。${request}`;
    if (error.kind === "unavailable") return `Agent 运行时暂不可用，请稍后恢复。${request}`;
    if (error.kind === "network") return "网络中断；正在以会话状态和历史确认本轮结果。";
    if (error.kind === "timeout") return "Agent 流长时间没有事件；正在恢复权威状态。";
    if (error.kind === "cancelled") return "已停止接收本轮结果；后台执行不一定已回滚。";
    return `${error.message}${request}`;
  }
  return "Agent 发生未知错误，请恢复会话状态后重试。";
}

export function AgentWorkspace() {
  const { conversationId = null } = useParams<{ conversationId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const service = useAgentService();
  const runtime = useAgentRuntime();
  const queryClient = useQueryClient();
  const { session, transitioning } = useSession();
  const [turn, dispatch] = useReducer(turnReducer, initialTurnState);
  const initialText = typeof (location.state as { initialText?: unknown } | null)?.initialText === "string"
    ? (location.state as { initialText: string }).initialText
    : "";
  const [draft, setDraft] = useState(initialText);
  const [compatibilityMode, setCompatibilityMode] = useState(false);
  const [selectedSlotValue, setSelectedSlotValue] = useState<unknown>(undefined);
  const [confirming, setConfirming] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const active = useRef<AbortController | null>(null);
  const autoSubmitted = useRef(false);
  const previousConversation = useRef(conversationId);

  const listKey = useAgentKey("conversations", { limit: 50 });
  const statusKey = useAgentKey("conversation-status", { conversationId });
  const list = useQuery({ queryKey: listKey, queryFn: ({ signal }) => service.listConversations(50, signal) });
  const status = useQuery({
    queryKey: statusKey,
    queryFn: ({ signal }) => service.getConversation(conversationId!, signal),
    enabled: Boolean(conversationId),
    retry: false,
  });
  const historyKey = useAgentKey("conversation-history", {
    conversationId,
    houseId: status.data?.currentHouseId ?? null,
  });
  const history = useQuery({
    queryKey: historyKey,
    queryFn: ({ signal }) => service.listMessages(conversationId!, signal),
    enabled: Boolean(conversationId && status.data),
  });

  useEffect(() => {
    dispatch({ type: "restore-confirmation", confirmation: status.data?.pendingConfirmation ?? null });
  }, [status.data?.pendingConfirmation]);

  useEffect(() => {
    if (session.status !== "authenticated" || !conversationId || !status.data) return;
    const authoritative = status.data.currentHouseId;
    if (authoritative && authoritative !== session.currentHouseId) {
      runtime.abortAll();
      navigate("/agent", { replace: true, state: { scopeNotice: "该会话属于另一房屋，已停止显示；请选择对应房屋或新建会话。" } });
    }
  }, [conversationId, navigate, runtime, session, status.data]);

  useEffect(() => {
    if (previousConversation.current && previousConversation.current !== conversationId)
      dispatch({ type: "reset" });
    previousConversation.current = conversationId;
  }, [conversationId]);

  useEffect(() => {
    if (!conversationId) return;
    return () => active.current?.abort();
  }, [conversationId]);

  const refreshAuthority = async (id: string) => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: listKey }),
      queryClient.invalidateQueries({ queryKey: statusKey }),
      queryClient.invalidateQueries({ queryKey: historyKey }),
    ]);
    const recovered = await service.getConversation(id).catch(() => null);
    dispatch({ type: "restore-confirmation", confirmation: recovered?.pendingConfirmation ?? null });
  };

  const submit = async () => {
    const text = draft.trim();
    if (!text || session.status !== "authenticated") return;
    const id = conversationId ?? crypto.randomUUID();
    if (!conversationId) navigate(`/agent/conversations/${id}`);
    const controller = runtime.createController();
    active.current = controller;
    setStreaming(true);
    dispatch({ type: "submit" });
    setDraft("");
    try {
      const body = {
        text,
        house_id: session.currentHouseId,
        slots: turn.requestedSlot ? { [turn.requestedSlot]: selectedSlotValue ?? text } : null,
      };
      if (compatibilityMode) {
        const result = await service.sendMessage(id, body, controller.signal);
        dispatch({ type: "turn", turn: result });
        await reconcileTrustedFacts(queryClient, result.facts);
      } else {
        let terminal = false;
        for await (const event of service.streamMessage(id, body, controller.signal)) {
          dispatch({ type: "event", event });
          if (event.event === "facts") await reconcileTrustedFacts(queryClient, event.data.facts);
          if (event.event === "turn" && event.data.facts) await reconcileTrustedFacts(queryClient, event.data.facts);
          if (event.event === "done" || event.event === "failed") terminal = true;
        }
        if (!terminal) {
          dispatch({ type: "fail", message: "流连接已结束，正在核对权威会话状态。", uncertain: true });
          await refreshAuthority(id);
        }
      }
      await refreshAuthority(id);
      setSelectedSlotValue(undefined);
    } catch (error) {
      if (error instanceof ApiError && error.kind === "cancelled") dispatch({ type: "cancel" });
      else dispatch({ type: "fail", message: safeError(error), uncertain: true });
      await refreshAuthority(id);
    } finally {
      runtime.releaseController(controller);
      if (active.current === controller) active.current = null;
      setStreaming(false);
    }
  };

  useEffect(() => {
    const autoSubmit = (location.state as { autoSubmit?: unknown } | null)?.autoSubmit === true;
    if (autoSubmit && initialText && !autoSubmitted.current) {
      autoSubmitted.current = true;
      navigate(location.pathname, { replace: true });
      void submit();
    }
  });

  const confirm = async (confirmed: boolean) => {
    if (!conversationId || !turn.confirmation || confirming) return;
    setConfirming(true);
    const controller = runtime.createController();
    try {
      const result = await service.confirm(conversationId, {
        confirmed,
        action_hash: confirmed ? turn.confirmation.actionHash : null,
      }, controller.signal);
      if (confirmed) {
        dispatch({ type: "turn", turn: result });
        await reconcileTrustedFacts(queryClient, result.facts);
      } else dispatch({ type: "cancel" });
      await refreshAuthority(conversationId);
    } catch (error) {
      if (error instanceof ApiError && error.kind === "conflict") {
        dispatch({ type: "restore-confirmation", confirmation: null });
        dispatch({ type: "fail", message: "确认参数已变化，已加载最新状态；请重新审阅后操作。" });
        await refreshAuthority(conversationId);
      } else dispatch({ type: "fail", message: safeError(error) });
    } finally {
      runtime.releaseController(controller);
      setConfirming(false);
    }
  };

  const close = async () => {
    if (!conversationId) return;
    runtime.abortAll();
    const controller = runtime.createController();
    try {
      await service.closeConversation(conversationId, controller.signal);
    } catch (error) {
      dispatch({ type: "fail", message: safeError(error) });
      return;
    } finally {
      runtime.releaseController(controller);
    }
    queryClient.removeQueries({ queryKey: statusKey });
    queryClient.removeQueries({ queryKey: historyKey });
    await queryClient.invalidateQueries({ queryKey: listKey });
    navigate("/agent");
  };

  if (session.status !== "authenticated") return null;
  const canUseAgent = hasCapability(session.actor.roles, "resident-experience") || hasCapability(session.actor.roles, "operations");
  if (!canUseAgent) return <ErrorState title="无 Agent 权限" description="当前角色没有已知产品能力，未授予 Agent 访问。" />;
  return <div className={styles.workspace}>
    <aside className={styles.rail}><ConversationRail items={list.data ?? []} selected={conversationId} loading={list.isLoading} error={list.error} onSelect={(id) => navigate(`/agent/conversations/${id}`)} onNew={() => navigate("/agent")} /></aside>
    <main className={styles.conversation}>
      <header className={styles.workspaceHeader}>
        <div className={styles.mobileTools}><Drawer title="对话列表" trigger={<Button iconOnly aria-label="打开对话列表"><Menu /></Button>}><ConversationRail items={list.data ?? []} selected={conversationId} loading={list.isLoading} error={list.error} onSelect={(id) => navigate(`/agent/conversations/${id}`)} onNew={() => navigate("/agent")} /></Drawer></div>
        <div><span>REAL AGENT WORKSPACE</span><h1>{status.data ? status.data.status : "新对话"}</h1></div>
        <div className={styles.actions}>{conversationId ? <Button tone="ghost" onClick={() => void close()}><X size={16} />关闭会话</Button> : null}<div className={styles.mobileTools}><Drawer title="上下文与记忆" trigger={<Button iconOnly aria-label="打开上下文面板"><PanelRight /></Button>}><MemoryPanel conversationId={conversationId} /></Drawer></div></div>
      </header>
      {transitioning ? <InlineAlert>房屋上下文切换中，Agent 提交暂时停用。</InlineAlert> : null}
      {status.isLoading && conversationId ? <LoadingState label="恢复会话状态" /> : null}
      {status.error ? <ErrorState description={safeError(status.error)} /> : null}
      <section className={styles.transcript} aria-label="Agent 对话历史">
        {!conversationId ? <EmptyState title="开始真实 Agent 对话" description="会话 ID 将在首次提交时生成，并通过地址保持稳定。" /> : null}
        {history.data?.map((message) => <article key={message.id} className={`${styles.message} ${styles[message.role]}`}><strong>{message.role === "user" ? "你" : message.role === "assistant" ? "Agent" : "系统"}</strong><p>{message.content}</p>{message.createdAt ? <time>{message.createdAt}</time> : null}</article>)}
        {turn.reply ? <article className={`${styles.message} ${styles.assistant}`}><strong>Agent</strong><p>{turn.reply}</p></article> : null}
        <AgentFacts facts={turn.facts} />
        {turn.confirmation ? <ConfirmationCard value={turn.confirmation} busy={confirming} onConfirm={() => void confirm(true)} onCancel={() => void confirm(false)} /> : null}
        {turn.phase === "handed-over" || status.data?.handoverRequired ? <HumanHandoffCard ticketId={turn.handoverTicketId ?? status.data?.handoverTicketId ?? null} /> : null}
        {turn.error ? <InlineAlert>{turn.error}</InlineAlert> : null}
        {turn.progress ? <div className={styles.progress} role="status">{turn.progress}</div> : null}
      </section>
      <section className={styles.composer}>
        {turn.phase === "clarifying" ? <><strong>{turn.slotPrompt?.prompt ?? `请补充 ${turn.requestedSlot ?? turn.missingSlots.join("、")}`}</strong>{turn.slotPrompt?.options.length ? <div className={styles.slotOptions}>{turn.slotPrompt.options.map((option) => <Button key={option.label} tone="ghost" onClick={() => { setSelectedSlotValue(option.value); setDraft(option.label); }}>{option.label}</Button>)}</div> : null}</> : null}
        <label><span className="sr-only">发送给 Agent</span><Textarea rows={3} maxLength={2000} value={draft} onChange={(event) => setDraft(event.target.value)} placeholder={turn.phase === "clarifying" ? "补充所需信息" : "描述你的业务需求…"} /></label>
        <div className={styles.composerActions}><label><input type="checkbox" checked={compatibilityMode} onChange={(event) => setCompatibilityMode(event.target.checked)} /> 无流式兼容模式</label>{streaming ? <Button onClick={() => active.current?.abort()}><Square size={15} />停止接收</Button> : <Button tone="primary" disabled={!draft.trim() || transitioning || confirming} onClick={() => void submit()}><Send size={16} />发送</Button>}</div>
      </section>
      <div className="sr-only" aria-live="polite">{turn.phase === "failed" ? turn.error : turn.phase === "handed-over" ? "已转人工处理" : ""}</div>
    </main>
    <aside className={styles.context}><MemoryPanel conversationId={conversationId} /></aside>
  </div>;
}

function ConversationRail({ items, selected, loading, error, onSelect, onNew }: { items: ConversationSummary[]; selected: string | null; loading: boolean; error: unknown; onSelect(id: string): void; onNew(): void }) {
  return <div className={styles.railInner}><div className={styles.railHeader}><strong>真实对话</strong><Button iconOnly tone="ghost" aria-label="新对话" onClick={onNew}><Plus /></Button></div>{loading ? <LoadingState /> : null}{error ? <ErrorState description={safeError(error)} /> : null}{items.map((item) => <button key={item.conversationId} className={`${styles.conversationItem} ${selected === item.conversationId ? styles.selected : ""}`} onClick={() => onSelect(item.conversationId)}><Bot size={17} /><span><strong>{item.title ?? "未命名对话"}</strong><small>{item.status}{item.currentHouseId ? ` · 房屋 ${item.currentHouseId.slice(0, 8)}` : ""}</small></span></button>)}{!loading && !error && !items.length ? <EmptyState title="暂无对话" description="创建第一段真实 Agent 会话。" /> : null}</div>;
}

export function ResidentAgentEntry() {
  const navigate = useNavigate();
  const { session } = useSession();
  const [text, setText] = useState("");
  if (session.status !== "authenticated") return null;
  return <Card className={styles.residentEntry}>
    <div className={styles.cardHeading}><Bot /><div><strong>询问真实社区 Agent</strong><p>业务结果将以可信结构化卡片呈现；自然语言回复本身不代表操作成功。</p></div></div>
    <Textarea rows={3} maxLength={2000} value={text} onChange={(event) => setText(event.target.value)} aria-label="向社区 Agent 提问" placeholder="例如：帮我报修厨房漏水" />
    <Button tone="primary" disabled={!text.trim()} onClick={() => {
      const id = crypto.randomUUID();
      navigate(`/agent/conversations/${id}`, { state: { initialText: text.trim(), autoSubmit: true } });
    }}><Send size={16} />开始对话</Button>
  </Card>;
}
