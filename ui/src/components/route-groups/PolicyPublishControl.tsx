import { useState } from 'react';
import Button from '../Button';
import ConfirmDialog from '../ConfirmDialog';
import { selectorPublishConfirmation } from '../../lib/routeGroupSelector';

export default function PolicyPublishControl({ policy, activePolicy, busy, disabled, onPublish }: {
  policy: Record<string, unknown> | null;
  activePolicy: Record<string, unknown> | null;
  busy: boolean;
  disabled: boolean;
  onPublish: () => void;
}) {
  const [pending, setPending] = useState<{ policy: Record<string, unknown>; active: Record<string, unknown> | null } | null>(null);
  const confirmation = policy ? selectorPublishConfirmation(policy, activePolicy) : null;
  return <>
    <Button size="sm" disabled={disabled || busy || !policy} loading={busy} onClick={() => {
      if (confirmation && policy) setPending({ policy, active: activePolicy });
      else onPublish();
    }}>Publish</Button>
    <ConfirmDialog open={pending !== null && pending.policy === policy && pending.active === activePolicy}
      title={confirmation?.title ?? 'Publish policy?'} description={confirmation?.description ?? ''}
      confirmLabel="Publish" confirming={busy} confirmDisabled={disabled || !policy}
      onClose={() => setPending(null)} onConfirm={() => {
        if (pending?.policy !== policy || pending?.active !== activePolicy || disabled || busy) return;
        setPending(null);
        onPublish();
      }} />
  </>;
}
