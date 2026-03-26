import { Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './lib/auth';
import Layout from './components/Layout';
import AcceptInvite from './pages/AcceptInvite';
import ForgotPassword from './pages/ForgotPassword';
import Login from './pages/Login';
import ForcePasswordChange from './pages/ForcePasswordChange';
import MFAEnrollment from './pages/MFAEnrollment';
import MFAVerify from './pages/MFAVerify';
import ResetPassword from './pages/ResetPassword';
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
import OrganizationCreate from './pages/OrganizationCreate';
import TeamCreate from './pages/TeamCreate';
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
import Playground from './pages/Playground';
import { ToastProvider } from './components/ToastProvider';
import { defaultRouteForUiAccess, resolveUiAccess } from './lib/authorization';

function AppRoutes() {
  const { isAuthenticated, isLoading, session, authMode, mfaSkipped } = useAuth();
  const uiAccess = resolveUiAccess(authMode, session);
  const defaultRoute = defaultRouteForUiAccess(uiAccess);

  if (isLoading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route path="/accept-invite" element={<AcceptInvite />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  if (authMode === 'session' && session?.mfa_enabled && !session?.mfa_verified) {
    return <MFAVerify />;
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
        <Route path="/" element={uiAccess.dashboard ? <Dashboard /> : <Navigate to={defaultRoute} replace />} />
        <Route path="/models" element={uiAccess.models ? <Models /> : <Navigate to="/" replace />} />
        <Route path="/models/new" element={uiAccess.model_admin ? <ModelCreate /> : <Navigate to="/models" replace />} />
        <Route path="/models/:deploymentId" element={uiAccess.models ? <ModelDetail /> : <Navigate to="/" replace />} />
        <Route path="/models/:deploymentId/edit" element={uiAccess.model_admin ? <ModelEdit /> : <Navigate to="/models" replace />} />
        <Route path="/route-groups" element={uiAccess.route_groups ? <RouteGroups /> : <Navigate to="/" replace />} />
        <Route path="/route-groups/:groupKey" element={uiAccess.route_groups ? <RouteGroupDetail /> : <Navigate to="/" replace />} />
        <Route path="/prompts" element={uiAccess.prompts ? <PromptRegistry /> : <Navigate to="/" replace />} />
        <Route path="/prompts/:templateKey" element={uiAccess.prompts ? <PromptTemplateDetail /> : <Navigate to="/" replace />} />
        <Route path="/mcp-servers" element={uiAccess.mcp_servers ? <MCPServers /> : <Navigate to="/" replace />} />
        <Route path="/mcp-servers/:serverId" element={uiAccess.mcp_servers ? <MCPServerDetail /> : <Navigate to="/" replace />} />
        <Route path="/mcp-approvals" element={uiAccess.mcp_approvals ? <MCPApprovalQueue /> : <Navigate to="/" replace />} />
        <Route path="/keys" element={uiAccess.keys ? <ApiKeys /> : <Navigate to="/" replace />} />
        <Route path="/organizations" element={uiAccess.organizations ? <Organizations /> : <Navigate to="/" replace />} />
        <Route path="/organizations/new" element={uiAccess.organization_create ? <OrganizationCreate /> : <Navigate to="/organizations" replace />} />
        <Route path="/organizations/:orgId" element={uiAccess.organizations ? <OrganizationDetail /> : <Navigate to="/" replace />} />
        <Route path="/teams" element={uiAccess.teams ? <Teams /> : <Navigate to="/" replace />} />
        <Route path="/teams/new" element={uiAccess.team_create ? <TeamCreate /> : <Navigate to="/teams" replace />} />
        <Route path="/teams/:teamId" element={uiAccess.teams ? <TeamDetail /> : <Navigate to="/" replace />} />
        <Route path="/users" element={uiAccess.people_access ? <UsersPage /> : <Navigate to="/" replace />} />
        <Route path="/audit" element={uiAccess.audit ? <AuditLogs /> : <Navigate to="/" replace />} />
        <Route path="/usage" element={uiAccess.usage ? <Usage /> : <Navigate to="/" replace />} />
        <Route path="/batches" element={uiAccess.batches ? <BatchJobs /> : <Navigate to="/" replace />} />
        <Route path="/batches/:batchId" element={uiAccess.batches ? <BatchJobDetail /> : <Navigate to="/" replace />} />
        <Route path="/guardrails" element={uiAccess.guardrails ? <Guardrails /> : <Navigate to="/" replace />} />
        <Route path="/playground" element={uiAccess.playground ? <Playground /> : <Navigate to="/" replace />} />
        <Route path="/settings" element={uiAccess.settings ? <SettingsPage /> : <Navigate to="/" replace />} />
        <Route path="/access-control" element={<Navigate to={uiAccess.people_access ? "/users" : defaultRoute} replace />} />
        <Route path="/login" element={<Navigate to={defaultRoute} replace />} />
        <Route path="/forgot-password" element={<Navigate to={defaultRoute} replace />} />
        <Route path="/reset-password" element={<Navigate to={defaultRoute} replace />} />
        <Route path="/accept-invite" element={<Navigate to={defaultRoute} replace />} />
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
