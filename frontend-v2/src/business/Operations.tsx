import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import type { ApiSchemas } from "../api/contracts";
import { InspectionTaskCard, SecurityEventCard } from "../domain/cards";
import { formatDate, labelFor } from "../presentation/format";
import {
  Badge,
  Button,
  Card,
  Field,
  Input,
  Tabs,
  Textarea,
} from "../shared/ui";
import styles from "../styles/business.module.css";
import {
  ActionWorkbench,
  BusinessHeader,
  DetailGrid,
  MutationNotice,
  QueryBoundary,
  type ActionSpec,
} from "./BusinessUi";
import { newIdempotencyKey, useBusinessClient, useBusinessKey } from "./hooks";
import type { InspectionTask, SecurityEvent } from "./models";
import { canPresentAction } from "./permissions";
import { useSession } from "../auth/useSession";

function inspectionCard(item: InspectionTask) {
  return {
    id: item.id,
    title: item.title,
    assignee: item.assigneeId ?? "待分派",
    status: item.status,
    dueAt: item.dueAt ? formatDate(item.dueAt) : "未设置",
    progress: ["COMPLETED", "CLOSED"].includes(item.status)
      ? 100
      : item.status === "IN_PROGRESS"
        ? 50
        : 10,
  };
}
function securityCard(item: SecurityEvent) {
  return {
    id: item.id,
    title: labelFor(item.eventType),
    location: item.location,
    risk: item.riskLevel,
    status: item.status,
    reportedAt: item.createdAt,
  };
}

function CreateInspection({ onCreated }: { onCreated(): void }) {
  const client = useBusinessClient();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    const data = new FormData(event.currentTarget);
    try {
      await client.createInspection(
        {
          title: String(data.get("title")),
          description: String(data.get("description")),
          route_points: String(data.get("route_points"))
            .split(/[,，]/)
            .map((value) => value.trim())
            .filter(Boolean),
          planned_at: data.get("planned_at")
            ? new Date(String(data.get("planned_at"))).toISOString()
            : null,
          due_at: data.get("due_at")
            ? new Date(String(data.get("due_at"))).toISOString()
            : null,
          attachment_ids: [],
        },
        newIdempotencyKey(),
      );
      event.currentTarget.reset();
      onCreated();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card>
      <h2>创建巡检任务</h2>
      <form className={styles.form} onSubmit={(event) => void submit(event)}>
        <Field label="任务标题">
          <Input name="title" required />
        </Field>
        <Field label="说明">
          <Textarea name="description" required />
        </Field>
        <Field label="路线点（逗号分隔）">
          <Input name="route_points" required />
        </Field>
        <Field label="计划时间">
          <Input name="planned_at" type="datetime-local" />
        </Field>
        <Field label="截止时间">
          <Input name="due_at" type="datetime-local" />
        </Field>
        <Button tone="primary" disabled={busy}>
          创建任务
        </Button>
        <MutationNotice error={error} />
      </form>
    </Card>
  );
}

function CreateSecurity({ onCreated }: { onCreated(): void }) {
  const client = useBusinessClient();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    const data = new FormData(event.currentTarget);
    const event_type = String(
      data.get("event_type"),
    ) as ApiSchemas["EventType"];
    const risk_level = String(
      data.get("risk_level"),
    ) as ApiSchemas["EventRiskLevel"];
    const location = String(data.get("location"));
    const description = String(data.get("description"));
    try {
      const confirmation = await client.confirm({
        action: "SECURITY_EVENT_CREATE",
        parameters: { event_type, risk_level, location },
      });
      await client.createSecurityEvent(
        {
          event_type,
          risk_level,
          location,
          description,
          report_source: "MANUAL",
          source_task_id: null,
          attachment_ids: [],
          confirmation_token: confirmation.token,
        },
        newIdempotencyKey(),
      );
      event.currentTarget.reset();
      onCreated();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card>
      <h2>人工上报安防事件</h2>
      <form className={styles.form} onSubmit={(event) => void submit(event)}>
        <Field label="事件类型">
          <select name="event_type" className="business-select">
            <option value="EQUIPMENT_FAULT">设备故障</option>
            <option value="GAS_LEAK">燃气泄漏</option>
            <option value="FIRE">火情</option>
            <option value="PERSONAL_SAFETY">人身安全</option>
            <option value="OTHER">其他</option>
          </select>
        </Field>
        <Field label="风险等级">
          <select name="risk_level" className="business-select">
            <option value="LOW">低风险</option>
            <option value="MEDIUM">中风险</option>
            <option value="HIGH_RISK">高风险</option>
          </select>
        </Field>
        <Field label="位置">
          <Input name="location" required />
        </Field>
        <Field label="事件描述">
          <Textarea name="description" required />
        </Field>
        <Button tone="primary" disabled={busy}>
          {busy ? "正在确认…" : "审阅并确认上报"}
        </Button>
        <MutationNotice error={error} />
      </form>
    </Card>
  );
}

