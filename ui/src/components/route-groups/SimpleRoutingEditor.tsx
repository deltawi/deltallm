import { useMemo, useState } from 'react';
import { ChevronRight, Plus } from 'lucide-react';
import Button from '../Button';
import ToggleSwitch from '../ToggleSwitch';
import type { RouteGroup, RouteGroupMemberDetail, RoutePolicy } from '../../lib/api';
import { publisherLabel } from '../../lib/routeGroupPolicyDiff';
import { relativeTime } from '../../lib/requestLogs';
import {
  buildSimpleRoutingPolicy,
  memberLabels,
  readSimpleRouting,
  routableMemberIds,
  simpleRoutingChanges,
  validateSimpleRouting,
  withSplitMembers,
  type SimpleRouting,
} from '../../lib/routeGroupRouting';
import { ModelAvatar } from './RouteGroupModelCell';
import SimpleRoutingChoices from './SimpleRoutingChoices';

export interface SavedNotice {
  version: number;
  busy: boolean;
  onUndo: (() => void) | null;
}

interface SimpleRoutingEditorProps {
  group: RouteGroup;
  members: RouteGroupMemberDetail[];
  published: RoutePolicy | null;
  canWrite: boolean;
  saving: boolean;
  saved: SavedNotice | null;
  onSave: (policy: Record<string, unknown>) => void;
  onOpenAdvanced: (seed: Record<string, unknown> | null) => void;
  onOpenHistory: () => void;
  onAddModel: () => void;
}

const RETRY_TEXT = {
  on: 'Covers timeouts and rate limits, up to 2 retries. Recommended.',
  off: 'Failed calls return an error to the caller.',
  default: 'Uses the gateway default now. Turn on to retry timeouts and rate limits on another model.',
};

