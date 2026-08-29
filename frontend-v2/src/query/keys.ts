import type { QueryKey } from "@tanstack/react-query";

export type QueryScope = {
  actorId: string;
  houseId: string | null;
  communityId?: string;
  mode?: "house" | "community" | "actor";
};

export type ResourceIdentity = {
  resourceId?: string;
  conversationId?: string;
  filters?: Readonly<
    Record<string, string | number | boolean | null | undefined>
  >;
};

function normalizedFilters(
  filters: ResourceIdentity["filters"],
): readonly (readonly [string, string | number | boolean | null])[] {
  if (!filters) return [];
  return Object.entries(filters)
    .filter(
      (entry): entry is [string, string | number | boolean | null] =>
        entry[1] !== undefined,
    )
    .sort(([left], [right]) => left.localeCompare(right));
}

export function scopeQueryKey(
  scope: QueryScope,
  resource: string,
  identity: ResourceIdentity = {},
): QueryKey {
  return [
    "scope",
    {
      actorId: scope.actorId,
      communityId: scope.communityId ?? null,
      houseId: scope.mode === "house" || !scope.mode ? scope.houseId : null,
      mode: scope.mode ?? "house",
    },
    resource,
    {
      resourceId: identity.resourceId ?? null,
      conversationId: identity.conversationId ?? null,
      filters: normalizedFilters(identity.filters),
    },
  ] as const;
}

export function isScopeQuery(
  key: QueryKey,
  actorId: string,
  houseId: string | null,
): boolean {
  if (key[0] !== "scope" || typeof key[1] !== "object" || key[1] === null)
    return false;
  const scope = key[1] as Partial<QueryScope>;
  return scope.actorId === actorId && scope.houseId === houseId;
}

export function isActorQuery(key: QueryKey, actorId: string): boolean {
  if (key[0] !== "scope" || typeof key[1] !== "object" || key[1] === null)
    return false;
  return (key[1] as Partial<QueryScope>).actorId === actorId;
}
