import { useState } from 'react';
import Button from '../Button';
import type { RouteGroupMemberDetail, RoutePolicy } from '../../lib/api';
import { relativeTime } from '../../lib/requestLogs';
import { policyChangeSummary, publisherLabel } from '../../lib/routeGroupPolicyDiff';
import { memberLabels } from '../../lib/routeGroupRouting';
import { JsonDiff } from './PolicyReviewDrawer';

function StatusPill({ status }: { status: string }) {
  if (status === 'published') {
    return <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-1.5 text-[10px] font-semibold text-emerald-700"><span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-emerald-500" />Live</span>;
  }
  if (status === 'draft') return <span className="rounded-full bg-brand-primary-soft px-1.5 text-[10px] font-semibold text-brand-primary-ink">Draft</span>;
  return null;
}

/** The version a row is compared with: the live one for a draft, else the version before it. */
function previousOf(policy: RoutePolicy, policies: RoutePolicy[]): RoutePolicy | null {
  if (policy.status === 'draft') return policies.find((item) => item.status === 'published') ?? null;
  return policies.filter((item) => item.status !== 'draft' && item.version < policy.version).sort((a, b) => b.version - a.version)[0] ?? null;
}

interface PolicyHistoryPanelProps {
  policies: RoutePolicy[];
  members: RouteGroupMemberDetail[];
  groupDefault: string | null;
  canWrite: boolean;
  busy: boolean;
  onRestore: (policy: RoutePolicy) => void;
  onOpenDraft: () => void;
}

export default function PolicyHistoryPanel({ policies, members, groupDefault, canWrite, busy, onRestore, onOpenDraft }: PolicyHistoryPanelProps) {
  const sorted = [...policies].sort((a, b) => b.version - a.version);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = sorted.find((item) => item.route_policy_id === selectedId)
    ?? sorted.find((item) => item.status === 'published') ?? sorted[0] ?? null;
  const names = memberLabels(members);

  if (sorted.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-gray-300 bg-white px-4 py-8 text-center text-sm text-gray-500">
        No versions yet. The group uses its default routing until you save or publish one.
      </div>
    );
  }
  const previous = selected ? previousOf(selected, sorted) : null;
  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,24rem)]">
      <section aria-label="Versions" className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] text-sm">
            <thead>
              <tr className="text-left text-[11px] font-semibold uppercase tracking-wide text-gray-500">
                <th scope="col" className="px-4 py-2">Version</th>
                <th scope="col" className="px-2 py-2">Change</th>
                <th scope="col" className="px-2 py-2">By</th>
                <th scope="col" className="px-4 py-2">When</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((policy) => {
                const before = previousOf(policy, sorted);
                const active = policy.route_policy_id === selected?.route_policy_id;
                return (
                  <tr key={policy.route_policy_id} onClick={() => setSelectedId(policy.route_policy_id)} className={`cursor-pointer border-t border-gray-100 ${active ? 'bg-brand-primary-soft/60' : 'hover:bg-gray-50'}`}>
                    <td className="whitespace-nowrap px-4 py-2">
                      <button type="button" aria-pressed={active} onClick={() => setSelectedId(policy.route_policy_id)} className="mr-1.5 font-semibold text-gray-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary">v{policy.version}</button>
                      <StatusPill status={policy.status} />
                    </td>
                    <td className="px-2 py-2 text-xs text-gray-700">{policyChangeSummary(before?.policy_json ?? null, policy.policy_json, names, groupDefault)}</td>
                    <td className="px-2 py-2 text-xs text-gray-600">{policy.status === 'draft' ? '—' : publisherLabel(policy)}</td>
                    <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-500">{relativeTime(policy.published_at ?? policy.updated_at ?? policy.created_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="border-t border-gray-100 bg-gray-50 px-4 py-2 text-xs text-gray-500">Each summary compares a version with the one before it. A draft is compared with the live version.</p>
      </section>

      {selected ? (
        <section aria-label={`Version ${selected.version}`} className="space-y-3 rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-semibold text-gray-900">v{selected.version} <StatusPill status={selected.status} /></h3>
            <p className="text-xs text-gray-500">
              {selected.status === 'draft' ? 'Not published' : `${publisherLabel(selected)} · ${relativeTime(selected.published_at ?? selected.created_at)}`}
              {previous ? ` · compared with v${previous.version}` : ''}
            </p>
          </div>
          <JsonDiff before={previous?.policy_json ?? null} after={selected.policy_json} label={`JSON changes in version ${selected.version}`} />
          {selected.status === 'archived' ? (
            <>
              {canWrite ? <Button size="sm" disabled={busy} onClick={() => onRestore(selected)}>Restore as draft</Button> : null}
              <p className="text-xs text-gray-500">Restoring copies v{selected.version} into the draft. You review it and publish like any other change.</p>
            </>
          ) : selected.status === 'draft'
            ? <Button size="sm" variant="outline" onClick={onOpenDraft}>Open draft</Button>
            : <p className="text-xs text-gray-500">This version is serving traffic now.</p>}
        </section>
      ) : null}
    </div>
  );
}
