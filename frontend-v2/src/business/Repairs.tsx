import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import type { ApiSchemas } from "../api/contracts";
import { hasCapability } from "../auth/capabilities";
import { useSession } from "../auth/useSession";
import { WorkOrderCard } from "../domain/cards";
import { formatDate, labelFor } from "../presentation/format";
import {
  Badge,
  Button,
  Card,
  Field,
  InlineAlert,
  Input,
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
import {
  describeBusinessError,
  newIdempotencyKey,
  useBusinessClient,
  useBusinessKey,
} from "./hooks";
import type { WorkOrder } from "./models";
import { canPresentAction } from "./permissions";

function cardModel(item: WorkOrder) {
  return {
    id: item.id,
    number: item.number,
    title: labelFor(item.category),
    location: item.location,
    status: item.status,
    priority: item.urgency,
    summary: item.description,
    updatedAt: item.updatedAt,
  };
}

function CreateRepairForm({ onCreated }: { onCreated(): void }) {
  const client = useBusinessClient();
  const { session } = useSession();
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (session.status !== "authenticated" || !session.currentHouseId)
    return <InlineAlert>请选择当前房屋后再创建报修。</InlineAlert>;
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const data = new FormData(event.currentTarget);
    const category = String(
      data.get("category"),
    ) as ApiSchemas["RepairCategory"];
    const location = String(data.get("location"));
    const description = String(data.get("description"));
    const urgency = String(data.get("urgency")) as ApiSchemas["Urgency"];
    const parameters = {
      house_id:
        session.status === "authenticated" ? session.currentHouseId : "",
      category,
      location,
      description,
      urgency,
      attachment_ids: [],
    };
    try {
      const confirmation = await client.confirm({
        action: "CREATE_WORK_ORDER",
        parameters,
      });
      await client.createWorkOrder(
        {
          ...parameters,
          house_id: parameters.house_id!,
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
      <h2>创建真实报修</h2>
      <p>确认前请核对问题、位置和紧急程度；房屋来自当前安全会话。</p>
      <form className={styles.form} onSubmit={(event) => void submit(event)}>
        <Field label="问题类别">
          <select className="business-select" name="category" required>
            <option value="WATER_PLUMBING">给排水</option>
            <option value="ELECTRICAL">电气</option>
            <option value="ELEVATOR">电梯</option>
            <option value="OTHER">其他</option>
          </select>
        </Field>
        <Field label="位置">
          <Input name="location" required />
        </Field>
        <Field label="问题描述">
          <Textarea name="description" required />
        </Field>
        <Field label="紧急程度">
          <select className="business-select" name="urgency" required>
            <option value="NORMAL">普通</option>
            <option value="URGENT">紧急</option>
            <option value="HIGH_RISK">高风险（将进入人工接管）</option>
          </select>
        </Field>
        <Button tone="primary" type="submit" disabled={busy}>
          {busy ? "正在确认并创建…" : "审阅并确认创建"}
        </Button>
        <MutationNotice error={error} />
      </form>
    </Card>
  );
}

export function RealRepairsPage() {
  const client = useBusinessClient();
  const { session } = useSession();
  const queryClient = useQueryClient();
  const staffMode =
    session.status === "authenticated" &&
    hasCapability(session.actor.roles, "operations");
  const key = useBusinessKey(staffMode ? "community" : "house", "work-orders", {
    filters: {
      house_id: staffMode
        ? null
        : session.status === "authenticated"
          ? session.currentHouseId
          : null,
      limit: 50,
      offset: 0,
    },
  });
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      client.listWorkOrders(
        {
          house_id: staffMode
            ? null
            : session.status === "authenticated"
              ? session.currentHouseId
              : null,
          limit: 50,
          offset: 0,
        },
        signal,
      ),
    enabled:
      staffMode ||
      (session.status === "authenticated" && Boolean(session.currentHouseId)),
  });
  return (
    <div className={styles.page}>
      <BusinessHeader
        eyebrow={staffMode ? "REPAIR OPERATIONS" : "RESIDENT REPAIRS"}
        title={staffMode ? "工单队列" : "报修与进度"}
        description={
          staffMode
            ? "社区队列、本人分派与服务端允许的操作。"
            : "仅显示当前房屋的真实工单，不跨房屋复用缓存。"
        }
      />
      {!staffMode &&
      session.status === "authenticated" &&
      !session.currentHouseId ? (
        <InlineAlert>请先在顶部选择当前房屋。</InlineAlert>
      ) : null}
      {!staffMode ? (
        <CreateRepairForm
          onCreated={() =>
            void queryClient.invalidateQueries({ queryKey: key })
          }
        />
      ) : null}
      <QueryBoundary
        pending={query.isPending && query.fetchStatus !== "idle"}
        error={query.error}
        empty={!query.data?.items.length}
      >
        <div className={styles.grid}>
          {query.data?.items.map((item) => (
            <Link key={item.id} to={`/repairs/${item.id}`}>
              <WorkOrderCard value={cardModel(item)} />
            </Link>
          ))}
        </div>
      </QueryBoundary>
    </div>
  );
}

const repairSpecs: Record<
  string,
  Omit<ActionSpec, "code"> & {
    action?: Parameters<
      ReturnType<typeof useBusinessClient>["workOrderAction"]
    >[1];
  }
> = {
  ASSIGN: { label: "派单", action: "assign" },
  ACCEPT: { label: "接单", action: "accept" },
  REJECT: {
    label: "拒绝",
    action: "reject",
    destructive: true,
    fields: [{ name: "reason", label: "拒绝原因", required: true }],
  },
  RECORD_PROGRESS: {
    label: "记录进度",
    action: "record-progress",
    fields: [{ name: "note", label: "进度说明", required: true }],
  },
  SUBMIT_COMPLETION: {
    label: "提交完工",
    action: "submit-completion",
    fields: [{ name: "note", label: "完工说明", required: true }],
  },
  SUBMIT_REWORK_COMPLETION: {
    label: "提交返工完工",
    action: "submit-completion",
    fields: [{ name: "note", label: "返工完工说明", required: true }],
  },
  VERIFY_PASS: { label: "验收通过", action: "verify-pass" },
  REQUEST_REWORK: {
    label: "要求返工",
    action: "request-rework",
    destructive: true,
    fields: [{ name: "reason", label: "返工原因", required: true }],
  },
  CREATE_REVIEW: {
    label: "服务评价",
    fields: [
      { name: "rating", label: "评分（1-5）", kind: "number", required: true },
      { name: "comment", label: "评价内容" },
    ],
  },
};

export function RealRepairDetailPage() {
  const { id = "" } = useParams();
  const client = useBusinessClient();
  const queryClient = useQueryClient();
  const { session } = useSession();
  const staffMode =
    session.status === "authenticated" &&
    hasCapability(session.actor.roles, "operations");
  const detailKey = useBusinessKey(
    staffMode ? "community" : "house",
    "work-order",
    { resourceId: id },
  );
  const listKey = useBusinessKey(
    staffMode ? "community" : "house",
    "work-orders",
  );
  const detail = useQuery({
    queryKey: detailKey,
    queryFn: ({ signal }) => client.getWorkOrder(id, signal),
    enabled: Boolean(id),
  });
  const timeline = useQuery({
    queryKey: useBusinessKey(
      staffMode ? "community" : "house",
      "work-order-timeline",
      { resourceId: id },
    ),
    queryFn: ({ signal }) => client.getWorkOrderTimeline(id, signal),
    enabled: Boolean(id),
  });
  const staff = useQuery({
    queryKey: useBusinessKey("community", "staff", {
      filters: { role: "REPAIR_WORKER" },
    }),
    queryFn: ({ signal }) => client.listStaff("REPAIR_WORKER", signal),
  });
  const mutation = useMutation({
    mutationFn: async ({
      code,
      values,
    }: {
      code: string;
      values: Record<string, string>;
    }) => {
      const item = detail.data!;
      const spec = repairSpecs[code];
      const version = item.version;
      if (code === "CREATE_REVIEW")
        return client.reviewWorkOrder(
          id,
          { rating: Number(values.rating), comment: values.comment || null },
          newIdempotencyKey(),
        );
      let body:
        | ApiSchemas["AssignRequest"]
        | ApiSchemas["VersionedActionRequest"]
        | ApiSchemas["RejectRequest"]
        | ApiSchemas["ProgressRequest"]
        | ApiSchemas["CompletionRequest"]
        | ApiSchemas["ReworkRequest"];
      if (code === "ASSIGN")
        body = { expected_version: version, assignee_id: values.assignee_id };
      else if (code === "REJECT" || code === "REQUEST_REWORK")
        body = { expected_version: version, reason: values.reason };
      else if (code === "RECORD_PROGRESS")
        body = {
          expected_version: version,
          note: values.note,
          record_type: "PROGRESS",
          attachment_ids: [],
        };
      else if (
        code === "SUBMIT_COMPLETION" ||
        code === "SUBMIT_REWORK_COMPLETION"
      )
        body = {
          expected_version: version,
          note: values.note,
          attachment_ids: [],
        };
      else body = { expected_version: version };
      return client.workOrderAction(
        id,
        spec.action!,
        body,
        newIdempotencyKey(),
      );
    },
    retry: false,
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: detailKey }),
        queryClient.invalidateQueries({ queryKey: listKey }),
      ]);
    },
  });
  const item = detail.data;
  const roles = session.status === "authenticated" ? session.actor.roles : [];
  const actions =
    item?.availableActions
      .filter(
        (code) =>
          code in repairSpecs && canPresentAction("repair", code, roles),
      )
      .map((code) => ({
        code,
        ...repairSpecs[code],
        fields:
          code === "ASSIGN"
            ? [
                {
                  name: "assignee_id",
                  label: "维修人员",
                  required: true,
                  choices: (staff.data ?? []).map((person) => ({
                    value: person.id,
                    label: person.display_name,
                  })),
                },
              ]
            : repairSpecs[code].fields,
      })) ?? [];
  return (
    <div className={styles.page}>
      <BusinessHeader
        eyebrow="WORK ORDER"
        title={item?.number ?? "工单详情"}
        description="状态、时间线与操作均来自真实工单服务。"
      />
      <QueryBoundary
        pending={detail.isPending}
        error={detail.error}
        empty={!item}
      >
        {item ? (
          <div className={styles.split}>
            <div className={styles.stack}>
              <Card>
                <div className={styles.actions}>
                  <Badge>{labelFor(item.status)}</Badge>
                  <Badge>{labelFor(item.urgency)}</Badge>
                </div>
                <h2>{labelFor(item.category)}</h2>
                <p>{item.description}</p>
                <DetailGrid
                  entries={[
                    ["位置", item.location],
                    ["房屋", item.houseId],
                    ["处理人", item.assigneeId],
                    ["版本", item.version],
                    ["更新时间", formatDate(item.updatedAt)],
                  ]}
                />
              </Card>
              <Card>
                <h2>状态时间线</h2>
                <QueryBoundary
                  pending={timeline.isPending}
                  error={timeline.error}
                  empty={!timeline.data?.length}
                >
                  <ol className={styles.timeline}>
                    {timeline.data?.map((entry) => (
                      <li key={entry.id}>
                        <strong>{labelFor(entry.action)}</strong>
                        <p>
                          {entry.note ??
                            entry.reason ??
                            `${entry.fromStatus ?? "开始"} → ${entry.toStatus ?? "当前"}`}
                        </p>
                        <small>
                          {entry.createdAt ? formatDate(entry.createdAt) : ""}
                        </small>
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
              onSubmit={async (code, values) => {
                try {
                  await mutation.mutateAsync({ code, values });
                } catch {
                  /* displayed by MutationNotice */
                }
              }}
            />
          </div>
        ) : null}
      </QueryBoundary>
      {mutation.error ? (
        <InlineAlert>{describeBusinessError(mutation.error)}</InlineAlert>
      ) : null}
    </div>
  );
}
