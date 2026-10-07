import { useEffect, useRef, useState } from 'react';
import { apiFetch } from '../../lib/api/transport';

interface SelectedKey {
  selected: boolean;
  key_name?: string | null;
}

export default function ConsoleInferenceKey({ onSelection }: { onSelection: (ready: boolean) => void }) {
  const [draft, setDraft] = useState('');
  const [selected, setSelected] = useState<SelectedKey>({ selected: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const attempt = useRef(0);
  const callback = useRef(onSelection);
  useEffect(() => { callback.current = onSelection; }, [onSelection]);

  useEffect(() => {
    const controller = new AbortController();
    const id = ++attempt.current;
    void apiFetch<SelectedKey>('/console/inference-key', { signal: controller.signal })
      .then((result) => {
        if (attempt.current !== id) return;
        setSelected(result);
        callback.current(result.selected === true);
      })
      .catch(() => { if (attempt.current === id) setError('Select an API key to use the Playground.'); });
    return () => { attempt.current += 1; controller.abort(); };
  }, []);

  async function selectKey() {
    const id = ++attempt.current;
    setBusy(true);
    setError(null);
    callback.current(false);
    try {
      const result = await apiFetch<SelectedKey>('/console/inference-key', {
        method: 'POST', json: { api_key: draft.trim() },
      });
      if (attempt.current !== id) return;
      setDraft('');
      setSelected(result);
      callback.current(result.selected === true);
    } catch {
      if (attempt.current === id) {
        setSelected({ selected: false });
        setError('The key could not be selected. Use an active API key that you own.');
      }
    } finally {
      if (attempt.current === id) setBusy(false);
    }
  }

  return <div className="border-b bg-white px-4 py-3">
    <label className="text-sm font-medium" htmlFor="console-inference-key">Your Playground API key</label>
    <div className="mt-2 flex gap-2">
      <input id="console-inference-key" type="password" autoComplete="off" value={draft}
        onChange={(event) => setDraft(event.target.value)} placeholder="Paste your API key"
        className="min-w-0 flex-1 rounded border px-3 py-2" disabled={busy} />
      <button type="button" disabled={busy || !draft.trim()} onClick={() => void selectKey()}
        className="rounded bg-brand-primary px-4 text-white disabled:opacity-50">{busy ? 'Selecting…' : 'Select key'}</button>
    </div>
    {selected.selected && <p className="mt-2 text-sm text-green-700">Selected: {selected.key_name || 'Your API key'}</p>}
    {error && <p className="mt-2 text-sm text-amber-700" role="alert">{error}</p>}
  </div>;
}
