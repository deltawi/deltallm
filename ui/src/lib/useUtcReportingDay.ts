import { useEffect, useState } from 'react';

import { millisecondsUntilNextUtcDay, todayIsoDate } from './reportingRange';

export function useUtcReportingDay(): string {
  const [utcDay, setUtcDay] = useState(() => todayIsoDate());

  useEffect(() => {
    let timer: number | null = null;

    const scheduleRollover = () => {
      if (timer !== null) window.clearTimeout(timer);
      timer = window.setTimeout(handleRollover, millisecondsUntilNextUtcDay());
    };

    const refreshDay = () => {
      setUtcDay(todayIsoDate());
      scheduleRollover();
    };

    function handleRollover() {
      refreshDay();
    }

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') refreshDay();
    };

    scheduleRollover();
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      if (timer !== null) window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, []);

  return utcDay;
}
