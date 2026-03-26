import React, { useState } from 'react';
import {
  LayoutDashboard,
  Key,
  Box,
  Workflow,
  FileText,
  Activity,
  CheckCircle2,
  Zap,
  Building2,
  UsersRound,
  Users,
  BarChart3,
  Shield,
  Layers,
  Settings,
  Sparkles,
  LogOut,
} from 'lucide-react';

type NavItemProps = {
  icon: React.ElementType;
  label: string;
  isActive?: boolean;
  expanded: boolean;
};

function NavItem({ icon: Icon, label, isActive, expanded }: NavItemProps) {
  return (
    <button
      className={`w-full flex items-center gap-3 rounded-xl transition-all duration-200 ${
        expanded ? 'px-3 py-2.5' : 'px-0 py-2.5 justify-center'
      } ${
        isActive
          ? 'bg-indigo-500 text-white shadow-sm'
          : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
      }`}
    >
      <Icon className="w-5 h-5 shrink-0" strokeWidth={isActive ? 2 : 1.5} />
      <span
        className={`text-sm font-medium whitespace-nowrap transition-all duration-200 ${
          expanded ? 'opacity-100 w-auto' : 'opacity-0 w-0 overflow-hidden'
        }`}
      >
        {label}
      </span>
    </button>
  );
}

function SectionLabel({ label, expanded }: { label: string; expanded: boolean }) {
  return (
    <div className={`mt-4 mb-1 ${expanded ? 'px-3' : 'px-0 flex justify-center'}`}>
      {expanded ? (
        <span className="text-[10px] font-semibold uppercase tracking-widest text-slate-600">
          {label}
        </span>
      ) : (
        <div className="w-6 h-px bg-slate-700" />
      )}
    </div>
  );
}

