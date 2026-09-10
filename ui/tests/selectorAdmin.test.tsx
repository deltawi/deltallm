import assert from 'node:assert/strict';
import test from 'node:test';
import { act, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { JSDOM } from 'jsdom';
import PolicySelectorEditor from '../src/components/route-groups/PolicySelectorEditor';
import PolicySelectorSummary from '../src/components/route-groups/PolicySelectorSummary';
import PolicyPublishControl from '../src/components/route-groups/PolicyPublishControl';
import SelectorCostPanel from '../src/components/route-groups/SelectorCostPanel';
import SelectorEvaluationPanel from '../src/components/route-groups/SelectorEvaluationPanel';
import RoutingCostSummary from '../src/components/route-groups/RoutingCostSummary';
import { buildPolicyFromGuided, toGuidedPolicy, validateGuidedPolicy } from '../src/lib/routeGroups';
import { routeGroups } from '../src/lib/api/routeGroups';
import { useExplicitReport } from '../src/lib/useExplicitReport';
import { useSelectorOptions } from '../src/lib/useSelectorOptions';
import type { SelectorOptionsPage, SelectorOptionsQuery } from '../src/lib/api/routeGroups';
import { emptyCosts, selectorPolicy } from './fixtures/selectorReports';
import { selectorReports, type RoutingCostPage, type RoutingCostRequest } from '../src/lib/api/selectorReports';

const selectorOptions = ['tiny', 'mini', 'large'].map((id) => ({
  deployment_id: id, model_name: id, provider: 'openai', mode: 'chat',
  eligible: true, unavailable_reason: null,
}));

test('independent selector lookup aborts stale pages and clears protected data across scopes', async () => {
  const previous = routeGroups.selectorOptions;
  const calls: Array<{ group: string; query: SelectorOptionsQuery; signal: AbortSignal; resolve: (page: SelectorOptionsPage) => void; reject: (error: Error) => void }> = [];
  routeGroups.selectorOptions = (group, query = {}, signal) => {
    assert.ok(signal);
    return new Promise((resolve, reject) => calls.push({ group, query, signal, resolve, reject }));
  };
  try {
    await withDom(async (root) => {
      let current: ReturnType<typeof useSelectorOptions> | null = null;
      function Probe({ scope, selected = 'tiny', allowed = true }: { scope: string; selected?: string; allowed?: boolean }) {
        const lookup = useSelectorOptions('group', scope, selected, allowed);
        useEffect(() => { current = lookup; }, [lookup]);
        return <p>{lookup.data?.selected?.deployment_id ?? 'empty'}</p>;
      }
      const page: SelectorOptionsPage = { data: selectorOptions, selected: selectorOptions[0], limit: 20, offset: 0, has_more: true };
      await act(async () => root.render(<Probe scope="principal-a/group" />));
      assert.equal(calls.length, 1);
      assert.equal(calls[0].query.selected_id, 'tiny');
      await act(async () => calls[0].resolve(page));
      assert.equal(document.querySelector('p')?.textContent, 'tiny');
      await act(async () => current!.next());
      assert.equal(calls[1].query.offset, 20);
      await act(async () => root.render(<Probe scope="principal-a/group" selected="large" />));
      assert.ok(calls[1].signal.aborted);
      await act(async () => calls[2].resolve({ ...page, selected: selectorOptions[2], offset: 20 }));
      await act(async () => calls[1].resolve(page));
      assert.equal(document.querySelector('p')?.textContent, 'large');
      await act(async () => { void current!.refresh(); });
      await act(async () => calls[3].reject(new Error('unavailable')));
      assert.ok(current!.error);
      assert.equal(document.querySelector('p')?.textContent, 'large');
      await act(async () => root.render(<Probe scope="principal-b/group" />));
      assert.equal(document.querySelector('p')?.textContent, 'empty');
      await act(async () => root.render(<Probe scope="principal-b/other-group" allowed={false} />));
      assert.ok(calls[4].signal.aborted);
      assert.equal(calls.length, 5);
      await act(async () => calls[4].resolve(page));
      assert.equal(document.querySelector('p')?.textContent, 'empty');
    });
  } finally { routeGroups.selectorOptions = previous; }
});

test('selector search debounces, resets pagination only for changed search and aborts the old page', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  const previous = routeGroups.selectorOptions;
  const calls: Array<{ query: SelectorOptionsQuery; signal: AbortSignal }> = [];
  routeGroups.selectorOptions = (_group, query = {}, signal) => {
    assert.ok(signal);
    calls.push({ query, signal });
    return Promise.resolve({ data: [], selected: null, limit: 20, offset: query.offset ?? 0, has_more: true });
  };
  try {
    await withDom(async (root) => {
      let current: ReturnType<typeof useSelectorOptions> | null = null;
      function Probe() {
        const lookup = useSelectorOptions('group', 'principal/group', 'tiny', true);
        useEffect(() => { current = lookup; }, [lookup]);
        return null;
      }
      await act(async () => root.render(<Probe />));
      await act(async () => current!.next());
      assert.equal(calls.at(-1)?.query.offset, 20);
      await act(async () => context.mock.timers.tick(250));
      assert.equal(calls.length, 2, 'initial debounce must not reset a selected page');
      await act(async () => current!.setSearch('tiny model'));
      await act(async () => context.mock.timers.tick(249));
      assert.equal(calls.length, 2);
      await act(async () => context.mock.timers.tick(1));
      assert.equal(calls.at(-1)?.query.search, 'tiny model');
      assert.equal(calls.at(-1)?.query.offset, 0);
      assert.equal(calls.at(-1)?.query.selected_id, 'tiny');
    });
  } finally { routeGroups.selectorOptions = previous; context.mock.timers.reset(); }
});

