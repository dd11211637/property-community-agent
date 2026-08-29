import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import type { ApiSchemas } from "../api/contracts";
import { useSession } from "../auth/useSession";
import { AnnouncementCard } from "../domain/cards";
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
import { describeAudience, type Announcement } from "./models";
import { canPresentAction } from "./permissions";

function announcementCard(item: Announcement) {
  return {
    id: item.id,
    title: item.title,
    category: item.category,
    audience: describeAudience(item.audience),
    status: item.status,
    summary: item.body,
    publishedAt: item.publishedAt ?? item.updatedAt,
  };
}

function AnnouncementEditor({ onCreated }: { onCreated(): void }) {
  const client = useBusinessClient();
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const data = new FormData(event.currentTarget);
    const buildings = String(data.get("buildings") ?? "")
      .split(/[,，]/)
      .map((value) => value.trim())
      .filter(Boolean);
    const body: ApiSchemas["CreateAnnouncementRequest"] = {
      title: String(data.get("title")),
      body: String(data.get("body")),
      category: String(
        data.get("category"),
      ) as ApiSchemas["AnnouncementCategory"],
      audience_condition: buildings.length ? { buildings } : {},
    };
    try {
      await client.createAnnouncement(body, newIdempotencyKey());
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
      <h2>创建公告草稿</h2>
      <form className={styles.form} onSubmit={(event) => void submit(event)}>
        <Field label="标题">
          <Input name="title" required />
        </Field>
        <Field label="正文">
          <Textarea name="body" required />
        </Field>
        <Field label="分类">
          <select name="category" className="business-select">
            <option value="GENERAL">一般通知</option>
            <option value="MAINTENANCE">维护</option>
            <option value="SAFETY">安全</option>
            <option value="EMERGENCY">紧急</option>
          </select>
        </Field>
        <Field label="指定楼栋（可选，以逗号分隔）">
          <Input name="buildings" placeholder="1栋，2栋" />
        </Field>
        <Button tone="primary" disabled={busy}>
          {busy ? "正在创建…" : "创建草稿"}
        </Button>
        <MutationNotice error={error} />
      </form>
    </Card>
  );
}

export function RealCommunityPage() {
  const client = useBusinessClient();
  const { session } = useSession();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState("announcements");
  const roles = session.status === "authenticated" ? session.actor.roles : [];
  const canAuthor = roles.some((role) =>
    ["CUSTOMER_SERVICE", "MANAGER", "SYSTEM_ADMIN"].includes(role),
  );
  const key = useBusinessKey("community", "announcements", {
    filters: { limit: 50, offset: 0 },
  });
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      client.listAnnouncements({ limit: 50, offset: 0 }, signal),
  });
  return (
    <div className={styles.page}>
      <BusinessHeader
        eyebrow="COMMUNITY"
        title="社区公告"
        description="居民获得可读通知；工作人员按服务端生命周期完成创作和审核。"
      />
      {canAuthor ? (
        <Tabs
          items={[
            { id: "announcements", label: "公告" },
            { id: "create", label: "创建草稿" },
          ]}
          active={tab}
          onChange={setTab}
        />
      ) : null}
      {tab === "create" && canAuthor ? (
        <AnnouncementEditor
          onCreated={() => {
            void queryClient.invalidateQueries({ queryKey: key });
            setTab("announcements");
          }}
        />
      ) : (
        <QueryBoundary
          pending={query.isPending}
          error={query.error}
          empty={!query.data?.items.length}
        >
          <div className={styles.grid}>
            {query.data?.items.map((item) => (
              <Link key={item.id} to={`/community/announcements/${item.id}`}>
                <AnnouncementCard value={announcementCard(item)} />
              </Link>
            ))}
          </div>
        </QueryBoundary>
      )}
    </div>
  );
}

const actionSpecs: Record<string, ActionSpec> = {
  EDIT: {
    code: "EDIT",
    label: "编辑草稿",
    fields: [
      { name: "title", label: "标题", required: true },
      { name: "body", label: "正文", required: true },
      { name: "buildings", label: "楼栋（逗号分隔）" },
    ],
  },
  SUBMIT_REVIEW: { code: "SUBMIT_REVIEW", label: "送审" },
  APPROVE: { code: "APPROVE", label: "批准" },
  REJECT: {
    code: "REJECT",
    label: "驳回",
    destructive: true,
    fields: [{ name: "reason", label: "驳回原因", required: true }],
  },
  PUBLISH: { code: "PUBLISH", label: "二次确认并发布" },
  SCHEDULE: {
    code: "SCHEDULE",
    label: "定时发布",
    fields: [
      {
        name: "scheduled_at",
        label: "发布时间",
        kind: "datetime",
        required: true,
      },
    ],
  },
  WITHDRAW: {
    code: "WITHDRAW",
    label: "撤回",
    destructive: true,
    fields: [{ name: "reason", label: "撤回原因", required: true }],
  },
};

