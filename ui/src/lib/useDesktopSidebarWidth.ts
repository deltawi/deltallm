import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';

const WIDTH_KEY = 'deltallm.sidebar.width';
const COLLAPSED_KEY = 'deltallm.sidebar.collapsed';
const DEFAULT_WIDTH = 260;
const MIN_WIDTH = 180;
const MAX_WIDTH = 360;
const COLLAPSED_WIDTH = 56;

function clampSidebarWidth(value: number): number {
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(value)));
}

function readStoredSidebarWidth(): number {
  if (typeof window === 'undefined') return DEFAULT_WIDTH;
  const raw = window.localStorage.getItem(WIDTH_KEY);
  if (!raw) return DEFAULT_WIDTH;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? clampSidebarWidth(parsed) : DEFAULT_WIDTH;
}

function readStoredCollapsed(): boolean {
  if (typeof window === 'undefined') return false;
  return window.localStorage.getItem(COLLAPSED_KEY) === '1';
}

export function useDesktopSidebarWidth() {
  const [width, setWidth] = useState<number>(() => readStoredSidebarWidth());
  const [collapsed, setCollapsed] = useState<boolean>(() => readStoredCollapsed());
  const [hovered, setHovered] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const frameRef = useRef<number | null>(null);
  const pendingWidthRef = useRef(width);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(WIDTH_KEY, String(width));
  }, [width]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(COLLAPSED_KEY, collapsed ? '1' : '0');
  }, [collapsed]);

  useEffect(() => {
    if (!isResizing || typeof window === 'undefined') return undefined;

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

  const toggleCollapsed = () => {
    setCollapsed((c) => !c);
    setHovered(false);
  };

  const showExpanded = !collapsed || hovered;
  const resolvedWidth = collapsed ? (hovered ? width : COLLAPSED_WIDTH) : width;

  return {
    width: resolvedWidth,
    expandedWidth: width,
    collapsed,
    hovered,
    showExpanded,
    isResizing,
    startResizing,
    resetWidth,
    toggleCollapsed,
    setHovered,
  };
}
