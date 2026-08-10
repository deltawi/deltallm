export type ReportingRefreshTab = 'overview' | 'logs';
export type ReportingRefreshPart = 'summary' | 'trend' | 'breakdown' | 'logs';
export type ReportingPartStatus = 'pending' | 'success' | 'error' | 'skipped';
export type ReportingRefreshStatus = 'pending' | 'success' | 'error';

export interface ReportingPartOutcome {
  generation: string;
  part: ReportingRefreshPart;
  attempt?: number;
  status: Exclude<ReportingPartStatus, 'pending'>;
}

export interface ReportingPartAttempt {
  generation: string;
  part: ReportingRefreshPart;
  attempt: number;
}

export interface ReportingRefreshOutcomeState {
  generation: string;
  parts: Partial<Record<ReportingRefreshPart, ReportingPartStatus>>;
  attempts: Partial<Record<ReportingRefreshPart, number>>;
}

export interface ReportingAttemptToken {
  generation: string;
  attempt: number;
  key: string;
}

export interface ReportingAttemptTracker {
  generation: string;
  attempt: number;
  started: boolean;
  settled: boolean;
}

export interface BegunReportingAttempt {
  tracker: ReportingAttemptTracker;
  token: ReportingAttemptToken;
  notifyParent: boolean;
}

export function createReportingAttemptTracker(generation: string): ReportingAttemptTracker {
  return { generation, attempt: 0, started: false, settled: false };
}

export function beginTrackedReportingAttempt(
  current: ReportingAttemptTracker,
  generation: string,
): BegunReportingAttempt {
  if (current.generation !== generation || !current.started) {
    const tracker = { generation, attempt: 0, started: true, settled: false };
    return {
      tracker,
      token: { generation, attempt: 0, key: `${generation}:0` },
      notifyParent: false,
    };
  }

  const tracker = {
    generation,
    attempt: current.attempt + 1,
    started: true,
    settled: false,
  };
  return {
    tracker,
    token: {
      generation,
      attempt: tracker.attempt,
      key: `${generation}:${tracker.attempt}`,
    },
    notifyParent: true,
  };
}

export function settleTrackedReportingAttempt(
  current: ReportingAttemptTracker,
  token: ReportingAttemptToken,
): { tracker: ReportingAttemptTracker; accepted: boolean } {
  if (
    current.generation !== token.generation
    || current.attempt !== token.attempt
    || current.settled
  ) {
    return { tracker: current, accepted: false };
  }
  return { tracker: { ...current, settled: true }, accepted: true };
}

export function reportingRefreshParts(tab: ReportingRefreshTab): ReportingRefreshPart[] {
  return tab === 'overview'
    ? ['summary', 'trend', 'breakdown']
    : ['summary', 'logs'];
}

export function beginReportingRefresh(
  generation: string,
  tab: ReportingRefreshTab,
): ReportingRefreshOutcomeState {
  return {
    generation,
    parts: Object.fromEntries(
      reportingRefreshParts(tab).map((part) => [part, 'pending']),
    ),
    attempts: Object.fromEntries(
      reportingRefreshParts(tab).map((part) => [part, 0]),
    ),
  };
}

export function beginReportingPartAttempt(
  state: ReportingRefreshOutcomeState,
  next: ReportingPartAttempt,
): ReportingRefreshOutcomeState {
  const currentAttempt = state.attempts[next.part];
  if (
    next.generation !== state.generation
    || currentAttempt === undefined
    || next.attempt <= currentAttempt
  ) {
    return state;
  }

  return {
    ...state,
    parts: {
      ...state.parts,
      [next.part]: 'pending',
    },
    attempts: {
      ...state.attempts,
      [next.part]: next.attempt,
    },
  };
}

export function recordReportingPartOutcome(
  state: ReportingRefreshOutcomeState,
  outcome: ReportingPartOutcome,
): ReportingRefreshOutcomeState {
  if (
    outcome.generation !== state.generation
    || state.parts[outcome.part] === undefined
    || (outcome.attempt ?? 0) !== state.attempts[outcome.part]
    || state.parts[outcome.part] !== 'pending'
  ) {
    return state;
  }

  return {
    ...state,
    parts: {
      ...state.parts,
      [outcome.part]: outcome.status,
    },
  };
}

export function reportingRefreshStatus(
  state: ReportingRefreshOutcomeState,
): ReportingRefreshStatus {
  const statuses = Object.values(state.parts);
  if (statuses.some((status) => status === 'pending')) return 'pending';
  if (statuses.some((status) => status === 'error' || status === 'skipped')) return 'error';
  return 'success';
}

export function reportingCoreSettled(state: ReportingRefreshOutcomeState): boolean {
  return state.parts.summary !== undefined
    && state.parts.trend !== undefined
    && state.parts.summary !== 'pending'
    && state.parts.trend !== 'pending';
}

export function reportingBreakdownReady(
  state: ReportingRefreshOutcomeState,
  displayedRangeMatchesSelection: boolean,
): boolean {
  return displayedRangeMatchesSelection && reportingCoreSettled(state);
}
