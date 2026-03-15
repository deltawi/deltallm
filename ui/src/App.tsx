import { Routes, Route, Navigate } from 'react-router-dom';
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
import BatchJobs from './pages/BatchJobs';
import BatchJobDetail from './pages/BatchJobDetail';
import OrganizationDetail from './pages/OrganizationDetail';
import TeamDetail from './pages/TeamDetail';
import ModelDetail from './pages/ModelDetail';
import ModelEdit from './pages/ModelEdit';
import ModelCreate from './pages/ModelCreate';
import AuditLogs from './pages/AuditLogs';
import RouteGroups from './pages/RouteGroups';
import RouteGroupDetail from './pages/RouteGroupDetail';
import PromptRegistry from './pages/PromptRegistry';
import PromptTemplateDetail from './pages/PromptTemplateDetail';
import MCPServers from './pages/MCPServers';
import MCPServerDetail from './pages/MCPServerDetail';
import MCPApprovalQueue from './pages/MCPApprovalQueue';
import { ToastProvider } from './components/ToastProvider';

function AppRoutes() {
  const { isAuthenticated, isLoading, session, authMode, mfaSkipped } = useAuth();
  const userRole = session?.role || (authMode === 'master_key' ? 'platform_admin' : '');
  const isPlatformAdmin = userRole === 'platform_admin';
  const permissions = new Set(session?.effective_permissions || []);
  const canReadMcp = isPlatformAdmin || permissions.has('key.read');
  const canReviewMcp = isPlatformAdmin || permissions.has('key.update');
  const canReadAudit = isPlatformAdmin || (session?.effective_permissions || []).includes('audit.read');

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
        <Route path="/models/new" element={<ModelCreate />} />
        <Route path="/models/:deploymentId" element={<ModelDetail />} />
        <Route path="/models/:deploymentId/edit" element={<ModelEdit />} />
        <Route path="/route-groups" element={isPlatformAdmin ? <RouteGroups /> : <Navigate to="/" replace />} />
        <Route path="/route-groups/:groupKey" element={isPlatformAdmin ? <RouteGroupDetail /> : <Navigate to="/" replace />} />
        <Route path="/prompts" element={isPlatformAdmin ? <PromptRegistry /> : <Navigate to="/" replace />} />
        <Route path="/prompts/:templateKey" element={isPlatformAdmin ? <PromptTemplateDetail /> : <Navigate to="/" replace />} />
        <Route path="/mcp-servers" element={canReadMcp ? <MCPServers /> : <Navigate to="/" replace />} />
        <Route path="/mcp-servers/:serverId" element={canReadMcp ? <MCPServerDetail /> : <Navigate to="/" replace />} />
        <Route path="/mcp-approvals" element={canReviewMcp ? <MCPApprovalQueue /> : <Navigate to="/" replace />} />
        <Route path="/keys" element={<ApiKeys />} />
        <Route path="/organizations" element={<Organizations />} />
        <Route path="/organizations/:orgId" element={<OrganizationDetail />} />
        <Route path="/teams" element={<Teams />} />
        <Route path="/teams/:teamId" element={<TeamDetail />} />
        <Route path="/users" element={<UsersPage />} />
        <Route path="/audit" element={canReadAudit ? <AuditLogs /> : <Navigate to="/" replace />} />
        <Route path="/usage" element={<Usage />} />
        <Route path="/batches" element={<BatchJobs />} />
        <Route path="/batches/:batchId" element={<BatchJobDetail />} />
        <Route path="/guardrails" element={isPlatformAdmin ? <Guardrails /> : <Navigate to="/" replace />} />
        <Route path="/settings" element={isPlatformAdmin ? <SettingsPage /> : <Navigate to="/" replace />} />
        <Route path="/access-control" element={<Navigate to="/users" replace />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <AppRoutes />
      </ToastProvider>
    </AuthProvider>
  );
}
