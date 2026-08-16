import { X } from 'lucide-react';
import { useEffect, useId, useRef, type ReactNode } from 'react';

type TierEditorDrawerProps = {
  title: string;
  description: string;
  saving: boolean;
  onClose: () => void;
  children: ReactNode;
};

export default function TierEditorDrawer({
  title,
  description,
  saving,
  onClose,
  children,
}: TierEditorDrawerProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef(onClose);
  const savingRef = useRef(saving);
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    closeRef.current = onClose;
    savingRef.current = saving;
  }, [onClose, saving]);

  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const focusableElements = () => {
      const selector = 'button, input, textarea, select, [href], [tabindex]:not([tabindex="-1"])';
      return Array.from(panelRef.current?.querySelectorAll<HTMLElement>(selector) || [])
        .filter((element) => !element.hasAttribute('disabled') && element.offsetParent !== null);
    };
    requestAnimationFrame(() => {
      const focusable = focusableElements();
      (focusable.find((element) => !element.hasAttribute('data-drawer-close')) || focusable[0])?.focus();
    });

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !savingRef.current) {
        event.preventDefault();
        closeRef.current();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = focusableElements();
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = previousOverflow;
      previouslyFocused?.focus();
    };
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex" role="presentation">
      <button
        type="button"
        aria-label="Close editor"
        disabled={saving}
        onClick={onClose}
        className="min-w-0 flex-1 bg-black/30 disabled:cursor-default"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        className="flex h-full w-full max-w-3xl shrink-0 flex-col border-l border-gray-200 bg-gray-50 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-4 border-b border-gray-200 bg-white px-5 py-4">
          <div>
            <h2 id={titleId} className="text-base font-semibold text-gray-900">{title}</h2>
            <p id={descriptionId} className="mt-0.5 text-xs text-gray-500">{description}</p>
          </div>
          <button
            type="button"
            data-drawer-close="true"
            aria-label={`Close ${title.toLowerCase()}`}
            disabled={saving}
            onClick={onClose}
            className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-5">{children}</div>
      </div>
    </div>
  );
}
