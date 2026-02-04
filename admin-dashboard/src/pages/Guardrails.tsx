import { useState } from 'react';
import { Search, Shield, ShieldCheck, ShieldAlert, TestTube, AlertTriangle, CheckCircle, XCircle, Filter } from 'lucide-react';
import { useGuardrailPolicies, useGuardrailsStatus, useCheckContent, useSetOrgPolicy } from '@/hooks/useGuardrails';
import { useOrganizations } from '@/hooks/useOrganizations';
import { DataTable } from '@/components/DataTable';
import { Modal } from '@/components/Modal';
import type { GuardrailPolicy } from '@/types';

const POLICY_ICONS: Record<string, React.ReactNode> = {
  default: <Shield className="w-6 h-6 text-blue-600" />,
  strict: <ShieldAlert className="w-6 h-6 text-red-600" />,
  permissive: <ShieldCheck className="w-6 h-6 text-green-600" />,
  research: <TestTube className="w-6 h-6 text-purple-600" />,
};

const POLICY_COLORS: Record<string, string> = {
  default: 'bg-blue-50 border-blue-200',
  strict: 'bg-red-50 border-red-200',
  permissive: 'bg-green-50 border-green-200',
  research: 'bg-purple-50 border-purple-200',
};

const ACTION_COLORS: Record<string, string> = {
  block: 'text-red-600 bg-red-50',
  redact: 'text-amber-600 bg-amber-50',
  flag: 'text-yellow-600 bg-yellow-50',
  log: 'text-gray-600 bg-gray-50',
  warn: 'text-orange-600 bg-orange-50',
  allow: 'text-green-600 bg-green-50',
};

