import { useState } from 'react';
import { ArrowUpRight, Ban, Equal, FlaskConical, Minus, Plus, X } from 'lucide-react';
import Button from '../../Button';
import { SettingsField, SettingsSection } from '../SettingsSection';
import { ssoGroupSync } from '../../../lib/api/ssoGroupSync';
import type { SsoGroupPreviewChange, SsoGroupPreviewResponse, SsoGroupSyncSettings } from '../../../lib/api/ssoGroupSync';
import { errorMessage } from '../../../lib/errorMessage';
import { buildSsoGroupSyncPayload, describeChange, parsePreviewInput, roleLabel, TARGET_STATUS_LABELS } from '../../../lib/ssoGroupSync';

const INPUT_CLASS = 'w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm transition-all focus:border-transparent focus:bg-white focus:outline-none focus:ring-2 focus:ring-brand-primary';

const CHANGE_STYLES: Record<SsoGroupPreviewChange['action'], { icon: typeof Plus; className: string }> = {
  add: { icon: Plus, className: 'bg-emerald-50 text-emerald-700' },
  update: { icon: ArrowUpRight, className: 'bg-blue-50 text-blue-700' },
  remove: { icon: Minus, className: 'bg-red-50 text-red-700' },
  kept: { icon: Equal, className: 'bg-amber-50 text-amber-700' },
};

function ChangeRow({ change }: { change: SsoGroupPreviewChange }) {
  const style = CHANGE_STYLES[change.action];
  const Icon = style.icon;
  return (
    <li className="flex items-start gap-3 py-2.5">
      <span className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md ${style.className}`}>
        <Icon className="h-3.5 w-3.5" />
      </span>
      <div className="min-w-0 text-sm">
        <p className="font-medium text-gray-900">{describeChange(change)}</p>
        {change.revokes_keys && (
          <p className="mt-0.5 text-xs text-red-600">Their API keys in this team will be deleted.</p>
        )}
        {change.kind !== 'platform_role' && change.target_id && (
          <p className="mt-0.5 font-mono text-[11px] text-gray-400">{change.target_id}</p>
        )}
      </div>
    </li>
  );
}

function PreviewResult({ result, removal }: { result: SsoGroupPreviewResponse; removal: boolean }) {
  if (!result.claim_found) {
    return (
      <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
        The token has no such claim. {result.sign_in_denied ? 'Sign-in is denied, because a mapped value is required.' : 'Sign-in works and SSO changes no access.'}
      </p>
    );
  }
  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-500">
        Claim found: {result.values.length} value{result.values.length === 1 ? '' : 's'}.
        {' '}Matched: {result.matched_values.length ? result.matched_values.join(', ') : 'none'}.
        {' '}{result.account_found ? `Account found (${roleLabel(result.account_role)}).` : 'No account with this email yet: a new account is created at sign-in.'}
      </p>
      {result.sign_in_denied ? (
        <p className="flex items-center gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
          <Ban className="h-4 w-4" /> Sign-in is denied, because no value is mapped.
        </p>
      ) : result.changes.length === 0 ? (
        <p className="rounded-lg bg-gray-50 px-3 py-2 text-sm text-gray-600">No changes. The account keeps its access.</p>
      ) : (
        <ul className="divide-y divide-gray-100">
          {result.changes.map((change) => (
            <ChangeRow key={`${change.kind}:${change.target_id ?? ''}:${change.action}`} change={change} />
          ))}
        </ul>
      )}
      {result.skipped.length > 0 && (
        <div className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
          Skipped: {result.skipped.map((item) => `${item.name || item.id} (${TARGET_STATUS_LABELS[item.status].toLowerCase()})`).join(', ')}.
        </div>
      )}
      {!removal && (
        <p className="text-xs text-gray-500">Removal is off, so SSO does not remove access. Turn on removal in Group sync to see what it would remove.</p>
      )}
    </div>
  );
}

export default function SsoPreviewPanel({ settings, onClose }: { settings: SsoGroupSyncSettings; onClose: () => void }) {
  const [email, setEmail] = useState('');
  const [input, setInput] = useState('');
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SsoGroupPreviewResponse | null>(null);

  const run = async () => {
    setError(null);
    let parsed: ReturnType<typeof parsePreviewInput>;
    try {
      parsed = parsePreviewInput(input);
    } catch (err) {
      setError(err instanceof SyntaxError ? 'The token claims are not valid JSON.' : errorMessage(err, 'Check the input.'));
      return;
    }
    setRunning(true);
    try {
      setResult(await ssoGroupSync.preview({
        settings: buildSsoGroupSyncPayload(settings),
        email: email.trim() || null,
        ...parsed,
      }));
    } catch (err) {
      setResult(null);
      setError(errorMessage(err, 'The preview failed.'));
    } finally {
      setRunning(false);
    }
  };

  return (
    <SettingsSection
      title="Test a sign-in"
      description="See what the next SSO sign-in would change. Uses the settings on this page, saved or not. Nothing is saved."
      icon={FlaskConical}
    >
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <div className="space-y-4">
          <SettingsField label="Email" htmlFor="sso-preview-email" hint="Leave empty to preview a new person.">
            <input
              id="sso-preview-email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="priya@acme.com"
              className={INPUT_CLASS}
            />
          </SettingsField>
          <SettingsField
            label="Token claims or claim values"
            htmlFor="sso-preview-input"
            hint="Paste a decoded ID token as JSON, or type values separated by commas, such as team-ml, admin."
          >
            <textarea
              id="sso-preview-input"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              rows={6}
              spellCheck={false}
              placeholder={`{\n  "${settings.claim || 'groups'}": ["team-ml", "admin"]\n}`}
              className={`${INPUT_CLASS} font-mono text-xs`}
            />
          </SettingsField>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => { void run(); }} loading={running}>Run preview</Button>
            <Button size="sm" variant="ghost" onClick={onClose}><X className="h-4 w-4" />Close</Button>
          </div>
          {error && <p className="text-xs text-red-600" role="alert">{error}</p>}
        </div>
        <div className="min-w-0 rounded-lg border border-gray-100 p-4">
          {result ? (
            <PreviewResult result={result} removal={settings.remove_unlisted} />
          ) : (
            <p className="text-sm text-gray-400">Run a preview to see the result here.</p>
          )}
        </div>
      </div>
    </SettingsSection>
  );
}