export function RealOperationsPage() {
  const client = useBusinessClient();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState("inspection");
  const inspectionKey = useBusinessKey("community", "inspections", {
    filters: { limit: 50, offset: 0 },
  });
  const securityKey = useBusinessKey("community", "security-events", {
    filters: { limit: 50, offset: 0 },
  });
  const inspections = useQuery({
    queryKey: inspectionKey,
    queryFn: ({ signal }) =>
      client.listInspections({ limit: 50, offset: 0 }, signal),
  });
  const events = useQuery({
    queryKey: securityKey,
    queryFn: ({ signal }) =>
      client.listSecurityEvents({ limit: 50, offset: 0 }, signal),
  });
  return (
    <div className={styles.page}>
      <BusinessHeader
        eyebrow="OPERATIONS"
        title="巡检与安防"
        description="两个领域保持独立状态机；AI 建议与 CONFIRM_AI 操作在本阶段明确禁用。"
      />
      <Tabs
        items={[
          { id: "inspection", label: "巡检任务" },
          { id: "security", label: "安防事件" },
          { id: "new-inspection", label: "创建巡检" },
          { id: "new-security", label: "上报安防" },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "inspection" ? (
        <QueryBoundary
          pending={inspections.isPending}
          error={inspections.error}
          empty={!inspections.data?.items.length}
        >
          <div className={styles.grid}>
            {inspections.data?.items.map((item) => (
              <Link key={item.id} to={`/operations/inspections/${item.id}`}>
                <InspectionTaskCard value={inspectionCard(item)} />
              </Link>
            ))}
          </div>
        </QueryBoundary>
      ) : null}
      {tab === "security" ? (
        <QueryBoundary
          pending={events.isPending}
          error={events.error}
          empty={!events.data?.items.length}
        >
          <div className={styles.grid}>
            {events.data?.items.map((item) => (
              <Link key={item.id} to={`/operations/security/${item.id}`}>
                <SecurityEventCard value={securityCard(item)} />
              </Link>
            ))}
          </div>
        </QueryBoundary>
      ) : null}
      {tab === "new-inspection" ? (
        <CreateInspection
          onCreated={() => {
            void queryClient.invalidateQueries({ queryKey: inspectionKey });
            setTab("inspection");
          }}
        />
      ) : null}
      {tab === "new-security" ? (
        <CreateSecurity
          onCreated={() => {
            void queryClient.invalidateQueries({ queryKey: securityKey });
            setTab("security");
          }}
        />
      ) : null}
    </div>
  );
}

const inspectionSpecs: Record<string, ActionSpec> = {
  ASSIGN: { code: "ASSIGN", label: "分派" },
  START: { code: "START", label: "开始巡检" },
  ADD_RECORD: {
    code: "ADD_RECORD",
    label: "添加记录",
    fields: [
      { name: "point", label: "巡检点", required: true },
      { name: "note", label: "记录", required: true },
    ],
  },
  SUBMIT_RECORDS: {
    code: "SUBMIT_RECORDS",
    label: "二次确认并提交记录",
    fields: [
      { name: "point", label: "巡检点", required: true },
      { name: "note", label: "提交说明", required: true },
    ],
  },
  COMPLETE: { code: "COMPLETE", label: "复核完成" },
};

export function RealInspectionDetailPage() {
  const { id = "" } = useParams();
  const client = useBusinessClient();
  const queryClient = useQueryClient();
  const { session } = useSession();
  const key = useBusinessKey("community", "inspection", { resourceId: id });
  const listKey = useBusinessKey("community", "inspections");
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => client.getInspection(id, signal),
    enabled: Boolean(id),
  });
  const timeline = useQuery({
    queryKey: useBusinessKey("community", "inspection-timeline", {
      resourceId: id,
    }),
    queryFn: ({ signal }) => client.inspectionTimeline(id, signal),
    enabled: Boolean(id),
  });
  const staff = useQuery({
    queryKey: useBusinessKey("community", "staff", {
      filters: { role: "SECURITY_GUARD" },
    }),
    queryFn: ({ signal }) => client.listStaff("SECURITY_GUARD", signal),
  });
  const mutation = useMutation({
    mutationFn: async ({
      action,
      values,
    }: {
      action: string;
      values: Record<string, string>;
    }) => {
      const version = query.data!.version;
      const keyValue = newIdempotencyKey();
      if (action === "ASSIGN")
        return client.inspectionAction(
          id,
          "assign",
          { expected_version: version, assignee_id: values.assignee_id },
          keyValue,
        );
      if (action === "ADD_RECORD")
        return client.inspectionAction(
          id,
          "add-record",
          {
            expected_version: version,
            point: values.point,
            note: values.note,
            record_type: "POINT_RECORD",
            is_supplement: false,
            attachment_ids: [],
          },
          keyValue,
        );
      if (action === "SUBMIT_RECORDS") {
        const parameters = {
          note: values.note,
          record_type: "COMPLETION",
          point: values.point,
        };
        const confirmation = await client.confirm({
          action: "INSPECTION_TASK_SUBMIT_RECORDS",
          parameters,
        });
        return client.inspectionAction(
          id,
          "submit-records",
          {
            expected_version: version,
            ...parameters,
            confirmation_token: confirmation.token,
            attachment_ids: [],
          },
          keyValue,
        );
      }
      return client.inspectionAction(
        id,
        action === "START" ? "start" : "complete",
        { expected_version: version },
        keyValue,
      );
    },
    retry: false,
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: key }),
        queryClient.invalidateQueries({ queryKey: listKey }),
      ]);
    },
  });
  const roles = session.status === "authenticated" ? session.actor.roles : [];
  const item = query.data;
  const actions =
    item?.availableActions
      .filter(
        (action) =>
          action in inspectionSpecs &&
          canPresentAction("inspection", action, roles),
      )
      .map((action) => ({
        ...inspectionSpecs[action],
        fields:
          action === "ASSIGN"
            ? [
                {
                  name: "assignee_id",
                  label: "安保人员",
                  required: true,
                  choices: (staff.data ?? []).map((person) => ({
                    value: person.id,
                    label: person.display_name,
                  })),
                },
              ]
            : inspectionSpecs[action].fields,
      })) ?? [];
  return (
    <div className={styles.page}>
      <BusinessHeader
        eyebrow="INSPECTION"
        title={item?.title ?? "巡检详情"}
        description="真实巡检记录与操作；AI 建议端点不接入。"
      />
      <QueryBoundary
        pending={query.isPending}
        error={query.error}
        empty={!item}
      >
        {item ? (
          <div className={styles.split}>
            <div className={styles.stack}>
              <Card>
                <div className={styles.actions}>
                  <Badge>{labelFor(item.status)}</Badge>
                  <Badge>v{item.version}</Badge>
                </div>
                <p>{item.description}</p>
                <DetailGrid
                  entries={[
                    ["路线", item.routePoints.join(" → ")],
                    ["处理人", item.assigneeId],
                    [
                      "计划时间",
                      item.plannedAt ? formatDate(item.plannedAt) : "未设置",
                    ],
                    [
                      "截止时间",
                      item.dueAt ? formatDate(item.dueAt) : "未设置",
                    ],
                  ]}
                />
              </Card>
              <Card>
                <h2>时间线</h2>
                <QueryBoundary
                  pending={timeline.isPending}
                  error={timeline.error}
                  empty={!timeline.data?.length}
                >
                  <ol className={styles.timeline}>
                    {timeline.data?.map((entry) => (
                      <li key={entry.id}>
                        <strong>{labelFor(entry.action)}</strong>
                        <p>{entry.note ?? entry.reason ?? "状态已更新"}</p>
                      </li>
                    ))}
                  </ol>
                </QueryBoundary>
              </Card>
            </div>
            <ActionWorkbench
              actions={actions}
              busy={mutation.isPending}
              error={mutation.error}
              onSubmit={async (action, values) => {
                try {
                  await mutation.mutateAsync({ action, values });
                } catch {
                  /* rendered */
                }
              }}
            />
          </div>
        ) : null}
      </QueryBoundary>
    </div>
  );
}

