import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, useState, type ReactNode } from "react";
import {
  BrowserRouter,
  HashRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { hasCapability, type Capability } from "../auth/capabilities";
import { SessionProvider } from "../auth/SessionContext";
import { useSession } from "../auth/useSession";
import type { AuthenticationPort, SessionStore } from "../auth/session";
import { ApiProvider } from "../api/ApiContext";
import type { ApiClient } from "../api/client";
import { AppShell } from "../layout/AppShell";
import { ShowcaseProvider } from "../models/ShowcaseContext";
import type { ShowcaseModels } from "../models/viewModels";
import {
  AdminPage,
  BillingPage,
  CommunityPage,
  ForbiddenPage,
  HomePage,
  LoginPage,
  MessagesPage,
  NotFoundPage,
  OperationsPage,
  RepairsPage,
} from "../pages/pages";
import { BootPage, RealLoginPage } from "../pages/runtimePages";
import { RealRepairsPage, RealRepairDetailPage } from "../business/Repairs";
import {
  RealBillingPage,
  RealBillDetailPage,
  RealConsultationDetailPage,
} from "../business/Billing";
import {
  RealAnnouncementDetailPage,
  RealCommunityPage,
} from "../business/Community";
import {
  RealInspectionDetailPage,
  RealOperationsPage,
  RealSecurityDetailPage,
} from "../business/Operations";
import {
  RealAdminPage,
  RealBusinessHomePage,
  RealMessagesPage,
} from "../business/MessagesAdminHome";
import { RuntimeModeProvider } from "./runtimeMode";
import { useRuntimeMode, type RuntimeMode } from "./runtimeModeDefinition";
import { AgentRuntimeProvider } from "../agent/runtime";
import { AgentWorkspace } from "../agent/AgentWorkspace";

export type ApplicationServices = {
  sessionStore: SessionStore;
  authentication: AuthenticationPort;
  mode: RuntimeMode;
  showcaseModels?: ShowcaseModels;
  queryClient?: QueryClient;
  apiClient?: ApiClient;
};

function ProtectedRoute({ children }: { children: ReactNode }) {
  const { session } = useSession();
  const location = useLocation();
  if (session.status === "restoring") return <BootPage />;
  return session.status === "authenticated" ? (
    children
  ) : (
    <Navigate to="/login" state={{ from: location.pathname }} replace />
  );
}

function CapabilityRoute({
  capability,
  children,
}: {
  capability: Capability;
  children: ReactNode;
}) {
  const { session } = useSession();
  if (session.status === "restoring") return <BootPage />;
  if (session.status !== "authenticated")
    return <Navigate to="/login" replace />;
  return hasCapability(session.actor.roles, capability) ? (
    children
  ) : (
    <ForbiddenPage />
  );
}

function LoginRoute() {
  const { session } = useSession();
  const mode = useRuntimeMode();
  if (session.status === "restoring") return <BootPage />;
  if (session.status === "authenticated") return <Navigate to="/" replace />;
  return mode === "demo" ? <LoginPage /> : <RealLoginPage />;
}

function BusinessPage({ demo, real }: { demo: ReactNode; real: ReactNode }) {
  return useRuntimeMode() === "demo" ? demo : real;
}

export function AppRoutes() {
  const mode = useRuntimeMode();
  return (
    <Routes>
      <Route path="/login" element={<LoginRoute />} />
      <Route
        element={
          <ProtectedRoute>
            <AppShell />
          </ProtectedRoute>
        }
      >
        <Route
          index
          element={mode === "demo" ? <HomePage /> : <RealHome />}
        />
        <Route path="agent" element={<BusinessPage demo={<HomePage />} real={<AgentWorkspace />} />} />
        <Route path="agent/conversations/:conversationId" element={<BusinessPage demo={<HomePage />} real={<AgentWorkspace />} />} />
        <Route
          path="repairs"
          element={
            <BusinessPage demo={<RepairsPage />} real={<RealRepairsPage />} />
          }
        />
        <Route
          path="repairs/:id"
          element={
            <BusinessPage
              demo={<RepairsPage />}
              real={<RealRepairDetailPage />}
            />
          }
        />
        <Route
          path="billing"
          element={
            <BusinessPage demo={<BillingPage />} real={<RealBillingPage />} />
          }
        />
        <Route
          path="billing/bills/:id"
          element={
            <BusinessPage
              demo={<BillingPage />}
              real={<RealBillDetailPage />}
            />
          }
        />
        <Route
          path="billing/consultations/:id"
          element={
            <BusinessPage
              demo={<BillingPage />}
              real={<RealConsultationDetailPage />}
            />
          }
        />
        <Route
          path="community"
          element={
            <BusinessPage
              demo={<CommunityPage />}
              real={<RealCommunityPage />}
            />
          }
        />
        <Route
          path="community/announcements/:id"
          element={
            <BusinessPage
              demo={<CommunityPage />}
              real={<RealAnnouncementDetailPage />}
            />
          }
        />
        <Route
          path="operations"
          element={
            <CapabilityRoute capability="operations">
              <BusinessPage
                demo={<OperationsPage />}
                real={<RealOperationsPage />}
              />
            </CapabilityRoute>
          }
        />
        <Route
          path="operations/inspections/:id"
          element={
            <CapabilityRoute capability="operations">
              <BusinessPage
                demo={<OperationsPage />}
                real={<RealInspectionDetailPage />}
              />
            </CapabilityRoute>
          }
        />
        <Route
          path="operations/security/:id"
          element={
            <CapabilityRoute capability="operations">
              <BusinessPage
                demo={<OperationsPage />}
                real={<RealSecurityDetailPage />}
              />
            </CapabilityRoute>
          }
        />
        <Route
          path="messages"
          element={
            <BusinessPage demo={<MessagesPage />} real={<RealMessagesPage />} />
          }
        />
        <Route
          path="admin"
          element={
            <CapabilityRoute capability="admin">
              <BusinessPage demo={<AdminPage />} real={<RealAdminPage />} />
            </CapabilityRoute>
          }
        />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}

function OptionalShowcase({
  models,
  children,
}: {
  models?: ShowcaseModels;
  children: ReactNode;
}) {
  return models ? (
    <ShowcaseProvider models={models}>{children}</ShowcaseProvider>
  ) : (
    children
  );
}

function RealHome() {
  const { session } = useSession();
  if (session.status !== "authenticated") return null;
  return hasCapability(session.actor.roles, "operations")
    ? <AgentWorkspace />
    : <RealBusinessHomePage />;
}

export function AppProviders({
  services,
  children,
}: {
  services: ApplicationServices;
  children: ReactNode;
}) {
  const [fallbackQueryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false },
          mutations: { retry: false },
        },
      }),
  );
  const queryClient = services.queryClient ?? fallbackQueryClient;
  const content = services.apiClient ? (
    <ApiProvider client={services.apiClient}>{children}</ApiProvider>
  ) : (
    children
  );
  return (
    <QueryClientProvider client={queryClient}>
      <SessionProvider
        store={services.sessionStore}
        auth={services.authentication}
      >
        <RuntimeModeProvider mode={services.mode}>
          <AgentRuntimeProvider>
            <OptionalShowcase models={services.showcaseModels}>
              {content}
            </OptionalShowcase>
          </AgentRuntimeProvider>
        </RuntimeModeProvider>
      </SessionProvider>
    </QueryClientProvider>
  );
}

export function Application({ services }: { services: ApplicationServices }) {
  const Router = services.mode === "demo" ? HashRouter : BrowserRouter;
  return (
    <StrictMode>
      <Router>
        <AppProviders services={services}>
          <AppRoutes />
        </AppProviders>
      </Router>
    </StrictMode>
  );
}
