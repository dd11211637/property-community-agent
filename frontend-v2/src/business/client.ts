import {
  ApiError,
  type ApiClient,
  type RequestDescriptor,
} from "../api/client";
import type { ApiPaths, ApiSchemas } from "../api/contracts";
import {
  parseAdminDashboard,
  parseAnnouncement,
  parseCollection,
  parseInspectionTask,
  parseMessage,
  parseSecurityEvent,
  parseTimeline,
  parseWorkOrder,
  type AdminDashboard,
  type Announcement,
  type InspectionTask,
  type PlatformMessage,
  type SecurityEvent,
  type TimelineEntry,
  type WorkOrder,
} from "./models";

type Path = keyof ApiPaths;
type Endpoint = {
  template: Path;
  descriptor: RequestDescriptor;
  idempotent?: boolean;
};
const business = (
  template: Path,
  house: "none" | "required" = "none",
  idempotent = false,
): Endpoint => ({
  template,
  idempotent,
  descriptor: {
    authentication: "required",
    house,
    decoder: "envelope",
    invalidateSessionOn401: true,
  },
});
const direct = (template: Path): Endpoint => ({
  template,
  descriptor: {
    authentication: "required",
    house: "none",
    decoder: "direct",
    invalidateSessionOn401: true,
  },
});

export const endpoints = {
  confirmations: direct("/api/confirmations"),
  staff: business("/api/staff"),
  admin: business("/api/admin/dashboard"),
  workOrders: business("/api/work-orders"),
  workOrder: business("/api/work-orders/{work_order_id}"),
  workOrderTimeline: business("/api/work-orders/{work_order_id}/timeline"),
  attachments: business("/api/attachments"),
  attachment: business("/api/attachments/{attachment_id}"),
  bills: business("/api/billing/bills", "required"),
  bill: business("/api/billing/bills/{bill_id}", "required"),
  billRule: business("/api/billing/bills/rules/{fee_type}", "required"),
  consultations: business("/api/billing/consultations"),
  createConsultation: business("/api/billing/consultations", "required"),
  consultation: business("/api/billing/consultations/{consultation_id}"),
  announcements: business("/api/announcements"),
  announcement: business("/api/announcements/{announcement_id}"),
  announcementVersions: business(
    "/api/announcements/{announcement_id}/versions",
  ),
  announcementAudience: business(
    "/api/announcements/{announcement_id}/audience-preview",
  ),
  inspections: business("/api/inspection-tasks"),
  inspection: business("/api/inspection-tasks/{task_id}"),
  inspectionTimeline: business("/api/inspection-tasks/{task_id}/timeline"),
  securityEvents: business("/api/security-events"),
  securityEvent: business("/api/security-events/{event_id}"),
  securityTimeline: business("/api/security-events/{event_id}/timeline"),
  messages: business("/api/messages"),
  markAllRead: business("/api/messages/read-all", "none", true),
  markRead: business("/api/messages/{message_id}/read", "none", true),
} as const;

function path(
  endpoint: Endpoint,
  values: Record<string, string> = {},
  query: Record<string, string | number | boolean | null | undefined> = {},
): string {
  let result = endpoint.template as string;
  for (const [key, value] of Object.entries(values))
    result = result.replace(`{${key}}`, encodeURIComponent(value));
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query))
    if (value !== undefined && value !== null && value !== "")
      params.append(key, String(value));
  return params.size ? `${result}?${params}` : result;
}

function invalid(error: unknown): never {
  if (error instanceof Error && error.message.includes("必须")) {
    throw new ApiError(
      "invalid-response",
      200,
      "INVALID_BUSINESS_RESPONSE",
      "服务返回的业务数据结构无效。",
      "",
      { reason: error.message },
    );
  }
  throw error;
}

function stableIntentValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableIntentValue);
  if (typeof value !== "object" || value === null) return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .filter(([key]) => key !== "confirmation_token")
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => [key, stableIntentValue(item)]),
  );
}

export class BusinessClient {
  private readonly writeIntents = new Map<string, string>();
  constructor(private readonly api: ApiClient) {}
  private read<T>(
    endpoint: Endpoint,
    url: string,
    signal?: AbortSignal,
  ): Promise<T> {
    return this.api.request<T>(endpoint.descriptor, url, { signal });
  }
  private async write<T>(
    endpoint: Endpoint,
    url: string,
    body: unknown,
    idempotencyKey?: string,
  ): Promise<T> {
    if (!idempotencyKey && !endpoint.idempotent)
      return this.api.request<T>(endpoint.descriptor, url, {
        method: "POST",
        body,
      });
    const fingerprint = JSON.stringify([url, stableIntentValue(body)]);
    const existing = this.writeIntents.get(fingerprint);
    const key = existing ?? idempotencyKey ?? `web_v2_${crypto.randomUUID()}`;
    this.writeIntents.set(fingerprint, key);
    try {
      const result = await this.api.request<T>(endpoint.descriptor, url, {
        method: "POST",
        body,
        idempotencyKey: key,
      });
      this.writeIntents.delete(fingerprint);
      return result;
    } catch (error) {
      if (
        !(error instanceof ApiError) ||
        !["network", "timeout"].includes(error.kind)
      )
        this.writeIntents.delete(fingerprint);
      throw error;
    }
  }