const securitySpecs: Record<string, ActionSpec> = {
  ASSIGN: { code: "ASSIGN", label: "分派" },
  SUBMIT_DISPOSAL: {
    code: "SUBMIT_DISPOSAL",
    label: "提交处置",
    fields: [{ name: "note", label: "处置说明", required: true }],
  },
  GRADE_CONFIRM: { code: "GRADE_CONFIRM", label: "确认风险评级" },
  RETURN: {
    code: "RETURN",
    label: "退回",
    destructive: true,
    fields: [{ name: "note", label: "退回原因", required: true }],
  },
  REVIEW_PASS: { code: "REVIEW_PASS", label: "复核通过" },
};

export function RealSecurityDetailPage() {
  const { id = "" } = useParams();
  const client = useBusinessClient();
  const queryClient = useQueryClient();
  const { session } = useSession();
  const key = useBusinessKey("community", "security-event", { resourceId: id });
  const listKey = useBusinessKey("community", "security-events");
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => client.getSecurityEvent(id, signal),
    enabled: Boolean(id),
  });
  const timeline = useQuery({
    queryKey: useBusinessKey("community", "security-timeline", {
      resourceId: id,
    }),
    queryFn: ({ signal }) => client.securityTimeline(id, signal),
    enabled: Boolean(id),
  });
  const staff = useQuery({
    queryKey: useBusinessKey("community", "staff", {
      filters: { role: "SECURITY_GUARD" },
    }),
    queryFn: ({ signal }) => client.listStaff("SECURITY_GUARD", signal),
  });
  const mutation = useMutation({
    mutationFn: ({
      action,
      values,
    }: {
      action: string;
      values: Record<string, string>;
    }) => {
      const version = query.data!.version;
      const keyValue = newIdempotencyKey();
      if (action === "ASSIGN")
        return client.securityAction(
          id,
          "assign",
          { expected_version: version, assignee_id: values.assignee_id },
          keyValue,
        );
      if (action === "SUBMIT_DISPOSAL")
        return client.securityAction(
          id,
          "submit-disposal",
          { expected_version: version, note: values.note, attachment_ids: [] },
          keyValue,
        );
      if (action === "RETURN")
        return client.securityAction(
          id,
          "return",
          { expected_version: version, note: values.note },
          keyValue,
        );
      return client.securityAction(
        id,
        action === "GRADE_CONFIRM" ? "grade-confirm" : "review-pass",
        { expected_version: version },
        keyValue,
      );
    },
    retry: false,
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: key }),
        queryClient.invalidateQueries({ queryKey: listKey }),
      ]);
    },
  });
  const roles = session.status === "authenticated" ? session.actor.roles : [];
  const item = query.data;
  const actions =
    item?.availableActions
      .filter(
        (action) =>
          action in securitySpecs &&
          canPresentAction("security", action, roles),
      )
      .map((action) => ({
        ...securitySpecs[action],
        fields:
          action === "ASSIGN"
            ? [
                {
                  name: "assignee_id",
                  label: "安保人员",
                  required: true,
                  choices: (staff.data ?? []).map((person) => ({
                    value: person.id,
                    label: person.display_name,
                  })),
                },
              ]
            : securitySpecs[action].fields,
      })) ?? [];
  return (
    <div className={styles.page}>
      <BusinessHeader
        eyebrow="SECURITY"
        title={item?.number ?? "安防详情"}
        description="风险状态使用文字、结构和颜色共同表达。"
      />
      <QueryBoundary
        pending={query.isPending}
        error={query.error}
        empty={!item}
      >
        {item ? (
          <div className={styles.split}>
            <div className={styles.stack}>
              <Card
                className={item.riskLevel === "HIGH_RISK" ? styles.risk : ""}
              >
                <div className={styles.actions}>
                  <Badge>{labelFor(item.status)}</Badge>
                  <Badge
                    tone={
                      item.riskLevel === "HIGH_RISK" ? "dangerTone" : "warning"
                    }
                  >
                    风险：{labelFor(item.riskLevel)}
                  </Badge>
                  <Badge>v{item.version}</Badge>
                </div>
                <h2>{labelFor(item.eventType)}</h2>
                <p>{item.description}</p>
                <DetailGrid
                  entries={[
                    ["位置", item.location],
                    ["处理人", item.assigneeId],
                    [
                      "上报时间",
                      item.createdAt ? formatDate(item.createdAt) : "",
                    ],
                    [
                      "更新时间",
                      item.updatedAt ? formatDate(item.updatedAt) : "",
                    ],
                  ]}
                />
              </Card>
              <Card>
                <h2>处置时间线</h2>
                <QueryBoundary
                  pending={timeline.isPending}
                  error={timeline.error}
                  empty={!timeline.data?.length}
                >
                  <ol className={styles.timeline}>
                    {timeline.data?.map((entry) => (
                      <li key={entry.id}>
                        <strong>{labelFor(entry.action)}</strong>
                        <p>{entry.note ?? entry.reason ?? "状态已更新"}</p>
                      </li>
                    ))}
                  </ol>
                </QueryBoundary>
              </Card>
            </div>
            <ActionWorkbench
              actions={actions}
              busy={mutation.isPending}
              error={mutation.error}
              onSubmit={async (action, values) => {
                try {
                  await mutation.mutateAsync({ action, values });
                } catch {
                  /* rendered */
                }
              }}
            />
          </div>
        ) : null}
      </QueryBoundary>
    </div>
  );
}
