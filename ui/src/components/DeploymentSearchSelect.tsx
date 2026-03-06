import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, Search, X } from 'lucide-react';

type DeploymentOption = {
  deployment_id: string;
  model_name?: string | null;
  provider?: string | null;
  mode?: string | null;
  healthy?: boolean;
};

interface DeploymentSearchSelectProps {
  search: string;
  onSearchChange: (value: string) => void;
  options: DeploymentOption[];
  loading?: boolean;
  selectedDeploymentId?: string;
  onSelect: (option: DeploymentOption) => void;
  searchPlaceholder?: string;
  helperText?: string;
  emptyText?: string;
}

export default function DeploymentSearchSelect({
  search,
  onSearchChange,
  options,
  loading = false,
  selectedDeploymentId,
  onSelect,
  searchPlaceholder = 'Search deployments...',
  helperText,
  emptyText = 'No deployments found.',
}: DeploymentSearchSelectProps) {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  const selectedOption = useMemo(
    () => options.find((option) => option.deployment_id === selectedDeploymentId),
    [options, selectedDeploymentId]
  );
  const selectedLabel = selectedOption
    ? `${selectedOption.model_name || selectedOption.deployment_id} (${selectedOption.deployment_id})`
    : (selectedDeploymentId || null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [open]);

  return (
    <div ref={wrapperRef} className="space-y-2 relative">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className="w-full flex items-center justify-between gap-2 px-3 py-2 border border-gray-300 rounded-lg text-sm text-left hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className={selectedLabel ? 'text-gray-900' : 'text-gray-500'}>
          {selectedLabel || 'Select deployment'}
        </span>
        <ChevronDown className={`w-4 h-4 text-gray-500 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {helperText && <p className="text-xs text-gray-500">{helperText}</p>}
      {open && (
        <div className="absolute z-20 mt-1 w-full rounded-lg border border-gray-200 bg-white shadow-lg">
          <div className="p-2 border-b border-gray-100">
            <div className="flex items-center gap-2 px-2 py-1.5 border border-gray-200 rounded-lg">
              <Search className="w-4 h-4 text-gray-400" />
              <input
                value={search}
                onChange={(event) => onSearchChange(event.target.value)}
                placeholder={searchPlaceholder}
                className="w-full text-sm focus:outline-none"
              />
              {search && (
                <button
                  type="button"
                  onClick={() => onSearchChange('')}
                  className="p-1 rounded hover:bg-gray-100"
                  aria-label="Clear deployment search"
                >
                  <X className="w-3.5 h-3.5 text-gray-500" />
                </button>
              )}
            </div>
          </div>
          <div className="max-h-56 overflow-y-auto">
            {loading ? (
              <div className="px-3 py-2 text-sm text-gray-500">Searching deployments...</div>
            ) : options.length === 0 ? (
              <div className="px-3 py-2 text-sm text-gray-500">{emptyText}</div>
            ) : (
              options.map((option) => {
                const isSelected = selectedDeploymentId === option.deployment_id;
                return (
                  <button
                    key={option.deployment_id}
                    type="button"
                    onClick={() => {
                      onSelect(option);
                      setOpen(false);
                    }}
                    className={`w-full text-left px-3 py-2 border-b border-gray-100 last:border-b-0 hover:bg-blue-50 transition-colors ${
                      isSelected ? 'bg-blue-50' : ''
                    }`}
                    role="option"
                    aria-selected={isSelected}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-gray-900">{option.model_name || option.deployment_id}</span>
                      <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${option.healthy ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'}`}>
                        {option.healthy ? 'Healthy' : 'Unknown'}
                      </span>
                    </div>
                    <div className="text-xs text-gray-500 mt-0.5">
                      {option.deployment_id}
                      {option.provider ? ` · ${option.provider}` : ''}
                      {option.mode ? ` · ${option.mode}` : ''}
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
      {selectedDeploymentId && (
        <button
          type="button"
          onClick={() => onSelect({ deployment_id: '', model_name: '', provider: '', mode: '' })}
          className="text-xs text-gray-500 hover:text-gray-700 inline-flex items-center gap-1"
        >
          <X className="w-3 h-3" />
          Clear selection
        </button>
      )}
      {!open && (
        <div className="sr-only" aria-live="polite">
          {selectedDeploymentId ? `Selected ${selectedDeploymentId}` : 'No deployment selected'}
        </div>
      )}
    </div>
  );
}