export function IconRail() {
  const [hovered, setHovered] = useState(false);

  return (
    <div className="flex min-h-screen bg-slate-50 font-sans text-slate-900">
      <aside
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        className="bg-slate-900 flex flex-col shrink-0 border-r border-slate-800 shadow-xl z-10 transition-all duration-300 ease-in-out"
        style={{ width: hovered ? 220 : 64 }}
      >
        <div className={`flex items-center gap-3 py-4 shrink-0 ${hovered ? 'px-4' : 'px-0 justify-center'}`}>
          <div className="w-9 h-9 bg-indigo-500/20 rounded-xl flex items-center justify-center text-indigo-400 shrink-0">
            <Zap className="w-5 h-5" fill="currentColor" />
          </div>
          <span
            className={`text-base font-bold text-white whitespace-nowrap transition-all duration-200 ${
              hovered ? 'opacity-100 w-auto' : 'opacity-0 w-0 overflow-hidden'
            }`}
          >
            DeltaLLM
          </span>
        </div>

        <nav className="flex-1 overflow-y-auto overflow-x-hidden px-2 pb-4 space-y-0.5">
          <NavItem icon={LayoutDashboard} label="Dashboard" expanded={hovered} />
          <NavItem icon={Key} label="API Keys" expanded={hovered} />

          <SectionLabel label="AI Gateway" expanded={hovered} />
          <NavItem icon={Box} label="Models" isActive expanded={hovered} />
          <NavItem icon={Workflow} label="Route Groups" expanded={hovered} />
          <NavItem icon={FileText} label="Prompt Registry" expanded={hovered} />
          <NavItem icon={Activity} label="MCP Servers" expanded={hovered} />
          <NavItem icon={CheckCircle2} label="Tool Approvals" expanded={hovered} />
          <NavItem icon={Zap} label="Playground" expanded={hovered} />

          <SectionLabel label="Access" expanded={hovered} />
          <NavItem icon={Building2} label="Organizations" expanded={hovered} />
          <NavItem icon={UsersRound} label="Teams" expanded={hovered} />
          <NavItem icon={Users} label="People & Access" expanded={hovered} />

          <SectionLabel label="" expanded={hovered} />
          <NavItem icon={BarChart3} label="Usage" expanded={hovered} />
          <NavItem icon={Shield} label="Audit Logs" expanded={hovered} />
          <NavItem icon={Layers} label="Batch Jobs" expanded={hovered} />
          <NavItem icon={Shield} label="Guardrails" expanded={hovered} />
          <NavItem icon={Settings} label="Settings" expanded={hovered} />
        </nav>

        <div className="border-t border-slate-800 p-3 shrink-0">
          <div className={`flex items-center gap-3 ${hovered ? '' : 'justify-center'}`}>
            <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-indigo-500 to-purple-500 flex items-center justify-center text-white text-xs font-medium shrink-0 border-2 border-slate-800">
              AD
            </div>
            <div
              className={`flex-1 min-w-0 transition-all duration-200 ${
                hovered ? 'opacity-100' : 'opacity-0 w-0 overflow-hidden'
              }`}
            >
              <p className="text-sm text-slate-300 truncate">admin@deltallm.com</p>
              <p className="text-[10px] text-slate-500">Platform Admin</p>
            </div>
          </div>
          {hovered && (
            <button className="mt-3 w-full flex items-center gap-2 text-sm text-slate-500 hover:text-slate-300 transition-colors">
              <LogOut className="w-4 h-4 shrink-0" />
              <span>Sign Out</span>
            </button>
          )}
        </div>
      </aside>

      <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <header className="h-14 border-b border-slate-200 bg-white flex items-center px-8 shrink-0">
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <span>AI Gateway</span>
            <span className="text-slate-300">/</span>
            <span className="text-slate-900 font-medium">Models</span>
          </div>
        </header>

        <div className="flex-1 overflow-auto p-8 bg-slate-50/50">
          <div className="max-w-5xl mx-auto space-y-6">
            <div className="flex items-center justify-between">
              <div>
                <h1 className="text-2xl font-semibold text-slate-900">Models</h1>
                <p className="text-slate-500 text-sm mt-1">Manage and deploy AI models across your infrastructure.</p>
              </div>
              <button className="bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-indigo-700 transition-colors shadow-sm">
                Add Model
              </button>
            </div>

            <div className="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden">
              <div className="grid grid-cols-4 gap-4 p-4 border-b border-slate-100 bg-slate-50/50 text-xs font-medium text-slate-500 uppercase tracking-wider">
                <div className="col-span-2">Model Name</div>
                <div>Provider</div>
                <div>Status</div>
              </div>
              <div className="divide-y divide-slate-100">
                {[
                  { name: 'gpt-4o', provider: 'OpenAI', status: 'Active' },
                  { name: 'claude-3-5-sonnet-20240620', provider: 'Anthropic', status: 'Active' },
                  { name: 'llama-3.1-70b', provider: 'Meta', status: 'Degraded' },
                  { name: 'mixtral-8x7b-instruct', provider: 'Mistral', status: 'Active' },
                  { name: 'gemini-1.5-pro', provider: 'Google', status: 'Active' },
                ].map((model, i) => (
                  <div key={i} className="grid grid-cols-4 gap-4 p-4 items-center hover:bg-slate-50/50 transition-colors cursor-pointer">
                    <div className="col-span-2 flex items-center gap-3">
                      <div className="w-8 h-8 rounded-lg bg-indigo-50 border border-indigo-100 flex items-center justify-center text-indigo-600">
                        <Box className="w-4 h-4" />
                      </div>
                      <span className="font-medium text-slate-900 text-sm">{model.name}</span>
                    </div>
                    <div className="text-sm text-slate-600">{model.provider}</div>
                    <div>
                      <span className={`inline-flex items-center px-2 py-1 rounded-md text-xs font-medium ${
                        model.status === 'Active'
                          ? 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-600/20 ring-inset'
                          : 'bg-amber-50 text-amber-700 ring-1 ring-amber-600/20 ring-inset'
                      }`}>
                        {model.status}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

export default IconRail;
