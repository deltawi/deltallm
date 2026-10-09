import { useState } from 'react';
import { Check, Copy, KeyRound } from 'lucide-react';
import Button from '../Button';
import Modal from '../Modal';

export default function ApiKeySecretDialog({ secret, onClose }: { secret: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(secret);
      setCopied(true);
      setError(null);
    } catch {
      setError('Could not copy the key. Select and copy it below.');
    }
  };
  return <Modal open focused title="API key created" description="Copy the key and store it in a safe place." icon={<KeyRound className="h-5 w-5" />} onClose={onClose} footer={<div className="flex justify-end"><Button onClick={onClose}>Done</Button></div>}>
    <div className="space-y-4">
      <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm leading-5 text-amber-900">This key is shown once. You cannot view it after you close this dialog.</p>
      <code className="block select-all break-all rounded-lg border border-gray-200 bg-gray-50 p-4 text-sm text-gray-900">{secret}</code>
      <Button data-autofocus="true" variant="secondary" fullWidth onClick={copy}>{copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}{copied ? 'Copied' : 'Copy key'}</Button>
      <p role="status" className="text-xs text-gray-500">{copied ? 'Key copied to the clipboard.' : 'Use this key to authenticate API requests.'}</p>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    </div>
  </Modal>;
}