export function Guardrails() {
  const { data: policies, isLoading: isLoadingPolicies } = useGuardrailPolicies();
  const { data: status } = useGuardrailsStatus();
  const { data: organizations } = useOrganizations();
  const checkContent = useCheckContent();
  const setOrgPolicy = useSetOrgPolicy();

  const [searchQuery, setSearchQuery] = useState('');
  const [selectedPolicy, setSelectedPolicy] = useState<GuardrailPolicy | null>(null);
  const [isDetailModalOpen, setIsDetailModalOpen] = useState(false);
  const [isTestModalOpen, setIsTestModalOpen] = useState(false);
  const [testContent, setTestContent] = useState('');
  const [testResult, setTestResult] = useState<{
    allowed: boolean;
    action: string;
    message?: string;
    violations: Array<{
      policy_id: string;
      policy_name: string;
      severity: string;
      message: string;
    }>;
  } | null>(null);
  const [selectedOrgId, setSelectedOrgId] = useState('');

  const filteredPolicies = policies?.filter(
    (policy) =>
      policy.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      policy.description.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleTestContent = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!testContent.trim() || !selectedPolicy) return;

    try {
      const result = await checkContent.mutateAsync({
        content: testContent,
        policy_id: selectedPolicy.id,
      });
      setTestResult(result);
    } catch (error) {
      console.error('Failed to check content:', error);
    }
  };

  const handleSetOrgPolicy = async (orgId: string, policyId: string) => {
    try {
      await setOrgPolicy.mutateAsync({ orgId, policyId });
    } catch (error) {
      console.error('Failed to set org policy:', error);
    }
  };



  const columns = [
    {
      key: 'name',
      header: 'Policy',
      render: (policy: GuardrailPolicy) => (
        <div className="flex items-center">
          <div className={`w-12 h-12 rounded-lg flex items-center justify-center mr-4 ${
            POLICY_COLORS[policy.id]?.replace('border-', 'bg-').replace('200', '100') || 'bg-gray-100'
          }`}>
            {POLICY_ICONS[policy.id] || <Shield className="w-6 h-6 text-gray-600" />}
          </div>
          <div>
            <p className="font-medium text-gray-900">{policy.name}</p>
            <p className="text-sm text-gray-500">{policy.id}</p>
          </div>
        </div>
      ),
    },
    {
      key: 'description',
      header: 'Description',
      render: (policy: GuardrailPolicy) => (
        <p className="text-sm text-gray-600 max-w-md">{policy.description}</p>
      ),
    },
    {
      key: 'filters',
      header: 'Active Filters',
      render: (policy: GuardrailPolicy) => (
        <div className="flex flex-wrap gap-1">
          {policy.enable_pii_filter && (
            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-indigo-100 text-indigo-800">
              PII
            </span>
          )}
          {policy.enable_toxicity_filter && (
            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-rose-100 text-rose-800">
              Toxicity
            </span>
          )}
          {policy.enable_injection_filter && (
            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-orange-100 text-orange-800">
              Injection
            </span>
          )}
        </div>
      ),
    },
    {
      key: 'actions',
      header: 'Actions',
      render: (policy: GuardrailPolicy) => (
        <div className="space-y-1">
          {policy.enable_pii_filter && (
            <div className="flex items-center text-xs">
              <span className="text-gray-500 w-16">PII:</span>
              <span className={`px-2 py-0.5 rounded ${ACTION_COLORS[policy.pii_action]}`}>
                {policy.pii_action}
              </span>
            </div>
          )}
          {policy.enable_toxicity_filter && (
            <div className="flex items-center text-xs">
              <span className="text-gray-500 w-16">Toxicity:</span>
              <span className={`px-2 py-0.5 rounded ${ACTION_COLORS[policy.toxicity_action]}`}>
                {policy.toxicity_action}
              </span>
            </div>
          )}
          {policy.enable_injection_filter && (
            <div className="flex items-center text-xs">
              <span className="text-gray-500 w-16">Injection:</span>
              <span className={`px-2 py-0.5 rounded ${ACTION_COLORS[policy.injection_action]}`}>
                {policy.injection_action}
              </span>
            </div>
          )}
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (policy: GuardrailPolicy) => {
        const isActive = status?.active_policy_id === policy.id;
        return (
          <div className="flex items-center">
            {isActive ? (
              <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
                <CheckCircle className="w-3 h-3 mr-1" />
                Active
              </span>
            ) : (
              <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600">
                Inactive
              </span>
            )}
          </div>
        );
      },
    },
  ];

  if (isLoadingPolicies) {
    return (
      <div className="p-8 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      </div>
    );
  }

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Guardrails</h1>
          <p className="text-gray-600 mt-1">
            Manage content filtering policies for safe AI usage
          </p>
        </div>
        <div className="flex items-center space-x-4">
          {/* Organization Selector for setting policy */}
          <div className="flex items-center space-x-2">
            <Filter className="w-4 h-4 text-gray-500" />
            <select
              value={selectedOrgId}
              onChange={(e) => setSelectedOrgId(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
            >
              <option value="">Global Policies</option>
              {organizations?.items.map((org) => (
                <option key={org.id} value={org.id}>
                  {org.name}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* Status Card */}
      <div className="bg-white rounded-xl border border-gray-200 p-6 mb-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">
              {status?.active_policy_name || 'Default Policy'}
            </h2>
            <p className="text-sm text-gray-500 mt-1">
              Currently active policy • {status?.total_requests_checked.toLocaleString()} requests checked
            </p>
          </div>
          <div className="flex items-center space-x-4">
            <div className="text-right">
              <p className="text-2xl font-bold text-gray-900">
                {status?.total_violations.toLocaleString()}
              </p>
              <p className="text-sm text-gray-500">Violations detected</p>
            </div>
            <div className="w-12 h-12 rounded-full bg-amber-100 flex items-center justify-center">
              <AlertTriangle className="w-6 h-6 text-amber-600" />
            </div>
          </div>
        </div>
      </div>

      {/* Search */}
      <div className="mb-6">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
          <input
            type="text"
            placeholder="Search policies..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
          />
        </div>
      </div>

      {/* Policies Table */}
      <DataTable
        columns={columns}
        data={filteredPolicies || []}
        keyExtractor={(policy) => policy.id}
        onRowClick={(policy) => {
          setSelectedPolicy(policy);
          setIsDetailModalOpen(true);
        }}
      />

      {/* Policy Detail Modal */}
      <Modal
        isOpen={isDetailModalOpen}
        onClose={() => setIsDetailModalOpen(false)}
        title={selectedPolicy?.name || 'Policy Details'}
        size="lg"
      >
        {selectedPolicy && (
          <div className="space-y-6">
            <div>
              <h3 className="text-sm font-medium text-gray-700 mb-2">Description</h3>
              <p className="text-sm text-gray-600">{selectedPolicy.description}</p>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="bg-gray-50 rounded-lg p-4">
                <h4 className="text-sm font-medium text-gray-700 mb-3">Content Filters</h4>
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-gray-600">PII Detection</span>
                    {selectedPolicy.enable_pii_filter ? (
                      <CheckCircle className="w-4 h-4 text-green-600" />
                    ) : (
                      <XCircle className="w-4 h-4 text-gray-400" />
                    )}
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-gray-600">Toxicity Filter</span>
                    {selectedPolicy.enable_toxicity_filter ? (
                      <CheckCircle className="w-4 h-4 text-green-600" />
                    ) : (
                      <XCircle className="w-4 h-4 text-gray-400" />
                    )}
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-gray-600">Injection Detection</span>
                    {selectedPolicy.enable_injection_filter ? (
                      <CheckCircle className="w-4 h-4 text-green-600" />
                    ) : (
                      <XCircle className="w-4 h-4 text-gray-400" />
                    )}
                  </div>
                </div>
              </div>

              <div className="bg-gray-50 rounded-lg p-4">
                <h4 className="text-sm font-medium text-gray-700 mb-3">Filter Actions</h4>
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-gray-600">PII Action</span>
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${ACTION_COLORS[selectedPolicy.pii_action]}`}>
                      {selectedPolicy.pii_action}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-gray-600">Toxicity Action</span>
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${ACTION_COLORS[selectedPolicy.toxicity_action]}`}>
                      {selectedPolicy.toxicity_action}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-gray-600">Injection Action</span>
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${ACTION_COLORS[selectedPolicy.injection_action]}`}>
                      {selectedPolicy.injection_action}
                    </span>
                  </div>
                </div>
              </div>
            </div>

            {selectedPolicy.blocked_topics.length > 0 && (
              <div>
                <h4 className="text-sm font-medium text-gray-700 mb-2">Blocked Topics</h4>
                <div className="flex flex-wrap gap-2">
                  {selectedPolicy.blocked_topics.map((topic) => (
                    <span
                      key={topic}
                      className="inline-flex items-center px-2 py-1 rounded text-xs font-medium bg-red-100 text-red-800"
                    >
                      {topic}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {selectedPolicy.custom_blocked_patterns.length > 0 && (
              <div>
                <h4 className="text-sm font-medium text-gray-700 mb-2">Custom Blocked Patterns</h4>
                <div className="flex flex-wrap gap-2">
                  {selectedPolicy.custom_blocked_patterns.map((pattern, index) => (
                    <code
                      key={index}
                      className="inline-flex items-center px-2 py-1 rounded text-xs font-mono bg-gray-100 text-gray-800"
                    >
                      {pattern}
                    </code>
                  ))}
                </div>
              </div>
            )}

            <div className="flex items-center justify-between pt-4 border-t border-gray-200">
              <div>
                <span className="text-sm text-gray-500">Violation Action: </span>
                <span className={`px-2 py-0.5 rounded text-xs font-medium ${ACTION_COLORS[selectedPolicy.violation_action]}`}>
                  {selectedPolicy.violation_action}
                </span>
              </div>
              <div className="flex items-center space-x-2">
                <span className="text-sm text-gray-500">Alert on violation:</span>
                {selectedPolicy.alert_on_violation ? (
                  <CheckCircle className="w-4 h-4 text-green-600" />
                ) : (
                  <XCircle className="w-4 h-4 text-gray-400" />
                )}
              </div>
            </div>

            <div className="flex justify-end space-x-3 pt-4">
              <button
                onClick={() => {
                  setIsDetailModalOpen(false);
                  setTestContent('');
                  setTestResult(null);
                  setIsTestModalOpen(true);
                }}
                className="flex items-center px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors"
              >
                <TestTube className="w-4 h-4 mr-2" />
                Test Policy
              </button>
              {selectedOrgId && (
                <button
                  onClick={() => {
                    handleSetOrgPolicy(selectedOrgId, selectedPolicy.id);
                    setIsDetailModalOpen(false);
                  }}
                  disabled={setOrgPolicy.isPending}
                  className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 transition-colors"
                >
                  {setOrgPolicy.isPending ? 'Setting...' : 'Set as Active Policy'}
                </button>
              )}
              <button
                onClick={() => setIsDetailModalOpen(false)}
                className="px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 transition-colors"
              >
                Close
              </button>
            </div>
          </div>
        )}
      </Modal>

      {/* Test Content Modal */}
      <Modal
        isOpen={isTestModalOpen}
        onClose={() => {
          setIsTestModalOpen(false);
          setTestContent('');
          setTestResult(null);
        }}
        title={`Test: ${selectedPolicy?.name}`}
        size="lg"
      >
        <form onSubmit={handleTestContent} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Content to Test
            </label>
            <textarea
              value={testContent}
              onChange={(e) => setTestContent(e.target.value)}
              rows={4}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              placeholder="Enter text to check against this policy..."
            />
            <p className="text-xs text-gray-500 mt-1">
              Enter content to test how this policy would handle it
            </p>
          </div>

          {testResult && (
            <div className={`p-4 rounded-lg ${testResult.allowed ? 'bg-green-50 border border-green-200' : 'bg-red-50 border border-red-200'}`}>
              <div className="flex items-center mb-3">
                {testResult.allowed ? (
                  <CheckCircle className="w-5 h-5 text-green-600 mr-2" />
                ) : (
                  <XCircle className="w-5 h-5 text-red-600 mr-2" />
                )}
                <span className={`font-medium ${testResult.allowed ? 'text-green-800' : 'text-red-800'}`}>
                  {testResult.allowed ? 'Content Allowed' : 'Content Blocked'}
                </span>
              </div>
              
              {testResult.message && (
                <p className="text-sm text-gray-700 mb-3">{testResult.message}</p>
              )}

              {testResult.violations.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium text-gray-700 mb-2">Violations:</h4>
                  <ul className="space-y-2">
                    {testResult.violations.map((violation, index) => (
                      <li key={index} className="flex items-start">
                        <AlertTriangle className="w-4 h-4 text-amber-500 mr-2 mt-0.5" />
                        <div>
                          <span className="text-sm font-medium text-gray-800">{violation.policy_name}</span>
                          <span className="text-xs text-gray-500 ml-2">({violation.severity})</span>
                          <p className="text-sm text-gray-600">{violation.message}</p>
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          <div className="flex justify-end space-x-3 pt-4">
            <button
              type="button"
              onClick={() => {
                setIsTestModalOpen(false);
                setTestContent('');
                setTestResult(null);
              }}
              className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
            >
              Close
            </button>
            <button
              type="submit"
              disabled={!testContent.trim() || checkContent.isPending}
              className="flex items-center px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 transition-colors"
            >
              {checkContent.isPending ? (
                <>
                  <div className="w-4 h-4 mr-2 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Checking...
                </>
              ) : (
                <>
                  <TestTube className="w-4 h-4 mr-2" />
                  Check Content
                </>
              )}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
