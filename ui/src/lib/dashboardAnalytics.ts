export type DashboardReportStatus = 'idle' | 'loading' | 'success' | 'error';

export interface DashboardReportState<T> {
  generation: string | null;
  status: DashboardReportStatus;
  data: T | null;
  error: string | null;
}

interface DashboardReportingRange {
  startDate?: string;
  endDate?: string;
  bucket: string;
}

export function initialDashboardReportState<T>(): DashboardReportState<T> {
  return {
    generation: null,
    status: 'idle',
    data: null,
    error: null,
  };
}

export function beginDashboardReport<T>(
  state: DashboardReportState<T>,
  generation: string,
): DashboardReportState<T> {
  return {
    ...state,
    generation,
    status: 'loading',
    error: null,
  };
}

export function completeDashboardReport<T>(
  state: DashboardReportState<T>,
  generation: string,
  data: T,
): DashboardReportState<T> {
  if (state.generation !== generation) return state;
  return {
    generation,
    status: 'success',
    data,
    error: null,
  };
}

export function failDashboardReport<T>(
  state: DashboardReportState<T>,
  generation: string,
  error: string,
): DashboardReportState<T> {
  if (state.generation !== generation) return state;
  return {
    ...state,
    status: 'error',
    error,
  };
}

export function dashboardReportPending<T>(
  state: DashboardReportState<T>,
  generation: string,
): boolean {
  return state.generation !== generation || state.status === 'loading';
}

export function dashboardReportError<T>(
  state: DashboardReportState<T>,
  generation: string,
): string | null {
  return state.generation === generation && state.status === 'error'
    ? state.error
    : null;
}

export function dashboardReportingRangesMatch(
  left: DashboardReportingRange | null | undefined,
  right: DashboardReportingRange | null | undefined,
): boolean {
  return Boolean(
    left
    && right
    && left.startDate === right.startDate
    && left.endDate === right.endDate
    && left.bucket === right.bucket,
  );
}
