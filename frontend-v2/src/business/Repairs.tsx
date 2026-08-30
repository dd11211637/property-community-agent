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

function AttachmentLinks({ ids, label }: { ids: string[]; label: string }) {
  const client = useBusinessClient();
  const [error, setError] = useState<unknown>(null);
  async function download(id: string, index: number) {
    setError(null);
    try {
      const blob = await client.downloadAttachment(id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${label}-${index + 1}`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError(caught);
    }
  }
  if (!ids.length) return null;
  return (
    <Card>
      <h2>{label}</h2>
      <div className={styles.actions}>
        {ids.map((id, index) => (
          <Button key={id} onClick={() => void download(id, index)}>
            下载附件 {index + 1}
          </Button>
        ))}
      </div>
      <MutationNotice error={error} />
    </Card>
  );
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
    const form = event.currentTarget;
    setBusy(true);
    setError(null);
    const data = new FormData(form);
    const category = String(
      data.get("category"),
    ) as ApiSchemas["RepairCategory"];
    const location = String(data.get("location"));
    const description = String(data.get("description"));
    const urgency = String(data.get("urgency")) as ApiSchemas["Urgency"];
    const selectedFile = data.get("attachment");
    try {
      const attachmentIds =
        selectedFile instanceof File && selectedFile.size > 0
          ? [(await client.uploadAttachment(selectedFile)).id]
          : [];
      const parameters = {
      house_id:
        session.status === "authenticated" ? session.currentHouseId : "",
      category,
      location,
      description,
      urgency,
        contact_name: String(data.get("contact_name") || "") || null,
        contact_phone: String(data.get("contact_phone") || "") || null,
        access_instructions: String(data.get("access_instructions") || "") || null,
        preferred_time_windows: String(data.get("preferred_time_windows") || "")
          .split("、")
          .map((value) => value.trim())
          .filter(Boolean),
        attachment_ids: attachmentIds,
      };
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
      form.reset();
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
        <Field label="联系人">
          <Input name="contact_name" placeholder="选填" />
        </Field>
        <Field label="联系电话">
          <Input name="contact_phone" type="tel" placeholder="选填" />
        </Field>
        <Field label="方便服务时间">
          <Input name="preferred_time_windows" placeholder="如：工作日晚上、周末上午" />
        </Field>
        <Field label="入户说明">
          <Textarea name="access_instructions" placeholder="门禁、宠物或其他注意事项" />
        </Field>
        <Field label="现场照片或附件">
          <Input name="attachment" type="file" accept="image/jpeg,image/png,image/webp,image/heic,video/mp4,application/pdf" />
        </Field>
        <Button tone="primary" type="submit" disabled={busy}>
          {busy ? "正在确认并创建…" : "审阅并确认创建"}
        </Button>
        <MutationNotice error={error} />
      </form>
    </Card>
  );
}

export function RealRepairsPage({ fieldService = false }: { fieldService?: boolean }) {
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
      assigned_to_me: fieldService || null,
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
          assigned_to_me: fieldService || null,
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
        eyebrow={fieldService ? "现场服务" : staffMode ? "维修运营" : "居民报修"}
        title={fieldService ? "我的现场任务" : staffMode ? "工单队列" : "报修与进度"}
        description={
          fieldService
            ? "查看地址、联系人、预约和现场推进操作。"
            : staffMode
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
    label: "记录现场进展",
    action: "record-progress",
    fields: [
      {
        name: "record_type",
        label: "记录类型",
        required: true,
        choices: [
          { value: "APPOINTMENT", label: "预约服务" },
          { value: "ARRIVAL", label: "已到场" },
          { value: "PROGRESS", label: "处理进度" },
          { value: "BLOCKED", label: "现场受阻" },
        ],
      },
      { name: "appointment_at", label: "预约时间（预约时填写）" },
      { name: "note", label: "说明", required: true },
    ],
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
    enabled: staffMode,
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
          record_type: values.record_type as ApiSchemas["ProcessRecordType"],
          appointment_at: values.appointment_at || null,
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
                    ["房屋", item.houseDisplay ?? "房屋信息未解析"],
                    [
                      "报修人",
                      item.reporterName ??
                        (session.status === "authenticated" &&
                        session.actor.id === item.reporterId
                          ? session.actor.displayName
                          : "用户信息未解析"),
                    ],
                    [
                      "处理人",
                      item.assigneeName ??
                        (item.assigneeId ? "人员信息未解析" : "待分派"),
                    ],
                    ["服务阶段", labelFor(item.servicePhase)],
                    [
                      "当前预约",
                      item.currentAppointment
                        ? formatDate(item.currentAppointment.appointmentAt)
                        : "尚未预约",
                    ],
                    ["联系人", item.contactName ?? "未填写"],
                    ["联系电话", item.contactPhone ?? "未填写"],
                    [
                      "方便时间",
                      item.preferredTimeWindows.join("、") || "未填写",
                    ],
                    ["入户说明", item.accessInstructions ?? "无"],
                    ["更新时间", formatDate(item.updatedAt)],
                  ]}
                />
              </Card>
              <AttachmentLinks ids={item.requestAttachmentIds} label="报修附件" />
              <AttachmentLinks ids={item.completionAttachmentIds} label="完工附件" />
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
