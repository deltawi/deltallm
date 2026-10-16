import { useMemo, useState } from 'react';
import { ArrowRight, Braces, ChevronLeft } from 'lucide-react';
import Button from '../Button';
import ConfirmDialog from '../ConfirmDialog';
import { IconTabs } from '../admin/shells';
import type { RouteGroup, RouteGroupMemberDetail, RoutePolicy } from '../../lib/api';
import { relativeTime } from '../../lib/requestLogs';
import { policyChanges, publisherLabel, stableJson } from '../../lib/routeGroupPolicyDiff';
import { memberLabels, plannedTrafficShares, routingLabel, strategyLabel } from '../../lib/routeGroupRouting';
import { draftIssues, dryRunSummary } from '../../lib/routeGroupSummary';
import { orderedGuidedMemberIds, restoreDraftPolicyTombstones, validateGuidedPolicy, validatePolicyContextCompatibility } from '../../lib/routeGroups';
import { savedRoutingDocument, useAdvancedRoutingDraft } from '../../lib/useAdvancedRoutingDraft';
import { useRoutePolicySimulation } from '../../lib/useRoutePolicySimulation';
import { DeploymentsCard, StrategyCard, TimeoutsCard } from './AdvancedRoutingCards';
import { ContextCard, SelectorCard } from './AdvancedRoutingOptions';
import DraftCheckRail from './DraftCheckRail';
import PolicyHistoryPanel from './PolicyHistoryPanel';
import PolicyJsonDrawer from './PolicyJsonDrawer';
import PolicyReviewDrawer from './PolicyReviewDrawer';
import RouteGroupPolicySimulationPanel from './RouteGroupPolicySimulationPanel';
import SelectorTools from './SelectorTools';

export type AdvancedSection = 'configure' | 'simulate' | 'history';
type Policy = Record<string, unknown>;

export interface AdvancedRoutingActions {
  saveDraft: (policy: Policy) => Promise<boolean>;
  publish: (policy: Policy, options: { hasDraft: boolean; saveFirst: boolean }) => Promise<boolean>;
  discardDraft: () => Promise<boolean>;
  /** Saves an old version as the draft. A stored draft is discarded first, so nothing of it is kept. */
  restore: (policy: Policy, options: { hasDraft: boolean }) => Promise<boolean>;
}

interface AdvancedRoutingProps {
  group: RouteGroup;
  members: RouteGroupMemberDetail[];
  policies: RoutePolicy[];
  published: RoutePolicy | null;
  draft: RoutePolicy | null;
  seed: Policy | null;
  section: AdvancedSection;
  canWrite: boolean;
  canSimulate: boolean;
  canGoSimple: boolean;
  busy: boolean;
  promptRef: Policy | null;
  actions: AdvancedRoutingActions;
  onSection: (section: AdvancedSection) => void;
  onBackToSimple: () => void;
  onOpenModels: () => void;
}

const SECTIONS_ID = 'advanced-routing';