async function withDom(run: (root: ReturnType<typeof createRoot>, dom: JSDOM) => Promise<void>) {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>');
  const keys = ['window', 'document', 'HTMLElement', 'IS_REACT_ACT_ENVIRONMENT'] as const;
  const previous = keys.map((key) => Object.getOwnPropertyDescriptor(globalThis, key));
  Object.defineProperties(globalThis, {
    window: { configurable: true, value: dom.window },
    document: { configurable: true, value: dom.window.document },
    HTMLElement: { configurable: true, value: dom.window.HTMLElement },
    IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: true },
  });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'offsetParent', { configurable: true, get() { return this.parentNode; } });
  const root = createRoot(document.getElementById('root')!);
  try { await run(root, dom); } finally {
    await act(async () => root.unmount());
    dom.window.close();
    keys.forEach((key, i) => {
      if (previous[i]) Object.defineProperty(globalThis, key, previous[i]!);
      else Reflect.deleteProperty(globalThis, key);
    });
  }
}

interface CostCall {
  query: RoutingCostRequest;
  signal: AbortSignal;
  resolve: (page: RoutingCostPage) => void;
  reject: (error: Error) => void;
}

async function withCostCalls(run: (calls: CostCall[]) => Promise<void>) {
  const original = selectorReports.costs;
  const calls: CostCall[] = [];
  selectorReports.costs = (query, signal) => {
    assert.ok(signal, 'cost reads must forward cancellation');
    return new Promise((resolve, reject) => calls.push({ query, signal, resolve, reject }));
  };
  try { await run(calls); } finally { selectorReports.costs = original; }
}

function costPage(id: string): RoutingCostPage {
  return {
    generated_at: '2026-09-09T01:00:00Z', summary: emptyCosts, has_more: true,
    next_cursor: { created_at: '2026-09-09T00:00:00Z', operation_id: id },
  };
}

function costButton(label: 'Load latest costs' | 'Older operations'): HTMLButtonElement {
  const button = [...document.querySelectorAll('button')].find((item) => item.textContent === label);
  assert.ok(button);
  return button;
}

async function selectCostDays(dom: JSDOM, days: string) {
  await act(async () => {
    const select = document.querySelector('select')!;
    select.value = days;
    select.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  });
}

function assertCostContinuation(call: CostCall, first: CostCall, page: RoutingCostPage) {
  assert.deepEqual(call.query, {
    ...first.query, before_created_at: page.next_cursor!.created_at, before_operation_id: page.next_cursor!.operation_id,
  });
}

