import { useEffect, useId, useRef } from 'react';
import type { ReactNode } from 'react';
import { X } from 'lucide-react';

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  wide?: boolean;
  focused?: boolean;
  description?: string;
  icon?: ReactNode;
  footer?: ReactNode;
  navigation?: ReactNode;
}

export default function Modal({ open, onClose, title, children, wide, focused, description, icon, footer, navigation }: ModalProps) {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const onCloseRef = useRef(onClose);
  const titleId = useId();

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (open) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => { document.body.style.overflow = ''; };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;

    const getFocusable = () => {
      const dialogElement = dialogRef.current;
      if (!dialogElement) return [] as HTMLElement[];
      const selector = 'button, [href], input, select, textarea, summary, [tabindex]:not([tabindex="-1"])';
      return Array.from(dialogElement.querySelectorAll<HTMLElement>(selector)).filter(
        (element) => !element.matches(':disabled') && element.tabIndex !== -1 && element.offsetParent !== null
      );
    };

    const preferredFocus = dialogRef.current?.querySelector<HTMLElement>('[data-autofocus="true"]');
    const focusable = getFocusable();
    const initialFocus = preferredFocus || focusable.find((element) => !element.hasAttribute('data-modal-close')) || focusable[0];
    initialFocus?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (!open) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== 'Tab') return;
      const currentFocusable = getFocusable();
      if (currentFocusable.length === 0) return;
      const first = currentFocusable[0];
      const last = currentFocusable[currentFocusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      previouslyFocused?.focus();
    };
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center" role="presentation">
      <div className="fixed inset-0 bg-black/50" onClick={onClose} />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`relative flex flex-col bg-white rounded-t-xl sm:rounded-xl shadow-xl max-h-[90vh] overflow-hidden w-full ${wide ? 'sm:max-w-[700px]' : focused ? 'sm:max-w-[548px]' : 'sm:max-w-[500px]'} sm:mx-4`}
      >
        <div className="flex shrink-0 items-center justify-between gap-3 p-4 sm:p-5 border-b bg-white">
          <div className="flex min-w-0 items-center gap-3">
            {icon && <div className="rounded-lg bg-brand-primary-soft p-2 text-brand-primary-ink">{icon}</div>}
            <div>
              <h2 id={titleId} className="text-lg font-semibold text-gray-900">{title}</h2>
              {description && <p className="mt-0.5 text-xs text-gray-500">{description}</p>}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1 hover:bg-gray-100 rounded-lg transition-colors"
            aria-label="Close dialog"
            data-modal-close="true"
          >
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>
        {navigation && <div className="shrink-0 px-4 pt-2 sm:px-5 [&>div]:mb-0">{navigation}</div>}
        <div className="min-h-0 overflow-y-auto p-4 sm:p-5">{children}</div>
        {footer && <div className="shrink-0 border-t bg-gray-50 p-4 sm:px-5">{footer}</div>}
      </div>
    </div>
  );
}
