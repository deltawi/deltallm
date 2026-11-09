import { Star, X } from 'lucide-react';
import { REPO_URL, dismissStarNudge, useIsWhiteLabeled, useStarNudgeDismissed } from '../lib/starNudge';

/** Sidebar card asking platform admins to star the repository. Never calls GitHub. */
export default function StarRepoNudge() {
  const dismissed = useStarNudgeDismissed();
  const whiteLabeled = useIsWhiteLabeled();
  if (dismissed || whiteLabeled) return null;

  return (
    <div className="relative mx-2 mb-1 rounded-lg border border-gray-200 bg-brand-menu-hover p-3">
      <button
        type="button"
        onClick={dismissStarNudge}
        aria-label="Dismiss star reminder"
        title="Dismiss"
        className="control-icon-sm absolute right-1 top-1 rounded-md text-gray-400 transition-colors hover:bg-white hover:text-gray-600"
      >
        <X className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
      <p className="pr-6 text-sm font-medium text-gray-900">Enjoying DeltaLLM?</p>
      <p className="mt-0.5 text-xs text-gray-500">A GitHub star helps other teams find it. It takes one click.</p>
      <a
        href={REPO_URL}
        target="_blank"
        rel="noopener noreferrer"
        onClick={dismissStarNudge}
        className="control-sm mt-2.5 flex w-full items-center justify-center gap-1.5 rounded-md border border-gray-200 bg-white px-2.5 text-xs font-medium text-gray-700 transition-colors hover:border-amber-300 hover:text-gray-900"
      >
        <Star className="h-3.5 w-3.5 text-amber-500" aria-hidden="true" />
        Star on GitHub
      </a>
    </div>
  );
}
