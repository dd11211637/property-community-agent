import type { QueryClient, QueryKey } from "@tanstack/react-query";

const targets: Record<string, ReadonlySet<string>> = {
  work_order: new Set(["work-orders", "work-order", "work-order-timeline"]),
  bill: new Set(["bills", "bill"]),
  consultation: new Set(["consultations", "consultation"]),
  announcement: new Set(["announcements", "announcement", "announcement-versions", "announcement-audience"]),
  task: new Set(["inspections", "inspection", "inspection-timeline"]),
  event: new Set(["security-events", "security-event", "security-timeline"]),
};

function source(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const data = record.data;
  return data && typeof data === "object" && !Array.isArray(data)
    ? (data as Record<string, unknown>)
    : record;
}
export function factKinds(value: unknown): string[] {
  const facts = source(value);
  return facts ? Object.keys(targets).filter((key) => facts[key] !== undefined) : [];
}

function businessResource(key: QueryKey): string | null {
  return key[0] === "scope" && typeof key[2] === "string" ? key[2] : null;
}

export async function reconcileTrustedFacts(queryClient: QueryClient, facts: unknown) {
  const resources = new Set(factKinds(facts).flatMap((kind) => [...targets[kind]]));
  if (!resources.size) return;
  await queryClient.invalidateQueries({
    predicate: (query) => {
      const resource = businessResource(query.queryKey);
      return resource !== null && resources.has(resource);
    },
  });
}
