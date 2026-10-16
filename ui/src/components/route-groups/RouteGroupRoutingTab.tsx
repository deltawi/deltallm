import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import Button from '../Button';
import { useToast } from '../ToastProvider';
import { Skeleton } from '../usage/ReportBits';
import { routeGroups, type RouteGroup, type RouteGroupMemberDetail, type RoutePolicy } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { routeGroupMutationOutcome } from '../../lib/routeGroups';
import { readSimpleRouting } from '../../lib/routeGroupRouting';
import type { useRouteGroupMutationScope } from '../../lib/useRouteGroupMutationScope';
import type { AdvancedRoutingActions, AdvancedSection } from './AdvancedRouting';
import SimpleRoutingEditor, { type SavedNotice } from './SimpleRoutingEditor';

// Most groups use simple routing, so the advanced workbench loads only when it opens.
const AdvancedRouting = lazy(() => import('./AdvancedRouting'));

type MutationScope = ReturnType<typeof useRouteGroupMutationScope>;
type Policy = Record<string, unknown>;

const SAVED_NOTICE_MS = 30_000;

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

interface RouteGroupRoutingTabProps {
  group: RouteGroup;
  members: RouteGroupMemberDetail[];
  canWrite: boolean;
  canSimulate: boolean;
  mutations: MutationScope;
  promptRef: Policy | null;
  /** Called after live routing changes, so the page header and the Models tab show it. */
  onPublished: () => void;
  onAddModel: () => void;
}

interface Saved {
  version: number;
  previousVersion: number | null;
}

