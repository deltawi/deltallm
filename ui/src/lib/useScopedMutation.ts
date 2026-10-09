import { useLayoutEffect, useState } from 'react';
import { useAuth } from './auth';
import { MutationGuard } from './mutationGuard';

export function useScopedMutation(identity: string) {
  const { session, authMode, authStatus, isLoggingOut } = useAuth();
  const key = JSON.stringify([identity, session?.account_id, authMode, authStatus, isLoggingOut, session?.effective_permissions]);
  const [guard] = useState(() => new MutationGuard());
  useLayoutEffect(() => {
    guard.reset(key);
    return () => guard.reset('');
  }, [guard, key]);
  return { begin: () => guard.begin(key), cancel: () => guard.reset(key) };
}
