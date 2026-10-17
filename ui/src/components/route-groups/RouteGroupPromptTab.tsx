import { useEffect, useState, type ReactNode } from 'react';
import { FileText } from 'lucide-react';
import { Link } from 'react-router-dom';
import Button from '../Button';
import ConfirmDialog from '../ConfirmDialog';
import ToggleSwitch from '../ToggleSwitch';
import { useToast } from '../ToastProvider';
import { Skeleton } from '../usage/ReportBits';
import { promptRegistry, type PromptBinding } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { requiredPromptVariables } from '../../lib/routeGroupSnippets';
import type { useRouteGroupMutationScope } from '../../lib/useRouteGroupMutationScope';

type MutationScope = ReturnType<typeof useRouteGroupMutationScope>;
type BindingInput = { template_key: string; label: string; priority: number; enabled: boolean };

const PROMPT_LIMIT = 100;
const SEARCH_DELAY_MS = 250;
const fieldClass = 'control-field w-full rounded-lg border border-gray-200 bg-white px-3 focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:bg-gray-50';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function Card({ description, children }: { description: string; children: ReactNode }) {
  return (
    <section aria-labelledby="route-group-prompt-title" className="max-w-4xl overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
      <header className="border-b border-gray-100 px-4 py-3">
        <h2 id="route-group-prompt-title" className="text-sm font-semibold text-gray-900">Prompt</h2>
        <p className="mt-0.5 max-w-xl text-xs text-gray-500">{description}</p>
      </header>
      {children}
    </section>
  );
}

/** Version labels of one prompt. Fetched only for the prompt the user picked. */
function useTemplateDetail(templateKey: string) {
  return useApi((signal) => (templateKey ? promptRegistry.getTemplate(templateKey, signal) : Promise.resolve(null)), [templateKey]);
}

