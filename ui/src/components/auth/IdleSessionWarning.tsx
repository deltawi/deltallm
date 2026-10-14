import { useEffect, useState } from 'react';
import { Clock } from 'lucide-react';
import Button from '../Button';
import Modal from '../Modal';
import { useAuth } from '../../lib/auth';

function formatRemaining(ms: number): string {
  const totalSeconds = Math.max(0, Math.ceil(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

export default function IdleSessionWarning() {
  const { idleWarningDeadline, staySignedIn, logout, isLoggingOut } = useAuth();
  const [now, setNow] = useState(() => Date.now());
  const [renewing, setRenewing] = useState(false);
  const open = idleWarningDeadline !== null;

  useEffect(() => {
    if (!open) return undefined;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [open]);

  const stay = async () => {
    setRenewing(true);
    try {
      await staySignedIn();
    } finally {
      setRenewing(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={() => void stay()}
      title="Are you still there?"
      icon={<Clock className="h-5 w-5" />}
      footer={(
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => void logout().catch(() => undefined)} loading={isLoggingOut}>
            Sign out
          </Button>
          <Button data-autofocus="true" onClick={() => void stay()} loading={renewing}>
            Stay signed in
          </Button>
        </div>
      )}
    >
      <p className="text-sm text-gray-600">
        For security, you will be signed out in{' '}
        <span className="font-semibold tabular-nums text-gray-900">
          {formatRemaining((idleWarningDeadline ?? now) - now)}
        </span>{' '}
        because there has been no activity.
      </p>
    </Modal>
  );
}
