import { useState } from 'react';
import Button from '../Button';
import SideDrawer from '../SideDrawer';
import { IconTabs } from '../admin/shells';
import { stableJson } from '../../lib/routeGroupPolicyDiff';
import { parsePolicyTextLoose, validatePolicyContextCompatibility } from '../../lib/routeGroups';
import { JsonDiff } from './PolicyReviewDrawer';

type View = 'draft' | 'live' | 'diff';

const VIEWS: ReadonlyArray<{ id: View; label: string }> = [{ id: 'draft', label: 'Draft' }, { id: 'live', label: 'Live' }, { id: 'diff', label: 'Diff' }];
const VIEWS_ID = 'policy-json';

interface PolicyJsonDrawerProps {
  title: string;
  policy: Record<string, unknown>;
  livePolicy: Record<string, unknown> | null;
  workloadMode: string;
  canEdit: boolean;
  onApply: (policy: Record<string, unknown>) => void;
  onClose: () => void;
}

// JSON for people who prefer it: edit or paste the draft, see the live document, or compare both.
export default function PolicyJsonDrawer({ title, policy, livePolicy, workloadMode, canEdit, onApply, onClose }: PolicyJsonDrawerProps) {
  const original = JSON.stringify(policy, null, 2);
  const [view, setView] = useState<View>('draft');
  const [text, setText] = useState(original);
  const [copy, setCopy] = useState<'idle' | 'copied' | 'failed'>('idle');
  const parsed = parsePolicyTextLoose(text);
  const error = !parsed ? 'Enter a valid JSON object.' : validatePolicyContextCompatibility(parsed, workloadMode);
  const changed = !!parsed && stableJson(parsed) !== stableJson(policy);
  const shown = view === 'live' ? JSON.stringify(livePolicy ?? {}, null, 2) : text;
  const copyText = () => {
    const write = navigator.clipboard?.writeText(shown);
    if (!write) { setCopy('failed'); return; }
    write.then(() => setCopy('copied'), () => setCopy('failed'));
  };
  return (
    <SideDrawer
      title={title}
      description="Edit or paste a policy. Apply loads it into the form; nothing is saved until you save the draft."
      saving={false}
      onClose={onClose}
      size="md"
      overlayLabel="Close policy JSON"
      footer={(
        <>
          <span role="status" className="mr-auto text-xs text-gray-500">{copy === 'copied' ? 'Copied.' : copy === 'failed' ? 'Could not copy. Select the text and copy it.' : ''}</span>
          <Button size="sm" variant="outline" onClick={copyText}>Copy</Button>
          {canEdit && view === 'draft' ? <Button size="sm" disabled={!!error || !changed} onClick={() => parsed && onApply(parsed)}>Apply</Button> : null}
        </>
      )}
    >
      <div className="space-y-3">
        <IconTabs id={VIEWS_ID} label="JSON view" variant="segment" items={VIEWS} active={view} onChange={setView} />
        <div role="tabpanel" id={`${VIEWS_ID}-panel-${view}`} aria-labelledby={`${VIEWS_ID}-tab-${view}`} className="space-y-3">
        {view === 'draft' ? (
          <>
            <textarea aria-label="Draft policy JSON" value={text} readOnly={!canEdit} spellCheck={false} rows={22} onChange={(event) => setText(event.target.value)}
              aria-invalid={!!error} aria-describedby="policy-json-error"
              className="w-full rounded-lg bg-gray-950 p-3 font-mono text-xs leading-5 text-emerald-300 focus:outline-none focus:ring-2 focus:ring-brand-primary" />
            <p id="policy-json-error" role={error ? 'alert' : undefined} className={`text-xs ${error ? 'text-red-700' : 'text-gray-500'}`}>
              {error ?? (changed ? 'Apply to load these changes into the form.' : 'Same as the form.')}
            </p>
          </>
        ) : view === 'live' ? (
          <pre aria-label="Live policy JSON" className="max-h-[32rem] overflow-auto rounded-lg bg-gray-950 p-3 font-mono text-xs leading-5 text-gray-300">{shown}</pre>
        ) : <JsonDiff before={livePolicy} after={parsed ?? policy} label="JSON changes from live to draft" />}
        </div>
      </div>
    </SideDrawer>
  );
}
