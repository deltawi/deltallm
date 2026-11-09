import { useSyncExternalStore } from 'react';
import { useBranding } from './brandingContext';
import { DEFAULT_BRANDING } from './branding';

export const REPO_URL = 'https://github.com/deltawi/deltallm';
export const STAR_NUDGE_DISMISSED_KEY = 'deltallm.starNudgeDismissed';

// The desktop sidebar and the mobile drawer each render the card, so dismissal is shared
// through this small store and hides every copy at once.
const listeners = new Set<() => void>();
let dismissedInMemory = false;

function isDismissed(): boolean {
  if (dismissedInMemory) return true;
  try {
    return window.localStorage.getItem(STAR_NUDGE_DISMISSED_KEY) === '1';
  } catch {
    return false;
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key === STAR_NUDGE_DISMISSED_KEY) listener();
  };
  window.addEventListener('storage', onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener('storage', onStorage);
  };
}

/** Hides the star card for good in this browser, in every open tab. */
export function dismissStarNudge(): void {
  dismissedInMemory = true;
  try {
    window.localStorage.setItem(STAR_NUDGE_DISMISSED_KEY, '1');
  } catch {
    // Storage blocked: the card stays hidden until the page reloads.
  }
  listeners.forEach((listener) => listener());
}

export function useStarNudgeDismissed(): boolean {
  return useSyncExternalStore(subscribe, isDismissed, () => true);
}

/** True when the operator replaced the DeltaLLM name or logos, so the UI stays white-labeled. */
export function useIsWhiteLabeled(): boolean {
  const { branding } = useBranding();
  return (
    branding.instance_name !== DEFAULT_BRANDING.instance_name ||
    Boolean(branding.logo_mark_url) ||
    Boolean(branding.logo_full_url)
  );
}
