import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowRight,
  Brain,
  ChevronRight,
  GitBranch,
  Layers,
  Mic,
  Plus,
  Search,
  Shuffle,
  Sparkles,
  Trash2,
  X,
  Zap,
} from 'lucide-react';
import ConfirmDialog from '../components/ConfirmDialog';
import IndexShell from '../components/admin/shells/IndexShell';
import { routeGroups } from '../lib/api';
import type { RouteGroup } from '../lib/api';
import { ROUTE_GROUP_MODE_OPTIONS } from '../lib/routeGroups';
import { useApi } from '../lib/hooks';
import { useToast } from '../components/ToastProvider';

/* ─── Mode chip ─────────────────────────────────────────────────────────── */
const MODE_ICONS: Record<string, React.ElementType> = {
  chat:                Brain,
  embedding:           Zap,
  audio_speech:        Mic,
  audio_transcription: Mic,
  image_generation:    Layers,
  rerank:              GitBranch,
};

const MODE_COLORS: Record<string, string> = {
  chat:                'bg-blue-50 text-blue-700 border-blue-100',
  embedding:           'bg-violet-50 text-violet-700 border-violet-100',
  audio_speech:        'bg-orange-50 text-orange-700 border-orange-100',
  audio_transcription: 'bg-orange-50 text-orange-700 border-orange-100',
  image_generation:    'bg-pink-50 text-pink-700 border-pink-100',
  rerank:              'bg-teal-50 text-teal-700 border-teal-100',
};

