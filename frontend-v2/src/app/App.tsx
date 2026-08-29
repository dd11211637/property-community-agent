import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, useState, type ReactNode } from "react";
import { BrowserRouter, HashRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { hasCapability, type Capability } from "../auth/capabilities";
import { SessionProvider } from "../auth/SessionContext";
import { useSession } from "../auth/useSession";
import type { AuthenticationPort, SessionStore } from "../auth/session";
import { AppShell } from "../layout/AppShell";
import { ShowcaseProvider } from "../models/ShowcaseContext";
import type { ShowcaseModels } from "../models/viewModels";
import { AdminPage, BillingPage, CommunityPage, ForbiddenPage, HomePage, LoginPage, MessagesPage, NotFoundPage, OperationsPage, RepairsPage } from "../pages/pages";
import { BootPage, MigrationPlaceholder, RealHomePage, RealLoginPage } from "../pages/runtimePages";
import { RuntimeModeProvider } from "./runtimeMode";
import { useRuntimeMode, type RuntimeMode } from "./runtimeModeDefinition";

export type ApplicationServices = {
  sessionStore: SessionStore;
  authentication: AuthenticationPort;
  mode: RuntimeMode;
  showcaseModels?: ShowcaseModels;
  queryClient?: QueryClient;
};

function ProtectedRoute({ children }: { children: ReactNode }) {
  const { session } = useSession();
  const location = useLocation();
  if (session.status === "restoring") return <BootPage />;
  return session.status === "authenticated" ? children : <Navigate to="/login" state={{ from: location.pathname }} replace />;
}

function CapabilityRoute({ capability, children }: { capability: Capability; children: ReactNode }) {
  const { session } = useSession();
  if (session.status === "restoring") return <BootPage />;
  if (session.status !== "authenticated") return <Navigate to="/login" replace />;
  return hasCapability(session.actor.roles, capability) ? children : <ForbiddenPage />;
}

function LoginRoute() {
  const { session } = useSession();
  const mode = useRuntimeMode();
  if (session.status === "restoring") return <BootPage />;
  if (session.status === "authenticated") return <Navigate to="/" replace />;
  return mode === "demo" ? <LoginPage /> : <RealLoginPage />;
}

function BusinessPage({ demo }: { demo: ReactNode }) {
  return useRuntimeMode() === "demo" ? demo : <MigrationPlaceholder />;
}

export function AppRoutes() {
  const mode = useRuntimeMode();
  return <Routes><Route path="/login" element={<LoginRoute />} /><Route element={<ProtectedRoute><AppShell /></ProtectedRoute>}><Route index element={mode === "demo" ? <HomePage /> : <RealHomePage />} /><Route path="repairs" element={<BusinessPage demo={<RepairsPage />} />} /><Route path="billing" element={<BusinessPage demo={<BillingPage />} />} /><Route path="community" element={<BusinessPage demo={<CommunityPage />} />} /><Route path="operations" element={<CapabilityRoute capability="operations"><BusinessPage demo={<OperationsPage />} /></CapabilityRoute>} /><Route path="messages" element={<BusinessPage demo={<MessagesPage />} />} /><Route path="admin" element={<CapabilityRoute capability="admin"><BusinessPage demo={<AdminPage />} /></CapabilityRoute>} /></Route><Route path="*" element={<NotFoundPage />} /></Routes>;
}

function OptionalShowcase({ models, children }: { models?: ShowcaseModels; children: ReactNode }) {
  return models ? <ShowcaseProvider models={models}>{children}</ShowcaseProvider> : children;
}

export function AppProviders({ services, children }: { services: ApplicationServices; children: ReactNode }) {
  const [fallbackQueryClient] = useState(() => new QueryClient({ defaultOptions: { queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false }, mutations: { retry: false } } }));
  const queryClient = services.queryClient ?? fallbackQueryClient;
  return <QueryClientProvider client={queryClient}><SessionProvider store={services.sessionStore} auth={services.authentication}><RuntimeModeProvider mode={services.mode}><OptionalShowcase models={services.showcaseModels}>{children}</OptionalShowcase></RuntimeModeProvider></SessionProvider></QueryClientProvider>;
}

export function Application({ services }: { services: ApplicationServices }) {
  const Router = services.mode === "demo" ? HashRouter : BrowserRouter;
  return <StrictMode><Router><AppProviders services={services}><AppRoutes /></AppProviders></Router></StrictMode>;
}