export default function RouteGroupRoutingTab({
  group, members, canWrite, canSimulate, mutations, promptRef, onPublished, onAddModel,
}: RouteGroupRoutingTabProps) {
  const { pushToast } = useToast();
  const routeGroupId = group.route_group_id;
  const history = useApi((signal) => routeGroups.listPolicies(routeGroupId, signal), [routeGroupId]);
  // Versions read right after a change, so the form never shows the old state as unsaved edits.
  const [latest, setLatest] = useState<RoutePolicy[] | null>(null);
  // Set when a reload after a change failed: the list it holds is out of date until a new one arrives.
  const [staleData, setStaleData] = useState<typeof history.data | undefined>(undefined);
  if (staleData !== undefined && history.data !== staleData) setStaleData(undefined);
  const loaded = latest !== null || history.data !== null;
  const policies = useMemo(() => latest ?? history.data?.policies ?? [], [history.data, latest]);
  const published = policies.find((policy) => policy.status === 'published') ?? null;
  const draft = policies.find((policy) => policy.status === 'draft') ?? null;
  const simple = useMemo(
    () => readSimpleRouting(published?.policy_json ?? null, group.routing_strategy, members),
    [group.routing_strategy, members, published],
  );
  const [view, setView] = useState<'simple' | 'advanced'>('simple');
  const [section, setSection] = useState<AdvancedSection>('configure');
  const [seed, setSeed] = useState<Policy | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<Saved | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  if (loaded && view === 'simple' && (draft || simple.routing === null)) {
    // A draft or a live policy that simple routing cannot show opens advanced routing, and it stays open after a discard.
    setView('advanced');
  }
  const advanced = view === 'advanced' || !!draft || simple.routing === null;

  useEffect(() => {
    if (!saved) return undefined;
    const timer = window.setTimeout(() => setSaved(null), SAVED_NOTICE_MS);
    return () => window.clearTimeout(timer);
  }, [saved]);

  /** A change can replace the control that had focus. Focus then moves to the new state, not to the page body. */
  const keepFocus = () => requestAnimationFrame(() => {
    const active = document.activeElement;
    if (active && active !== document.body && active.isConnected) return;
    const root = rootRef.current;
    (root?.querySelector<HTMLElement>('[data-routing-focus]') ?? root)?.focus();
  });

  /** Reads the versions again. A failed read shows a reload warning, never the list from before the change. */
  const reload = async (operation: NonNullable<ReturnType<MutationScope['begin']>>) => {
    const next = await routeGroups.listPolicies(routeGroupId, operation.signal).catch(() => null);
    if (!mutations.isCurrent(operation)) return;
    if (next) {
      setLatest(next.policies);
    } else {
      setLatest(null);
      setStaleData(history.data);
      history.refetch();
    }
    keepFocus();
  };

  /** Runs one policy change at a time, then reloads the versions. Resolves true when it worked. */
  const run = async (
    failure: string,
    action: (signal: AbortSignal) => Promise<{ title: string; message: string; warnings?: string[]; live: boolean }>,
  ): Promise<boolean> => {
    if (!canWrite) return false;
    const operation = mutations.begin();
    if (!operation) return false;
    setBusy(true);
    try {
      const result = await action(operation.signal);
      if (!mutations.isCurrent(operation)) return false;
      const outcome = routeGroupMutationOutcome(result.message, result.warnings ?? []);
      pushToast({ tone: outcome.tone, title: result.title, message: outcome.message });
      setSeed(null);
      if (result.live) onPublished();
      await reload(operation);
      return true;
    } catch (error: unknown) {
      if (!mutations.isCurrent(operation)) return false;
      pushToast({ tone: 'error', title: failure, message: errorMessage(error, 'The routing change was not saved. Try again.') });
      // A change with two steps can stop after the first one, so read what is stored now.
      await reload(operation);
      return false;
    } finally {
      if (mutations.finish(operation)) setBusy(false);
    }
  };

  const saveSimple = (policy: Policy) => void run('Routing not saved', async (signal) => {
    const result = await routeGroups.publishPolicy(routeGroupId, policy, signal);
    setSaved({ version: result.policy.version, previousVersion: published?.version ?? null });
    return { title: 'Routing saved', message: `Version ${result.policy.version} is live.`, warnings: result.warnings, live: true };
  });

  const undo = (version: number) => void run('Could not undo', async (signal) => {
    const result = await routeGroups.rollbackPolicy(routeGroupId, version, signal);
    setSaved(null);
    return { title: 'Change undone', message: `Version ${result.policy.version} restores the routing of version ${version}.`, warnings: result.warnings, live: true };
  });

  const actions: AdvancedRoutingActions = {
    saveDraft: (policy) => run('Draft not saved', async (signal) => {
      const result = await routeGroups.savePolicyDraft(routeGroupId, policy, signal);
      return { title: 'Draft saved', message: `Draft v${result.policy.version} is saved. Live routing did not change.`, warnings: result.warnings, live: false };
    }),
    publish: (policy, { hasDraft, saveFirst }) => run('Routing not published', async (signal) => {
      // A saved draft is published as it is stored, so unsaved edits are saved into it first.
      if (saveFirst) await routeGroups.savePolicyDraft(routeGroupId, policy, signal);
      const result = await routeGroups.publishPolicy(routeGroupId, hasDraft ? undefined : policy, signal);
      return { title: 'Routing published', message: `Version ${result.policy.version} is live. New requests use it within a few seconds.`, warnings: result.warnings, live: true };
    }),
    discardDraft: () => run('Draft not discarded', async (signal) => {
      const result = await routeGroups.discardPolicyDraft(routeGroupId, signal);
      return { title: 'Draft discarded', message: `Draft v${result.discarded_version} is deleted. Live routing did not change.`, live: false };
    }),
    restore: async (policy, { hasDraft }) => {
      const ok = await run('Version not restored', async (signal) => {
        // The server merges a draft save into the stored draft, which would keep settings the old version does not have.
        if (hasDraft) await routeGroups.discardPolicyDraft(routeGroupId, signal);
        const result = await routeGroups.savePolicyDraft(routeGroupId, policy, signal);
        return { title: 'Restored as draft', message: `Draft v${result.policy.version} has the restored settings. Review it, then publish.`, live: false };
      });
      if (ok) setSection('configure');
      return ok;
    },
  };

  const loading = (
    <div role="status" aria-label="Loading routing" className="max-w-4xl space-y-3">
      <Skeleton className="h-4 w-64" />
      <div className="space-y-3 rounded-xl border border-gray-200 bg-white px-4 py-4 shadow-sm">
        <Skeleton className="h-4 w-48" />
        <div className="grid gap-2 sm:grid-cols-3"><Skeleton className="h-16" /><Skeleton className="h-16" /><Skeleton className="h-16" /></div>
        <Skeleton className="h-3 w-80" />
      </div>
    </div>
  );
  const stale = staleData !== undefined;
  if ((history.error && !history.data) || (stale && history.error)) {
    return (
      <div role="alert" className="flex max-w-4xl items-center justify-between gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
        <span>{stale ? 'The change is saved, but the routing could not be loaded again.' : 'The routing could not be loaded.'}</span>
        <Button size="sm" variant="outline" onClick={() => { setLatest(null); history.refetch(); }}>Retry</Button>
      </div>
    );
  }
  if (!loaded || stale) return loading;

  if (!advanced) {
    const previous = saved?.previousVersion ?? null;
    const notice: SavedNotice | null = saved && saved.version === published?.version
      ? { version: saved.version, busy, onUndo: previous ? () => undo(previous) : null }
      : null;
    return (
      <div ref={rootRef} tabIndex={-1} aria-label="Routing" className="focus:outline-none">
        <SimpleRoutingEditor group={group} members={members} published={published} canWrite={canWrite} saving={busy} saved={notice}
          onSave={saveSimple}
          onOpenAdvanced={(next) => { setSeed(next); setSection('configure'); setSaved(null); setView('advanced'); }}
          onOpenHistory={() => { setSection('history'); setSaved(null); setView('advanced'); }}
          onAddModel={onAddModel} />
      </div>
    );
  }

  return (
    <div ref={rootRef} tabIndex={-1} aria-label="Routing" className="focus:outline-none">
    <Suspense fallback={loading}>
      <AdvancedRouting
        // A new live version or a new draft starts the form again from what is stored.
        key={`${published?.route_policy_id ?? 'none'}:${draft?.route_policy_id ?? 'none'}:${draft?.updated_at ?? ''}`}
        group={group} members={members} policies={policies} published={published} draft={draft} seed={seed}
        section={section} canWrite={canWrite} canSimulate={canSimulate} canGoSimple={simple.routing !== null} busy={busy}
        promptRef={promptRef} actions={actions} onSection={setSection}
        onBackToSimple={() => { setSeed(null); setView('simple'); }}
        onOpenModels={onAddModel} />
    </Suspense>
    </div>
  );
}
