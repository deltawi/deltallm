import assert from 'node:assert/strict';
import test from 'node:test';

import { createReportCache, createReportQueue, isAbortError, reportCacheFresh } from '../src/lib/reportLoader';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

test('report queue runs at most the configured number of loads at once', async () => {
  const queue = createReportQueue(2);
  const gates = [deferred<string>(), deferred<string>(), deferred<string>()];
  const started: number[] = [];
  const results = gates.map((gate, index) => queue.run(() => {
    started.push(index);
    return gate.promise;
  }));

  assert.deepEqual(started, [0, 1]);
  assert.equal(queue.active, 2);
  assert.equal(queue.waiting, 1);

  gates[0].resolve('a');
  await results[0];
  await Promise.resolve();
  assert.deepEqual(started, [0, 1, 2]);

  gates[1].resolve('b');
  gates[2].resolve('c');
  assert.deepEqual(await Promise.all(results), ['a', 'b', 'c']);
  assert.equal(queue.active, 0);
});

test('a waiting load that is cancelled never starts', async () => {
  const queue = createReportQueue(1);
  const gate = deferred<string>();
  const first = queue.run(() => gate.promise);
  const controller = new AbortController();
  let secondStarted = false;
  const second = queue.run(async () => {
    secondStarted = true;
    return 'late';
  }, controller.signal);

  controller.abort();
  await assert.rejects(second, (error) => isAbortError(error));
  gate.resolve('done');
  assert.equal(await first, 'done');
  assert.equal(secondStarted, false);
  assert.equal(queue.waiting, 0);
});

test('a load that fails frees its slot for the next one', async () => {
  const queue = createReportQueue(1);
  const failed = queue.run(() => Promise.reject(new Error('busy')));
  const next = queue.run(() => Promise.resolve('ok'));
  await assert.rejects(failed, /busy/);
  assert.equal(await next, 'ok');
});

test('report cache keeps the most recently used entries', () => {
  const cache = createReportCache(2);
  cache.set('a', 1, 0);
  cache.set('b', 2, 0);
  assert.equal(cache.get<number>('a')?.value, 1);
  cache.set('c', 3, 0);
  assert.equal(cache.get('b'), undefined);
  assert.equal(cache.get<number>('a')?.value, 1);
  assert.equal(cache.get<number>('c')?.value, 3);
  assert.equal(cache.size, 2);
});

test('cached reports are fresh only inside their maximum age', () => {
  assert.equal(reportCacheFresh(undefined, 60_000, 0), false);
  assert.equal(reportCacheFresh({ value: 1, storedAt: 1_000 }, 60_000, 60_999), true);
  assert.equal(reportCacheFresh({ value: 1, storedAt: 1_000 }, 60_000, 61_000), false);
});
