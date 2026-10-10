import { useEffect, useState } from 'react';
import { keyRevocationStatus, type KeyRemovalResult } from '../lib/api/keyRevocations';

export default function KeyRevocationNotice({ result }: { result: KeyRemovalResult }) {
  const [enforcement, setEnforcement] = useState(result.enforcement);
  useEffect(() => {
    if (result.enforcement !== 'pending' || !result.invalidation_id) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;
    const id = result.invalidation_id;
    async function poll() {
      try {
        const state = await keyRevocationStatus(id, controller.signal);
        if (controller.signal.aborted) return;
        if (state.enforcement === 'enforced') { setEnforcement('enforced'); return; }
      } catch { /* Keep the pending notice when the status service is unavailable. */ }
      if (!controller.signal.aborted && ++attempts < 60) timer = setTimeout(() => void poll(), 2000);
    }
    timer = setTimeout(() => void poll(), 2000);
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [result]);
  if (!enforcement) return null;
  return <div role="status" className="mb-4 rounded border border-amber-200 bg-amber-50 p-3 text-sm">
    {enforcement === 'enforced' ? 'The key is revoked and cache enforcement is complete.' :
      `The key is removed. Cache enforcement is pending; existing cached access can last up to ${result.maximum_enforcement_delay_seconds || 61} seconds.`}
  </div>;
}