export default function SimpleRoutingEditor({
  group, members, published, canWrite, saving, saved, onSave, onOpenAdvanced, onOpenHistory, onAddModel,
}: SimpleRoutingEditorProps) {
  const read = useMemo(
    () => readSimpleRouting(published?.policy_json ?? null, group.routing_strategy, members),
    [group.routing_strategy, members, published],
  );
  const fallback: SimpleRouting = { choice: 'even', order: [], percents: {}, retry: 'default' };
  const baseline = read.routing ?? fallback;
  const [routing, setRouting] = useState(() => withSplitMembers(baseline, members));
  const [synced, setSynced] = useState(read);
  const changes = simpleRoutingChanges(synced.routing ?? fallback, routing, members);
  if (synced !== read) {
    // New members or a new live version: keep unsaved edits, otherwise follow what is live.
    setSynced(read);
    setRouting(withSplitMembers(changes.length > 0 ? routing : baseline, members));
  }
  const error = validateSimpleRouting(routing, members);
  const routable = routableMemberIds(members);
  const labels = memberLabels(members);
  const disabled = !canWrite || saving;
  const seed = () => (changes.length > 0 && !error ? buildSimpleRoutingPolicy(routing, members) : null);

  return (
    <div className="max-w-4xl space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs text-gray-500">
        <p>How requests to <code className="font-mono text-gray-700">{group.group_key}</code> reach your models.</p>
        <p>
          {published
            ? <>Version {published.version} · saved {relativeTime(published.published_at ?? published.created_at)} by {publisherLabel(published)} · <button type="button" onClick={onOpenHistory} className="font-medium text-brand-primary-ink underline-offset-2 hover:underline">History</button></>
            : 'Using the default · nothing to set up'}
        </p>
      </div>

      {!canWrite ? <p className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs text-gray-600">You can view this routing. You cannot change it.</p> : null}

      {routable.length <= 1 ? (
        <section className="space-y-2 rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
          {routable.length === 1 ? (
            <>
              <p className="flex items-center gap-2 text-sm text-gray-800">
                <ModelAvatar provider={members.find((member) => member.deployment_id === routable[0])?.provider} model={labels[routable[0]]} />
                <span>Every request goes to <strong>{labels[routable[0]]}</strong>.</span>
              </p>
              <p className="text-xs text-gray-500">There is nothing to choose with one model. Add a second model to share traffic, or to keep a backup in case {labels[routable[0]]} fails.</p>
            </>
          ) : <p className="text-sm text-gray-700">This group has no models yet. Add a model so it can answer requests.</p>}
          {canWrite ? <Button size="sm" onClick={onAddModel}><Plus aria-hidden="true" className="h-3.5 w-3.5" />Add a model</Button> : null}
        </section>
      ) : (
        <>
          <section aria-labelledby="simple-routing-title" className="rounded-xl border border-gray-200 bg-white shadow-sm">
            <header className="border-b border-gray-100 px-4 py-3">
              <h2 id="simple-routing-title" className="text-sm font-semibold text-gray-900">How should requests be shared?</h2>
              <p className="mt-0.5 text-xs text-gray-500">Pick one. You can change it any time.</p>
            </header>
            <div className="px-4 py-3">
              <SimpleRoutingChoices routing={routing} members={members} disabled={disabled} onChange={setRouting} onAddModel={canWrite ? onAddModel : undefined} />
            </div>
          </section>
          <section className="flex items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
            <div>
              <h3 id="simple-retry-title" className="text-sm font-semibold text-gray-900">Retry on another model when a call fails</h3>
              <p className="mt-0.5 text-xs text-gray-500">{RETRY_TEXT[routing.retry]}</p>
            </div>
            <ToggleSwitch checked={routing.retry === 'on'} disabled={disabled} activeColor="rgb(var(--brand-primary))" aria-label="Retry on another model when a call fails"
              onCheckedChange={(on) => setRouting({ ...routing, retry: on ? 'on' : 'off' })} />
          </section>
        </>
      )}

      <section className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed border-gray-300 px-4 py-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">Advanced routing</h3>
          <p className="mt-0.5 text-xs text-gray-500">Other strategies, timeouts, model selector, dry runs, drafts, JSON and history.</p>
        </div>
        <Button size="sm" variant="outline" onClick={() => onOpenAdvanced(seed())}>Open<ChevronRight aria-hidden="true" className="h-3.5 w-3.5" /></Button>
      </section>

      {canWrite && changes.length > 0 ? (
        <div role="region" aria-label="Unsaved routing changes" className="sticky bottom-4 z-10 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-brand-primary/30 bg-white px-4 py-2.5 shadow-lg">
          <div className="min-w-0 flex-1 basis-56">
            <p className="text-sm font-semibold text-gray-900">{changes.length} unsaved {changes.length === 1 ? 'change' : 'changes'}</p>
            <p className={`truncate text-xs ${error ? 'text-red-700' : 'text-gray-500'}`} role={error ? 'alert' : undefined}>{error ?? changes.join(' · ')}</p>
          </div>
          <span className="flex gap-2">
            <Button size="sm" variant="ghost" disabled={saving} onClick={() => setRouting(withSplitMembers(baseline, members))}>Discard</Button>
            <Button size="sm" disabled={!!error} loading={saving} onClick={() => onSave(buildSimpleRoutingPolicy(routing, members))}>Save changes</Button>
          </span>
        </div>
      ) : saved ? (
        <div role="status" tabIndex={-1} data-routing-focus={saved.onUndo ? undefined : true}
          className="sticky bottom-4 z-10 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-gray-900 px-4 py-2.5 text-sm text-white shadow-lg focus:outline-none">
          <span>Routing saved as version {saved.version}. New requests use it within a few seconds.</span>
          {saved.onUndo ? (
            <button type="button" data-routing-focus disabled={saved.busy} onClick={saved.onUndo}
              className="rounded font-medium underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white disabled:opacity-60">
              {saved.busy ? 'Undoing…' : 'Undo'}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
