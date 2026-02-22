import { useState, useEffect, useCallback } from 'react';
import { guardrails } from '../lib/api';
import { Shield, X, Plus, ChevronDown } from 'lucide-react';

interface ScopedGuardrailEditorProps {
  scope: 'organization' | 'team' | 'key';
  entityId: string;
  entityLabel?: string;
  onClose?: () => void;
}

interface GuardrailsConfig {
  mode: 'inherit' | 'override';
  include: string[];
  exclude: string[];
}

export default function ScopedGuardrailEditor({ scope, entityId, entityLabel, onClose }: ScopedGuardrailEditorProps) {
  const [config, setConfig] = useState<GuardrailsConfig>({ mode: 'inherit', include: [], exclude: [] });
  const [available, setAvailable] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [hasConfig, setHasConfig] = useState(false);
  const [addingTo, setAddingTo] = useState<'include' | 'exclude' | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await guardrails.getScoped(scope, entityId);
      const cfg = res.guardrails_config;
      if (cfg && (cfg.mode || cfg.include?.length || cfg.exclude?.length)) {
        setConfig({ mode: cfg.mode || 'inherit', include: cfg.include || [], exclude: cfg.exclude || [] });
        setHasConfig(true);
      } else {
        setConfig({ mode: 'inherit', include: [], exclude: [] });
        setHasConfig(false);
      }
      setAvailable(res.available_guardrails || []);
    } catch {
      setConfig({ mode: 'inherit', include: [], exclude: [] });
    }
    setLoading(false);
  }, [scope, entityId]);

  useEffect(() => { load(); }, [load]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await guardrails.updateScoped(scope, entityId, { guardrails_config: config });
      setHasConfig(true);
    } catch (e: any) {
      alert(e.message || 'Failed to save');
    }
    setSaving(false);
  };

  const handleClear = async () => {
    setSaving(true);
    try {
      await guardrails.deleteScoped(scope, entityId);
      setConfig({ mode: 'inherit', include: [], exclude: [] });
      setHasConfig(false);
    } catch (e: any) {
      alert(e.message || 'Failed to clear');
    }
    setSaving(false);
  };

  const addGuardrail = (list: 'include' | 'exclude', name: string) => {
    setConfig(prev => ({
      ...prev,
      [list]: [...prev[list].filter(n => n !== name), name],
    }));
    setAddingTo(null);
  };

  const removeGuardrail = (list: 'include' | 'exclude', name: string) => {
    setConfig(prev => ({
      ...prev,
      [list]: prev[list].filter(n => n !== name),
    }));
  };

  const scopeLabel = scope === 'organization' ? 'Organization' : scope === 'team' ? 'Team' : 'API Key';

  if (loading) {
    return (
      <div className="bg-white border border-gray-200 rounded-xl p-6">
        <div className="animate-pulse space-y-3">
          <div className="h-5 bg-gray-200 rounded w-48"></div>
          <div className="h-4 bg-gray-100 rounded w-full"></div>
        </div>
      </div>
    );
  }

  const usedNames = new Set([...config.include, ...config.exclude]);
  const unusedGuardrails = available.filter(n => !usedNames.has(n));

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Shield className="w-5 h-5 text-blue-600" />
          <h3 className="font-semibold text-gray-900">
            {scopeLabel} Guardrails
          </h3>
          {entityLabel && <span className="text-sm text-gray-500">({entityLabel})</span>}
        </div>
        <div className="flex items-center gap-2">
          {hasConfig && (
            <button onClick={handleClear} disabled={saving} className="text-xs text-red-600 hover:text-red-700 px-2 py-1 rounded hover:bg-red-50">
              Clear Override
            </button>
          )}
          {onClose && (
            <button onClick={onClose} className="p-1 hover:bg-gray-100 rounded">
              <X className="w-4 h-4 text-gray-400" />
            </button>
          )}
        </div>
      </div>

      {!hasConfig && (
        <p className="text-sm text-gray-500 bg-gray-50 rounded-lg p-3">
          No guardrail override configured. This {scopeLabel.toLowerCase()} inherits guardrails from its parent scope (global defaults).
        </p>
      )}

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1.5">Resolution Mode</label>
        <div className="flex gap-3">
          <label className={`flex items-center gap-2 px-4 py-2.5 rounded-lg border cursor-pointer text-sm ${config.mode === 'inherit' ? 'border-blue-500 bg-blue-50 text-blue-700' : 'border-gray-200 hover:bg-gray-50'}`}>
            <input type="radio" name={`mode-${entityId}`} value="inherit" checked={config.mode === 'inherit'} onChange={() => setConfig({ ...config, mode: 'inherit' })} className="sr-only" />
            <span className="font-medium">Inherit</span>
          </label>
          <label className={`flex items-center gap-2 px-4 py-2.5 rounded-lg border cursor-pointer text-sm ${config.mode === 'override' ? 'border-orange-500 bg-orange-50 text-orange-700' : 'border-gray-200 hover:bg-gray-50'}`}>
            <input type="radio" name={`mode-${entityId}`} value="override" checked={config.mode === 'override'} onChange={() => setConfig({ ...config, mode: 'override' })} className="sr-only" />
            <span className="font-medium">Override</span>
          </label>
        </div>
        <p className="text-xs text-gray-500 mt-1.5">
          {config.mode === 'inherit'
            ? 'Starts with parent guardrails, then applies include/exclude modifications.'
            : 'Replaces all parent guardrails with only the included list below.'}
        </p>
      </div>

      <div>
        <div className="flex items-center justify-between mb-2">
          <label className="text-sm font-medium text-gray-700">
            {config.mode === 'override' ? 'Active Guardrails' : 'Additional Guardrails (Include)'}
          </label>
          <div className="relative">
            <button onClick={() => setAddingTo(addingTo === 'include' ? null : 'include')} className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-700 px-2 py-1 rounded hover:bg-blue-50" disabled={unusedGuardrails.length === 0}>
              <Plus className="w-3 h-3" /> Add <ChevronDown className="w-3 h-3" />
            </button>
            {addingTo === 'include' && unusedGuardrails.length > 0 && (
              <div className="absolute right-0 top-full mt-1 z-10 bg-white border border-gray-200 rounded-lg shadow-lg py-1 min-w-[200px]">
                {unusedGuardrails.map(name => (
                  <button key={name} onClick={() => addGuardrail('include', name)} className="block w-full text-left px-3 py-1.5 text-sm hover:bg-gray-50">
                    {name}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-2 min-h-[32px]">
          {config.include.length === 0 && <span className="text-xs text-gray-400 py-1">None</span>}
          {config.include.map(name => (
            <span key={name} className="inline-flex items-center gap-1 px-2.5 py-1 bg-green-50 text-green-700 rounded-full text-xs font-medium border border-green-200">
              {name}
              <button onClick={() => removeGuardrail('include', name)} className="hover:text-green-900"><X className="w-3 h-3" /></button>
            </span>
          ))}
        </div>
      </div>

      {config.mode === 'inherit' && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="text-sm font-medium text-gray-700">Exclude Guardrails</label>
            <div className="relative">
              <button onClick={() => setAddingTo(addingTo === 'exclude' ? null : 'exclude')} className="flex items-center gap-1 text-xs text-red-600 hover:text-red-700 px-2 py-1 rounded hover:bg-red-50" disabled={unusedGuardrails.length === 0}>
                <Plus className="w-3 h-3" /> Add <ChevronDown className="w-3 h-3" />
              </button>
              {addingTo === 'exclude' && unusedGuardrails.length > 0 && (
                <div className="absolute right-0 top-full mt-1 z-10 bg-white border border-gray-200 rounded-lg shadow-lg py-1 min-w-[200px]">
                  {unusedGuardrails.map(name => (
                    <button key={name} onClick={() => addGuardrail('exclude', name)} className="block w-full text-left px-3 py-1.5 text-sm hover:bg-gray-50">
                      {name}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
          <div className="flex flex-wrap gap-2 min-h-[32px]">
            {config.exclude.length === 0 && <span className="text-xs text-gray-400 py-1">None</span>}
            {config.exclude.map(name => (
              <span key={name} className="inline-flex items-center gap-1 px-2.5 py-1 bg-red-50 text-red-700 rounded-full text-xs font-medium border border-red-200">
                {name}
                <button onClick={() => removeGuardrail('exclude', name)} className="hover:text-red-900"><X className="w-3 h-3" /></button>
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="flex justify-end pt-2 border-t border-gray-100">
        <button onClick={handleSave} disabled={saving} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50">
          {saving ? 'Saving...' : 'Save Configuration'}
        </button>
      </div>
    </div>
  );
}
