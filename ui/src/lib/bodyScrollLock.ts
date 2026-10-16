// Dialogs and drawers can stack, and close in any order. The page scrolls again only
// when the last one closes, and it gets back the overflow it had before the first one.
const locks = new WeakMap<Document, { count: number; overflow: string }>();

export function lockBodyScroll(doc: Document = document): () => void {
  const current = locks.get(doc) ?? { count: 0, overflow: doc.body.style.overflow };
  if (current.count === 0) {
    current.overflow = doc.body.style.overflow;
    doc.body.style.overflow = 'hidden';
  }
  current.count += 1;
  locks.set(doc, current);
  let released = false;
  return () => {
    if (released) return;
    released = true;
    current.count -= 1;
    if (current.count === 0) doc.body.style.overflow = current.overflow;
  };
}