test('selector choice stays simple with safe defaults and keyboard-accessible optional limits', async () => {
  await withDom(async (root, dom) => {
    const members = ['mini', 'large'].map((id) => ({ deployment_id: id, mode: 'chat', enabled: true, weight: null, priority: null }));
    function Editor() {
      const [values, onChange] = useState(toGuidedPolicy({}, members));
      return <PolicySelectorEditor values={values} onChange={onChange} members={members} selectorOptions={selectorOptions} workloadMode="chat" />;
    }
    await act(async () => root.render(<Editor />));
    const select = document.querySelector('select')!;
    assert.equal(select.value, '');
    select.focus();
    assert.equal(document.activeElement, select);
    await act(async () => { select.value = 'tiny'; select.dispatchEvent(new dom.window.Event('change', { bubbles: true })); });
    assert.match(document.body.textContent!, /customers pay its actual cost/);
    assert.equal(document.querySelector('details')?.open, false);
    assert.equal(document.querySelector('[aria-label="Answer lane for tiny"]'), null);
    assert.equal((document.querySelector('[aria-label="Answer lane for mini"]') as HTMLSelectElement).value, '');
    assert.match(document.querySelector('[role="alert"]')!.textContent!, /Assign every/);
    for (const [id, lane] of [['mini', 'economy'], ['large', 'quality']]) {
      await act(async () => {
        const assignment = document.querySelector(`[aria-label="Answer lane for ${id}"]`) as HTMLSelectElement;
        assignment.value = lane;
        assignment.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
      });
    }
    assert.match(document.body.innerHTML, /md:flex-row/);
    assert.equal(document.querySelector('[role="alert"]'), null);
    await act(async () => { select.value = ''; select.dispatchEvent(new dom.window.Event('change', { bubbles: true })); });
    assert.equal(document.querySelector('[aria-label="Answer lane for mini"]'), null);
  });
});

for (const width of [375, 1024]) {
  test(`imported partial policy at ${width}px sends explicit removal after choosing None`, async () => {
    const previousFetch = globalThis.fetch;
    const submissions: Record<string, unknown>[] = [];
    globalThis.fetch = async (input, init) => {
      assert.equal(String(input), '/ui/api/route-groups/by-id/group-id/policy/publish');
      assert.equal(init?.method, 'POST');
      submissions.push(JSON.parse(String(init?.body)));
      return new Response('{}', { headers: { 'content-type': 'application/json' } });
    };
    try {
      await withDom(async (root, dom) => {
        Object.defineProperty(dom.window, 'innerWidth', { value: width });
        const imported: Record<string, unknown> = JSON.parse('{"strategy":"least-busy"}');
        const active = selectorPolicy();
        const members = ['mini', 'large'].map((deployment_id) => ({ deployment_id, enabled: true, mode: 'chat', weight: null, priority: null }));
        function Editor() {
          const [values, onChange] = useState(toGuidedPolicy(imported, members));
          const policy = buildPolicyFromGuided(imported, values);
          return <>
            <PolicySelectorEditor values={values} onChange={onChange} members={members} selectorOptions={selectorOptions} workloadMode="chat" />
            <PolicyPublishControl policy={policy} activePolicy={active} busy={false}
              disabled={validateGuidedPolicy(values, members, 'chat') !== null}
              onPublish={() => { void routeGroups.publishPolicy('group-id', policy); }} />
          </>;
        }
        await act(async () => root.render(<Editor />));
        const select = document.querySelector('select')!;
        for (const value of ['mini', '']) {
          await act(async () => { select.value = value; select.dispatchEvent(new dom.window.Event('change', { bubbles: true })); });
        }
        assert.equal(select.value, '');
        assert.equal(document.querySelector('[aria-label="Answer lane for mini"]'), null);
        assert.equal(document.querySelector('[role="alert"]'), null);
        assert.equal(submissions.length, 0);
        await act(async () => [...document.querySelectorAll('button')].find((button) => button.textContent === 'Publish')!.click());
        const dialog = document.querySelector('[role="dialog"]')!;
        assert.match(dialog.textContent!, /Disable the model selector/);
        assert.match(dialog.textContent!, /without classification/);
        await act(async () => [...dialog.querySelectorAll('button')].find((button) => button.textContent === 'Publish')!.click());
        assert.equal(submissions.length, 1);
        assert.equal(submissions[0].selector, null);
        assert.equal(submissions[0].strategy, 'least-busy');
        assert.ok((submissions[0].members as Array<Record<string, unknown>>).every((member) => !('lane' in member)));
      });
    } finally { globalThis.fetch = previousFetch; }
  });
}

