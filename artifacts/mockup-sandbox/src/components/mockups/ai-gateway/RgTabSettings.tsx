import React, { useState } from 'react';
import { 
  ArrowLeft, 
  Brain, 
  Shuffle, 
  Server, 
  Terminal, 
  Settings, 
  Layers, 
  Edit, 
  Trash2,
  CheckCircle2,
  FileText,
  ChevronDown
} from 'lucide-react';

export function RgTabSettings() {
  const [liveTraffic, setLiveTraffic] = useState(true);
  const [stagingMode, setStagingMode] = useState(false);

  return (
    <div className="min-h-screen bg-gray-50 font-sans text-slate-900">
      {/* Top Nav */}
      <div className="bg-white border-b border-gray-200 px-6 py-3">
        <a href="#" className="flex items-center text-sm font-medium text-gray-500 hover:text-gray-900">
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Model Groups
        </a>
      </div>

      {/* Hero Header */}
      <div className="bg-gradient-to-r from-blue-50 via-white to-slate-50 border-b border-gray-200 px-6 pt-8 pb-6">
        <div className="max-w-5xl mx-auto">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <span className="flex items-center gap-1.5 rounded-full bg-blue-100 px-2.5 py-1 text-xs font-semibold text-blue-700">
                <Brain className="h-3.5 w-3.5" />
                Chat
              </span>
              <span className="flex items-center gap-1.5 rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-semibold text-emerald-700">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500"></span>
                Live
              </span>
              <span className="flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-700">
                <Shuffle className="h-3.5 w-3.5" />
                Priority Routing
              </span>
            </div>
            <div className="flex items-center gap-2">
              <button className="flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50">
                <Edit className="h-4 w-4" />
                Edit
              </button>
              <button className="flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-red-600 shadow-sm hover:bg-gray-50">
                <Trash2 className="h-4 w-4" />
                Delete
              </button>
            </div>
          </div>
          
          <div className="mb-6">
            <h1 className="text-2xl font-bold text-gray-900 mb-2">Production Chat</h1>
            <code className="text-sm font-mono text-gray-500 bg-gray-100 px-1.5 py-0.5 rounded">prod-chat-primary</code>
          </div>

          <div className="flex items-center gap-6 text-sm text-gray-600">
            <div className="flex items-center gap-2">
              <Server className="h-4 w-4 text-gray-400" />
              <span><strong className="text-gray-900">4</strong> Members</span>
            </div>
            <div className="flex items-center gap-2">
              <CheckCircle2 className="h-4 w-4 text-emerald-500" />
              <span><strong className="text-gray-900">100%</strong> Healthy</span>
            </div>
            <div className="flex items-center gap-2">
              <Shuffle className="h-4 w-4 text-gray-400" />
              <span><strong className="text-gray-900">Cost</strong> Policy</span>
            </div>
            <div className="flex items-center gap-2">
              <FileText className="h-4 w-4 text-gray-400" />
              <span><strong className="text-gray-900">System</strong> Prompt</span>
            </div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="max-w-5xl mx-auto px-6 py-6">
        {/* Tabs */}
        <div className="flex items-center gap-6 border-b border-gray-200 mb-6">
          <button className="flex items-center gap-2 pb-3 text-sm font-medium text-gray-500 hover:text-gray-700 border-b-2 border-transparent">
            <Server className="h-4 w-4" />
            Models
          </button>
          <button className="flex items-center gap-2 pb-3 text-sm font-medium text-gray-500 hover:text-gray-700 border-b-2 border-transparent">
            <Terminal className="h-4 w-4" />
            Test Console
          </button>
          <button className="flex items-center gap-2 pb-3 text-sm font-medium text-blue-600 border-b-2 border-blue-600">
            <Settings className="h-4 w-4" />
            Settings
          </button>
          <button className="flex items-center gap-2 pb-3 text-sm font-medium text-gray-500 hover:text-gray-700 border-b-2 border-transparent">
            <Layers className="h-4 w-4" />
            Advanced
          </button>
        </div>

        {/* Tab Content Panel */}
        <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
          <div className="max-w-lg">
            <div className="mb-6">
              <h2 className="text-sm font-semibold text-gray-900">Group identity & traffic state</h2>
              <p className="text-sm text-gray-500 mt-1">Routing behavior is configured separately in the Advanced tab.</p>
            </div>

            <div className="grid gap-3 sm:grid-cols-2 mb-8">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Display Name</label>
                <input 
                  type="text" 
                  defaultValue="Production Chat"
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Workload Mode</label>
                <div className="relative">
                  <select className="w-full appearance-none rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 bg-white">
                    <option>Chat</option>
                    <option>Completion</option>
                    <option>Embedding</option>
                  </select>
                  <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center px-2 text-gray-500">
                    <ChevronDown className="h-4 w-4" />
                  </div>
                </div>
              </div>
            </div>

            <div className="space-y-4 mb-8">
              {/* Live Traffic Toggle */}
              <div className="flex items-center justify-between rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900">Live Traffic</h3>
                  <p className="text-xs text-gray-500 mt-0.5">This group is accepting requests from the gateway.</p>
                </div>
                <button 
                  onClick={() => setLiveTraffic(!liveTraffic)}
                  className={`relative inline-flex h-6 w-10 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${liveTraffic ? 'bg-blue-600' : 'bg-gray-300'}`}
                >
                  <span className={`pointer-events-none absolute top-[3px] h-[18px] w-[18px] transform rounded-full bg-white shadow-sm ring-0 transition duration-200 ease-in-out ${liveTraffic ? 'translate-x-[19px]' : 'translate-x-[3px]'}`} />
                </button>
              </div>

              {/* Staging Mode Toggle */}
              <div className="flex items-center justify-between rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900">Staging Mode</h3>
                  <p className="text-xs text-gray-500 mt-0.5">Requests in staging mode are not counted toward spend limits.</p>
                </div>
                <button 
                  onClick={() => setStagingMode(!stagingMode)}
                  className={`relative inline-flex h-6 w-10 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${stagingMode ? 'bg-blue-600' : 'bg-gray-300'}`}
                >
                  <span className={`pointer-events-none absolute top-[3px] h-[18px] w-[18px] transform rounded-full bg-white shadow-sm ring-0 transition duration-200 ease-in-out ${stagingMode ? 'translate-x-[19px]' : 'translate-x-[3px]'}`} />
                </button>
              </div>
            </div>

            <div className="flex justify-end pt-4 border-t border-gray-100">
              <button className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 shadow-sm transition-colors">
                Save Settings
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
