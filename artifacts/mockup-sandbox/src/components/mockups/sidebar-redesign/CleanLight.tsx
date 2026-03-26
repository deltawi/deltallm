import React, { useState, useCallback, useRef } from 'react';
import {
  LayoutDashboard,
  Box,
  Activity,
  Key,
  Users,
  UsersRound,
  Building2,
  BarChart3,
  Layers,
  Workflow,
  FileText,
  Shield,
  Settings,
  LogOut,
  Zap,
  CheckCircle2,
  PanelLeftClose,
  PanelLeftOpen,
  type LucideIcon,
} from 'lucide-react';
import clsx from 'clsx';

const MIN_WIDTH = 180;
const MAX_WIDTH = 360;
const DEFAULT_WIDTH = 260;
const COLLAPSED_WIDTH = 56;

type NavItem = {
  label: string;
  icon: LucideIcon;
  isActive?: boolean;
};

type NavGroup = {
  label: string;
  items: NavItem[];
};

type NavSection = NavItem | NavGroup;

const navigation: NavSection[] = [
  { label: 'Dashboard', icon: LayoutDashboard },
  { label: 'API Keys', icon: Key },
  {
    label: 'AI Gateway',
    items: [
      { label: 'Models', icon: Box, isActive: true },
      { label: 'Route Groups', icon: Workflow },
      { label: 'Prompt Registry', icon: FileText },
      { label: 'MCP Servers', icon: Activity },
      { label: 'Tool Approvals', icon: CheckCircle2 },
      { label: 'Playground', icon: Zap },
    ],
  },
  {
    label: 'Access',
    items: [
      { label: 'Organizations', icon: Building2 },
      { label: 'Teams', icon: UsersRound },
      { label: 'People & Access', icon: Users },
    ],
  },
  { label: 'Usage', icon: BarChart3 },
  { label: 'Audit Logs', icon: Shield },
  { label: 'Batch Jobs', icon: Layers },
  { label: 'Guardrails', icon: Shield },
  { label: 'Settings', icon: Settings },
];

function NavItemComponent({ item, collapsed }: { item: NavItem; collapsed: boolean }) {
  const { label, icon: Icon, isActive } = item;
  return (
    <a
      href="#"
      onClick={(e) => e.preventDefault()}
      title={collapsed ? label : undefined}
      className={clsx(
        'flex items-center gap-3 text-sm font-medium transition-colors min-w-0',
        collapsed ? 'justify-center px-0 py-2 mx-auto w-10 rounded-lg' : 'px-4 py-2 border-l-2',
        isActive
          ? collapsed
            ? 'bg-blue-50 text-blue-700'
            : 'bg-blue-50 text-blue-700 border-blue-600'
          : collapsed
            ? 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
            : 'text-gray-600 border-transparent hover:bg-gray-50 hover:text-gray-900'
      )}
    >
      <Icon className={clsx('w-4 h-4 shrink-0', isActive ? 'text-blue-600' : 'text-gray-400')} />
      {!collapsed && <span className="truncate">{label}</span>}
    </a>
  );
}