export default function AdvancedRouting({
  group, members, policies, published, draft, seed, section, canWrite, canSimulate, canGoSimple, busy, promptRef, actions, onSection, onBackToSimple, onOpenModels,
}: AdvancedRoutingProps) {
  const saved = useMemo(() => savedRoutingDocument(published, draft), [draft, published]);
  const editor = useAdvancedRoutingDraft(seed ?? saved, saved, members, group.routing_strategy);
  const [drawer, setDrawer] = useState<'review' | 'json' | null>(null);
  const [confirm, setConfirm] = useState<{ kind: 'discard' } | { kind: 'restore'; policy: RoutePolicy } | null>(null);
  const live = published?.policy_json ?? null;
  const names = memberLabels(members);
  const changes = policyChanges(live, editor.doc, names, group.routing_strategy);
  const error = validateGuidedPolicy(editor.guided, members, group.mode) ?? validatePolicyContextCompatibility(editor.doc, group.mode);
  const included = orderedGuidedMemberIds(editor.guided, members).filter((id) => members.some((member) => member.deployment_id === id && member.enabled));
  const issues = draftIssues(error, members, included);
  const draftShares = plannedTrafficShares(editor.doc, group.routing_strategy, members);
  const liveShares = plannedTrafficShares(live, group.routing_strategy, members);
  const simulation = useRoutePolicySimulation(group.route_group_id, stableJson(editor.doc));
  const hasDraft = !!draft;
  const pending = hasDraft || editor.dirty;
  const nextVersion = draft?.version ?? Math.max(0, ...policies.map((policy) => policy.version)) + 1;
  const changedLabels = new Set(changes.map((change) => change.label));
  const memberNames = new Set(Object.values(names));
  const changed = (...labels: string[]) => labels.some((label) => changedLabels.has(label));
  const disabled = !canWrite || busy;
  const cardProps = { values: editor.guided, disabled, onChange: editor.setGuided };
  const simpleBlocked = !canGoSimple ? 'The live routing uses settings that simple routing cannot show.' : pending ? 'Publish or discard the draft first.' : null;

  const publish = async () => {
    if (await actions.publish(editor.doc, { hasDraft, saveFirst: hasDraft && editor.dirty })) setDrawer(null);
  };
  const runDryRun = () => void simulation.run({ iterations: 100, policy: editor.doc, prompt_ref: promptRef });
  const restore = (policy: RoutePolicy) => actions.restore(restoreDraftPolicyTombstones(policy.policy_json, live), { hasDraft });
  const sections = [
    { id: 'configure' as const, label: 'Configure' },
    { id: 'simulate' as const, label: 'Simulate' },
    { id: 'history' as const, label: 'History', count: policies.length },
  ];

  // Drawers and dialogs sit outside the spaced column: its margins would move their fixed layer.
  return (
    <>
      <div className="space-y-3">
        <div className="space-y-1">
          <button type="button" disabled={!!simpleBlocked} onClick={onBackToSimple} title={simpleBlocked ?? undefined}
            className="inline-flex items-center gap-1 text-xs font-medium text-brand-primary-ink hover:underline disabled:cursor-not-allowed disabled:text-gray-400 disabled:no-underline">
            <ChevronLeft aria-hidden="true" className="h-3.5 w-3.5" />Back to simple routing
          </button>
          <p className="text-xs text-gray-500">{simpleBlocked && canWrite ? `${simpleBlocked} ` : ''}Advanced routing keeps a draft, a review before publish, dry runs and history.</p>
        </div>

        {/* Focus lands here after a publish, discard or restore replaces the form. */}
        <section aria-label="Live and draft" tabIndex={-1} data-routing-focus className="flex flex-wrap items-center focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary gap-x-4 gap-y-2 rounded-xl border border-gray-200 bg-white px-4 py-2.5 shadow-sm">
          <div className="flex min-w-0 items-center gap-2">
            <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700"><span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-emerald-500" />Live</span>
            <div className="min-w-0 text-xs">
              <p className="font-semibold text-gray-900">{published ? `v${published.version} · ${routingLabel(live, group.routing_strategy)}` : `Group default · ${strategyLabel(group.routing_strategy)}`}</p>
              <p className="text-gray-500">{published ? `Published ${relativeTime(published.published_at ?? published.created_at)} by ${publisherLabel(published)}` : 'No version is published yet.'}</p>
            </div>
          </div>
          <ArrowRight aria-hidden="true" className="h-3.5 w-3.5 text-gray-400" />
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <span className="rounded-full bg-brand-primary-soft px-2 py-0.5 text-[11px] font-semibold text-brand-primary-ink">{hasDraft ? `Draft v${draft.version}` : editor.dirty ? 'New draft' : 'No draft'}</span>
            <div className="min-w-0 text-xs">
              <p className="font-semibold text-gray-900">{changes.length === 0 ? 'Same as live' : `${changes.length} ${changes.length === 1 ? 'change' : 'changes'} vs live`}</p>
              <p className="text-gray-500">{hasDraft ? `Saved ${relativeTime(draft.updated_at ?? draft.created_at)}${editor.dirty ? ' · unsaved edits' : ''}` : editor.dirty ? 'Not saved yet' : 'Edit any setting to start a draft.'}</p>
            </div>
          </div>
          {canWrite ? (
            <div className="flex flex-wrap gap-2">
              {pending ? <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirm({ kind: 'discard' })}>Discard draft</Button> : null}
              {editor.dirty ? <Button size="sm" variant="outline" disabled={busy || !!error} onClick={() => void actions.saveDraft(editor.doc)}>Save draft</Button> : null}
              {pending || changes.length > 0 ? <Button size="sm" disabled={busy} onClick={() => setDrawer('review')}>Review &amp; publish</Button> : null}
            </div>
          ) : null}
        </section>

        <div className="flex flex-wrap items-center justify-between gap-2">
          <IconTabs id={SECTIONS_ID} label="Advanced routing sections" variant="segment" items={sections} active={section} onChange={onSection} />
          <Button size="sm" variant="outline" onClick={() => setDrawer('json')}><Braces aria-hidden="true" className="h-3.5 w-3.5" />View JSON</Button>
        </div>

        <div role="tabpanel" id={`${SECTIONS_ID}-panel-${section}`} aria-labelledby={`${SECTIONS_ID}-tab-${section}`}>
        {section === 'configure' ? (
          <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,1fr)_18rem]">
            <div className="space-y-3">
              <StrategyCard {...cardProps} changed={changed('How to pick')} />
              <DeploymentsCard {...cardProps} changed={changes.some((change) => change.label === 'Models' || change.label === 'Order' || memberNames.has(change.label))}
                members={members} draftShares={draftShares} liveShares={liveShares} onManageMembers={onOpenModels} />
              <TimeoutsCard {...cardProps} changed={changed('Request timeout', 'Retries', 'Retry on')} />
              <ContextCard {...cardProps} workloadMode={group.mode} changed={changed('Context-aware routing')} />
              <SelectorCard {...cardProps} workloadMode={group.mode} changed={changed('Model selector')} routeGroupId={group.route_group_id} members={members} />
            </div>
            <DraftCheckRail simulation={simulation} onRun={runDryRun} issues={issues} members={members} draftShares={draftShares} liveShares={liveShares}
              canSimulate={canSimulate} blocked={!!error} onOpenSimulate={() => onSection('simulate')} />
          </div>
        ) : section === 'simulate' ? (
          <div className="space-y-3">
            <section className="rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
              <RouteGroupPolicySimulationPanel routeGroupId={group.route_group_id} policy={error ? null : editor.doc} policyError={error} members={members} canSimulate={canSimulate} promptRef={promptRef} />
            </section>
            {editor.doc.selector || live?.selector ? <SelectorTools routeGroupId={group.route_group_id} groupKey={group.group_key} policy={error ? null : editor.doc} /> : null}
          </div>
        ) : (
          <PolicyHistoryPanel policies={policies} members={members} groupDefault={group.routing_strategy} canWrite={canWrite} busy={busy}
            onOpenDraft={() => onSection('configure')}
            onRestore={(policy) => (pending ? setConfirm({ kind: 'restore', policy }) : void restore(policy))} />
        )}
        </div>
      </div>

      {drawer === 'review' ? (
        <PolicyReviewDrawer title={`Publish ${hasDraft ? 'draft ' : ''}v${nextVersion}?`} changes={changes} issues={issues} policy={editor.doc} livePolicy={live}
          dryRun={dryRunSummary(simulation)} busy={busy} onPublish={() => void publish()} onClose={() => setDrawer(null)} />
      ) : drawer === 'json' ? (
        <PolicyJsonDrawer title={`Policy JSON · ${hasDraft ? `draft v${draft.version}` : 'draft'}`} policy={editor.doc} livePolicy={live} workloadMode={group.mode} canEdit={canWrite}
          onApply={(next) => { editor.load(next); setDrawer(null); }} onClose={() => setDrawer(null)} />
      ) : null}

      <ConfirmDialog
        open={confirm !== null}
        title={confirm?.kind === 'restore' ? `Restore v${confirm.policy.version} as the draft?` : hasDraft ? `Discard draft v${draft.version}?` : 'Discard your changes?'}
        description={confirm?.kind === 'restore'
          ? 'This replaces the current draft and any unsaved edits. Live routing does not change.'
          : 'The draft and any unsaved edits are deleted. Live routing does not change.'}
        confirmLabel={confirm?.kind === 'restore' ? 'Restore' : 'Discard'}
        destructive={confirm?.kind !== 'restore'}
        confirming={busy}
        onClose={() => { if (!busy) setConfirm(null); }}
        onConfirm={() => {
          const current = confirm;
          if (!current) return;
          if (current.kind === 'restore') {
            void restore(current.policy).then((ok) => { if (ok) setConfirm(null); });
          } else if (hasDraft) {
            void actions.discardDraft().then((ok) => { if (ok) setConfirm(null); });
          } else {
            editor.load(saved);
            setConfirm(null);
          }
        }}
      />
    </>
  );
}
