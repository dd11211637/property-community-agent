import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { hasCapability } from "../auth/capabilities";
import { useSession } from "../auth/useSession";
import { formatDate, labelFor } from "../presentation/format";
import { Badge, Button, Card, InlineAlert } from "../shared/ui";
import styles from "../styles/business.module.css";
import { BusinessHeader, QueryBoundary } from "./BusinessUi";
import {
  describeBusinessError,
  newIdempotencyKey,
  useBusinessClient,
  useBusinessKey,
} from "./hooks";
import { ResidentAgentEntry } from "../agent/AgentWorkspace";
import type { BusinessRecord, PlatformMessage } from "./models";

function relatedPath(message: PlatformMessage): string | null {
  if (!message.resourceId) return null;
  if (message.businessType === "REPAIR")
    return `/repairs/${message.resourceId}`;
  if (message.businessType === "ANNOUNCEMENT")
    return `/community/announcements/${message.resourceId}`;
  if (message.businessType === "BILLING")
    return `/billing/consultations/${message.resourceId}`;
  if (message.businessType === "INSPECTION")
    return `/operations/inspections/${message.resourceId}`;
  if (message.businessType === "SECURITY")
    return `/operations/security/${message.resourceId}`;
  return null;
}

export function RealMessagesPage() {
  const client = useBusinessClient();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("");
  const [businessType, setBusinessType] = useState("");
  const key = useBusinessKey("actor", "messages", {
    filters: { status, business_type: businessType, limit: 50, offset: 0 },
  });
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      client.listMessages(
        {
          status: status || undefined,
          business_type: businessType || undefined,
          limit: 50,
          offset: 0,
        },
        signal,
      ),
  });
  const markOne = useMutation({
    mutationFn: (id: string) => client.markMessageRead(id, newIdempotencyKey()),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }),
  });
  const markAll = useMutation({
    mutationFn: () => client.markAllMessagesRead(newIdempotencyKey()),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }),
  });
  return (
    <div className={styles.page}>
      <BusinessHeader
        eyebrow="UNIFIED INBOX"
        title="消息中心"
        description="真实消息记录，不假装为聊天会话；列表严格遵循后端 recipient 隔离。"
        actions={
          <Button onClick={() => markAll.mutate()} disabled={markAll.isPending}>
            全部标为已读
          </Button>
        }
      />
      <div className={styles.filters}>
        <label>
          阅读状态
          <select
            className="business-select"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="">全部</option>
            <option value="UNREAD">未读</option>
            <option value="READ">已读</option>
            <option value="FAILED">失败</option>
          </select>
        </label>
        <label>
          业务类型
          <select
            className="business-select"
            value={businessType}
            onChange={(event) => setBusinessType(event.target.value)}
          >
            <option value="">全部</option>
            <option value="REPAIR">报修</option>
            <option value="BILLING">财务</option>
            <option value="ANNOUNCEMENT">公告</option>
            <option value="INSPECTION">巡检/安防</option>
          </select>
        </label>
      </div>
      {markOne.error || markAll.error ? (
        <InlineAlert>
          {describeBusinessError(markOne.error ?? markAll.error)}
        </InlineAlert>
      ) : null}
      <QueryBoundary
        pending={query.isPending}
        error={query.error}
        empty={!query.data?.items.length}
      >
        <div className={styles.stack}>
          {query.data?.items.map((message) => {
            const link = relatedPath(message);
            return (
              <Card key={message.id} className={styles.record}>
                <div className={styles.actions}>
                  <Badge tone={message.isRead ? "neutral" : "info"}>
                    {message.isRead ? "已读" : "未读"}
                  </Badge>
                  {message.businessType ? (
                    <Badge>{labelFor(message.businessType)}</Badge>
                  ) : null}
                  {message.handoverRequired ? (
                    <Badge tone="warning">需要人工接管</Badge>
                  ) : null}
                </div>
                <h3>{message.title}</h3>
                <p>{message.content}</p>
                {message.failureReason ? (
                  <InlineAlert>
                    投递失败：{message.failureReason}；已重试{" "}
                    {message.retryCount} 次。
                  </InlineAlert>
                ) : null}
                <div className={styles.actions}>
                  <small>
                    {message.createdAt ? formatDate(message.createdAt) : ""}
                  </small>
                  {!message.isRead ? (
                    <Button onClick={() => markOne.mutate(message.id)}>
                      标为已读
                    </Button>
                  ) : null}
                  {link ? (
                    <Button>
                      <Link to={link}>查看关联业务</Link>
                    </Button>
                  ) : null}
                </div>
              </Card>
            );
          })}
        </div>
      </QueryBoundary>
    </div>
  );
}