function PromptPicker({ initial, busy, onSubmit, onCancel }: {
  initial: PromptBinding | null;
  busy: boolean;
  onSubmit: (templateKey: string, label: string) => void;
  onCancel?: () => void;
}) {
  const [input, setInput] = useState('');
  const [search, setSearch] = useState('');
  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(input.trim()), SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [input]);
  const templates = useApi(
    (signal) => promptRegistry.listTemplates({ search: search || undefined, limit: PROMPT_LIMIT, offset: 0 }, signal),
    [search],
  );
  const [templateKey, setTemplateKey] = useState(initial?.template_key ?? '');
  const [typedLabel, setLabel] = useState(initial?.label ?? 'production');
  const detail = useTemplateDetail(templateKey);
  const labels = detail.data?.labels.map((item) => item.label) ?? [];
  // A prompt with labels can only bind to one of them. With one label, it is picked for you.
  const label = labels.length === 0 || labels.includes(typedLabel) ? typedLabel : labels.length === 1 ? labels[0] : '';
  const items = templates.data?.data ?? [];
  const truncated = templates.data?.pagination.has_more === true;
  // Search shows once the list is cut off, and stays while a search is typed.
  const searchable = truncated || input !== '' || search !== '';
  if (!searchable && !templates.loading && !templates.error && items.length === 0) {
    return (
      <p className="px-4 py-4 text-sm text-gray-600">
        No prompts exist yet. <Link to="/prompts" className="font-medium text-brand-primary-ink underline">Create a prompt</Link>, then attach it here.
      </p>
    );
  }
  return (
    <div className="space-y-3 px-4 py-3">
      {searchable ? (
        <label className="block max-w-sm space-y-1 text-xs font-medium text-gray-700">
          <span>Search prompts</span>
          <input type="search" className={fieldClass} value={input} disabled={busy} onChange={(event) => setInput(event.target.value)} placeholder="Name or key" />
        </label>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block space-y-1 text-xs font-medium text-gray-700">
          <span>Prompt</span>
          <select className={fieldClass} value={templateKey} disabled={busy || (templates.loading && !templates.data)} onChange={(event) => setTemplateKey(event.target.value)}>
            <option value="">{templates.loading ? 'Loading prompts…' : items.length === 0 ? 'No prompts match' : 'Choose a prompt…'}</option>
            {templateKey && !items.some((item) => item.template_key === templateKey) ? <option value={templateKey}>{templateKey}</option> : null}
            {items.map((item) => <option key={item.prompt_template_id} value={item.template_key}>{item.name || item.template_key}</option>)}
          </select>
        </label>
        <label className="block space-y-1 text-xs font-medium text-gray-700">
          <span>Version label</span>
          {labels.length > 0 ? (
            <select className={fieldClass} value={label} disabled={busy} onChange={(event) => setLabel(event.target.value)}>
              <option value="" disabled>Choose a label…</option>
              {labels.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          ) : (
            <input className={fieldClass} value={label} disabled={busy || !templateKey} onChange={(event) => setLabel(event.target.value)} placeholder="production" />
          )}
        </label>
      </div>
      {templates.error ? <p role="alert" className="text-xs text-red-700">Prompts could not be loaded. <button type="button" className="underline" onClick={templates.refetch}>Retry</button></p> : null}
      {truncated ? <p className="text-xs text-gray-500">The list shows the first {PROMPT_LIMIT} prompts. Search to find others.</p> : null}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-gray-500">{initial ? `Replaces ${initial.template_key} (${initial.label}).` : 'No prompt is attached. Requests are sent as the caller wrote them.'}</p>
        <span className="flex gap-2">
          {onCancel ? <Button size="sm" variant="ghost" disabled={busy} onClick={onCancel}>Cancel</Button> : null}
          <Button size="sm" loading={busy} disabled={!templateKey || detail.loading || !label.trim()} onClick={() => onSubmit(templateKey, label.trim())}>
            {initial ? 'Save' : 'Attach prompt'}
          </Button>
        </span>
      </div>
    </div>
  );
}

function BindingRow({ binding, busy, onSave, onChange, onRemove }: {
  binding: PromptBinding;
  busy: boolean;
  onSave: (next: BindingInput) => void;
  onChange: () => void;
  onRemove: () => void;
}) {
  const detail = useTemplateDetail(binding.template_key);
  const [label, setLabel] = useState(binding.label);
  const [priority, setPriority] = useState(String(binding.priority));
  const data = detail.data;
  const version = data?.labels.find((item) => item.label === binding.label)?.version;
  const variables = requiredPromptVariables(data?.versions.find((item) => item.version === version)?.variables_schema);
  const labels = data?.labels.map((item) => item.label) ?? [binding.label];
  const priorityValid = /^\d+$/.test(priority.trim());
  return (
    <div className="space-y-2 px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span aria-hidden="true" className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-primary-soft text-brand-primary-ink"><FileText className="h-3.5 w-3.5" /></span>
          <div className="min-w-0">
            <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-gray-900">
              <Link to={`/prompts/${encodeURIComponent(binding.template_key)}`} className="hover:underline">{binding.template_key}</Link>
              <span className="rounded-full border border-gray-200 px-2 py-0.5 text-[11px] font-medium text-gray-600">{binding.label}</span>
            </p>
            <p className="text-xs text-gray-500">
              {detail.loading && !data ? 'Loading variables…' : variables.length > 0
                ? <>Variables callers send: <code className="font-mono">{variables.join(', ')}</code></>
                : 'Callers send no variables.'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <ToggleSwitch checked={binding.enabled} disabled={busy} activeColor="rgb(var(--brand-primary))" aria-label={`Use ${binding.template_key} for requests`}
            onCheckedChange={(enabled) => onSave({ template_key: binding.template_key, label: binding.label, priority: binding.priority, enabled })} />
          <span className="text-xs text-gray-700">{binding.enabled ? 'Active' : 'Off'}</span>
          <Button size="sm" variant="outline" disabled={busy} onClick={onChange}>Change</Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={onRemove}>Remove</Button>
        </div>
      </div>
      <details className="text-xs text-gray-600">
        <summary className="cursor-pointer select-none">More options</summary>
        <div className="mt-2 grid max-w-lg gap-3 sm:grid-cols-[1fr_8rem_auto] sm:items-end">
          <label className="block space-y-1 font-medium text-gray-700">
            <span>Version label</span>
            <select className={fieldClass} value={label} disabled={busy} onChange={(event) => setLabel(event.target.value)}>
              {[...new Set([binding.label, ...labels])].map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <label className="block space-y-1 font-medium text-gray-700">
            <span>Priority</span>
            <input className={fieldClass} inputMode="numeric" value={priority} disabled={busy} onChange={(event) => setPriority(event.target.value)} aria-invalid={!priorityValid} />
          </label>
          <Button size="sm" variant="outline" disabled={busy || !priorityValid || (label === binding.label && Number(priority) === binding.priority)}
            onClick={() => onSave({ template_key: binding.template_key, label, priority: Number(priority), enabled: binding.enabled })}>Save</Button>
        </div>
        <p className="mt-1 text-gray-500">When more than one active prompt is attached, the lowest priority number is used.</p>
      </details>
    </div>
  );
}

// Platform admins attach a saved prompt to the group, so callers only send variables.
export default function RouteGroupPromptTab({ groupKey, mutations, onChanged }: {
  groupKey: string;
  mutations: MutationScope;
  onChanged: () => void;
}) {
  const { pushToast } = useToast();
  const bindings = useApi(
    (signal) => promptRegistry.listBindings({ scope_type: 'group', scope_id: groupKey, limit: 20, offset: 0 }, signal),
    [groupKey],
  );
  const [busy, setBusy] = useState(false);
  const [changing, setChanging] = useState<PromptBinding | null>(null);
  const [removing, setRemoving] = useState<PromptBinding | null>(null);
  const list = bindings.data?.data ?? [];

  /** The action returns a warning when only part of the change was saved. */
  const run = async (title: string, action: (signal: AbortSignal) => Promise<string | void>) => {
    const operation = mutations.begin();
    if (!operation) return;
    setBusy(true);
    try {
      const warning = await action(operation.signal);
      if (!mutations.isCurrent(operation)) return;
      pushToast(warning
        ? { tone: 'error', title: 'Prompt partly saved', message: warning }
        : { tone: 'success', title, message: 'Requests to this group use the new prompt setting.' });
      setChanging(null);
      setRemoving(null);
      bindings.refetch();
      onChanged();
    } catch (error: unknown) {
      if (!mutations.isCurrent(operation)) return;
      pushToast({ tone: 'error', title: 'Prompt not saved', message: errorMessage(error, 'Failed to save the prompt binding.') });
    } finally {
      if (mutations.finish(operation)) setBusy(false);
    }
  };

  // The binding key is (prompt, label): a new prompt or label is a new row, so the old one is removed after.
  const save = (next: BindingInput, previous: PromptBinding | null) => run(previous ? 'Prompt updated' : 'Prompt attached', async (signal) => {
    await promptRegistry.upsertBinding({ scope_type: 'group', scope_id: groupKey, ...next }, signal);
    if (previous && (previous.template_key !== next.template_key || previous.label !== next.label)) {
      try {
        await promptRegistry.deleteBinding(previous.prompt_binding_id, signal);
      } catch (error: unknown) {
        if (signal.aborted) throw error;
        return `${next.template_key} (${next.label}) is attached, but ${previous.template_key} (${previous.label}) was not removed: ${errorMessage(error, 'the request failed').replace(/\.$/, '')}. Remove it below.`;
      }
    }
  });

  if (bindings.loading && !bindings.data) {
    return (
      <Card description="Every request to this group can use a saved prompt.">
        <div role="status" aria-label="Loading prompt" className="space-y-2 px-4 py-4"><Skeleton className="h-4 w-48" /><Skeleton className="h-3 w-72" /></div>
      </Card>
    );
  }
  if (bindings.error && !bindings.data) {
    return (
      <Card description="Every request to this group can use a saved prompt.">
        <p role="alert" className="px-4 py-4 text-sm text-red-700">The prompt could not be loaded. <Button size="sm" variant="outline" onClick={bindings.refetch}>Retry</Button></p>
      </Card>
    );
  }
  if (list.length === 0) {
    return (
      <Card description="Optional. Attach a saved prompt and every request to this group uses it, so callers only send variables.">
        <PromptPicker initial={null} busy={busy} onSubmit={(templateKey, label) => void save({ template_key: templateKey, label, priority: 100, enabled: true }, null)} />
      </Card>
    );
  }
  return (
    <Card description="Every request to this group uses this prompt. Callers only send the variables.">
      <div className="divide-y divide-gray-100">
        {list.map((binding) => changing?.prompt_binding_id === binding.prompt_binding_id ? (
          <PromptPicker key={binding.prompt_binding_id} initial={binding} busy={busy} onCancel={() => setChanging(null)}
            onSubmit={(templateKey, label) => void save({ template_key: templateKey, label, priority: binding.priority, enabled: binding.enabled }, binding)} />
        ) : (
          <BindingRow key={`${binding.prompt_binding_id}:${binding.label}:${binding.priority}`} binding={binding} busy={busy}
            onSave={(next) => void save(next, binding)} onChange={() => setChanging(binding)} onRemove={() => setRemoving(binding)} />
        ))}
      </div>
      <ConfirmDialog
        open={removing !== null}
        title="Remove prompt"
        description={removing ? `Stop using ${removing.template_key} for ${groupKey}? Requests are then sent as the caller wrote them.` : ''}
        confirmLabel="Remove prompt"
        destructive
        confirming={busy}
        onConfirm={() => removing && void run('Prompt removed', (signal) => promptRegistry.deleteBinding(removing.prompt_binding_id, signal).then(() => undefined))}
        onClose={() => { if (!busy) setRemoving(null); }}
      />
    </Card>
  );
}
