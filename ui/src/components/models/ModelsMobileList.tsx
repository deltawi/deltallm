import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Search,
  Copy,
  Check,
  ChevronLeft,
  ChevronRight,
  MoreHorizontal,
  Pencil,
  Trash2,
  Eye,
  Inbox,
  Bot,
  MessageSquare,
  FileText,
  Image as ImageIcon,
  Volume2,
  Mic,
  ArrowUpDown,
  type LucideIcon,
} from 'lucide-react';
import { PROVIDER_LOGOS } from '../../lib/providerLogos';
import { normalizeProvider, providerDisplayName } from '../../lib/providers';

type Pagination = {
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
};

type ModelRow = {
  model_name?: string;
  mode?: string;
  model_info?: { mode?: string; rpm_limit?: number | null; tpm_limit?: number | null };
  provider?: string;
  deltallm_params?: { model?: string; rpm?: number | null; tpm?: number | null };
  credential_source?: 'named' | 'inline' | string;
  deployment_id: string;
  healthy?: boolean;
};

export type ModelFilterValue = 'all' | 'chat' | 'embedding' | 'image_generation' | 'audio_speech' | 'audio_transcription' | 'rerank';

type Props = {
  items: ModelRow[];
  loading: boolean;
  pagination?: Pagination | null;
  pageSize: number;
  onPageChange: (offset: number) => void;
  searchValue: string;
  onSearchChange: (value: string) => void;
  activeFilter: ModelFilterValue;
  onFilterChange: (value: ModelFilterValue) => void;
  emptyMessage: string;
  canManage: boolean;
  onView: (deploymentId: string) => void;
  onEdit: (deploymentId: string) => void;
  onDelete: (deploymentId: string) => void;
};

const FILTERS: { value: ModelFilterValue; label: string }[] = [
  { value: 'all', label: 'All Types' },
  { value: 'chat', label: 'Chat' },
  { value: 'embedding', label: 'Embedding' },
  { value: 'rerank', label: 'Rerank' },
  { value: 'audio_speech', label: 'TTS' },
  { value: 'audio_transcription', label: 'STT' },
  { value: 'image_generation', label: 'Image' },
];

const PROVIDER_TONE: Record<string, string> = {
  openai: 'bg-emerald-50 border-emerald-100',
  anthropic: 'bg-amber-50 border-amber-100',
  azure: 'bg-sky-50 border-sky-100',
  azure_openai: 'bg-sky-50 border-sky-100',
  openrouter: 'bg-violet-50 border-violet-100',
  groq: 'bg-fuchsia-50 border-fuchsia-100',
  together: 'bg-indigo-50 border-indigo-100',
  fireworks: 'bg-orange-50 border-orange-100',
  deepinfra: 'bg-cyan-50 border-cyan-100',
  perplexity: 'bg-rose-50 border-rose-100',
  gemini: 'bg-blue-50 border-blue-100',
  bedrock: 'bg-stone-100 border-stone-200',
  vllm: 'bg-lime-50 border-lime-100',
  lmstudio: 'bg-slate-100 border-slate-200',
  ollama: 'bg-teal-50 border-teal-100',
  unknown: 'bg-gray-100 border-gray-200',
};

type TypeStyle = { icon: LucideIcon; color: string; label: string };

function getTypeConfig(mode: string | undefined): TypeStyle {
  switch (mode) {
    case 'chat':
      return { icon: MessageSquare, color: 'text-blue-700 bg-blue-100', label: 'Chat' };
    case 'embedding':
      return { icon: FileText, color: 'text-purple-700 bg-purple-100', label: 'Embedding' };
    case 'rerank':
      return { icon: ArrowUpDown, color: 'text-orange-700 bg-orange-100', label: 'Rerank' };
    case 'image_generation':
      return { icon: ImageIcon, color: 'text-pink-700 bg-pink-100', label: 'Image' };
    case 'audio_speech':
      return { icon: Volume2, color: 'text-green-700 bg-green-100', label: 'TTS' };
    case 'audio_transcription':
      return { icon: Mic, color: 'text-yellow-700 bg-yellow-100', label: 'STT' };
    default:
      return { icon: Bot, color: 'text-gray-700 bg-gray-100', label: 'Unknown' };
  }
}

