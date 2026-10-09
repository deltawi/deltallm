import { useLayoutEffect, useRef, useState } from 'react';
import { users, type RuntimeUserProfile } from '../../lib/api';
import { useAuth } from '../../lib/auth';
import { hasPermission } from '../../lib/authorization';
import { parseOutputTpm } from '../../lib/outputTpm';
import OutputTpmField from './OutputTpmField';

export default function RuntimeOutputTpmEditor({ user, onSaved }: { user: RuntimeUserProfile; onSaved: (limit: number | null) => void }) {
  const { authMode, session } = useAuth();
  const [value, setValue] = useState(user.output_tpm_limit == null ? '' : String(user.output_tpm_limit));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<AbortController | null>(null);
  const allowed = hasPermission(authMode, session, 'user.update');
  useLayoutEffect(() => {
    setValue(user.output_tpm_limit == null ? '' : String(user.output_tpm_limit));
    setSaving(false);
    setError(null);
    return () => {
      pending.current?.abort();
      pending.current = null;
    };
  }, [user.user_id, user.output_tpm_limit, authMode, session, allowed]);
  if (!allowed) return null;
  const save = async () => {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    const active = () => pending.current === controller && !controller.signal.aborted;
    setSaving(true);
    setError(null);
    try {
      const limit = parseOutputTpm(value);
      const saved = await users.updateOutputTpm(user.user_id, limit, controller.signal);
      if (active()) onSaved(saved.output_tpm_limit);
    } catch (cause) {
      if (active()) setError(cause instanceof Error ? cause.message : 'Could not save the output TPM limit.');
    } finally {
      if (active()) {
        pending.current = null;
        setSaving(false);
      }
    }
  };
  return <div className="mt-4">
    <OutputTpmField value={value} onChange={setValue} error={error} disabled={saving} />
    <button type="button" onClick={() => void save()} disabled={saving} className="mt-2 rounded-lg bg-brand-primary px-3 py-2 text-sm text-white disabled:opacity-50">{saving ? 'Saving…' : 'Save output limit'}</button>
  </div>;
}
