import React, { useState } from "react";
import {
  ArrowLeft,
  Brain,
  Pencil,
  Trash2,
  Server,
  Terminal,
  Settings,
  Layers,
  Shuffle,
  Plus,
  Search,
  ChevronDown,
  CheckCircle2,
  XCircle,
} from "lucide-react";

export function RgTabModels() {
  const [showAddForm, setShowAddForm] = useState(true);

  return (
    <div className="min-h-screen bg-gray-50 font-sans text-sm text-slate-900">
      {/* Top Nav */}
      <div className="bg-white border-b border-gray-200 px-6 py-3 flex items-center">
        <button className="flex items-center text-sm font-medium text-slate-500 hover:text-slate-800 transition-colors">
          <ArrowLeft className="w-4 h-4 mr-2" />
          Back to Model Groups
        </button>
      </div>

      {/* Hero Header */}
      <div className="bg-gradient-to-r from-blue-50 via-white to-slate-50 border-b border-gray-200 px-6 pt-8 pb-4">
        <div className="max-w-5xl mx-auto">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center space-x-3">
              <div className="flex items-center px-2.5 py-1 bg-blue-100 text-blue-700 rounded-md font-medium text-xs">
                <Brain className="w-3.5 h-3.5 mr-1.5" />
                Chat
              </div>
              <div className="flex items-center px-2.5 py-1 bg-emerald-100 text-emerald-700 rounded-md font-medium text-xs">
                Live
              </div>
              <div className="flex items-center px-2.5 py-1 bg-slate-100 text-slate-700 rounded-md font-medium text-xs">
                <Shuffle className="w-3.5 h-3.5 mr-1.5" />
                Load Balanced
              </div>
            </div>
            <div className="flex items-center space-x-2">
              <button className="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-md transition-colors">
                <Pencil className="w-4 h-4" />
              </button>
              <button className="p-2 text-slate-400 hover:text-red-600 hover:bg-red-50 rounded-md transition-colors">
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>

          <div className="mb-6">
            <h1 className="text-3xl font-semibold text-slate-900 mb-2">Production Chat</h1>
            <code className="text-sm bg-slate-100 text-slate-600 px-2 py-1 rounded">prod-chat-primary</code>
          </div>

          <div className="flex items-center space-x-6 text-sm text-slate-500">
            <div><span className="font-medium text-slate-900">4</span> Members</div>
            <div><span className="font-medium text-emerald-600">3</span> Healthy</div>
            <div><span className="font-medium text-slate-900">Active</span> Policy</div>
            <div><span className="font-medium text-slate-900">Linked</span> Prompt</div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="max-w-5xl mx-auto px-6 py-8">
        {/* Tab Row */}
        <div className="flex items-center space-x-8 border-b border-gray-200 mb-6">
          <button className="flex items-center pb-3 border-b-2 border-blue-600 text-blue-600 font-medium">
            <Server className="w-4 h-4 mr-2" />
            Models
          </button>
          <button className="flex items-center pb-3 border-b-2 border-transparent text-slate-500 hover:text-slate-700 font-medium transition-colors">
            <Layers className="w-4 h-4 mr-2" />
            Routing Policy
          </button>
          <button className="flex items-center pb-3 border-b-2 border-transparent text-slate-500 hover:text-slate-700 font-medium transition-colors">
            <Terminal className="w-4 h-4 mr-2" />
            Prompts
          </button>
          <button className="flex items-center pb-3 border-b-2 border-transparent text-slate-500 hover:text-slate-700 font-medium transition-colors">
            <Settings className="w-4 h-4 mr-2" />
            Settings
          </button>
        </div>

        {/* Tab Panel */}
        <div className="bg-white rounded-2xl border border-gray-200 p-5 shadow-sm">
          {/* Summary Row */}
          <div className="flex flex-row items-center justify-between mb-6">
            <p className="text-slate-600 font-medium">4 deployments · <span className="text-emerald-600">3 healthy</span></p>
            <button 
              className="flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg transition-colors shadow-sm"
              onClick={() => setShowAddForm(true)}
            >
              <Plus className="w-4 h-4 mr-2" />
              Add Deployment
            </button>
          </div>

          {/* Add Deployment Form (Expanded) */}
          {showAddForm && (
            <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 mb-8 shadow-sm">
              <h3 className="text-sm font-semibold text-blue-900 mb-3">Add a deployment — must be compatible with chat traffic</h3>
              
              <div className="relative mb-3">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input 
                  type="text" 
                  placeholder="Search by model name or provider..." 
                  className="w-full pl-9 pr-4 py-2 bg-white border border-blue-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-shadow"
                  defaultValue="claude-3-5"
                />
              </div>

              <div className="bg-white border border-blue-100 rounded-lg px-4 py-3 mb-4 flex items-center justify-between shadow-sm">
                <div>
                  <div className="font-semibold text-slate-900 mb-0.5">claude-3-5-haiku</div>
                  <div className="text-xs text-slate-500 font-mono">dep_claude35h_07</div>
                </div>
                <div className="flex items-center space-x-3">
                  <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-violet-100 text-violet-700 border border-violet-200">
                    <span className="w-1.5 h-1.5 rounded-full bg-violet-500 mr-1.5"></span>
                    Anthropic
                  </span>
                  <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-slate-100 text-slate-600 border border-slate-200">
                    chat
                  </span>
                </div>
              </div>

              <button className="flex items-center text-sm font-medium text-blue-700 hover:text-blue-800 mb-4 transition-colors">
                Advanced options <ChevronDown className="w-4 h-4 ml-1" />
              </button>

              <div className="flex items-center space-x-3">
                <button className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg transition-colors shadow-sm text-sm">
                  Add
                </button>
                <button 
                  className="px-4 py-2 bg-white border border-gray-300 hover:bg-gray-50 text-slate-700 font-medium rounded-lg transition-colors shadow-sm text-sm"
                  onClick={() => setShowAddForm(false)}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {/* Member Table */}
          <div className="rounded-2xl border border-gray-200 overflow-hidden shadow-sm">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  <th className="px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider w-[35%]">Deployment</th>
                  <th className="px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider w-[20%]">Provider</th>
                  <th className="px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider w-[25%]">Weight</th>
                  <th className="px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider w-[15%]">Status</th>
                  <th className="px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider w-[5%]"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 bg-white">
                {/* Row 1 */}
                <tr className="hover:bg-gray-50 transition-colors group">
                  <td className="px-4 py-3">
                    <div className="font-medium text-slate-900">gpt-4-turbo</div>
                    <div className="text-xs text-slate-500 font-mono mt-0.5">dep_gpt4t_01</div>
                  </td>
                  <td className="px-4 py-3">
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-emerald-50 text-emerald-700 border border-emerald-200">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 mr-1.5"></span>
                      openai
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-col justify-center">
                      <div className="w-full h-1.5 bg-gray-100 rounded-full overflow-hidden mb-1">
                        <div className="h-full bg-blue-400 rounded-full" style={{ width: '40%' }}></div>
                      </div>
                      <div className="text-xs text-slate-500">4 (40%)</div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center text-emerald-600 text-sm font-medium">
                      <CheckCircle2 className="w-4 h-4 mr-1.5" />
                      Healthy
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button className="p-1.5 text-gray-300 hover:text-red-500 hover:bg-red-50 rounded transition-colors opacity-0 group-hover:opacity-100">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </td>
                </tr>

                {/* Row 2 */}
                <tr className="hover:bg-gray-50 transition-colors group">
                  <td className="px-4 py-3">
                    <div className="font-medium text-slate-900">gpt-4o</div>
                    <div className="text-xs text-slate-500 font-mono mt-0.5">dep_gpt4o_02</div>
                  </td>
                  <td className="px-4 py-3">
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-emerald-50 text-emerald-700 border border-emerald-200">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 mr-1.5"></span>
                      openai
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-col justify-center">
                      <div className="w-full h-1.5 bg-gray-100 rounded-full overflow-hidden mb-1">
                        <div className="h-full bg-blue-400 rounded-full" style={{ width: '30%' }}></div>
                      </div>
                      <div className="text-xs text-slate-500">3 (30%)</div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center text-emerald-600 text-sm font-medium">
                      <CheckCircle2 className="w-4 h-4 mr-1.5" />
                      Healthy
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button className="p-1.5 text-gray-300 hover:text-red-500 hover:bg-red-50 rounded transition-colors opacity-0 group-hover:opacity-100">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </td>
                </tr>

                {/* Row 3 */}
                <tr className="hover:bg-gray-50 transition-colors group">
                  <td className="px-4 py-3">
                    <div className="font-medium text-slate-900">claude-3-sonnet</div>
                    <div className="text-xs text-slate-500 font-mono mt-0.5">dep_claude3_03</div>
                  </td>
                  <td className="px-4 py-3">
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-violet-50 text-violet-700 border border-violet-200">
                      <span className="w-1.5 h-1.5 rounded-full bg-violet-500 mr-1.5"></span>
                      anthropic
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-col justify-center">
                      <div className="w-full h-1.5 bg-gray-100 rounded-full overflow-hidden mb-1">
                        <div className="h-full bg-blue-400 rounded-full" style={{ width: '20%' }}></div>
                      </div>
                      <div className="text-xs text-slate-500">2 (20%)</div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center text-emerald-600 text-sm font-medium">
                      <CheckCircle2 className="w-4 h-4 mr-1.5" />
                      Healthy
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button className="p-1.5 text-gray-300 hover:text-red-500 hover:bg-red-50 rounded transition-colors opacity-0 group-hover:opacity-100">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </td>
                </tr>

                {/* Row 4 */}
                <tr className="hover:bg-gray-50 transition-colors group">
                  <td className="px-4 py-3">
                    <div className="font-medium text-slate-900">llama-3-70b</div>
                    <div className="text-xs text-slate-500 font-mono mt-0.5">dep_llama3_04</div>
                  </td>
                  <td className="px-4 py-3">
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-orange-50 text-orange-700 border border-orange-200">
                      <span className="w-1.5 h-1.5 rounded-full bg-orange-500 mr-1.5"></span>
                      groq
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-col justify-center">
                      <div className="w-full h-1.5 bg-gray-100 rounded-full overflow-hidden mb-1">
                        <div className="h-full bg-blue-400 rounded-full" style={{ width: '10%' }}></div>
                      </div>
                      <div className="text-xs text-slate-500">1 (10%)</div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center text-red-600 text-sm font-medium">
                      <XCircle className="w-4 h-4 mr-1.5" />
                      Down
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button className="p-1.5 text-gray-300 hover:text-red-500 hover:bg-red-50 rounded transition-colors opacity-0 group-hover:opacity-100">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </td>
                </tr>

              </tbody>
            </table>
          </div>

        </div>
      </div>
    </div>
  );
}