test('publish uses one standard confirmation without requiring evaluation and restores keyboard focus', async () => {
  await withDom(async (root, dom) => {
    const policy = selectorPolicy();
    let published = 0;
    await act(async () => root.render(<PolicyPublishControl policy={policy} activePolicy={null} busy={false} disabled={false} onPublish={() => published++} />));
    const trigger = document.querySelector('button')!;
    trigger.focus();
    await act(async () => trigger.click());
    const dialog = document.querySelector('[role="dialog"]')!;
    assert.ok(dialog);
    assert.match(dialog.textContent!, /Evaluation is optional/);
    assert.equal(dialog.querySelectorAll('input').length, 0);
    const buttons = dialog.querySelectorAll('button');
    (buttons[buttons.length - 1] as HTMLButtonElement).focus();
    document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));
    assert.equal(document.activeElement, buttons[0]);
    await act(async () => document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    assert.equal(document.querySelector('[role="dialog"]'), null);
    assert.equal(document.activeElement, trigger);
    assert.equal(published, 0);
    await act(async () => trigger.click());
    const confirm = [...document.querySelectorAll('[role="dialog"] button')].find((button) => button.textContent === 'Publish') as HTMLButtonElement;
    await act(async () => confirm.click());
    assert.equal(published, 1);
  });
});

test('publishing partial JSON preserves the selector without promising to disable it', async () => {
  await withDom(async (root) => {
    const policy = Object.freeze({ strategy: 'least-busy' });
    const activePolicy = selectorPolicy();
    const submissions: Record<string, unknown>[] = [];
    await act(async () => root.render(<PolicyPublishControl policy={policy} activePolicy={activePolicy}
      busy={false} disabled={false} onPublish={() => submissions.push(policy)} />));
    await act(async () => document.querySelector('button')!.click());
    const dialog = document.querySelector('[role="dialog"]')!;
    assert.match(dialog.textContent!, /selector unchanged/);
    assert.match(dialog.textContent!, /customers continue to pay/);
    assert.doesNotMatch(dialog.textContent!, /Disable|removes the selector|without classification/);
    const confirm = [...dialog.querySelectorAll('button')].find((button) => button.textContent === 'Publish')!;
    await act(async () => confirm.click());
    assert.deepEqual(submissions, [{ strategy: 'least-busy' }]);
    assert.equal(document.querySelector('[role="dialog"]'), null);
  });
});

for (const changed of ['policy', 'activePolicy'] as const) {
  test(`publication invalidates confirmation when ${changed} changes`, async () => {
    await withDom(async (root) => {
      let policy: Record<string, unknown> = { strategy: 'least-busy' };
      let activePolicy = selectorPolicy();
      let submissions = 0;
      const render = () => root.render(<PolicyPublishControl policy={policy} activePolicy={activePolicy}
        busy={false} disabled={false} onPublish={() => submissions++} />);
      await act(async () => render());
      await act(async () => document.querySelector('button')!.click());
      assert.ok(document.querySelector('[role="dialog"]'));
      if (changed === 'policy') policy = { selector: null };
      else activePolicy = { ...activePolicy };
      await act(async () => render());
      assert.equal(document.querySelector('[role="dialog"]'), null);
      assert.equal(submissions, 0);
      await act(async () => document.querySelector('button')!.click());
      const dialog = document.querySelector('[role="dialog"]')!;
      assert.match(dialog.textContent!, changed === 'policy' ? /Disable the model selector/ : /selector unchanged/);
      await act(async () => [...dialog.querySelectorAll('button')].find((button) => button.textContent === 'Publish')!.click());
      assert.equal(submissions, 1);
    });
  });
}

