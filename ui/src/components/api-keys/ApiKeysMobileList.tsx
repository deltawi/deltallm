import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Search,
  MoreHorizontal,
  ChevronDown,
  ChevronUp,
  ChevronLeft,
  ChevronRight,
  Activity,
  Zap,
  Clock,
  Pencil,
  RefreshCw,
  Trash2,
  Inbox,
} from 'lucide-react';
import type { ApiKey } from '../../lib/api';

type Pagination = {
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
};

type Props = {
  items: ApiKey[];
  loading: boolean;
  pagination?: Pagination | null;
  pageSize: number;
  onPageChange: (offset: number) => void;
  searchValue: string;
  onSearchChange: (value: string) => void;
  currentUserId: string;
  emptyMessage: string;
  canEdit: boolean;
  canRegenerate: boolean;
  canRevoke: boolean;
  onEdit: (row: ApiKey) => void;
  onRegenerate: (token: string) => void;
  onRevoke: (token: string) => void;
};

function ownerInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function ownerColor(seed: string): string {
  const palette = [
    'bg-blue-100 text-blue-700',
    'bg-green-100 text-green-700',
    'bg-purple-100 text-purple-700',
    'bg-amber-100 text-amber-700',
    'bg-pink-100 text-pink-700',
    'bg-indigo-100 text-indigo-700',
    'bg-teal-100 text-teal-700',
  ];
  let hash = 0;
  for (let i = 0; i < seed.length; i++) {
    hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  }
  return palette[hash % palette.length];
}

function maskToken(token: string): string {
  if (!token) return '';
  if (token.length <= 12) return token;
  const head = token.slice(0, 6);
  const tail = token.slice(-4);
  return `${head}…${tail}`;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, { month: 'short', day: '2-digit', year: 'numeric' });
}

function ownerInfo(row: ApiKey, currentUserId: string) {
  if (row.owner_service_account_name) {
    return {
      name: row.owner_service_account_name,
      kind: 'service_account' as const,
    };
  }
  if (row.owner_account_id && row.owner_account_id === currentUserId) {
    return { name: 'You', kind: 'user' as const };
  }
  if (row.owner_account_email) {
    return { name: row.owner_account_email, kind: 'user' as const };
  }
  if (row.owner_account_id) {
    return { name: row.owner_account_id, kind: 'user' as const };
  }
  return { name: 'Unassigned', kind: 'user' as const };
}

function isExpired(row: ApiKey): boolean {
  if (!row.expires) return false;
  return new Date(row.expires).getTime() < Date.now();
}

