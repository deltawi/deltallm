import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { AlertCircle, CheckCircle2, Info, X } from 'lucide-react';

type ToastTone = 'success' | 'error' | 'info';

type ToastOptions = {
  title?: string;
  message: string;
  tone?: ToastTone;
  durationMs?: number;
};

type ToastItem = {
  id: number;
  title?: string;
  message: string;
  tone: ToastTone;
};

type ToastContextValue = {
  pushToast: (options: ToastOptions) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextIdRef = useRef(1);
  const timeoutRef = useRef<Map<number, number>>(new Map());

  const removeToast = useCallback((id: number) => {
    const timerId = timeoutRef.current.get(id);
    if (timerId) {
      window.clearTimeout(timerId);
      timeoutRef.current.delete(id);
    }
    setToasts((current) => current.filter((item) => item.id !== id));
  }, []);

  const pushToast = useCallback(
    ({ title, message, tone = 'info', durationMs = 4500 }: ToastOptions) => {
      const id = nextIdRef.current++;
      setToasts((current) => [...current, { id, title, message, tone }]);
      const timerId = window.setTimeout(() => removeToast(id), durationMs);
      timeoutRef.current.set(id, timerId);
    },
    [removeToast]
  );

  useEffect(
    () => () => {
      timeoutRef.current.forEach((timerId) => window.clearTimeout(timerId));
      timeoutRef.current.clear();
    },
    []
  );

  const value = useMemo(() => ({ pushToast }), [pushToast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="fixed top-4 right-4 z-[70] w-[min(92vw,420px)] space-y-2" aria-live="polite" aria-atomic="true">
        {toasts.map((toast) => {
          const styles: Record<ToastTone, string> = {
            success: 'border-green-200 bg-green-50 text-green-900',
            error: 'border-red-200 bg-red-50 text-red-900',
            info: 'border-blue-200 bg-blue-50 text-blue-900',
          };
          const icons: Record<ToastTone, typeof CheckCircle2> = {
            success: CheckCircle2,
            error: AlertCircle,
            info: Info,
          };
          const Icon = icons[toast.tone];
          return (
            <div key={toast.id} className={`rounded-lg border shadow-sm px-3 py-2 ${styles[toast.tone]}`} role="status">
              <div className="flex items-start gap-2">
                <Icon className="w-4 h-4 mt-0.5 shrink-0" />
                <div className="min-w-0 flex-1">
                  {toast.title && <p className="text-sm font-semibold">{toast.title}</p>}
                  <p className="text-sm">{toast.message}</p>
                </div>
                <button
                  type="button"
                  onClick={() => removeToast(toast.id)}
                  className="p-1 rounded hover:bg-white/60 transition-colors"
                  aria-label="Dismiss notification"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within ToastProvider');
  }
  return context;
}