test('publication keeps the ordinary path simple and locks while busy or disabled', async () => {
  await withDom(async (root) => {
    let submissions = 0;
    const policy = { strategy: 'least-busy' };
    const render = (busy: boolean, disabled: boolean) => root.render(<PolicyPublishControl policy={policy}
      activePolicy={null} busy={busy} disabled={disabled} onPublish={() => submissions++} />);
    for (const [busy, disabled] of [[true, false], [false, true]]) {
      await act(async () => render(busy, disabled));
      assert.equal(document.querySelector('button')!.disabled, true);
      await act(async () => document.querySelector('button')!.click());
    }
    assert.equal(submissions, 0);
    await act(async () => render(false, false));
    await act(async () => document.querySelector('button')!.click());
    assert.equal(submissions, 1);
    assert.equal(document.querySelector('[role="dialog"]'), null);
  });
});

test('cost pagination retains the successful window after a failed different-window refresh', async () => {
  const original = selectorReports.costs;
  const requests: Array<{
    query: RoutingCostRequest; resolve: (page: RoutingCostPage) => void; reject: (error: Error) => void;
  }> = [];
  selectorReports.costs = (query) => new Promise((resolve, reject) => requests.push({ query, resolve, reject }));
  try {
    await withDom(async (root, dom) => {
      await act(async () => root.render(<SelectorCostPanel scope="a" modelGroup="group" allowed />));
      await act(async () => document.querySelector('button')!.click());
      const cursor = { created_at: '2026-09-09T00:00:00Z', operation_id: 'first-page-tail' };
      await act(async () => requests[0].resolve({
        generated_at: '2026-09-09T01:00:00Z', summary: emptyCosts, has_more: true, next_cursor: cursor,
      }));
      const select = document.querySelector('select')!;
      await act(async () => { select.value = '7'; select.dispatchEvent(new dom.window.Event('change', { bubbles: true })); });
      await act(async () => document.querySelector('button')!.click());
      await act(async () => requests[1].reject(new Error('report unavailable')));
      await act(async () => { select.value = '1'; select.dispatchEvent(new dom.window.Event('change', { bubbles: true })); });
      const older = [...document.querySelectorAll('button')].find((button) => button.textContent === 'Older operations')!;
      assert.equal(older.disabled, false);
      await act(async () => older.click());
      assert.deepEqual(requests[2].query, {
        ...requests[0].query, before_created_at: cursor.created_at, before_operation_id: cursor.operation_id,
      });
      assert.notEqual(requests[2].query.start, requests[1].query.start);
    });
  } finally { selectorReports.costs = original; }
});

test('cost retries retain exact timestamps and cursors until a successful refresh replaces them', async (t) => {
  t.mock.timers.enable({ apis: ['Date'], now: new Date('2026-09-09T02:00:00Z') });
  await withCostCalls(async (calls) => withDom(async (root) => {
    await act(async () => root.render(<SelectorCostPanel scope="a" modelGroup="group" allowed />));
    await act(async () => costButton('Load latest costs').click());
    const first = costPage('first-tail');
    await act(async () => calls[0].resolve(first));
    t.mock.timers.setTime(new Date('2026-09-09T03:00:00Z').getTime());
    await act(async () => costButton('Load latest costs').click());
    assert.notEqual(calls[1].query.end, calls[0].query.end);
    await act(async () => calls[1].reject(new Error('refresh unavailable')));
    assert.match(document.body.textContent!, /refresh unavailable/);
    await act(async () => costButton('Older operations').click());
    assertCostContinuation(calls[2], calls[0], first);
    await act(async () => calls[2].reject(new Error('pagination unavailable')));
    assert.match(document.body.textContent!, /pagination unavailable/);
    assert.equal(costButton('Older operations').disabled, false);
    await act(async () => costButton('Older operations').click());
    assert.deepEqual(calls[3].query, calls[2].query);
    await act(async () => calls[3].resolve(costPage('second-tail')));
    t.mock.timers.setTime(new Date('2026-09-09T04:00:00Z').getTime());
    await act(async () => costButton('Load latest costs').click());
    assert.equal(calls[4].query.before_operation_id, undefined);
    assert.equal(calls[4].query.end, '2026-09-09T04:00:00.000Z');
    const fresh = costPage('fresh-tail');
    await act(async () => calls[4].resolve(fresh));
    await act(async () => costButton('Older operations').click());
    assertCostContinuation(calls[5], calls[4], fresh);
    assert.equal(calls.length, 6, 'only explicit button actions make requests');
  }));
});

