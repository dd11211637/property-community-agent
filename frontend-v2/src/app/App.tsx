import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, useState, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { hasCapability, type Capability } from "../auth/capabilities";
import { SessionProvider } from "../auth/SessionContext";
import { useSession } from "../auth/useSession";
import type { AuthenticationPort, SessionStore } from "../auth/session";
import { AppShell } from "../layout/AppShell";
import { ShowcaseProvider } from "../models/ShowcaseContext";
import type { ShowcaseModels } from "../models/viewModels";
import { AdminPage, BillingPage, CommunityPage, ForbiddenPage, HomePage, LoginPage, MessagesPage, NotFoundPage, OperationsPage, RepairsPage } from "../pages/pages";

export type ApplicationServices = {
  sessionStore: SessionStore;
  authentication: AuthenticationPort;
  showcaseModels: ShowcaseModels;
};

function ProtectedRoute({ children }: { children: ReactNode }) {
  const { session } = useSession();
  const location = useLocation();
  return session.status === "authenticated" ? children : <Navigate to="/login" state={{ from: location.pathname }} replace />;
}

function CapabilityRoute({ capability, children }: { capability: Capability; children: ReactNode }) {
  const { session } = useSession();
  if (session.status !== "authenticated") return <Navigate to="/login" replace />;
  return hasCapability(session.actor.roles, capability) ? children : <ForbiddenPage />;
}

function LoginRoute() {
  const { session } = useSession();
  return session.status === "authenticated" ? <Navigate to="/" replace /> : <LoginPage />;
}

export function AppRoutes() {
  return <Routes><Route path="/login" element={<LoginRoute />} /><Route element={<ProtectedRoute><AppShell /></ProtectedRoute>}><Route index element={<HomePage />} /><Route path="repairs" element={<RepairsPage />} /><Route path="billing" element={<BillingPage />} /><Route path="community" element={<CommunityPage />} /><Route path="operations" element={<CapabilityRoute capability="operations"><OperationsPage /></CapabilityRoute>} /><Route path="messages" element={<MessagesPage />} /><Route path="admin" element={<CapabilityRoute capability="admin"><AdminPage /></CapabilityRoute>} /></Route><Route path="*" element={<NotFoundPage />} /></Routes>;
}

export function AppProviders({ services, children }: { services: ApplicationServices; children: ReactNode }) {
  const [queryClient] = useState(() => new QueryClient({ defaultOptions: { queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false }, mutations: { retry: false } } }));
  return <QueryClientProvider client={queryClient}><SessionProvider store={services.sessionStore} auth={services.authentication}><ShowcaseProvider models={services.showcaseModels}>{children}</ShowcaseProvider></SessionProvider></QueryClientProvider>;
}

export function Application({ services }: { services: ApplicationServices }) {
  return <StrictMode><BrowserRouter><AppProviders services={services}><AppRoutes /></AppProviders></BrowserRouter></StrictMode>;
}
