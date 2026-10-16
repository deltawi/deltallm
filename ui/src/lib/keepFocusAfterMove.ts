/**
 * Keeps keyboard focus in a row after the row moves. When the pressed control is now
 * disabled (the row reached the top or the bottom), focus goes to an enabled control next to it.
 */
export function keepFocusAfterMove(button: HTMLButtonElement) {
  requestAnimationFrame(() => {
    if (button.isConnected && !button.disabled) {
      button.focus();
      return;
    }
    button.parentElement?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
  });
}

/**
 * After a control moves a row to another place in the same list, focus the first
 * enabled button of that row in its new place. Rows carry `data-row-id`.
 */
export function focusMovedRow(list: Element | null, rowId: string) {
  requestAnimationFrame(() => {
    const row = [...(list?.querySelectorAll<HTMLElement>('[data-row-id]') ?? [])].find((item) => item.dataset.rowId === rowId);
    row?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
  });
}