function value(record: BusinessRecord, ...keys: string[]): string {
  for (const key of keys) {
    const candidate = record[key];
    if (typeof candidate === "string" || typeof candidate === "number")
      return String(candidate);
  }
  return "—";
}
function adminLink(record: BusinessRecord): string | null {
  const id = value(record, "resource_id", "id");
  const type = value(record, "resource_type", "business_type", "type");
  if (id === "—") return null;
  if (type.includes("CONSULTATION")) return `/billing/consultations/${id}`;
  if (type.includes("WORK_ORDER") || type === "REPAIR") return `/repairs/${id}`;
  if (type.includes("SECURITY")) return `/operations/security/${id}`;
  if (type.includes("INSPECTION")) return `/operations/inspections/${id}`;
  if (type.includes("ANNOUNCEMENT")) return `/community/announcements/${id}`;
  return null;
}

function AdminRecords({
  title,
  records,
}: {
  title: string;
  records: BusinessRecord[];
}) {
  return (
    <Card>
      <h2>{title}</h2>
      {records.length ? (
        <div className={styles.stack}>
          {records.map((record, index) => {
            const link = adminLink(record);
            return (
              <div
                key={`${value(record, "id", "resource_id")}-${index}`}
                className={styles.record}
              >
                <div className={styles.actions}>
                  <Badge>
                    {labelFor(value(record, "status", "state", "risk_level"))}
                  </Badge>
                  <strong>
                    {value(
                      record,
                      "title",
                      "subject",
                      "business_no",
                      "event_type",
                      "resource_type",
                    )}
                  </strong>
                </div>
                <p>
                  {value(
                    record,
                    "message",
                    "description",
                    "failure_reason",
                    "last_error",
                  )}
                </p>
                {link ? <Link to={link}>进入真实业务详情</Link> : null}
              </div>
            );
          })}
        </div>
      ) : (
        <p>当前没有记录。</p>
      )}
    </Card>
  );
}

export function RealAdminPage() {
  const client = useBusinessClient();
  const key = useBusinessKey("community", "admin-dashboard");
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => client.getAdminDashboard(signal),
  });
  const data = query.data;
  return (
    <div className={styles.page}>
      <BusinessHeader
        eyebrow="ADMIN"
        title="管理工作台"
        description="只读展示真实待办、失败消息、高风险事件和服务状态；不提供后端不存在的 CRUD。"
      />
      <QueryBoundary
        pending={query.isPending}
        error={query.error}
        empty={!data}
      >
        {data ? (
          <>
            <div className={styles.grid}>
              <AdminRecords title="待处理工作" records={data.pending} />
              <AdminRecords title="失败消息" records={data.failedMessages} />
              <AdminRecords title="高风险事件" records={data.highRiskEvents} />
            </div>
            <Card>
              <h2>集成与服务状态</h2>
              <div className={styles.grid}>
                {data.integrationHealth.map((health, index) => (
                  <div
                    key={`${value(health, "name", "service")}-${index}`}
                    className={styles.record}
                  >
                    <strong>
                      {value(health, "name", "service", "component")}
                    </strong>
                    <Badge>{labelFor(value(health, "status", "state"))}</Badge>
                    <p>{value(health, "message", "detail", "description")}</p>
                  </div>
                ))}
              </div>
            </Card>
          </>
        ) : null}
      </QueryBoundary>
    </div>
  );
}

