import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import { AppShell } from "./components/AppShell";
import { AdminPage } from "./pages/AdminPage";
import { AnnouncementsPage } from "./pages/AnnouncementsPage";
import { BillingPage } from "./pages/BillingPage";
import { HomePage } from "./pages/HomePage";
import { InspectionPage } from "./pages/InspectionPage";
import { LoginPage } from "./pages/LoginPage";
import { MessagesPage } from "./pages/MessagesPage";
import { RepairsPage } from "./pages/RepairsPage";

function ProtectedLayout() {
  const { session } = useAuth();
  return session ? <AppShell /> : <Navigate to="/login" replace />;
}

export function App() {
  return <Routes><Route path="/login" element={<LoginPage />} /><Route element={<ProtectedLayout />}><Route index element={<HomePage />} /><Route path="repairs" element={<RepairsPage />} /><Route path="announcements" element={<AnnouncementsPage />} /><Route path="billing" element={<BillingPage />} /><Route path="inspection" element={<InspectionPage />} /><Route path="messages" element={<MessagesPage />} /><Route path="admin" element={<AdminPage />} /></Route><Route path="*" element={<Navigate to="/" replace />} /></Routes>;
}
