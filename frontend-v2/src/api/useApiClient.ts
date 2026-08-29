import { useContext } from "react";
import { ApiContext } from "./apiContextDefinition";
import type { ApiClient } from "./client";

export function useApiClient(): ApiClient {
  const client = useContext(ApiContext);
  if (!client) throw new Error("ApiProvider is missing.");
  return client;
}