function SummaryCard({
  title,
  count,
  description,
  to,
  error,
}: {
  title: string;
  count?: number;
  description: string;
  to: string;
  error: unknown;
}) {
  return (
    <Card>
      <span className={styles.eyebrow}>{title}</span>
      {error ? (
        <InlineAlert>{describeBusinessError(error)}</InlineAlert>
      ) : (
        <>
          <h2>{count ?? "—"}</h2>
          <p>{description}</p>
        </>
      )}
      <Link to={to}>进入真实业务</Link>
    </Card>
  );
}

export function RealBusinessHomePage() {
  const client = useBusinessClient();
  const { session, sessionNotice } = useSession();
  const authenticated = session.status === "authenticated" ? session : null;
  const operations = authenticated
    ? hasCapability(authenticated.actor.roles, "operations")
    : false;
  const resident = authenticated
    ? hasCapability(authenticated.actor.roles, "resident-experience")
    : false;
  const workKey = useBusinessKey(
    operations ? "community" : "house",
    "work-orders",
    { filters: { limit: 6, offset: 0 } },
  );
  const work = useQuery({
    queryKey: workKey,
    queryFn: ({ signal }) =>
      client.listWorkOrders(
        {
          house_id: operations ? null : authenticated?.currentHouseId,
          limit: 6,
          offset: 0,
        },
        signal,
      ),
    enabled:
      Boolean(authenticated) &&
      (operations || Boolean(authenticated?.currentHouseId)),
  });
  const announcement = useQuery({
    queryKey: useBusinessKey("community", "announcements", {
      filters: { limit: 6, offset: 0 },
    }),
    queryFn: ({ signal }) =>
      client.listAnnouncements({ limit: 6, offset: 0 }, signal),
  });
  const messages = useQuery({
    queryKey: useBusinessKey("actor", "messages", {
      filters: { limit: 6, offset: 0 },
    }),
    queryFn: ({ signal }) =>
      client.listMessages({ limit: 6, offset: 0 }, signal),
  });
  const bills = useQuery({
    queryKey: useBusinessKey("house", "bills"),
    queryFn: ({ signal }) => client.listBills(signal),
    enabled: Boolean(authenticated?.currentHouseId) && resident,
  });
  if (!authenticated) return null;
  return (
    <div className={styles.page}>
      <BusinessHeader
        eyebrow={operations ? "OPERATIONS HOME" : "COMMUNITY HOME"}
        title={`欢迎回来，${authenticated.actor.displayName}`}
        description={`${authenticated.actor.communityName} · 以下摘要均来自真实业务服务，单个服务失败不会转换成 Demo 成功数据。`}
      />
      {sessionNotice ? <InlineAlert>{sessionNotice}</InlineAlert> : null}
      {!authenticated.currentHouseId && authenticated.houses.length ? (
        <InlineAlert>选择当前房屋后可查看房屋账单和居民工单。</InlineAlert>
      ) : null}
      <div className={styles.grid}>
        <SummaryCard
          title="工单"
          count={work.data?.items.length}
          description="当前可见工单"
          to="/repairs"
          error={work.error}
        />
        <SummaryCard
          title="公告"
          count={announcement.data?.items.length}
          description="近期社区公告"
          to="/community"
          error={announcement.error}
        />
        <SummaryCard
          title="未读消息"
          count={messages.data?.items.filter((item) => !item.isRead).length}
          description="当前 Actor 的业务消息"
          to="/messages"
          error={messages.error}
        />
        {resident ? (
          <SummaryCard
            title="账单"
            count={bills.data?.length}
            description="当前房屋账单"
            to="/billing"
            error={bills.error}
          />
        ) : null}
      </div>
      {resident ? <ResidentAgentEntry /> : null}
    </div>
  );
}
