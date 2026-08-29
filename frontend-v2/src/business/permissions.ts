export type BusinessDomain =
  | "repair"
  | "announcement"
  | "inspection"
  | "security";

const actionRoles: Record<BusinessDomain, Record<string, readonly string[]>> = {
  repair: {
    ASSIGN: ["CUSTOMER_SERVICE", "MANAGER", "SYSTEM_ADMIN"],
    ACCEPT: ["REPAIR_WORKER"],
    REJECT: ["REPAIR_WORKER"],
    RECORD_PROGRESS: ["REPAIR_WORKER"],
    SUBMIT_COMPLETION: ["REPAIR_WORKER"],
    SUBMIT_REWORK_COMPLETION: ["REPAIR_WORKER"],
    VERIFY_PASS: ["RESIDENT", "MANAGER", "SYSTEM_ADMIN"],
    REQUEST_REWORK: ["RESIDENT", "MANAGER", "SYSTEM_ADMIN"],
    CREATE_REVIEW: ["RESIDENT"],
  },
  announcement: {
    EDIT: ["CUSTOMER_SERVICE", "MANAGER", "SYSTEM_ADMIN"],
    SUBMIT_REVIEW: ["CUSTOMER_SERVICE", "MANAGER", "SYSTEM_ADMIN"],
    WITHDRAW: ["CUSTOMER_SERVICE", "MANAGER", "SYSTEM_ADMIN"],
    APPROVE: ["MANAGER", "SYSTEM_ADMIN"],
    REJECT: ["MANAGER", "SYSTEM_ADMIN"],
    PUBLISH: ["MANAGER", "SYSTEM_ADMIN"],
    SCHEDULE: ["MANAGER", "SYSTEM_ADMIN"],
  },
  inspection: {
    ASSIGN: ["MANAGER", "SYSTEM_ADMIN"],
    COMPLETE: ["MANAGER", "SYSTEM_ADMIN"],
    START: ["SECURITY_STAFF", "SECURITY_GUARD"],
    ADD_RECORD: ["SECURITY_STAFF", "SECURITY_GUARD"],
    SUBMIT_RECORDS: ["SECURITY_STAFF", "SECURITY_GUARD"],
  },
  security: {
    ASSIGN: ["MANAGER", "SYSTEM_ADMIN"],
    GRADE_CONFIRM: ["MANAGER", "SYSTEM_ADMIN"],
    REVIEW_PASS: ["MANAGER", "SYSTEM_ADMIN"],
    RETURN: ["MANAGER", "SYSTEM_ADMIN"],
    SUBMIT_DISPOSAL: ["SECURITY_STAFF", "SECURITY_GUARD"],
  },
};

export function canPresentAction(
  domain: BusinessDomain,
  action: string,
  roles: readonly string[],
): boolean {
  const allowed = actionRoles[domain][action];
  return Boolean(allowed?.some((role) => roles.includes(role)));
}