export function CleanLight() {
  const [collapsed, setCollapsed] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [isResizing, setIsResizing] = useState(false);
  const startXRef = useRef(0);
  const startWidthRef = useRef(0);

  const showExpanded = !collapsed || hovered;
  const sidebarWidth = collapsed ? (hovered ? width : COLLAPSED_WIDTH) : width;

  const handlePointerDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    setIsResizing(true);
    startXRef.current = e.clientX;
    startWidthRef.current = width;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  }, [width]);

  const handlePointerMove = useCallback((e: React.PointerEvent) => {
    if (!isResizing) return;
    const delta = e.clientX - startXRef.current;
    const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidthRef.current + delta));
    setWidth(next);
  }, [isResizing]);

  const handlePointerUp = useCallback(() => {
    setIsResizing(false);
  }, []);

  const handleDoubleClick = useCallback(() => {
    setWidth(DEFAULT_WIDTH);
  }, []);

  return (
    <div className="flex min-h-screen bg-gray-50 font-sans select-none">
      <aside
        onMouseEnter={() => collapsed && setHovered(true)}
        onMouseLeave={() => { setHovered(false); setIsResizing(false); }}
        className={clsx(
          'group relative flex flex-col bg-white border-r border-gray-200 shrink-0 overflow-hidden transition-[width] z-10',
          isResizing ? 'duration-0' : 'duration-200'
        )}
        style={{ width: sidebarWidth }}
      >
        <div className="flex items-center justify-between px-3 py-4 min-w-0 shrink-0">
          <div className={clsx('flex items-center gap-2.5 min-w-0', !showExpanded && 'justify-center w-full')}>
            <div className="w-8 h-8 rounded-[10px] bg-violet-100 border border-violet-200/60 flex items-center justify-center shrink-0 shadow-sm">
              <Zap className="w-4 h-4 text-violet-600 fill-violet-600" />
            </div>
            {showExpanded && <span className="text-lg font-bold text-gray-900 truncate">DeltaLLM</span>}
          </div>
          {showExpanded && (
            <button
              type="button"
              onClick={() => { setCollapsed((c) => !c); setHovered(false); }}
              className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors shrink-0"
              title={collapsed ? 'Pin sidebar open' : 'Collapse sidebar'}
            >
              {collapsed ? <PanelLeftOpen className="w-4 h-4" /> : <PanelLeftClose className="w-4 h-4" />}
            </button>
          )}
        </div>

        <nav className="flex-1 overflow-y-auto overflow-x-hidden py-2">
          {navigation.map((section, idx) => {
            if ('items' in section) {
              return (
                <div key={idx} className="mb-4">
                  {showExpanded && (
                    <h3 className="px-5 mb-2 text-xs font-semibold tracking-wider text-gray-400 uppercase truncate">
                      {section.label}
                    </h3>
                  )}
                  {!showExpanded && <div className="w-5 h-px bg-gray-200 mx-auto my-2" />}
                  <div className="space-y-0.5">
                    {section.items.map((item, itemIdx) => (
                      <NavItemComponent key={itemIdx} item={item} collapsed={!showExpanded} />
                    ))}
                  </div>
                </div>
              );
            }
            return (
              <div key={idx} className="mb-0.5">
                <NavItemComponent item={section} collapsed={!showExpanded} />
              </div>
            );
          })}
        </nav>

        <div className="p-3 border-t border-gray-100 shrink-0">
          {showExpanded ? (
            <div className="flex items-center justify-between min-w-0">
              <div className="flex items-center gap-3 min-w-0">
                <div className="flex items-center justify-center w-8 h-8 rounded-full bg-blue-100 text-blue-700 font-medium shrink-0 text-sm">
                  A
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-900 truncate">admin@deltallm.com</p>
                  <span className="inline-block px-1.5 py-0.5 text-[10px] font-medium text-blue-700 bg-blue-50 rounded-sm">
                    Admin
                  </span>
                </div>
              </div>
              <button
                type="button"
                className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-50 rounded-lg transition-colors shrink-0"
                aria-label="Sign out"
              >
                <LogOut className="w-4 h-4" />
              </button>
            </div>
          ) : (
            <div className="flex justify-center">
              <div className="w-8 h-8 rounded-full bg-blue-100 text-blue-700 font-medium flex items-center justify-center text-sm" title="admin@deltallm.com">
                A
              </div>
            </div>
          )}
        </div>

        {showExpanded && (
          <div
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onDoubleClick={handleDoubleClick}
            className="absolute inset-y-0 right-0 w-3 cursor-col-resize z-20"
          >
            <span
              className={clsx(
                'absolute inset-y-0 right-0 w-px transition-colors',
                isResizing ? 'bg-blue-500' : 'bg-transparent group-hover:bg-gray-300'
              )}
            />
            <span
              className={clsx(
                'pointer-events-none absolute right-0.5 top-1/2 -translate-y-1/2 flex h-10 w-3 items-center justify-center rounded-full border bg-white text-[8px] text-gray-400 opacity-0 shadow-sm transition-opacity',
                isResizing
                  ? 'opacity-100 border-blue-400 text-blue-500'
                  : 'border-gray-200 group-hover:opacity-100'
              )}
            >
              ||
            </span>
          </div>
        )}
      </aside>

      <main className="flex-1 flex flex-col min-w-0">
        <header className="h-16 border-b border-gray-200 bg-white flex items-center px-8">
          <h1 className="text-xl font-semibold text-gray-900">Models</h1>
        </header>
        <div className="flex-1 p-8">
          <div className="max-w-4xl border-2 border-dashed border-gray-200 rounded-xl h-[400px] flex items-center justify-center">
            <p className="text-gray-500">Page Content</p>
          </div>
        </div>
      </main>
    </div>
  );
}

export default CleanLight;
