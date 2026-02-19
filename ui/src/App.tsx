import { Routes, Route } from 'react-router-dom';
import { AuthProvider, useAuth } from './lib/auth';
import Layout from './components/Layout';
import Login from './pages/Login';
import ForcePasswordChange from './pages/ForcePasswordChange';
import MFAEnrollment from './pages/MFAEnrollment';
import Dashboard from './pages/Dashboard';
import Models from './pages/Models';
import ApiKeys from './pages/ApiKeys';
import Organizations from './pages/Organizations';
import Teams from './pages/Teams';
import UsersPage from './pages/UsersPage';
import Usage from './pages/Usage';
import Guardrails from './pages/Guardrails';
import SettingsPage from './pages/SettingsPage';
import RBACAccounts from './pages/RBACAccounts';
import OrganizationDetail from './pages/OrganizationDetail';
import TeamDetail from './pages/TeamDetail';

function AppRoutes() {
  const { isAuthenticated, isLoading, session, authMode, mfaSkipped } = useAuth();

  if (isLoading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Login />;
  }

  if (authMode === 'session' && session?.force_password_change) {
    return <ForcePasswordChange />;
  }

  if (authMode === 'session' && session?.mfa_prompt && !session?.mfa_enabled && !mfaSkipped) {
    return <MFAEnrollment />;
  }

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/models" element={<Models />} />
        <Route path="/keys" element={<ApiKeys />} />
        <Route path="/organizations" element={<Organizations />} />
        <Route path="/organizations/:orgId" element={<OrganizationDetail />} />
        <Route path="/teams" element={<Teams />} />
        <Route path="/teams/:teamId" element={<TeamDetail />} />
        <Route path="/users" element={<UsersPage />} />
        <Route path="/usage" element={<Usage />} />
        <Route path="/guardrails" element={<Guardrails />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/access-control" element={<RBACAccounts />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  );
}