test('aborted cost responses cannot replace accepted windows or expose another group or principal', async (t) => {
  t.mock.timers.enable({ apis: ['Date'], now: new Date('2026-09-09T02:00:00Z') });
  await withCostCalls(async (calls) => {
    await withDom(async (root, dom) => {
      await act(async () => root.render(<SelectorCostPanel scope="a" modelGroup="group" allowed />));
      await act(async () => costButton('Load latest costs').click());
      await act(async () => calls[0].resolve(costPage('original-tail')));
      await selectCostDays(dom, '7');
      assert.equal(costButton('Older operations').disabled, true);
      await act(async () => costButton('Load latest costs').click());
      await selectCostDays(dom, '1');
      assert.equal(calls[1].signal.aborted, true);
      t.mock.timers.setTime(new Date('2026-09-09T03:00:00Z').getTime());
      await act(async () => costButton('Load latest costs').click());
      const fresh = costPage('fresh-tail');
      await act(async () => calls[2].resolve(fresh));
      await act(async () => calls[1].resolve(costPage('stale-tail')));
      await act(async () => costButton('Older operations').click());
      assertCostContinuation(calls[3], calls[2], fresh);
      await act(async () => root.render(<SelectorCostPanel key="other-group" scope="a-other-group" modelGroup="other" allowed />));
      assert.equal(calls[3].signal.aborted, true);
      await act(async () => calls[3].resolve(costPage('old-group-tail')));
      assert.doesNotMatch(document.body.textContent!, /generated/);
      await act(async () => costButton('Load latest costs').click());
      assert.equal(calls[4].query.model_group, 'other');
      assert.equal(calls[4].query.before_operation_id, undefined);
      await act(async () => root.render(<SelectorCostPanel key="new-principal" scope="b" modelGroup="other" allowed={false} />));
      assert.equal(calls[4].signal.aborted, true);
      await act(async () => calls[4].resolve(costPage('private-tail')));
      assert.doesNotMatch(document.body.textContent!, /generated/);
      assert.equal(costButton('Load latest costs').disabled, true);
      await act(async () => root.render(<SelectorCostPanel key="authorized" scope="c" modelGroup="other" allowed />));
      await act(async () => costButton('Load latest costs').click());
    });
    assert.equal(calls[5].signal.aborted, true, 'unmount aborts cost reads');
    await act(async () => calls[5].resolve(costPage('unmounted-tail')));
  });
});

test('history and cost summaries preserve unknown and tiny exact costs without claiming savings', () => {
  const history = renderToStaticMarkup(<PolicySelectorSummary policy={selectorPolicy()} />);
  assert.match(history, /mini/); assert.match(history, /economy/); assert.match(history, /quality/);
  const removed = renderToStaticMarkup(<PolicySelectorSummary policy={{ selector: null }} />);
  assert.match(removed, /none — routing strategy only/);
  assert.doesNotMatch(removed, /paid call|safe default/);
  const cost = renderToStaticMarkup(<RoutingCostSummary summary={{
    ...emptyCosts, operation_count: 1, pending_reconciliation_count: 1, partial: true,
    selector_provider_cost_count: 1, selector_provider_cost_exact: '0.000000000000000001',
  }} />);
  assert.match(cost, /0\.000000000000000001/);
  assert.match(cost, /Unavailable/);
  assert.match(cost, /not zero/);
});

test('report tools start collapsed, make no implicit requests, and show permission states', () => {
  const costs = renderToStaticMarkup(<SelectorCostPanel scope="a" modelGroup="group" allowed={false} />);
  const evaluation = renderToStaticMarkup(<SelectorEvaluationPanel scope="a" policy={selectorPolicy()} allowed={false} />);
  assert.doesNotMatch(costs + evaluation, /<details[^>]+open/);
  assert.match(costs, /not available for your account/);
  assert.match(evaluation, /do not have permission/);
  assert.match(evaluation, /does not call models/);
});

