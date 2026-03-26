import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';

const STORAGE_KEY = 'deltallm.sidebar.width';
const DEFAULT_WIDTH = 256;
const MIN_WIDTH = 240;
const MAX_WIDTH = 380;

function clampSidebarWidth(value: number): number {
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(value)));
}

function readStoredSidebarWidth(): number {
  if (typeof window === 'undefined') {
    return DEFAULT_WIDTH;
  }

  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) {
    return DEFAULT_WIDTH;
  }

  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? clampSidebarWidth(parsed) : DEFAULT_WIDTH;
}

export function useDesktopSidebarWidth() {
  const [width, setWidth] = useState<number>(() => readStoredSidebarWidth());
  const [isResizing, setIsResizing] = useState(false);
  const frameRef = useRef<number | null>(null);
  const pendingWidthRef = useRef(width);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }
    window.localStorage.setItem(STORAGE_KEY, String(width));
  }, [width]);

  useEffect(() => {
    if (!isResizing || typeof window === 'undefined') {
      return undefined;
    }

    const flushPendingWidth = () => {
      setWidth(pendingWidthRef.current);
      frameRef.current = null;
    };

    const handlePointerMove = (event: PointerEvent) => {
      pendingWidthRef.current = clampSidebarWidth(event.clientX);
      if (frameRef.current == null) {
        frameRef.current = window.requestAnimationFrame(flushPendingWidth);
      }
    };

    const handlePointerUp = () => {
      setIsResizing(false);
    };

    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp);
    window.addEventListener('pointercancel', handlePointerUp);

    return () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
      window.removeEventListener('pointercancel', handlePointerUp);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
      if (frameRef.current != null) {
        window.cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
    };
  }, [isResizing]);

  const startResizing = (event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    pendingWidthRef.current = width;
    setIsResizing(true);
  };

  const resetWidth = () => {
    pendingWidthRef.current = DEFAULT_WIDTH;
    setWidth(DEFAULT_WIDTH);
  };

  return {
    width,
    isResizing,
    startResizing,
    resetWidth,
  };
}
