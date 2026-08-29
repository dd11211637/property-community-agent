import type { ActorRole } from "./session";

export const knownRoles = [
  "RESIDENT",
  "CUSTOMER_SERVICE",
  "REPAIR_WORKER",
  "FINANCE",
  "FINANCE_STAFF",
  "SECURITY_STAFF",
  "SECURITY_GUARD",
  "DUTY_STAFF",
  "MANAGER",
  "SYSTEM_ADMIN",
] as const;

export type KnownRole = (typeof knownRoles)[number];
export type Capability = "resident-experience" | "operations" | "admin";

const capabilityRoles: Record<Capability, ReadonlySet<ActorRole>> = {
  "resident-experience": new Set(["RESIDENT"]),
  operations: new Set([
    "CUSTOMER_SERVICE",
    "REPAIR_WORKER",
    "FINANCE",
    "FINANCE_STAFF",
    "SECURITY_STAFF",
    "SECURITY_GUARD",
    "DUTY_STAFF",
    "MANAGER",
    "SYSTEM_ADMIN",
  ]),
  admin: new Set(["MANAGER", "SYSTEM_ADMIN"]),
};

export function hasCapability(roles: readonly ActorRole[], capability: Capability): boolean {
  return roles.some((role) => capabilityRoles[capability].has(role));
}
