import { useEffect, useRef, type RefObject } from 'react';

/** Calls onDismiss when the user presses outside the element while it is open. */
export function usePopoverDismiss(ref: RefObject<HTMLElement | null>, open: boolean, onDismiss: () => void) {
  const callback = useRef(onDismiss);
  useEffect(() => {
    callback.current = onDismiss;
  }, [onDismiss]);
  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) callback.current();
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [ref, open]);
}
