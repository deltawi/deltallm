import React from 'react';
import { 
  ArrowLeft, 
  Brain, 
  Shuffle, 
  Edit, 
  Trash2, 
  Server, 
  Terminal, 
  Settings, 
  Layers,
  Tag,
  Code2,
  CheckCircle2,
  XCircle,
  ShieldAlert,
  ChevronDown,
  Trash
} from 'lucide-react';

export function RgTabAdvanced() {
  return (
    <div className="min-h-screen bg-gray-50 font-sans text-slate-900">
      {/* Top Nav */}
      <div className="bg-white border-b border-gray-200 px-6 py-3">
        <a href="#" className="flex items-center text-sm text-gray-500 hover:text-gray-900 font-medium">
          <ArrowLeft className="w-4 h-4 mr-2" />
          Back to Model Groups
        </a>
      </div>

      {/* Hero Header */}
      <div className="bg-gradient-to-r from-blue-50 via-white to-slate-50 border-b border-gray-200 px-6 py-8">
        <div className="max-w-5xl mx-auto">
          <div className="flex items-start justify-between">
            <div>
              <div className="flex items-center gap-2 mb-3">
                <span className="inline-flex items-center gap-1.5 rounded-md bg-blue-100 px-2 py-1 text-xs font-medium text-blue-700">
                  <Brain className="w-3.5 h-3.5" />
                  Chat
                </span>
                <span className="inline-flex items-center gap-1.5 rounded-md bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700 border border-emerald-200">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
                  Live
                </span>
                <span className="inline-flex items-center gap-1.5 rounded-md bg-slate-100 px-2 py-1 text-xs font-medium text-slate-700">
                  <Shuffle className="w-3.5 h-3.5" />
                  Shuffle
                </span>
              </div>
              <h1 className="text-3xl font-bold tracking-tight text-gray-900 mb-2">Production Chat</h1>
              <div className="flex items-center gap-3 text-sm">
                <code className="text-xs bg-gray-100 text-gray-600 px-2 py-1 rounded font-mono border border-gray-200">
                  prod-chat-primary
                </code>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button className="flex items-center justify-center w-9 h-9 rounded-lg border border-gray-200 bg-white text-gray-500 hover:text-gray-900 hover:bg-gray-50 shadow-sm transition-colors">
                <Edit className="w-4 h-4" />
              </button>
              <button className="flex items-center justify-center w-9 h-9 rounded-lg border border-gray-200 bg-white text-gray-500 hover:text-red-600 hover:bg-red-50 shadow-sm transition-colors">
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>
          
          <div className="mt-8 flex items-center gap-6 text-sm text-gray-600">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-gray-900">3</span> Members
            </div>
            <div className="w-px h-4 bg-gray-300"></div>
            <div className="flex items-center gap-2">
              <span className="font-semibold text-emerald-600">3</span> Healthy
            </div>
            <div className="w-px h-4 bg-gray-300"></div>
            <div className="flex items-center gap-2">
              Policy <span className="font-semibold text-gray-900">v3</span>
            </div>
            <div className="w-px h-4 bg-gray-300"></div>
            <div className="flex items-center gap-2">
              <span className="font-semibold text-violet-600">1</span> Prompt
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-5xl mx-auto px-6 py-6">
        {/* Tabs */}
        <div className="flex items-center gap-8 border-b border-gray-200 mb-6 px-2">
          <button className="flex items-center gap-2 pb-4 text-sm font-medium text-gray-500 hover:text-gray-900 transition-colors border-b-2 border-transparent">
            <Server className="w-4 h-4" />
            Models
          </button>
          <button className="flex items-center gap-2 pb-4 text-sm font-medium text-gray-500 hover:text-gray-900 transition-colors border-b-2 border-transparent">
            <Terminal className="w-4 h-4" />
            Simulator
          </button>
          <button className="flex items-center gap-2 pb-4 text-sm font-medium text-blue-600 border-b-2 border-blue-600">
            <Settings className="w-4 h-4" />
            Advanced
          </button>
          <button className="flex items-center gap-2 pb-4 text-sm font-medium text-gray-500 hover:text-gray-900 transition-colors border-b-2 border-transparent">
            <Layers className="w-4 h-4" />
            Analytics
          </button>
        </div>

        {/* Tab Panel */}
        <div className="bg-white rounded-2xl border border-gray-200 p-5 shadow-sm space-y-5">
          
          {/* CARD 1: Prompt Binding */}
          <div className="rounded-2xl border border-gray-200 bg-white p-4">
            <div className="mb-4">
              <h2 className="text-base font-semibold text-gray-900">Prompt Binding</h2>
              <p className="text-sm text-gray-500 mt-0.5">Attach a prompt so the gateway resolves it automatically.</p>
            </div>
            
            <div className="space-y-3">
              <div className="flex items-center justify-between rounded-xl border border-violet-100 bg-violet-50 px-4 py-3">
                <div className="flex items-center gap-3">
                  <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-violet-100 text-violet-600">
                    <Tag className="w-4 h-4" />
                  </div>
                  <div>
                    <div className="font-mono text-sm font-semibold text-violet-900">support.reply</div>
                    <div className="text-xs text-violet-600 mt-0.5">label: production · priority 100 · active</div>
                  </div>
                </div>
                <button className="text-violet-300 hover:text-red-400 transition-colors">
                  <Trash className="w-4 h-4" />
                </button>
              </div>

              <div className="flex gap-2 items-center pt-1">
                <div className="relative flex-1">
                  <select className="w-full appearance-none rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-500 focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500">
                    <option value="" disabled selected>Select a prompt</option>
                    <option value="1">support.reply</option>
                    <option value="2">sales.inquiry</option>
                  </select>
                  <ChevronDown className="absolute right-3 top-2.5 w-4 h-4 text-gray-400 pointer-events-none" />
                </div>
                <input 
                  type="text" 
                  placeholder="production"
                  className="w-28 rounded-xl border border-gray-200 px-3 py-2 text-sm focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500" 
                />
                <input 
                  type="text" 
                  placeholder="100"
                  className="w-16 rounded-xl border border-gray-200 px-3 py-2 text-sm focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500" 
                />
                <button className="rounded-xl bg-violet-600 px-4 py-2 text-xs font-semibold text-white hover:bg-violet-700 transition-colors">
                  Bind
                </button>
              </div>
            </div>
          </div>

          {/* CARD 2: Routing Policy */}
          <div className="rounded-2xl border border-gray-200 bg-white p-4">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-base font-semibold text-gray-900 inline-flex items-center gap-2">
                  Routing Policy
                  <span className="text-xs font-normal text-gray-500 inline-flex items-center gap-1.5">
                    Version 3 · <span className="inline-flex items-center text-emerald-600 font-medium bg-emerald-50 px-1.5 py-0.5 rounded-md border border-emerald-100"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>Published</span>
                  </span>
                </h2>
              </div>
              <div className="flex items-center gap-2">
                <button className="px-3 py-1.5 text-xs font-medium text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors">
                  Validate
                </button>
                <button className="px-3 py-1.5 text-xs font-medium text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors">
                  Save Draft
                </button>
                <button className="px-3 py-1.5 text-xs font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors shadow-sm">
                  Publish
                </button>
              </div>
            </div>

            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1">Strategy</label>
                  <div className="relative">
                    <select className="w-full appearance-none rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500">
                      <option value="weighted">weighted</option>
                      <option value="priority">priority</option>
                      <option value="shuffle">shuffle</option>
                    </select>
                    <ChevronDown className="absolute right-3 top-2.5 w-4 h-4 text-gray-400 pointer-events-none" />
                  </div>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1">Fallback</label>
                  <div className="relative">
                    <select className="w-full appearance-none rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500">
                      <option value="shuffle">shuffle</option>
                      <option value="none">none</option>
                    </select>
                    <ChevronDown className="absolute right-3 top-2.5 w-4 h-4 text-gray-400 pointer-events-none" />
                  </div>
                </div>
              </div>

              <div className="rounded-xl bg-gray-950 px-4 py-3">
                <pre className="text-xs font-mono text-gray-100 whitespace-pre-wrap leading-relaxed">
{`{
  "strategy": "weighted",
  "fallback": "shuffle",
  "cooldown_seconds": 60,
  "members": [
    "dep_gpt4t_01",
    "dep_gpt4o_02",
    "dep_claude3_03"
  ]
}`}
                </pre>
              </div>

              <div className="flex items-center justify-between">
                <button className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-gray-600 transition-colors">
                  <Code2 className="w-3.5 h-3.5" />
                  Edit raw JSON
                </button>
              </div>

              <div className="flex items-center gap-2 rounded-xl border border-emerald-100 bg-emerald-50 px-3 py-2.5 text-sm text-emerald-700">
                <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                Policy is valid. 3 members resolved.
              </div>
            </div>
          </div>

          {/* CARD 3: Policy History */}
          <div className="rounded-2xl border border-gray-200 bg-white p-4">
            <div className="mb-4">
              <h2 className="text-base font-semibold text-gray-900">Policy History</h2>
              <p className="text-sm text-gray-500 mt-0.5">Rollback restores a previous policy as new published version.</p>
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between rounded-xl border border-gray-100 bg-white px-4 py-3 hover:bg-gray-50 transition-colors">
                <div className="flex items-center gap-3">
                  <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-emerald-50 text-emerald-700 border border-emerald-200 uppercase tracking-wide">
                    Published
                  </span>
                  <span className="text-sm font-medium text-gray-900">Version 3</span>
                  <span className="text-xs text-gray-500">(published)</span>
                </div>
                {/* No rollback button for active */}
              </div>

              <div className="flex items-center justify-between rounded-xl border border-gray-100 bg-white px-4 py-3 hover:bg-gray-50 transition-colors">
                <div className="flex items-center gap-3">
                  <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-gray-100 text-gray-600 border border-gray-200 uppercase tracking-wide">
                    Archived
                  </span>
                  <span className="text-sm font-medium text-gray-900">Version 2</span>
                  <span className="text-xs text-gray-500">2 days ago</span>
                </div>
                <button className="px-3 py-1.5 text-xs font-medium text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors">
                  Roll back
                </button>
              </div>

              <div className="flex items-center justify-between rounded-xl border border-gray-100 bg-white px-4 py-3 hover:bg-gray-50 transition-colors">
                <div className="flex items-center gap-3">
                  <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-gray-100 text-gray-600 border border-gray-200 uppercase tracking-wide">
                    Archived
                  </span>
                  <span className="text-sm font-medium text-gray-900">Version 1</span>
                  <span className="text-xs text-gray-500">5 days ago</span>
                </div>
                <button className="px-3 py-1.5 text-xs font-medium text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors">
                  Roll back
                </button>
              </div>
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}