function ModeChip({ mode }: { mode: string }) {
  const Icon = MODE_ICONS[mode] || Layers;
  const color = MODE_COLORS[mode] || 'bg-gray-50 text-gray-700 border-gray-200';
  const label = mode.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${color}`}>
      <Icon className="h-3 w-3" />
      {label}
    </span>
  );
}

/* ─── Health bar ─────────────────────────────────────────────────────────── */
function HealthBar({ enabled, memberCount }: { enabled: boolean; memberCount: number }) {
  if (memberCount === 0) {
    return <span className="text-xs text-gray-300">No members</span>;
  }
  const color = enabled ? 'bg-emerald-500' : 'bg-gray-300';
  const textColor = enabled ? 'text-emerald-600' : 'text-gray-400';
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-gray-100">
        <div className={`h-full rounded-full ${color}`} style={{ width: enabled ? '100%' : '0%' }} />
      </div>
      <span className={`text-xs font-semibold ${textColor}`}>{memberCount}</span>
    </div>
  );
}

/* ─── Routing strategy display ────────────────────────────────────────────── */
const ROUTING_LABELS: Record<string, string> = {
  'simple-shuffle':       'Shuffle',
  'weighted':             'Weighted',
  'least-busy':           'Least Busy',
  'latency-based-routing':'Latency',
  'cost-based-routing':   'Cost',
  'usage-based-routing':  'Usage',
  'tag-based-routing':    'Tag',
  'priority-based-routing':'Priority',
  'rate-limit-aware':     'Rate Limit',
};

function RoutingBadge({ strategy }: { strategy: string | null }) {
  const label = strategy ? (ROUTING_LABELS[strategy] || strategy) : 'Shuffle';
  const Icon = !strategy || strategy === 'simple-shuffle' ? Shuffle : GitBranch;
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] font-medium text-gray-600">
      <Icon className="h-3 w-3" />
      {label}
    </span>
  );
}

/* ─── Create drawer ──────────────────────────────────────────────────────── */
interface CreateDrawerProps {
  open: boolean;
  onClose: () => void;
  form: { group_key: string; name: string; mode: string };
  setForm: React.Dispatch<React.SetStateAction<{ group_key: string; name: string; mode: string }>>;
  formError: string | null;
  setFormError: React.Dispatch<React.SetStateAction<string | null>>;
  creating: boolean;
  onCreate: () => void;
}

function CreateDrawer({ open, onClose, form, setForm, formError, setFormError, creating, onCreate }: CreateDrawerProps) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-black/20" onClick={onClose} />
      <div className="flex w-[440px] shrink-0 flex-col border-l border-gray-200 bg-white shadow-xl">
        {/* Drawer header */}
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-gray-900">Create Model Group</h2>
            <p className="text-xs text-gray-500">Add the shell — configure members on the next page.</p>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 hover:bg-gray-100">
            <X className="h-4 w-4 text-gray-400" />
          </button>
        </div>

        {/* Drawer body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {/* Info banner */}
          <div className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3">
            <div className="text-sm font-semibold text-blue-800">What happens next</div>
            <div className="mt-1 text-xs text-blue-700">
              Creates the group shell only. On the next page you'll add members, configure routing, and optionally bind a prompt.
            </div>
          </div>

          {/* Group key */}
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">
              Group Key <span className="text-red-500">*</span>
            </label>
            <input
              value={form.group_key}
              onChange={(e) => {
                setForm({ ...form, group_key: e.target.value });
                if (formError) setFormError(null);
              }}
              placeholder="prod-chat-primary"
              data-autofocus="true"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <p className="mt-1 text-xs text-gray-400">Stable key used by clients, policies, and bindings.</p>
          </div>

          {formError && (
            <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">{formError}</div>
          )}

          {/* Workload type + display name */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">Workload Type</label>
              <select
                value={form.mode}
                onChange={(e) => setForm({ ...form, mode: e.target.value })}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                {ROUTE_GROUP_MODE_OPTIONS.map((m) => (
                  <option key={m} value={m}>{m.replace(/_/g, ' ')}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">Display Name</label>
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Production Chat"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>
        </div>

        {/* Drawer footer */}
        <div className="flex justify-end gap-2 border-t border-gray-200 px-5 py-4">
          <button onClick={onClose} className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">
            Cancel
          </button>
          <button
            onClick={onCreate}
            disabled={creating}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {creating ? 'Creating…' : 'Create and continue →'}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ─── Page ───────────────────────────────────────────────────────────────── */
export default function RouteGroups() {
  const navigate = useNavigate();
  const { pushToast } = useToast();

  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [pageOffset, setPageOffset] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [deletingKey, setDeletingKey] = useState<string | null>(null);
  const [form, setForm] = useState({ group_key: '', name: '', mode: 'chat' });

  const pageSize = 20;
  const { data: result, loading, refetch } = useApi(
    () => routeGroups.list({ search, limit: pageSize, offset: pageOffset }),
    [search, pageOffset],
  );

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(searchInput.trim());
      setPageOffset(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  const resetForm = () => setForm({ group_key: '', name: '', mode: 'chat' });

  const handleCreate = async () => {
    const groupKey = form.group_key.trim();
    if (!groupKey) {
      setFormError('Group key is required.');
      return;
    }
    setFormError(null);
    setCreating(true);
    try {
      const created = await routeGroups.create({
        group_key: groupKey,
        name: form.name.trim() || null,
        mode: form.mode,
      });
      setCreateOpen(false);
      resetForm();
      pushToast({ tone: 'success', title: 'Model group created', message: `"${created.group_key}" is ready for configuration.` });
      navigate(`/route-groups/${created.group_key}`);
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Create failed', message: error?.message || 'Failed to create model group.' });
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeletingKey(deleteTarget);
    try {
      await routeGroups.delete(deleteTarget);
      pushToast({ tone: 'success', title: 'Model group deleted', message: `"${deleteTarget}" was deleted.` });
      setDeleteTarget(null);
      refetch();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Delete failed', message: error?.message || 'Failed to delete model group.' });
    } finally {
      setDeletingKey(null);
    }
  };

  const groups: RouteGroup[] = result?.data || [];
  const pagination = result?.pagination;

  return (
    <IndexShell
      title="Model Groups"
      titleIcon={Layers}
      count={pagination?.total ?? null}
      description="Group deployments behind a single key — clients call the group, DeltaLLM routes traffic."
      action={(
        <button
          onClick={() => setCreateOpen(true)}
          className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm hover:bg-blue-700"
        >
          <Plus className="h-4 w-4" /> Create Group
        </button>
      )}
      intro={(
        <div className="overflow-hidden rounded-2xl border border-blue-100 bg-gradient-to-br from-blue-50 via-white to-slate-50">
          <div className="flex items-center gap-4 px-5 py-4">
            <div className="flex-1">
              <div className="inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1 text-xs font-semibold text-blue-700 shadow-sm ring-1 ring-blue-100">
                <Sparkles className="h-3 w-3" /> Recommended setup order
              </div>
              <p className="mt-2 text-sm text-slate-600">
                Create the group shell, add members, then start with default shuffle. Upgrade to a routing policy only when you need it.
              </p>
            </div>
            <div className="hidden sm:flex items-center gap-2">
              {['Create shell', 'Add members', 'Use default shuffle'].map((step, i) => (
                <div key={i} className="flex items-center gap-2">
                  <div className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-center shadow-sm">
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">Step {i + 1}</div>
                    <div className="mt-0.5 text-xs font-semibold text-slate-700">{step}</div>
                  </div>
                  {i < 2 && <ChevronRight className="h-4 w-4 shrink-0 text-slate-300" />}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
      toolbar={(
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search model groups…"
            className="w-full rounded-xl border border-gray-200 bg-white py-2.5 pl-9 pr-4 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      )}
    >
      <CreateDrawer
        open={createOpen}
        onClose={() => { if (!creating) setCreateOpen(false); }}
        form={form}
        setForm={setForm}
        formError={formError}
        setFormError={setFormError}
        creating={creating}
        onCreate={handleCreate}
      />
      <div className="space-y-3">
        {result === null && !loading && (
          <div className="rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">
            Failed to load model groups.
          </div>
        )}

        <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
          {/* Header row */}
          <div className="grid items-center gap-4 border-b border-gray-100 bg-gray-50 px-4 py-2.5 text-[10px] font-semibold uppercase tracking-widest text-gray-400"
            style={{ gridTemplateColumns: '1fr 130px 110px 110px 72px 48px' }}>
            <div>Group</div>
            <div className="text-center">Mode</div>
            <div className="text-center">Health</div>
            <div className="text-center">Routing</div>
            <div className="text-center">Traffic</div>
            <div />
          </div>

          {/* Loading skeleton */}
          {loading && (
            <div className="divide-y divide-gray-100">
              {[...Array(4)].map((_, i) => (
                <div key={i} className="grid items-center gap-4 px-4 py-3 animate-pulse"
                  style={{ gridTemplateColumns: '1fr 130px 110px 110px 72px 48px' }}>
                  <div className="space-y-1.5">
                    <div className="h-3.5 w-40 rounded bg-gray-100" />
                    <div className="h-3 w-28 rounded bg-gray-100" />
                  </div>
                  <div className="flex justify-center"><div className="h-5 w-20 rounded-full bg-gray-100" /></div>
                  <div className="flex justify-center"><div className="h-3 w-20 rounded-full bg-gray-100" /></div>
                  <div className="flex justify-center"><div className="h-5 w-20 rounded-full bg-gray-100" /></div>
                  <div className="flex justify-center"><div className="h-5 w-10 rounded-full bg-gray-100" /></div>
                  <div />
                </div>
              ))}
            </div>
          )}

          {/* Data rows */}
          {!loading && groups.map((g, i) => (
            <div
              key={g.group_key}
              onClick={() => navigate(`/route-groups/${g.group_key}`)}
              className={`group grid cursor-pointer items-center gap-4 px-4 py-3 transition hover:bg-blue-50/40 ${i < groups.length - 1 ? 'border-b border-gray-100' : ''}`}
              style={{ gridTemplateColumns: '1fr 130px 110px 110px 72px 48px' }}
            >
              {/* Name + key */}
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-semibold text-gray-900">{g.name || g.group_key}</span>
                  {!g.enabled && (
                    <span className="shrink-0 rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-semibold text-gray-500">
                      paused
                    </span>
                  )}
                </div>
                <code className="text-[11px] text-gray-400 font-mono">{g.group_key}</code>
              </div>

              {/* Mode chip */}
              <div className="flex justify-center">
                <ModeChip mode={g.mode} />
              </div>

              {/* Health bar */}
              <div className="flex justify-center">
                <HealthBar enabled={g.enabled} memberCount={g.member_count} />
              </div>

              {/* Routing strategy */}
              <div className="flex justify-center">
                <RoutingBadge strategy={g.routing_strategy} />
              </div>

              {/* Traffic (enabled) */}
              <div className="flex justify-center">
                <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${g.enabled ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-100 text-gray-500'}`}>
                  {g.enabled ? 'Live' : 'Off'}
                </span>
              </div>

              {/* Actions */}
              <div className="flex items-center justify-end gap-1 opacity-0 transition group-hover:opacity-100">
                <button
                  onClick={(e) => { e.stopPropagation(); setDeleteTarget(g.group_key); }}
                  disabled={deletingKey === g.group_key}
                  className="rounded-lg p-1 hover:bg-red-50 disabled:opacity-40"
                  title="Delete group"
                >
                  <Trash2 className="h-3.5 w-3.5 text-red-400" />
                </button>
                <ArrowRight className="h-3.5 w-3.5 text-gray-300" />
              </div>
            </div>
          ))}

          {/* Empty state */}
          {!loading && groups.length === 0 && (
            <div className="px-6 py-12 text-center">
              <Layers className="mx-auto h-8 w-8 text-gray-200" />
              <p className="mt-3 text-sm text-gray-400">
                {search ? `No model groups matching "${search}"` : 'No model groups yet'}
              </p>
              {!search && (
                <button
                  onClick={() => setCreateOpen(true)}
                  className="mt-3 text-sm text-blue-600 hover:underline"
                >
                  Create your first group →
                </button>
              )}
            </div>
          )}
        </div>

        {/* Pagination footer */}
        {pagination && (
          <div className="flex items-center justify-between px-1 text-xs text-gray-400">
            <span>{pagination.total} group{pagination.total !== 1 ? 's' : ''}</span>
            <div className="flex items-center gap-2">
              <button
                disabled={pageOffset === 0}
                onClick={() => setPageOffset(Math.max(0, pageOffset - pageSize))}
                className="rounded-lg border border-gray-200 px-3 py-1.5 text-gray-500 hover:bg-gray-50 disabled:opacity-40"
              >
                ← Prev
              </button>
              <span className="text-gray-500">
                Page {Math.floor(pageOffset / pageSize) + 1} of {Math.max(1, Math.ceil(pagination.total / pageSize))}
              </span>
              <button
                disabled={!pagination.has_more}
                onClick={() => setPageOffset(pageOffset + pageSize)}
                className="rounded-lg border border-gray-200 px-3 py-1.5 text-gray-500 hover:bg-gray-50 disabled:opacity-40"
              >
                Next →
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Delete confirmation */}
      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete model group"
        description={deleteTarget ? `Delete "${deleteTarget}"? This removes all members and policy history references for this group.` : ''}
        confirmLabel="Delete Group"
        destructive
        confirming={!!deletingKey}
        onConfirm={handleDelete}
        onClose={() => { if (!deletingKey) setDeleteTarget(null); }}
      />
    </IndexShell>
  );
}
