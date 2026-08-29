import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import type { ApiSchemas } from "../api/contracts";
import { useSession } from "../auth/useSession";
import { BillCard } from "../domain/cards";
import { formatCurrency, formatDate, labelFor } from "../presentation/format";
import {
  Badge,
  Button,
  Card,
  Field,
  InlineAlert,
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

function billModel(bill: ApiSchemas["BillResponse"]) {
  return {
    id: bill.bill_id,
    period: bill.bill_period,
    total: Number(bill.total_amount),
    status: bill.status,
    dueDate: bill.due_date,
    items: [
      `物业费 ${formatCurrency(Number(bill.property_fee))}`,
      `公共事业费 ${formatCurrency(Number(bill.utility_fee))}`,
    ],
  };
}

function CreateConsultation({
  bills,
  onCreated,
}: {
  bills: ApiSchemas["BillResponse"][];
  onCreated(): void;
}) {
  const client = useBusinessClient();
  const { session } = useSession();
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (session.status !== "authenticated" || !session.currentHouseId)
    return <InlineAlert>选择当前房屋后才能创建财务咨询。</InlineAlert>;
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const data = new FormData(event.currentTarget);
    const subject = String(data.get("subject"));
    const description = String(data.get("description"));
    const rawBill = String(data.get("bill_id") ?? "");
    const bill_id = rawBill || null;
    try {
      const confirmation = await client.confirm({
        action: "CREATE_CONSULTATION",
        parameters: { subject, description, bill_id },
      });
      await client.createConsultation(
        {
          subject,
          description,
          bill_id,
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
      <h2>新建财务咨询</h2>
      <form className={styles.form} onSubmit={(event) => void submit(event)}>
        <Field label="主题">
          <Input name="subject" required />
        </Field>
        <Field label="关联账单（可选）">
          <select className="business-select" name="bill_id">
            <option value="">不关联账单</option>
            {bills.map((bill) => (
              <option key={bill.bill_id} value={bill.bill_id}>
                {bill.bill_period} · {formatCurrency(Number(bill.total_amount))}
              </option>
            ))}
          </select>
        </Field>
        <Field label="问题说明">
          <Textarea name="description" required />
        </Field>
        <Button tone="primary" disabled={busy}>
          {busy ? "正在确认…" : "审阅并确认创建"}
        </Button>
        <MutationNotice error={error} />
      </form>
    </Card>
  );
}

export function RealBillingPage() {
  const client = useBusinessClient();
  const { session } = useSession();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState("bills");
  const billsKey = useBusinessKey("house", "bills");
  const consultationKey = useBusinessKey("actor", "consultations");
  const bills = useQuery({
    queryKey: billsKey,
    queryFn: ({ signal }) => client.listBills(signal),
    enabled:
      session.status === "authenticated" && Boolean(session.currentHouseId),
  });
  const consultations = useQuery({
    queryKey: consultationKey,
    queryFn: ({ signal }) => client.listConsultations(signal),
  });
  return (
    <div className={styles.page}>
      <BusinessHeader
        eyebrow="BILLING"
        title="账单与财务咨询"
        description="账单严格按当前房屋读取；咨询列表遵循后端当前 Actor 范围。"
      />
      <Tabs
        items={[
          { id: "bills", label: "房屋账单" },
          { id: "consultations", label: "我的咨询" },
          { id: "create", label: "发起咨询" },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "bills" ? (
        <>
          {session.status === "authenticated" && !session.currentHouseId ? (
            <InlineAlert>请先选择当前房屋。</InlineAlert>
          ) : null}
          <QueryBoundary
            pending={bills.isPending && bills.fetchStatus !== "idle"}
            error={bills.error}
            empty={!bills.data?.length}
          >
            <div className={styles.grid}>
              {bills.data?.map((bill) => (
                <Link key={bill.bill_id} to={`/billing/bills/${bill.bill_id}`}>
                  <BillCard value={billModel(bill)} />
                </Link>
              ))}
            </div>
          </QueryBoundary>
        </>
      ) : null}
      {tab === "consultations" ? (
        <QueryBoundary
          pending={consultations.isPending}
          error={consultations.error}
          empty={!consultations.data?.length}
        >
          <div className={styles.grid}>
            {consultations.data?.map((item) => (
              <Link key={item.id} to={`/billing/consultations/${item.id}`}>
                <Card interactive className={styles.record}>
                  <div className={styles.actions}>
                    <Badge>{labelFor(item.status)}</Badge>
                    <Badge>v{item.version}</Badge>
                  </div>
                  <h3>{item.subject}</h3>
                  <p>{item.description}</p>
                  <small>
                    {item.updated_at ? formatDate(item.updated_at) : ""}
                  </small>
                </Card>
              </Link>
            ))}
          </div>
        </QueryBoundary>
      ) : null}
      {tab === "create" ? (
        <CreateConsultation
          bills={bills.data ?? []}
          onCreated={() => {
            void queryClient.invalidateQueries({ queryKey: consultationKey });
            setTab("consultations");
          }}
        />
      ) : null}
    </div>
  );
}

export function RealBillDetailPage() {
  const { id = "" } = useParams();
  const client = useBusinessClient();
  const key = useBusinessKey("house", "bill", { resourceId: id });
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => client.getBill(id, signal),
    enabled: Boolean(id),
  });
  const detail = query.data;
  const bill = detail?.bill;
  return (
    <div className={styles.page}>
      <BusinessHeader
        eyebrow="BILL DETAIL"
        title={bill ? `${bill.bill_period} 账单` : "账单详情"}
        description="金额、账期和计费规则来自账单服务。"
      />
      <QueryBoundary
        pending={query.isPending}
        error={query.error}
        empty={!bill}
      >
        {bill ? (
          <div className={styles.split}>
            <Card>
              <div className={styles.actions}>
                <Badge>{labelFor(bill.status)}</Badge>
                <Badge>v{bill.version}</Badge>
              </div>
              <h2>{formatCurrency(Number(bill.total_amount))}</h2>
              <DetailGrid
                entries={[
                  ["截止日期", bill.due_date],
                  ["物业费", formatCurrency(Number(bill.property_fee))],
                  ["公共事业费", formatCurrency(Number(bill.utility_fee))],
                  ["停车费", formatCurrency(Number(bill.parking_fee))],
                  ["滞纳金", formatCurrency(Number(bill.late_fee))],
                  ["收据", bill.receipt_no],
                ]}
              />
            </Card>
            <Card>
              <h2>计费规则</h2>
              {detail?.unknown_rule || !detail?.rule ? (
                <InlineAlert>
                  当前账单未提供可验证的计费规则；不会生成或猜测财务解释。
                </InlineAlert>
              ) : (
                <>
                  <strong>{detail.rule.name}</strong>
                  <p>版本 {detail.rule.version}</p>
                  <p>
                    有效期：{detail.rule.valid_from ?? "未注明"} —{" "}
                    {detail.rule.valid_until ?? "长期"}
                  </p>
                </>
              )}
            </Card>
          </div>
        ) : null}
      </QueryBoundary>
    </div>
  );
}

function consultationActions(
  item: ApiSchemas["ConsultationResponse"],
  roles: readonly string[],
): ActionSpec[] {
  const staff = roles.some((role) =>
    [
      "FINANCE",
      "FINANCE_STAFF",
      "CUSTOMER_SERVICE",
      "MANAGER",
      "SYSTEM_ADMIN",
    ].includes(role),
  );
  if (item.status === "DRAFT") return [{ code: "submit", label: "提交咨询" }];
  if (item.status === "SUBMITTED" && staff)
    return [{ code: "process", label: "开始处理" }];
  if ((item.status === "PROCESSING" || item.status === "APPEALED") && staff)
    return [
      {
        code: "answer",
        label: "答复",
        fields: [{ name: "answer", label: "答复内容", required: true }],
      },
    ];
  if (item.status === "ANSWERED")
    return staff
      ? [{ code: "resolve", label: "解决并关闭" }]
      : [{ code: "appeal", label: "申诉", destructive: true }];
  return [];
}

export function RealConsultationDetailPage() {
  const { id = "" } = useParams();
  const client = useBusinessClient();
  const { session } = useSession();
  const queryClient = useQueryClient();
  const key = useBusinessKey("actor", "consultation", { resourceId: id });
  const listKey = useBusinessKey("actor", "consultations");
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => client.getConsultation(id, signal),
    enabled: Boolean(id),
  });
  const mutation = useMutation({
    mutationFn: ({
      action,
      values,
    }: {
      action: string;
      values: Record<string, string>;
    }) =>
      client.consultationAction(
        id,
        action as "submit" | "process" | "answer" | "resolve" | "appeal",
        action === "answer"
          ? { expected_version: query.data!.version, answer: values.answer }
          : { expected_version: query.data!.version },
      ),
    retry: false,
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: key }),
        queryClient.invalidateQueries({ queryKey: listKey }),
      ]);
    },
  });
  const item = query.data;
  const roles = session.status === "authenticated" ? session.actor.roles : [];
  return (
    <div className={styles.page}>
      <BusinessHeader
        eyebrow="CONSULTATION"
        title={item?.subject ?? "咨询详情"}
        description="现有列表端点只返回当前 Actor；员工可从真实消息或 Admin 资源深链进入其他咨询。"
      />
      <QueryBoundary
        pending={query.isPending}
        error={query.error}
        empty={!item}
      >
        {item ? (
          <div className={styles.split}>
            <Card>
              <div className={styles.actions}>
                <Badge>{labelFor(item.status)}</Badge>
                <Badge>v{item.version}</Badge>
              </div>
              <p>{item.description}</p>
              {item.answer ? (
                <InlineAlert>答复：{item.answer}</InlineAlert>
              ) : null}
              <DetailGrid
                entries={[
                  ["咨询人", item.actor_id],
                  ["房屋", item.house_id],
                  ["关联账单", item.bill_id],
                  ["处理人", item.handler_id],
                  [
                    "更新时间",
                    item.updated_at ? formatDate(item.updated_at) : "",
                  ],
                ]}
              />
            </Card>
            <ActionWorkbench
              actions={consultationActions(item, roles)}
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