export function RealAnnouncementDetailPage() {
  const { id = "" } = useParams();
  const client = useBusinessClient();
  const queryClient = useQueryClient();
  const { session } = useSession();
  const key = useBusinessKey("community", "announcement", { resourceId: id });
  const listKey = useBusinessKey("community", "announcements");
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => client.getAnnouncement(id, signal),
    enabled: Boolean(id),
  });
  const versions = useQuery({
    queryKey: useBusinessKey("community", "announcement-versions", {
      resourceId: id,
    }),
    queryFn: ({ signal }) => client.announcementVersions(id, signal),
    enabled: Boolean(id),
  });
  const audience = useQuery({
    queryKey: useBusinessKey("community", "announcement-audience", {
      resourceId: id,
    }),
    queryFn: ({ signal }) => client.announcementAudience(id, signal),
    enabled: Boolean(id),
  });
  const mutation = useMutation({
    mutationFn: async ({
      action,
      values,
    }: {
      action: string;
      values: Record<string, string>;
    }) => {
      const item = query.data!;
      const keyValue = newIdempotencyKey();
      if (action === "EDIT")
        return client.editAnnouncement(
          id,
          {
            expected_version: item.version,
            title: values.title,
            body: values.body,
            category: item.category as ApiSchemas["AnnouncementCategory"],
            audience_condition: {
              buildings: values.buildings
                .split(/[,，]/)
                .map((value) => value.trim())
                .filter(Boolean),
            },
          },
          keyValue,
        );
      if (action === "PUBLISH") {
        const confirmation = await client.confirm({
          action: "ANNOUNCEMENT_PUBLISH",
          parameters: {
            announcement_id: id,
            expected_version: item.version,
            action: "PUBLISH",
          },
        });
        return client.announcementAction(
          id,
          "publish",
          {
            expected_version: item.version,
            confirmation_token: confirmation.token,
          },
          keyValue,
        );
      }
      if (action === "SCHEDULE") {
        const scheduled_at = new Date(values.scheduled_at).toISOString();
        const confirmation = await client.confirm({
          action: "ANNOUNCEMENT_SCHEDULE",
          parameters: {
            announcement_id: id,
            expected_version: item.version,
            scheduled_at,
          },
        });
        return client.announcementAction(
          id,
          "schedule",
          {
            expected_version: item.version,
            scheduled_at,
            confirmation_token: confirmation.token,
          },
          keyValue,
        );
      }
      if (action === "REJECT")
        return client.announcementAction(
          id,
          "reject",
          { expected_version: item.version, reason: values.reason },
          keyValue,
        );
      if (action === "WITHDRAW")
        return client.announcementAction(
          id,
          "withdraw",
          { expected_version: item.version, reason: values.reason },
          keyValue,
        );
      return client.announcementAction(
        id,
        action === "SUBMIT_REVIEW" ? "submit-review" : "approve",
        { expected_version: item.version },
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
          action in actionSpecs &&
          canPresentAction("announcement", action, roles),
      )
      .map((action) => ({
        ...actionSpecs[action],
        fields:
          action === "EDIT"
            ? [
                {
                  name: "title",
                  label: "标题",
                  required: true,
                  defaultValue: item.title,
                },
                {
                  name: "body",
                  label: "正文",
                  required: true,
                  defaultValue: item.body,
                },
                { name: "buildings", label: "楼栋（逗号分隔）" },
              ]
            : actionSpecs[action].fields,
      })) ?? [];
  return (
    <div className={styles.page}>
      <BusinessHeader
        eyebrow="ANNOUNCEMENT"
        title={item?.title ?? "公告详情"}
        description="受众、版本及审核动作均来自真实公告服务。"
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
                  <Badge>{labelFor(item.category)}</Badge>
                  <Badge>{labelFor(item.status)}</Badge>
                  <Badge>v{item.version}</Badge>
                </div>
                <p>{item.body}</p>
                <DetailGrid
                  entries={[
                    ["受众", describeAudience(item.audience)],
                    [
                      "计划发布",
                      item.scheduledAt
                        ? formatDate(item.scheduledAt)
                        : "未计划",
                    ],
                    [
                      "实际发布",
                      item.publishedAt
                        ? formatDate(item.publishedAt)
                        : "未发布",
                    ],
                    [
                      "更新时间",
                      item.updatedAt ? formatDate(item.updatedAt) : "",
                    ],
                  ]}
                />
              </Card>
              <Card>
                <h2>受众预览与版本</h2>
                <p>
                  受众预览：
                  {audience.isPending
                    ? "读取中…"
                    : audience.error
                      ? "暂不可用"
                      : audience.data
                        ? "已由服务端解析"
                        : "无预览"}
                </p>
                <p>
                  版本历史：
                  {versions.isPending
                    ? "读取中…"
                    : versions.error
                      ? "暂不可用"
                      : Array.isArray(versions.data)
                        ? `${versions.data.length} 个版本`
                        : "已读取"}
                </p>
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