  async listWorkOrders(
    query: Record<string, string | number | boolean | null | undefined>,
    signal?: AbortSignal,
  ) {
    try {
      return parseCollection(
        await this.read<unknown>(
          endpoints.workOrders,
          path(endpoints.workOrders, {}, query),
          signal,
        ),
        parseWorkOrder,
        "workOrders",
      );
    } catch (error) {
      return invalid(error);
    }
  }
  async getWorkOrder(id: string, signal?: AbortSignal): Promise<WorkOrder> {
    try {
      return parseWorkOrder(
        await this.read(
          endpoints.workOrder,
          path(endpoints.workOrder, { work_order_id: id }),
          signal,
        ),
      );
    } catch (error) {
      return invalid(error);
    }
  }
  async getWorkOrderTimeline(
    id: string,
    signal?: AbortSignal,
  ): Promise<TimelineEntry[]> {
    try {
      return parseTimeline(
        await this.read(
          endpoints.workOrderTimeline,
          path(endpoints.workOrderTimeline, { work_order_id: id }),
          signal,
        ),
      );
    } catch (error) {
      return invalid(error);
    }
  }
  createWorkOrder(body: ApiSchemas["CreateWorkOrderRequest"], key: string) {
    return this.write<unknown>(
      endpoints.workOrders,
      path(endpoints.workOrders),
      body,
      key,
    ).then(parseWorkOrder);
  }
  workOrderAction(
    id: string,
    action:
      | "assign"
      | "accept"
      | "reject"
      | "record-progress"
      | "submit-completion"
      | "verify-pass"
      | "request-rework",
    body:
      | ApiSchemas["AssignRequest"]
      | ApiSchemas["VersionedActionRequest"]
      | ApiSchemas["RejectRequest"]
      | ApiSchemas["ProgressRequest"]
      | ApiSchemas["CompletionRequest"]
      | ApiSchemas["ReworkRequest"],
    key: string,
  ) {
    return this.write<unknown>(
      business(`/api/work-orders/{work_order_id}/actions/${action}` as Path),
      `/api/work-orders/${encodeURIComponent(id)}/actions/${action}`,
      body,
      key,
    ).then(parseWorkOrder);
  }
  reviewWorkOrder(id: string, body: ApiSchemas["ReviewRequest"], key: string) {
    return this.write<unknown>(
      business("/api/work-orders/{work_order_id}/reviews"),
      `/api/work-orders/${encodeURIComponent(id)}/reviews`,
      body,
      key,
    ).then(parseWorkOrder);
  }
  uploadAttachment(file: File) {
    const form = new FormData();
    form.set("file", file);
    form.set("business_type", "REPAIR");
    return this.api.request<ApiSchemas["AttachmentResponse"]>(
      endpoints.attachments.descriptor,
      path(endpoints.attachments),
      { method: "POST", body: form },
    );
  }
  downloadAttachment(id: string, signal?: AbortSignal) {
    return this.api.download(
      endpoints.attachment.descriptor,
      path(endpoints.attachment, { attachment_id: id }),
      signal,
    );
  }

  listBills(signal?: AbortSignal) {
    return this.read<ApiSchemas["BillResponse"][]>(
      endpoints.bills,
      path(endpoints.bills),
      signal,
    );
  }
  getBill(id: string, signal?: AbortSignal) {
    return this.read<ApiSchemas["BillDetailResponse"]>(
      endpoints.bill,
      path(endpoints.bill, { bill_id: id }),
      signal,
    );
  }
  getBillRule(feeType: string, signal?: AbortSignal) {
    return this.read<ApiSchemas["BillingRuleLookupResponse"]>(
      endpoints.billRule,
      path(endpoints.billRule, { fee_type: feeType }),
      signal,
    );
  }
  listConsultations(signal?: AbortSignal) {
    return this.read<ApiSchemas["ConsultationResponse"][]>(
      endpoints.consultations,
      path(endpoints.consultations),
      signal,
    );
  }
  getConsultation(id: string, signal?: AbortSignal) {
    return this.read<ApiSchemas["ConsultationResponse"]>(
      endpoints.consultation,
      path(endpoints.consultation, { consultation_id: id }),
      signal,
    );
  }
  createConsultation(
    body: ApiSchemas["CreateConsultationRequest"],
    key: string,
  ) {
    return this.write<ApiSchemas["ConsultationResponse"]>(
      endpoints.createConsultation,
      path(endpoints.createConsultation),
      body,
      key,
    );
  }
  consultationAction(
    id: string,
    action: "submit" | "process" | "answer" | "resolve" | "appeal",
    body:
      | ApiSchemas["VersionedConsultationRequest"]
      | ApiSchemas["AnswerConsultationRequest"],
  ) {
    return this.write<ApiSchemas["ConsultationResponse"]>(
      business(
        `/api/billing/consultations/{consultation_id}/${action}` as Path,
      ),
      `/api/billing/consultations/${encodeURIComponent(id)}/${action}`,
      body,
    );
  }