function ProviderTile({ providerKey }: { providerKey: string }) {
  const [failed, setFailed] = useState(false);
  const logo = PROVIDER_LOGOS[providerKey];
  const tone = PROVIDER_TONE[providerKey] || PROVIDER_TONE.unknown;
  const label = providerDisplayName(providerKey);
  return (
    <div className={`w-9 h-9 rounded-lg border flex items-center justify-center shrink-0 ${tone}`}>
      {logo && !failed ? (
        <img
          src={logo}
          alt={`${label} logo`}
          className="w-5 h-5 object-contain"
          onError={() => setFailed(true)}
          loading="lazy"
        />
      ) : (
        <span className="text-[13px] font-semibold text-gray-700">
          {label.charAt(0).toUpperCase()}
        </span>
      )}
    </div>
  );
}

function rowMode(row: ModelRow): string {
  return row.mode || row.model_info?.mode || 'chat';
}

export default function ModelsMobileList({
  items,
  loading,
  pagination,
  pageSize,
  onPageChange,
  searchValue,
  onSearchChange,
  activeFilter,
  onFilterChange,
  emptyMessage,
  canManage,
  onView,
  onEdit,
  onDelete,
}: Props) {
  const [actionSheetFor, setActionSheetFor] = useState<ModelRow | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<number | null>(null);
  const copiedTimer = useRef<number | null>(null);
  const sheetTitleId = 'models-action-sheet-title';
  const sheetReturnFocus = useRef<HTMLElement | null>(null);
  const sheetFirstActionRef = useRef<HTMLButtonElement | null>(null);

  const total = pagination?.total ?? items.length;
  const offset = pagination?.offset ?? 0;
  const limit = pagination?.limit ?? pageSize;
  const hasMore = pagination?.has_more ?? false;
  const totalPages = Math.max(1, Math.ceil(total / Math.max(1, limit)));
  const currentPage = Math.floor(offset / Math.max(1, limit)) + 1;
  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + items.length, total);

  const emptyResultsMessage = activeFilter === 'all' ? emptyMessage : 'No models match this filter';

  const showToast = (msg: string) => {
    setToast(msg);
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 1800);
  };

  useEffect(() => {
    return () => {
      if (toastTimer.current) window.clearTimeout(toastTimer.current);
      if (copiedTimer.current) window.clearTimeout(copiedTimer.current);
    };
  }, []);

  const handleCopy = async (deploymentId: string) => {
    try {
      await navigator.clipboard.writeText(deploymentId);
      setCopiedId(deploymentId);
      showToast('Deployment ID copied');
      if (copiedTimer.current) window.clearTimeout(copiedTimer.current);
      copiedTimer.current = window.setTimeout(() => {
        setCopiedId((current) => (current === deploymentId ? null : current));
      }, 1500);
    } catch {
      showToast('Copy failed');
    }
  };

  const openSheet = (row: ModelRow, trigger: HTMLElement | null) => {
    sheetReturnFocus.current = trigger;
    setActionSheetFor(row);
  };

  const closeSheet = () => {
    setActionSheetFor(null);
    const restoreTo = sheetReturnFocus.current;
    sheetReturnFocus.current = null;
    if (restoreTo && typeof restoreTo.focus === 'function') {
      window.setTimeout(() => restoreTo.focus(), 0);
    }
  };

  // Escape closes sheet, focus first action on open
  useEffect(() => {
    if (!actionSheetFor) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        closeSheet();
      }
    };
    window.addEventListener('keydown', onKey);
    const focusTimer = window.setTimeout(() => {
      sheetFirstActionRef.current?.focus();
    }, 0);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.clearTimeout(focusTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actionSheetFor]);

  const sheetActions = useMemo(() => {
    if (!actionSheetFor) return [] as { label: string; icon: LucideIcon; tone?: 'danger'; onClick: () => void }[];
    const row = actionSheetFor;
    const list: { label: string; icon: LucideIcon; tone?: 'danger'; onClick: () => void }[] = [];
    list.push({
      label: 'View details',
      icon: Eye,
      onClick: () => {
        closeSheet();
        onView(row.deployment_id);
      },
    });
    list.push({
      label: 'Copy deployment ID',
      icon: Copy,
      onClick: () => {
        closeSheet();
        handleCopy(row.deployment_id);
      },
    });
    if (canManage) {
      list.push({
        label: 'Edit',
        icon: Pencil,
        onClick: () => {
          closeSheet();
          onEdit(row.deployment_id);
        },
      });
      list.push({
        label: 'Delete',
        icon: Trash2,
        tone: 'danger',
        onClick: () => {
          closeSheet();
          onDelete(row.deployment_id);
        },
      });
    }
    return list;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actionSheetFor, canManage]);

  return (
    <div className="relative">
      {/* Search */}
      <div className="mb-3">
        <div className="relative">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Search models..."
            value={searchValue}
            onChange={(e) => onSearchChange(e.target.value)}
            className="w-full bg-gray-100 border-transparent rounded-lg py-2 pl-9 pr-4 text-sm focus:bg-white focus:border-blue-500 focus:ring-2 focus:ring-blue-200 outline-none transition-all"
          />
        </div>
      </div>

      {/* Filter chips */}
      <div className="mb-3 -mx-1 px-1 flex gap-2 overflow-x-auto pb-1 scrollbar-hide">
        {FILTERS.map((f) => {
          const selected = activeFilter === f.value;
          return (
            <button
              key={f.value}
              type="button"
              onClick={() => onFilterChange(f.value)}
              aria-pressed={selected}
              className={`whitespace-nowrap px-3 py-1.5 rounded-full text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${
                selected
                  ? 'bg-gray-900 text-white'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }`}
            >
              {f.label}
            </button>
          );
        })}
      </div>

      {/* List */}
      <div className="space-y-3">
        {loading && items.length === 0 ? (
          <div className="space-y-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="bg-white rounded-xl border border-gray-200 p-4 animate-pulse">
                <div className="flex items-center gap-3 mb-3">
                  <div className="w-9 h-9 bg-gray-200 rounded-lg" />
                  <div className="flex-1">
                    <div className="h-4 bg-gray-200 rounded w-1/2 mb-2" />
                    <div className="h-3 bg-gray-100 rounded w-1/3" />
                  </div>
                </div>
                <div className="h-3 bg-gray-100 rounded w-2/3" />
              </div>
            ))}
          </div>
        ) : items.length === 0 ? (
          <div className="text-center py-12 bg-white rounded-xl border border-gray-200">
            <div className="w-12 h-12 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-3">
              <Inbox className="w-6 h-6 text-gray-400" />
            </div>
            <p className="text-gray-500 text-sm">{emptyResultsMessage}</p>
          </div>
        ) : (
          items.map((model) => {
            const mode = rowMode(model);
            const typeConfig = getTypeConfig(mode);
            const TypeIcon = typeConfig.icon;
            const providerKey = normalizeProvider(model.provider, model.deltallm_params?.model);
            const providerLabel = providerDisplayName(providerKey);
            const healthy = model.healthy !== false;
            const justCopied = copiedId === model.deployment_id;
            const credentialLabel = model.credential_source === 'named' ? 'Named creds' : 'Inline creds';
            const tpm = model.deltallm_params?.tpm ?? model.model_info?.tpm_limit;
            const rpm = model.deltallm_params?.rpm ?? model.model_info?.rpm_limit;
            const rateLimit = tpm
              ? `TPM ${Number(tpm).toLocaleString()}`
              : rpm
                ? `RPM ${Number(rpm).toLocaleString()}`
                : null;

            return (
              <div
                key={model.deployment_id}
                className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm relative active:bg-gray-50 transition-colors cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-inset"
                role="button"
                tabIndex={0}
                onClick={() => onView(model.deployment_id)}
                onKeyDown={(e) => {
                  if (e.target !== e.currentTarget) return;
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onView(model.deployment_id);
                  }
                }}
                aria-label={`View details for ${model.model_name || model.deployment_id}`}
              >
                <div className="flex items-start gap-3">
                  <ProviderTile providerKey={providerKey} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <h3 className="font-semibold text-gray-900 text-sm truncate">
                        {model.model_name || '(unnamed)'}
                      </h3>
                      <div
                        className={`w-2 h-2 rounded-full shrink-0 ${healthy ? 'bg-green-500' : 'bg-red-500'}`}
                        title={healthy ? 'Healthy' : 'Unhealthy'}
                        aria-label={healthy ? 'Healthy' : 'Unhealthy'}
                      />
                    </div>
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium uppercase tracking-wider ${typeConfig.color}`}>
                        <TypeIcon className="w-3 h-3" />
                        {typeConfig.label}
                      </span>
                      <span className="text-xs text-gray-500 truncate">{providerLabel}</span>
                    </div>
                  </div>
                  <button
                    type="button"
                    className="p-1 -mr-1 text-gray-400 hover:text-gray-600 active:bg-gray-100 rounded shrink-0 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                    onClick={(e) => { e.stopPropagation(); openSheet(model, e.currentTarget); }}
                    aria-label="More actions"
                    aria-haspopup="dialog"
                  >
                    <MoreHorizontal className="w-4 h-4" />
                  </button>
                </div>

                <div className="flex items-center gap-1.5 mt-3 mb-3">
                  <code className="text-xs font-mono text-gray-600 bg-gray-100 px-1.5 py-0.5 rounded truncate max-w-[220px]">
                    {model.deployment_id}
                  </code>
                  <button
                    type="button"
                    className="text-gray-400 hover:text-gray-600 p-1.5 -m-1 rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                    onClick={(e) => { e.stopPropagation(); handleCopy(model.deployment_id); }}
                    aria-label="Copy deployment id"
                  >
                    {justCopied ? <Check className="w-3.5 h-3.5 text-green-600" /> : <Copy className="w-3.5 h-3.5" />}
                  </button>
                </div>

                <div className="flex flex-wrap gap-2">
                  <span className="inline-flex items-center px-2 py-1 rounded border border-gray-200 text-[11px] font-medium text-gray-600 bg-gray-50">
                    {credentialLabel}
                  </span>
                  {rateLimit && (
                    <span className="inline-flex items-center px-2 py-1 rounded border border-gray-200 text-[11px] font-medium text-gray-600 bg-gray-50">
                      {rateLimit}
                    </span>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Pagination */}
      {(items.length > 0 || offset > 0) && (
        <nav className="mt-4 bg-white/95 backdrop-blur border border-gray-200 rounded-xl px-3 py-2.5 flex items-center justify-between">
          <span className="text-xs text-gray-500">
            {total === 0 ? 'No results' : `${rangeStart}–${rangeEnd} of ${total}`}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => onPageChange(Math.max(0, offset - limit))}
              disabled={offset <= 0 || loading}
              aria-label="Previous page"
              className="w-10 h-10 -my-2 flex items-center justify-center rounded-lg border border-gray-200 text-gray-600 disabled:text-gray-300 disabled:bg-gray-50 active:bg-gray-100"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className="text-xs text-gray-700 font-medium tabular-nums min-w-[44px] text-center">
              {currentPage} / {totalPages}
            </span>
            <button
              type="button"
              onClick={() => onPageChange(offset + limit)}
              disabled={!hasMore || loading}
              aria-label="Next page"
              className="w-10 h-10 -my-2 flex items-center justify-center rounded-lg border border-gray-200 text-gray-600 disabled:text-gray-300 disabled:bg-gray-50 active:bg-gray-100"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </nav>
      )}

      {/* Action sheet */}
      {actionSheetFor && (
        <div
          className="fixed inset-0 z-40 bg-black/40 flex items-end sm:items-center justify-center"
          onClick={closeSheet}
          role="dialog"
          aria-modal="true"
          aria-labelledby={sheetTitleId}
        >
          <div
            className="bg-white w-full sm:w-80 sm:rounded-2xl rounded-t-2xl shadow-xl p-2 pb-4 sm:pb-2 max-w-md"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-3 pt-2 pb-3 border-b border-gray-100">
              <div id={sheetTitleId} className="text-sm font-semibold text-gray-900 truncate">
                {actionSheetFor.model_name || '(unnamed)'}
              </div>
              <div className="text-xs text-gray-500 font-mono mt-0.5 truncate">
                {actionSheetFor.deployment_id}
              </div>
            </div>
            <div className="py-1">
              {sheetActions.map((action, idx) => {
                const Icon = action.icon;
                return (
                  <button
                    key={action.label}
                    ref={idx === 0 ? sheetFirstActionRef : undefined}
                    type="button"
                    onClick={action.onClick}
                    className={`w-full flex items-center gap-3 px-4 py-3 text-sm rounded-lg active:bg-gray-100 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${
                      action.tone === 'danger' ? 'text-red-600' : 'text-gray-700'
                    }`}
                  >
                    <Icon className="w-4 h-4" />
                    <span className="font-medium">{action.label}</span>
                  </button>
                );
              })}
              <button
                type="button"
                onClick={closeSheet}
                className="w-full mt-1 px-4 py-3 text-sm font-medium text-gray-500 rounded-lg active:bg-gray-100 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Toast */}
      {toast && (
        <div
          className="fixed left-1/2 -translate-x-1/2 bottom-6 z-50 bg-gray-900 text-white text-xs font-medium px-3 py-2 rounded-lg shadow-lg pointer-events-none"
          role="status"
        >
          {toast}
        </div>
      )}
    </div>
  );
}
