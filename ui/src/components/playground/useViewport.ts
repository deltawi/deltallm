import { useEffect, useState } from 'react';

const MD_QUERY = '(min-width: 768px)';

export function useIsMd(): boolean {
  const [isMd, setIsMd] = useState<boolean>(() => {
    if (typeof window === 'undefined') return true;
    return window.matchMedia(MD_QUERY).matches;
  });
  useEffect(() => {
    const mq = window.matchMedia(MD_QUERY);
    const onChange = (e: MediaQueryListEvent) => setIsMd(e.matches);
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else mq.addListener(onChange);
    return () => {
      if (mq.removeEventListener) mq.removeEventListener('change', onChange);
      else mq.removeListener(onChange);
    };
  }, []);
  return isMd;
}