  async listAnnouncements(
    query: Record<string, string | number | undefined>,
    signal?: AbortSignal,
  ) {
    try {
      return parseCollection(
        await this.read<unknown>(
          endpoints.announcements,
          path(endpoints.announcements, {}, query),
          signal,
        ),
        parseAnnouncement,
        "announcements",
      );
    } catch (error) {
      return invalid(error);
    }
  }
  async getAnnouncement(
    id: string,
    signal?: AbortSignal,
  ): Promise<Announcement> {
    try {
      return parseAnnouncement(
        await this.read(
          endpoints.announcement,
          path(endpoints.announcement, { announcement_id: id }),
          signal,
        ),
      );
    } catch (error) {
      return invalid(error);
    }
  }
  createAnnouncement(
    body: ApiSchemas["CreateAnnouncementRequest"],
    key: string,
  ) {
    return this.write<unknown>(
      endpoints.announcements,
      path(endpoints.announcements),
      body,
      key,
    ).then(parseAnnouncement);
  }
  editAnnouncement(
    id: string,
    body: ApiSchemas["EditAnnouncementRequest"],
    key: string,
  ) {
    return this.api
      .request<unknown>(
        endpoints.announcement.descriptor,
        path(endpoints.announcement, { announcement_id: id }),
        { method: "PATCH", body, idempotencyKey: key },
      )
      .then(parseAnnouncement);
  }
  announcementAction(
    id: string,
    action:
      | "submit-review"
      | "approve"
      | "reject"
      | "publish"
      | "schedule"
      | "withdraw",
    body:
      | ApiSchemas["VersionedActionRequest"]
      | ApiSchemas["RejectAnnouncementRequest"]
      | ApiSchemas["PublishAnnouncementRequest"]
      | ApiSchemas["ScheduleAnnouncementRequest"]
      | ApiSchemas["WithdrawAnnouncementRequest"],
    key: string,
  ) {
    const suffix = action === "submit-review" ? action : `actions/${action}`;
    return this.write<unknown>(
      business(`/api/announcements/{announcement_id}/${suffix}` as Path),
      `/api/announcements/${encodeURIComponent(id)}/${suffix}`,
      body,
      key,
    ).then(parseAnnouncement);
  }
  announcementVersions(id: string, signal?: AbortSignal) {
    return this.read<unknown>(
      endpoints.announcementVersions,
      path(endpoints.announcementVersions, { announcement_id: id }),
      signal,
    );
  }
  announcementAudience(id: string, signal?: AbortSignal) {
    return this.read<unknown>(
      endpoints.announcementAudience,
      path(endpoints.announcementAudience, { announcement_id: id }),
      signal,
    );
  }

  async listInspections(
    query: Record<string, string | number | boolean | undefined>,
    signal?: AbortSignal,
  ) {
    try {
      return parseCollection(
        await this.read<unknown>(
          endpoints.inspections,
          path(endpoints.inspections, {}, query),
          signal,
        ),
        parseInspectionTask,
        "inspections",
      );
    } catch (error) {
      return invalid(error);
    }
  }
  async getInspection(
    id: string,
    signal?: AbortSignal,
  ): Promise<InspectionTask> {
    try {
      return parseInspectionTask(
        await this.read(
          endpoints.inspection,
          path(endpoints.inspection, { task_id: id }),
          signal,
        ),
      );
    } catch (error) {
      return invalid(error);
    }
  }
  createInspection(
    body: ApiSchemas["CreateInspectionTaskRequest"],
    key: string,
  ) {
    return this.write<unknown>(
      endpoints.inspections,
      path(endpoints.inspections),
      body,
      key,
    ).then(parseInspectionTask);
  }
  inspectionAction(
    id: string,
    action: "assign" | "start" | "add-record" | "submit-records" | "complete",
    body:
      | ApiSchemas["AssignTaskRequest"]
      | ApiSchemas["VersionedActionRequest"]
      | ApiSchemas["AddTaskRecordRequest"]
      | ApiSchemas["SubmitTaskRecordsRequest"],
    key: string,
  ) {
    return this.write<unknown>(
      business(`/api/inspection-tasks/{task_id}/actions/${action}` as Path),
      `/api/inspection-tasks/${encodeURIComponent(id)}/actions/${action}`,
      body,
      key,
    ).then(parseInspectionTask);
  }
  inspectionTimeline(id: string, signal?: AbortSignal) {
    return this.read<unknown>(
      endpoints.inspectionTimeline,
      path(endpoints.inspectionTimeline, { task_id: id }),
      signal,
    ).then(parseTimeline);
  }

