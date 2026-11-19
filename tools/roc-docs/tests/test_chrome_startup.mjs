import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { waitForDevtools } from './chrome_startup.mjs';

function child() {
  return Object.assign(new EventEmitter(), { stderr: new PassThrough() });
}

test('waits for a full line across every endpoint chunk boundary', async () => {
  const line = 'DevTools listening on ws://127.0.0.1:12345/devtools/browser/session\r\n';
  for (let split = 1; split < line.length; split++) {
    const process = child();
    let settled = false;
    const endpoint = waitForDevtools(process, 1000);
    endpoint.then(() => { settled = true; });
    process.stderr.write(line.slice(0, split));
    await Promise.resolve();
    assert.equal(settled, false, `resolved at split ${split}`);
    process.stderr.write(line.slice(split));
    assert.equal((await endpoint).port, '12345');
    process.stderr.write('x'.repeat(100000));
    assert.equal(process.stderr.readableLength, 0, 'stderr must remain drained');
  }
});

test('reports spawn errors, early exits and signals with diagnostics', async () => {
  for (const [event, args, reason] of [
    ['error', [new Error('ENOENT')], /ENOENT/],
    ['exit', [3, null], /code 3/],
    ['exit', [null, 'SIGTERM'], /SIGTERM/],
  ]) {
    const process = child();
    const endpoint = waitForDevtools(process, 1000);
    process.stderr.write('startup diagnostic\n');
    process.emit(event, ...args);
    await assert.rejects(endpoint, error => reason.test(error.message) && error.message.includes('startup diagnostic'));
  }
});

test('times out with bounded diagnostic output', async () => {
  const process = child();
  const endpoint = waitForDevtools(process, 5);
  process.stderr.write(('diagnostic\n').repeat(10000));
  await assert.rejects(endpoint, error => /no endpoint/.test(error.message) && error.message.length < 8500);
});

test('rejects malformed endpoints without throwing from the stream callback', async () => {
  const process = child();
  const endpoint = waitForDevtools(process, 1000);
  process.stderr.write('DevTools listening on ws://broken:port/session\n');
  await assert.rejects(endpoint, /invalid endpoint/);
});
