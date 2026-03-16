import React, { useState } from 'react';
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
  CheckCircle2, 
  Copy,
  Tag,
  Check
} from 'lucide-react';

export function RgTabTest() {
  const [copied, setCopied] = useState(false);
  const [activeLang, setActiveLang] = useState('Python');

  const handleCopy = () => {
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const codeString = `from openai import OpenAI

client = OpenAI(
    base_url="https://api.yourgateway.com/v1",
    api_key="YOUR_API_KEY",
)

response = client.chat.completions.create(
    model="prod-chat-primary",
    messages=[{"role": "user", "content": "Say hello in one sentence."}],
    metadata={"prompt_variables": {"user_name": "<user_name>"}},
)

print(response.choices[0].message.content)`;

  return (
    <div className="min-h-screen bg-gray-50 font-sans text-gray-900">
      {/* Top Nav */}
      <div className="bg-white border-b border-gray-200 px-6 py-3">
        <a href="#" className="flex items-center text-sm text-gray-500 hover:text-gray-900 transition-colors w-fit">
          <ArrowLeft className="w-4 h-4 mr-2" />
          Back to Model Groups
        </a>
      </div>

      {/* Hero Header */}
      <div className="bg-gradient-to-r from-blue-50 via-white to-slate-50 border-b border-gray-200">
        <div className="max-w-6xl mx-auto px-6 py-8">
          <div className="flex flex-col gap-4">
            <div className="flex items-center gap-3">
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-blue-100 text-blue-700">
                <Brain className="w-3.5 h-3.5" />
                Chat
              </span>
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-100 text-emerald-700">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
                Live
              </span>
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-slate-100 text-slate-700">
                <Shuffle className="w-3.5 h-3.5" />
                Weighted
              </span>
            </div>

            <div className="flex items-start justify-between">
              <div>
                <h1 className="text-3xl font-bold tracking-tight text-gray-900 mb-2">Production Chat</h1>
                <div className="flex items-center gap-2">
                  <span className="text-sm text-gray-500">Key:</span>
                  <code className="text-sm font-mono bg-white px-2 py-1 rounded border border-gray-200 text-gray-700">
                    prod-chat-primary
                  </code>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button className="flex items-center justify-center p-2 rounded-lg border border-gray-200 bg-white hover:bg-gray-50 text-gray-600 transition-colors">
                  <Edit className="w-4 h-4" />
                </button>
                <button className="flex items-center justify-center p-2 rounded-lg border border-red-200 bg-red-50 hover:bg-red-100 text-red-600 transition-colors">
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>

            <div className="flex items-center gap-6 mt-2 text-sm text-gray-600">
              <div className="flex items-center gap-1.5">
                <span className="font-medium text-gray-900">4</span> Members
              </div>
              <div className="flex items-center gap-1.5">
                <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                <span className="font-medium text-gray-900">100%</span> Healthy
              </div>
              <div className="flex items-center gap-1.5">
                <span className="font-medium text-gray-900">v3</span> Policy
              </div>
              <div className="flex items-center gap-1.5">
                <span className="font-medium text-gray-900">support.reply</span> Prompt
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="max-w-6xl mx-auto px-6 py-8">
        {/* Tabs */}
        <div className="flex items-center gap-8 border-b border-gray-200 mb-6">
          <button className="flex items-center gap-2 pb-4 text-sm font-medium text-gray-500 hover:text-gray-900 transition-colors border-b-2 border-transparent">
            <Server className="w-4 h-4" />
            Models
          </button>
          <button className="flex items-center gap-2 pb-4 text-sm font-medium text-blue-600 border-b-2 border-blue-600">
            <Terminal className="w-4 h-4" />
            Test
          </button>
          <button className="flex items-center gap-2 pb-4 text-sm font-medium text-gray-500 hover:text-gray-900 transition-colors border-b-2 border-transparent">
            <Layers className="w-4 h-4" />
            Routing
          </button>
          <button className="flex items-center gap-2 pb-4 text-sm font-medium text-gray-500 hover:text-gray-900 transition-colors border-b-2 border-transparent">
            <Settings className="w-4 h-4" />
            Settings
          </button>
        </div>

        {/* Tab Panel */}
        <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-6">
          <div className="flex flex-col gap-6">
            
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {/* Blue routing context banner */}
              <div className="rounded-2xl border border-blue-100 bg-blue-50 p-4 flex gap-3">
                <Brain className="w-5 h-5 text-blue-500 shrink-0 mt-0.5" />
                <div>
                  <h3 className="font-semibold text-blue-800 mb-1">How this group routes traffic</h3>
                  <p className="text-sm text-blue-700 leading-relaxed">
                    Requests to <code className="bg-white/50 px-1 rounded">prod-chat-primary</code> are distributed across 4 deployments using weighted routing. Policy v3 is active. Replace YOUR_API_KEY with a master key or virtual API key.
                  </p>
                </div>
              </div>

              {/* Violet prompt banner */}
              <div className="rounded-2xl border border-violet-100 bg-violet-50 p-4 flex gap-3">
                <Tag className="w-5 h-5 text-violet-500 shrink-0 mt-0.5" />
                <div>
                  <h3 className="font-semibold text-violet-800 mb-1">Prompt auto-resolved</h3>
                  <p className="text-sm text-violet-700 leading-relaxed">
                    Requests resolve prompt <code className="bg-white px-1 rounded shadow-sm text-xs py-0.5">support.reply</code> at label <code className="bg-white px-1 rounded shadow-sm text-xs py-0.5">production</code>. Pass <code>metadata.prompt_variables</code> with keys: <code className="bg-white px-1 rounded shadow-sm text-xs py-0.5">user_name</code>.
                  </p>
                </div>
              </div>
            </div>

            {/* Dark Code Block */}
            <div className="rounded-2xl overflow-hidden border border-gray-200 shadow-sm">
              <div className="bg-gray-950 border-b border-gray-800 px-4 py-2.5 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  {['cURL', 'Python', 'JavaScript'].map((lang) => (
                    <button 
                      key={lang}
                      onClick={() => setActiveLang(lang)}
                      className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                        activeLang === lang 
                          ? 'bg-blue-600 text-white shadow-sm' 
                          : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800'
                      }`}
                    >
                      {lang}
                    </button>
                  ))}
                </div>
                <button 
                  onClick={handleCopy}
                  className="flex items-center gap-1.5 px-2 py-1.5 rounded-md text-xs font-medium text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition-colors"
                >
                  {copied ? <Check className="w-4 h-4 text-emerald-500" /> : <Copy className="w-4 h-4" />}
                  {copied ? <span className="text-emerald-500">Copied</span> : 'Copy'}
                </button>
              </div>
              <div className="bg-gray-950 px-5 py-4 overflow-x-auto">
                <pre className="text-xs font-mono text-gray-100 leading-relaxed">
                  <code>{codeString}</code>
                </pre>
              </div>
            </div>

          </div>
        </div>
      </div>
    </div>
  );
}