  async listSecurityEvents(
    query: Record<string, string | number | boolean | undefined>,
    signal?: AbortSignal,
  ) {
    try {
      return parseCollection(
        await this.read<unknown>(
          endpoints.securityEvents,
          path(endpoints.securityEvents, {}, query),
          signal,
        ),
        parseSecurityEvent,
        "securityEvents",
      );
    } catch (error) {
      return invalid(error);
    }
  }
  async getSecurityEvent(
    id: string,
    signal?: AbortSignal,
  ): Promise<SecurityEvent> {
    try {
      return parseSecurityEvent(
        await this.read(
          endpoints.securityEvent,
          path(endpoints.securityEvent, { event_id: id }),
          signal,
        ),
      );
    } catch (error) {
      return invalid(error);
    }
  }
  createSecurityEvent(
    body: ApiSchemas["CreateSecurityEventRequest"],
    key: string,
  ) {
    return this.write<unknown>(
      endpoints.securityEvents,
      path(endpoints.securityEvents),
      body,
      key,
    ).then(parseSecurityEvent);
  }
  securityAction(
    id: string,
    action:
      | "assign"
      | "submit-disposal"
      | "grade-confirm"
      | "return"
      | "review-pass",
    body:
      | ApiSchemas["AssignEventRequest"]
      | ApiSchemas["SubmitDisposalRequest"]
      | ApiSchemas["VersionedActionRequest"]
      | ApiSchemas["ReturnEventRequest"],
    key: string,
  ) {
    return this.write<unknown>(
      business(`/api/security-events/{event_id}/actions/${action}` as Path),
      `/api/security-events/${encodeURIComponent(id)}/actions/${action}`,
      body,
      key,
    ).then(parseSecurityEvent);
  }
  securityTimeline(id: string, signal?: AbortSignal) {
    return this.read<unknown>(
      endpoints.securityTimeline,
      path(endpoints.securityTimeline, { event_id: id }),
      signal,
    ).then(parseTimeline);
  }

  async listMessages(
    query: Record<string, string | number | undefined>,
    signal?: AbortSignal,
  ) {
    try {
      return parseCollection(
        await this.read<unknown>(
          endpoints.messages,
          path(endpoints.messages, {}, query),
          signal,
        ),
        parseMessage,
        "messages",
      );
    } catch (error) {
      return invalid(error);
    }
  }
  markMessageRead(id: string, key: string) {
    return this.write<unknown>(
      endpoints.markRead,
      path(endpoints.markRead, { message_id: id }),
      undefined,
      key,
    );
  }
  markAllMessagesRead(key: string) {
    return this.write<unknown>(
      endpoints.markAllRead,
      path(endpoints.markAllRead),
      undefined,
      key,
    );
  }
  async getAdminDashboard(signal?: AbortSignal): Promise<AdminDashboard> {
    try {
      return parseAdminDashboard(
        await this.read(endpoints.admin, path(endpoints.admin), signal),
      );
    } catch (error) {
      return invalid(error);
    }
  }
  listStaff(role: "REPAIR_WORKER" | "SECURITY_GUARD", signal?: AbortSignal) {
    return this.read<ApiSchemas["StaffOptionResponse"][]>(
      endpoints.staff,
      path(endpoints.staff, {}, { role }),
      signal,
    );
  }
  async confirm(
    body: ApiSchemas["ConfirmationGenerateRequest"],
  ): Promise<ApiSchemas["ConfirmationGenerateResponse"]> {
    const response = await this.api.request<unknown>(
      endpoints.confirmations.descriptor,
      path(endpoints.confirmations),
      { method: "POST", body },
    );
    if (
      typeof response !== "object" ||
      response === null ||
      typeof (response as { token?: unknown }).token !== "string" ||
      !(response as { token: string }).token.trim() ||
      typeof (response as { expires_in_seconds?: unknown })
        .expires_in_seconds !== "number"
    ) {
      throw new ApiError(
        "invalid-response",
        200,
        "INVALID_CONFIRMATION_RESPONSE",
        "服务返回的确认凭证结构无效。",
      );
    }
    return response as ApiSchemas["ConfirmationGenerateResponse"];
  }
}

export type { PlatformMessage };
