import { QueryClient } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import { ApiClient } from "./api/client";
import { Application } from "./app/App";
import { AuthenticationService } from "./auth/AuthenticationService";
import { createBrowserSessionStore } from "./auth/session";
import "./styles/global.css";

const sessionStore = createBrowserSessionStore(window.sessionStorage);
const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false }, mutations: { retry: false } },
});
const apiClient = new ApiClient(
  import.meta.env.VITE_API_BASE_URL ?? "",
  () => {
    const session = sessionStore.getSnapshot();
    return session.status === "authenticated"
      ? { accessToken: session.accessToken, currentHouseId: session.currentHouseId }
      : {};
  },
  fetch,
  async () => {
    await queryClient.cancelQueries();
    queryClient.clear();
    sessionStore.clear();
  },
);

const services = {
  sessionStore,
  queryClient,
  authentication: new AuthenticationService(apiClient),
  mode: "real" as const,
};

createRoot(document.getElementById("root")!).render(<Application services={services} />);