export default function ApiKeysMobileList({
  items,
  loading,
  pagination,
  pageSize,
  onPageChange,
  searchValue,
  onSearchChange,
  currentUserId,
  emptyMessage,
  canEdit,
  canRegenerate,
  canRevoke,
  onEdit,
  onRegenerate,
  onRevoke,
}: Props) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [actionSheetFor, setActionSheetFor] = useState<ApiKey | null>(null);
  const sheetTitleId = 'apikey-action-sheet-title';
  const sheetReturnFocus = useRef<HTMLElement | null>(null);
  const sheetFirstActionRef = useRef<HTMLButtonElement | null>(null);
  const hasActions = canEdit || canRegenerate || canRevoke;

  const total = pagination?.total ?? items.length;
  const offset = pagination?.offset ?? 0;
  const limit = pagination?.limit ?? pageSize;
  const hasMore = pagination?.has_more ?? false;
  const totalPages = Math.max(1, Math.ceil(total / Math.max(1, limit)));
  const currentPage = Math.floor(offset / Math.max(1, limit)) + 1;
  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + items.length, total);

  const openSheet = (row: ApiKey, trigger: HTMLElement | null) => {
    if (!hasActions) return;
    sheetReturnFocus.current = trigger;
    setActionSheetFor(row);
  };

  const closeSheet = () => {
    setActionSheetFor(null);
    // Restore focus to the originating trigger
    const restoreTo = sheetReturnFocus.current;
    sheetReturnFocus.current = null;
    if (restoreTo && typeof restoreTo.focus === 'function') {
      // Defer to allow the sheet to unmount first
      window.setTimeout(() => restoreTo.focus(), 0);
    }
  };

  // Close action sheet on Escape; focus first action when opened
  useEffect(() => {
    if (!actionSheetFor) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        closeSheet();
      }
    };
    window.addEventListener('keydown', onKey);
    // Focus first action button after the sheet renders
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
    if (!actionSheetFor) return [] as { label: string; icon: typeof Pencil; tone?: 'danger'; onClick: () => void }[];
    const row = actionSheetFor;
    const list: { label: string; icon: typeof Pencil; tone?: 'danger'; onClick: () => void }[] = [];
    if (canEdit) {
      list.push({
        label: 'Edit',
        icon: Pencil,
        onClick: () => {
          closeSheet();
          onEdit(row);
        },
      });
    }
    if (canRegenerate) {
      list.push({
        label: 'Regenerate',
        icon: RefreshCw,
        onClick: () => {
          closeSheet();
          onRegenerate(row.token);
        },
      });
    }
    if (canRevoke) {
      list.push({
        label: 'Revoke',
        icon: Trash2,
        tone: 'danger',
        onClick: () => {
          closeSheet();
          onRevoke(row.token);
        },
      });
    }
    return list;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actionSheetFor, canEdit, canRegenerate, canRevoke]);

  return (
    <div className="relative">
      {/* Search */}
      <div className="mb-3">
        <div className="relative">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Search keys..."
            value={searchValue}
            onChange={(e) => onSearchChange(e.target.value)}
            className="w-full bg-gray-100 border-transparent rounded-lg py-2 pl-9 pr-4 text-sm focus:bg-white focus:border-blue-500 focus:ring-2 focus:ring-blue-200 outline-none transition-all"
          />
        </div>
      </div>

      {/* List */}
      <div className="space-y-3">
        {loading && items.length === 0 ? (
          <div className="space-y-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="bg-white rounded-xl border border-gray-200 p-4 animate-pulse">
                <div className="h-4 bg-gray-200 rounded w-1/2 mb-3" />
                <div className="h-3 bg-gray-100 rounded w-2/3 mb-2" />
                <div className="h-1.5 bg-gray-100 rounded w-full" />
              </div>
            ))}
          </div>
        ) : items.length === 0 ? (
          <div className="text-center py-12 bg-white rounded-xl border border-gray-200">
            <div className="w-12 h-12 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-3">
              <Inbox className="w-6 h-6 text-gray-400" />
            </div>
            <p className="text-gray-500 text-sm">{emptyMessage}</p>
          </div>
        ) : (
          items.map((key) => {
            const isOpen = expandedId === key.token;
            const expired = isExpired(key);
            const status: 'active' | 'expired' = expired ? 'expired' : 'active';
            const overBudget = key.max_budget != null && (key.spend || 0) > key.max_budget;
            const spendPercent = key.max_budget && key.max_budget > 0
              ? Math.min(100, ((key.spend || 0) / key.max_budget) * 100)
              : 0;
            const owner = ownerInfo(key, currentUserId);
            const teamLabel = key.team_alias || key.team_id || 'No team';
            const initials = ownerInitials(owner.name);
            const colorClass = ownerColor(owner.name);
            const rateLimits: { label: string; value: string; iconColor: string }[] = [];
            if (key.rpm_limit != null) rateLimits.push({ label: 'RPM', value: Number(key.rpm_limit).toLocaleString(), iconColor: 'text-amber-500' });
            if (key.tpm_limit != null) rateLimits.push({ label: 'TPM', value: Number(key.tpm_limit).toLocaleString(), iconColor: 'text-blue-500' });
            if (key.rph_limit != null) rateLimits.push({ label: 'RPH', value: Number(key.rph_limit).toLocaleString(), iconColor: 'text-emerald-500' });
            if (key.rpd_limit != null) rateLimits.push({ label: 'RPD', value: Number(key.rpd_limit).toLocaleString(), iconColor: 'text-purple-500' });
            if (key.tpd_limit != null) rateLimits.push({ label: 'TPD', value: Number(key.tpd_limit).toLocaleString(), iconColor: 'text-pink-500' });

            return (
              <div
                key={key.token}
                className={`bg-white rounded-xl border ${expired ? 'border-gray-200 opacity-75' : overBudget ? 'border-red-200' : 'border-gray-200'} shadow-sm overflow-hidden transition-all`}
              >
                <div
                  className="p-4 cursor-pointer active:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-inset rounded-xl"
                  onClick={() => setExpandedId(isOpen ? null : key.token)}
                  onKeyDown={(e) => {
                    if (e.target !== e.currentTarget) return;
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      setExpandedId(isOpen ? null : key.token);
                    }
                  }}
                  role="button"
                  tabIndex={0}
                  aria-expanded={isOpen}
                  aria-controls={`apikey-details-${key.token}`}
                >
                  <div className="flex justify-between items-start mb-2">
                    <div className="flex items-center gap-2 min-w-0 flex-1">
                      <h3 className="font-semibold text-gray-900 text-sm truncate">{key.key_name || '(unnamed)'}</h3>
                      <span className={`shrink-0 text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded-sm ${
                        status === 'active' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'
                      }`}>
                        {status}
                      </span>
                    </div>
                    {hasActions && (
                      <button
                        type="button"
                        className="p-1 -mr-1 text-gray-400 hover:text-gray-600 active:bg-gray-100 rounded"
                        onClick={(e) => { e.stopPropagation(); openSheet(key, e.currentTarget); }}
                        aria-label="More actions"
                        aria-haspopup="dialog"
                      >
                        <MoreHorizontal className="w-4 h-4" />
                      </button>
                    )}
                  </div>

                  <div className="flex items-center gap-2 mb-3">
                    <code className="text-xs bg-gray-50 text-gray-700 px-2 py-1 rounded border border-gray-100 font-mono truncate max-w-[210px]">
                      {maskToken(key.token)}
                    </code>
                  </div>

                  <div className="flex items-center justify-between text-xs mb-3 gap-2">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <div className={`w-5 h-5 shrink-0 rounded-full flex items-center justify-center text-[9px] font-bold ${colorClass}`}>
                        {initials}
                      </div>
                      <span className="text-gray-600 truncate">{owner.name}</span>
                    </div>
                    <div className="text-gray-500 bg-gray-50 px-2 py-0.5 rounded-full border border-gray-100 truncate max-w-[140px]">
                      {teamLabel}
                    </div>
                  </div>

                  <div className="mt-1">
                    <div className="flex justify-between text-xs mb-1.5">
                      <span className="text-gray-500 font-medium">Spend</span>
                      <span className={`font-semibold ${overBudget ? 'text-red-600' : 'text-gray-700'}`}>
                        ${(key.spend || 0).toFixed(2)}{' '}
                        {key.max_budget != null ? (
                          <span className="text-gray-400 font-normal">/ ${Number(key.max_budget).toFixed(2)}</span>
                        ) : (
                          <span className="text-gray-400 font-normal">(No limit)</span>
                        )}
                      </span>
                    </div>
                    {key.max_budget != null && (
                      <div className="h-1.5 w-full bg-gray-100 rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full ${overBudget ? 'bg-red-500' : 'bg-blue-500'}`}
                          style={{ width: `${spendPercent}%` }}
                        />
                      </div>
                    )}
                  </div>

                  <div className="flex justify-center mt-2 -mb-2">
                    {isOpen ? (
                      <ChevronUp className="w-4 h-4 text-gray-300" />
                    ) : (
                      <ChevronDown className="w-4 h-4 text-gray-300" />
                    )}
                  </div>
                </div>

                {isOpen && (
                  <div id={`apikey-details-${key.token}`} className="px-4 pb-4 pt-1 border-t border-gray-100 bg-gray-50/50">
                    <div className="grid grid-cols-2 gap-4 mt-3">
                      <div className="col-span-2">
                        <h4 className="text-xs font-semibold text-gray-500 mb-2 flex items-center gap-1.5">
                          <Activity className="w-3 h-3" />
                          Rate Limits
                        </h4>
                        {rateLimits.length > 0 ? (
                          <div className="flex flex-wrap gap-1.5">
                            {rateLimits.map((rl) => (
                              <span key={rl.label} className="inline-flex items-center gap-1 text-xs bg-white border border-gray-200 text-gray-600 px-2 py-1 rounded-md">
                                <Zap className={`w-3 h-3 ${rl.iconColor}`} />
                                {rl.value} {rl.label}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="text-xs text-gray-400 italic">No limits configured</span>
                        )}
                      </div>

                      <div className="col-span-2 flex justify-between items-center bg-white border border-gray-100 rounded-lg p-2.5">
                        <div>
                          <div className="text-[10px] text-gray-400 font-medium uppercase tracking-wider mb-0.5">Created</div>
                          <div className="text-xs text-gray-700 flex items-center gap-1">
                            <Clock className="w-3 h-3 text-gray-400" />
                            {formatDate(key.created_at) || '—'}
                          </div>
                        </div>
                        <div className="text-right">
                          <div className="text-[10px] text-gray-400 font-medium uppercase tracking-wider mb-0.5">Expires</div>
                          <div className="text-xs text-gray-700 flex items-center gap-1 justify-end">
                            <Clock className="w-3 h-3 text-gray-400" />
                            {formatDate(key.expires) || 'Never'}
                          </div>
                        </div>
                      </div>
                    </div>

                    {(canEdit || canRevoke) && (
                      <div className="mt-4 flex gap-2">
                        {canEdit && (
                          <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); onEdit(key); }}
                            className="flex-1 py-2 bg-white border border-gray-200 text-gray-700 text-sm font-medium rounded-lg active:bg-gray-50 hover:bg-gray-50"
                          >
                            Edit
                          </button>
                        )}
                        {canRevoke && (
                          <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); onRevoke(key.token); }}
                            className="flex-1 py-2 bg-white border border-gray-200 text-red-600 text-sm font-medium rounded-lg active:bg-red-50 hover:bg-red-50"
                          >
                            Revoke
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                )}
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
                {actionSheetFor.key_name || '(unnamed)'}
              </div>
              <div className="text-xs text-gray-500 font-mono mt-0.5 truncate">
                {maskToken(actionSheetFor.token)}
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

    </div>
  );
}