test('explicit reports abort stale work, retain same-scope failures, and never display another principal result', async () => {
  await withDom(async (root) => {
    const requests: Array<{ signal: AbortSignal; resolve: (value: string) => void; reject: (error: Error) => void }> = [];
    const loader = (_: null, signal: AbortSignal) => new Promise<string>((resolve, reject) => requests.push({ signal, resolve, reject }));
    function Probe({ scope, fingerprint }: { scope: string; fingerprint: string }) {
      const report = useExplicitReport(scope, fingerprint, loader);
      return <><button disabled={report.loading} onClick={() => void report.run(null)}>run</button><span id="value">{report.data ?? 'empty'}</span><span id="stale">{String(report.stale)}</span><span id="error">{report.error instanceof Error ? report.error.message : ''}</span></>;
    }
    await act(async () => root.render(<Probe scope="principal-a" fingerprint="first" />));
    assert.equal(requests.length, 0);
    await act(async () => document.querySelector('button')!.click());
    await act(async () => requests[0].resolve('first result'));
    await act(async () => root.render(<Probe scope="principal-a" fingerprint="changed" />));
    assert.equal(document.getElementById('stale')!.textContent, 'true');
    await act(async () => document.querySelector('button')!.click());
    await act(async () => requests[1].reject(new Error('report unavailable')));
    assert.equal(document.getElementById('value')!.textContent, 'first result');
    assert.equal(document.getElementById('error')!.textContent, 'report unavailable');
    await act(async () => document.querySelector('button')!.click());
    await act(async () => root.render(<Probe scope="principal-a" fingerprint="temporary" />));
    await act(async () => root.render(<Probe scope="principal-a" fingerprint="changed" />));
    assert.equal(document.querySelector('button')!.disabled, false, 'returning to an aborted input must not stay busy');
    await act(async () => root.render(<Probe scope="principal-b" fingerprint="changed" />));
    assert.equal(requests[2].signal.aborted, true);
    await act(async () => requests[2].resolve('private stale result'));
    assert.equal(document.getElementById('value')!.textContent, 'empty');
    assert.equal(document.getElementById('error')!.textContent, '');
  });
});

for (const width of [375, 1024]) {
  test(`cost controls at ${width}px handle explicit loading, empty, stale and denied states`, async () => {
    const original = selectorReports.costs;
    const requests: Array<{ resolve: (page: RoutingCostPage) => void; reject: (error: Error) => void }> = [];
    selectorReports.costs = () => new Promise((resolve, reject) => requests.push({ resolve, reject }));
    try {
      await withDom(async (root, dom) => {
        Object.defineProperty(dom.window, 'innerWidth', { value: width });
        await act(async () => root.render(<SelectorCostPanel scope="a" modelGroup="group" allowed />));
        assert.equal(requests.length, 0);
        await act(async () => document.querySelector('button')!.click());
        assert.match(document.body.textContent!, /Loading costs/);
        assert.equal(document.querySelector('button')!.disabled, true);
        await act(async () => requests[0].resolve({
          generated_at: '2026-09-01T00:00:00Z', summary: emptyCosts, has_more: false, next_cursor: null,
        }));
        assert.match(document.body.textContent!, /No selector operations/);
        await act(async () => {
          const select = document.querySelector('select')!;
          select.value = '7'; select.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
        });
        assert.match(document.body.textContent!, /Window changed/);
        await act(async () => document.querySelector('button')!.click());
        await act(async () => requests[1].reject(new Error('Permission denied')));
        assert.match(document.body.textContent!, /Permission denied/);
        assert.match(document.body.textContent!, /No selector operations/);
        await act(async () => root.render(<SelectorCostPanel key="b" scope="b" modelGroup="group" allowed={false} />));
        assert.match(document.body.textContent!, /not available for your account/);
        assert.doesNotMatch(document.body.textContent!, /generated/);
      });
    } finally { selectorReports.costs = original; }
  });
}
