import { useMemo, useState } from 'react';
import type { RouteGroupMemberDetail, RoutePolicy } from './api';
import { stableJson } from './routeGroupPolicyDiff';
import {
  buildPolicyFromGuided,
  effectiveRouteGroupStrategy,
  reconcileGuidedPolicyMembers,
  restoreDraftPolicyTombstones,
  toGuidedPolicy,
  type PolicyGuidedValues,
} from './routeGroups';

/** The stored document the advanced editor compares with: the draft if there is one, else the live policy. */
export function savedRoutingDocument(published: RoutePolicy | null, draft: RoutePolicy | null): Record<string, unknown> {
  const live = published?.policy_json ?? null;
  // A draft that omits context or selector keeps the live ones on write; tombstones make removals explicit.
  return draft ? restoreDraftPolicyTombstones(draft.policy_json, live) : live ?? {};
}

/**
 * A document with no strategy inherits the group default. The form shows that strategy,
 * so a publish keeps it instead of the form's first option.
 */
export function withInheritedStrategy(doc: Record<string, unknown>, groupDefault: string | null | undefined): Record<string, unknown> {
  if (effectiveRouteGroupStrategy(doc, null)) return doc;
  return { ...doc, strategy: groupDefault || 'simple-shuffle' };
}

interface DraftState {
  base: Record<string, unknown>;
  guided: PolicyGuidedValues;
}

/**
 * Form state for advanced routing. `base` keeps keys the form does not show, so a
 * save never drops them. The editor is dirty when its document differs from `saved`.
 */
export function useAdvancedRoutingDraft(
  initial: Record<string, unknown>,
  stored: Record<string, unknown>,
  members: RouteGroupMemberDetail[],
  groupDefault: string | null | undefined,
) {
  const [state, setState] = useState<DraftState>(() => {
    const start = withInheritedStrategy(initial, groupDefault);
    return { base: start, guided: toGuidedPolicy(start, members) };
  });
  const saved = useMemo(() => withInheritedStrategy(stored, groupDefault), [groupDefault, stored]);
  const [syncedMembers, setSyncedMembers] = useState(members);
  if (syncedMembers !== members) {
    setSyncedMembers(members);
    setState((current) => ({ ...current, guided: reconcileGuidedPolicyMembers(current.guided, members) }));
  }
  const doc = useMemo(() => buildPolicyFromGuided(state.base, state.guided), [state]);
  const savedDoc = useMemo(() => buildPolicyFromGuided(saved, toGuidedPolicy(saved, members)), [members, saved]);
  return {
    guided: state.guided,
    doc,
    dirty: stableJson(doc) !== stableJson(savedDoc),
    setGuided: (guided: PolicyGuidedValues) => setState((current) => ({ ...current, guided })),
    // Pasted JSON that leaves out context or selector removes them: the server keeps omitted ones.
    load: (next: Record<string, unknown>) => {
      const doc = withInheritedStrategy(restoreDraftPolicyTombstones(next, stored), groupDefault);
      setState({ base: doc, guided: toGuidedPolicy(doc, members) });
    },
  };
}
