import type { Role } from "../api/contracts";

export type WorkspaceKind = "resident" | "maintenance" | "admin";

const adminRoles: Role[] = ["MANAGER", "SYSTEM_ADMIN"];
const maintenanceRoles: Role[] = [
  "REPAIR_WORKER",
  "SECURITY_GUARD",
  "SECURITY_STAFF",
  "DUTY_STAFF",
  "CUSTOMER_SERVICE",
  "FINANCE",
  "FINANCE_STAFF",
];

export function workspaceFor(roles: Role[] = []): WorkspaceKind {
  if (roles.some((role) => adminRoles.includes(role))) return "admin";
  if (roles.some((role) => maintenanceRoles.includes(role))) return "maintenance";
  return "resident";
}

export function workspaceLabel(kind: WorkspaceKind): string {
  return {
    resident: "住户服务空间",
    maintenance: "现场任务空间",
    admin: "社区运营空间",
  }[kind];
}
