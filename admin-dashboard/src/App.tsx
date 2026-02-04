import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';
import { Layout } from '@/components/Layout';
import { Login } from '@/pages/Login';
import { Dashboard } from '@/pages/Dashboard';
import { Organizations } from '@/pages/Organizations';
import { OrganizationDetail } from '@/pages/OrganizationDetail';
import { Teams } from '@/pages/Teams';
import { TeamDetail } from '@/pages/TeamDetail';
import { ApiKeys } from '@/pages/ApiKeys';
import { ApiKeyDetail } from '@/pages/ApiKeyDetail';
import { Models } from '@/pages/Models';
import { ModelDetail } from '@/pages/ModelDetail';
import { Providers } from '@/pages/Providers';
import { Deployments } from '@/pages/Deployments';
import { Guardrails } from '@/pages/Guardrails';
import { Budget } from '@/pages/Budget';
import { AuditLogs } from '@/pages/AuditLogs';
import { Settings } from '@/pages/Settings';
import './index.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="organizations" element={<Organizations />} />
        <Route path="organizations/:id" element={<OrganizationDetail />} />
        <Route path="teams" element={<Teams />} />
        <Route path="teams/:id" element={<TeamDetail />} />
        <Route path="api-keys" element={<ApiKeys />} />
        <Route path="api-keys/:id" element={<ApiKeyDetail />} />
        <Route path="models" element={<Models />} />
        <Route path="models/:id" element={<ModelDetail />} />
        <Route path="providers" element={<Providers />} />
          <Route path="deployments" element={<Deployments />} />
        <Route path="budget" element={<Budget />} />
        <Route path="audit" element={<AuditLogs />} />
        <Route path="settings" element={<Settings />} />
        <Route path="guardrails" element={<Guardrails />} />
      </Route>
    </Routes>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <AppRoutes />
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}

export default App;
