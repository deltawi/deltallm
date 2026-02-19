import { NavLink, Outlet } from 'react-router-dom';
import { useAuth } from '../lib/auth';
import {
  LayoutDashboard,
  Box,
  Key,
  Users,
  UsersRound,
  Building2,
  BarChart3,
  Shield,
  Settings,
  LogOut,
  Zap,
  UserCog,
} from 'lucide-react';
import clsx from 'clsx';

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/models', icon: Box, label: 'Models' },
  { to: '/keys', icon: Key, label: 'API Keys' },
  { to: '/organizations', icon: Building2, label: 'Organizations' },
  { to: '/teams', icon: UsersRound, label: 'Teams' },
  { to: '/users', icon: Users, label: 'Users' },
  { to: '/usage', icon: BarChart3, label: 'Usage' },
  { to: '/guardrails', icon: Shield, label: 'Guardrails' },
  { to: '/access-control', icon: UserCog, label: 'Access Control', adminOnly: true },
  { to: '/settings', icon: Settings, label: 'Settings' },
];

function RoleBadge({ role }: { role: string }) {
  const colors: Record<string, string> = {
    platform_admin: 'bg-purple-500/20 text-purple-300',
    platform_co_admin: 'bg-indigo-500/20 text-indigo-300',
    org_user: 'bg-gray-500/20 text-gray-400',
  };
  const label = role === 'platform_admin' ? 'Admin' : role === 'platform_co_admin' ? 'Co-Admin' : 'User';
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${colors[role] || 'bg-gray-500/20 text-gray-400'}`}>
      {label}
    </span>
  );
}

export default function Layout() {
  const { logout, session, authMode } = useAuth();

  const displayEmail = authMode === 'master_key' ? 'Master Key' : (session?.email || 'Unknown');
  const displayRole = session?.role || (authMode === 'master_key' ? 'platform_admin' : '');
  const isPlatformAdmin = displayRole === 'platform_admin' || displayRole === 'platform_co_admin';

  const visibleNavItems = navItems.filter(item => !item.adminOnly || isPlatformAdmin);

  return (
    <div className="flex h-screen bg-gray-50">
      <aside className="w-64 bg-gray-900 text-white flex flex-col">
        <div className="p-5 border-b border-gray-800">
          <div className="flex items-center gap-2">
            <Zap className="w-6 h-6 text-blue-400" />
            <span className="text-lg font-bold">DeltaLLM</span>
          </div>
          <p className="text-xs text-gray-400 mt-1">Admin Dashboard</p>
        </div>
        <nav className="flex-1 py-4 overflow-y-auto">
          {visibleNavItems.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                clsx(
                  'flex items-center gap-3 px-5 py-2.5 text-sm transition-colors',
                  isActive
                    ? 'bg-gray-800 text-white border-r-2 border-blue-400'
                    : 'text-gray-400 hover:text-white hover:bg-gray-800/50'
                )
              }
            >
              <Icon className="w-4 h-4" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="p-4 border-t border-gray-800">
          <div className="mb-3">
            <div className="flex items-center gap-2">
              <span className="text-sm text-gray-300 truncate">{displayEmail}</span>
              {displayRole && <RoleBadge role={displayRole} />}
            </div>
          </div>
          <button
            onClick={logout}
            className="flex items-center gap-2 text-sm text-gray-400 hover:text-white transition-colors w-full"
          >
            <LogOut className="w-4 h-4" />
            Sign Out
          </button>
        </div>
      </aside>
      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  );
}
