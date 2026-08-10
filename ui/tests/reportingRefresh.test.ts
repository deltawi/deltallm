import assert from 'node:assert/strict';
import test from 'node:test';

import {
  beginTrackedReportingAttempt,
  beginReportingRefresh,
  beginReportingPartAttempt,
  createReportingAttemptTracker,
  recordReportingPartOutcome,
  reportingBreakdownReady,
  reportingCoreSettled,
  reportingRefreshStatus,
  settleTrackedReportingAttempt,
} from '../src/lib/reportingRefresh';

test('overview refresh succeeds only after every panel succeeds', () => {
  let state = beginReportingRefresh('range:overview:1', 'overview');
  assert.equal(reportingRefreshStatus(state), 'pending');

  state = recordReportingPartOutcome(state, {
    generation: state.generation,
    part: 'summary',
    status: 'success',
  });
  state = recordReportingPartOutcome(state, {
    generation: state.generation,
    part: 'trend',
    status: 'success',
  });
  assert.equal(reportingCoreSettled(state), true);
  assert.equal(reportingRefreshStatus(state), 'pending');

  state = recordReportingPartOutcome(state, {
    generation: state.generation,
    part: 'breakdown',
    status: 'success',
  });
  assert.equal(reportingRefreshStatus(state), 'success');
});

test('any error or skipped prerequisite makes the completed refresh unsuccessful', () => {
  let failed = beginReportingRefresh('range:overview:2', 'overview');
  failed = recordReportingPartOutcome(failed, {
    generation: failed.generation,
    part: 'summary',
    status: 'success',
  });
  failed = recordReportingPartOutcome(failed, {
    generation: failed.generation,
    part: 'trend',
    status: 'error',
  });
  failed = recordReportingPartOutcome(failed, {
    generation: failed.generation,
    part: 'breakdown',
    status: 'success',
  });
  assert.equal(reportingRefreshStatus(failed), 'error');

  let skipped = beginReportingRefresh('range:overview:3', 'overview');
  skipped = recordReportingPartOutcome(skipped, {
    generation: skipped.generation,
    part: 'summary',
    status: 'error',
  });
  skipped = recordReportingPartOutcome(skipped, {
    generation: skipped.generation,
    part: 'trend',
    status: 'success',
  });
  skipped = recordReportingPartOutcome(skipped, {
    generation: skipped.generation,
    part: 'breakdown',
    status: 'skipped',
  });
  assert.equal(reportingRefreshStatus(skipped), 'error');
});

test('stale, unrelated, and duplicate outcomes are ignored', () => {
  const current = beginReportingRefresh('range:logs:4', 'logs');
  const stale = recordReportingPartOutcome(current, {
    generation: 'range:overview:3',
    part: 'summary',
    status: 'success',
  });
  const unrelated = recordReportingPartOutcome(current, {
    generation: current.generation,
    part: 'trend',
    status: 'error',
  });
  assert.equal(stale, current);
  assert.equal(unrelated, current);

  const settled = recordReportingPartOutcome(current, {
    generation: current.generation,
    part: 'summary',
    status: 'success',
  });
  const duplicate = recordReportingPartOutcome(settled, {
    generation: current.generation,
    part: 'summary',
    status: 'error',
  });
  assert.equal(duplicate, settled);
});

test('logs generations require summary and logs only', () => {
  let state = beginReportingRefresh('range:logs:5', 'logs');
  assert.equal(reportingCoreSettled(state), false);
  state = recordReportingPartOutcome(state, {
    generation: state.generation,
    part: 'summary',
    status: 'success',
  });
  assert.equal(reportingRefreshStatus(state), 'pending');
  state = recordReportingPartOutcome(state, {
    generation: state.generation,
    part: 'logs',
    status: 'success',
  });
  assert.equal(reportingRefreshStatus(state), 'success');
});

test('a settled failed range transition cannot start a mismatched breakdown', () => {
  let state = beginReportingRefresh('new-range:overview:6', 'overview');
  state = recordReportingPartOutcome(state, {
    generation: state.generation,
    part: 'summary',
    status: 'error',
  });
  state = recordReportingPartOutcome(state, {
    generation: state.generation,
    part: 'trend',
    status: 'success',
  });

  assert.equal(reportingCoreSettled(state), true);
  assert.equal(reportingBreakdownReady(state, false), false);
  assert.equal(reportingBreakdownReady(state, true), true);
});

test('a failed part can be reopened and recovered by a newer attempt', () => {
  let state = beginReportingRefresh('range:overview:7', 'overview');
  state = recordReportingPartOutcome(state, {
    generation: state.generation,
    part: 'summary',
    status: 'success',
  });
  state = recordReportingPartOutcome(state, {
    generation: state.generation,
    part: 'trend',
    status: 'success',
  });
  state = recordReportingPartOutcome(state, {
    generation: state.generation,
    part: 'breakdown',
    attempt: 0,
    status: 'error',
  });
  assert.equal(reportingRefreshStatus(state), 'error');

  state = beginReportingPartAttempt(state, {
    generation: state.generation,
    part: 'breakdown',
    attempt: 1,
  });
  assert.equal(reportingRefreshStatus(state), 'pending');

  state = recordReportingPartOutcome(state, {
    generation: state.generation,
    part: 'breakdown',
    attempt: 1,
    status: 'success',
  });
  assert.equal(reportingRefreshStatus(state), 'success');
});

test('stale attempts cannot overwrite a retried reporting part', () => {
  const initial = beginReportingRefresh('range:overview:8', 'overview');
  const retrying = beginReportingPartAttempt(initial, {
    generation: initial.generation,
    part: 'breakdown',
    attempt: 1,
  });
  const stale = recordReportingPartOutcome(retrying, {
    generation: initial.generation,
    part: 'breakdown',
    attempt: 0,
    status: 'error',
  });
  assert.equal(stale, retrying);

  const settled = recordReportingPartOutcome(retrying, {
    generation: initial.generation,
    part: 'breakdown',
    attempt: 1,
    status: 'success',
  });
  assert.equal(settled.parts.breakdown, 'success');
});

test('tracked requests reopen a settled breakdown and reject stale outcomes', () => {
  const initial = beginTrackedReportingAttempt(
    createReportingAttemptTracker('range:overview:9'),
    'range:overview:9',
  );
  assert.equal(initial.notifyParent, false);
  assert.equal(initial.token.attempt, 0);

  const firstSettlement = settleTrackedReportingAttempt(initial.tracker, initial.token);
  assert.equal(firstSettlement.accepted, true);

  const replacement = beginTrackedReportingAttempt(
    firstSettlement.tracker,
    'range:overview:9',
  );
  assert.equal(replacement.notifyParent, true);
  assert.equal(replacement.token.attempt, 1);
  assert.equal(
    settleTrackedReportingAttempt(replacement.tracker, initial.token).accepted,
    false,
  );
  assert.equal(
    settleTrackedReportingAttempt(replacement.tracker, replacement.token).accepted,
    true,
  );
});

test('tracked requests reset to attempt zero for a new reporting generation', () => {
  const oldAttempt = beginTrackedReportingAttempt(
    createReportingAttemptTracker('old'),
    'old',
  );
  const nextAttempt = beginTrackedReportingAttempt(oldAttempt.tracker, 'new');

  assert.equal(nextAttempt.notifyParent, false);
  assert.deepEqual(nextAttempt.token, { generation: 'new', attempt: 0, key: 'new:0' });
});
