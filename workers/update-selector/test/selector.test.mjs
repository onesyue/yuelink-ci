import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { after, before, beforeEach, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { Headers as MiniflareHeaders, Miniflare } from 'miniflare';
import { bucketForIdentity } from '../src/index.mjs';

const hash = bytes => createHash('sha256').update(bytes).digest('hex');
// Deliberately synthetic transport bytes: signature/schema admission is tested
// with real signed historical fixtures in the Python admission suite.
const baseline = Buffer.from('{"fixture":"synthetic baseline transport bytes"}\n');
const candidate = Buffer.from('{"fixture":"synthetic candidate transport bytes"}\n');
const planKey = 'stable/network-ip.json';
const expiry = '2099-01-01T00:00:00Z';
const salt = 'a'.repeat(64);
const descriptor = (version, bytes) => ({ version, key: `v${version}/update.json`, sha256: hash(bytes), expiresAt: expiry });
const plan = percent => ({ schemaVersion: 1, mode: 'network-ip', percent, salt,
  baseline: descriptor('1.3.172', baseline), candidate: descriptor('1.3.173', candidate) });
const options = cf => ({
  modules: true, scriptPath: fileURLToPath(new URL('../src/index.mjs', import.meta.url)),
  compatibilityDate: '2026-07-30', cf,
  r2Buckets: ['ARCHIVES', 'PLANS'],
  bindings: { ALLOWED_HOSTS: 'updates.test,mirror.test', LEGACY_ROOT_SHA256: hash(baseline), LEGACY_ROOT_EXPIRES_AT: expiry },
});
let runtime, archives, plans;
async function seed(mf) {
  const a = await mf.getR2Bucket('ARCHIVES'); const p = await mf.getR2Bucket('PLANS');
  await a.put('update.json', baseline);
  await a.put('v1.3.172/update.json', baseline);
  await a.put('v1.3.173/update.json', candidate);
  await p.put(planKey, JSON.stringify(plan(100)));
  return [a, p];
}
before(async () => {
  runtime = new Miniflare(options({ colo: 'SIN', asn: 13335 }));
  [archives, plans] = await seed(runtime);
});
beforeEach(async () => { await seed(runtime); });
after(async () => { await runtime?.dispose(); });
const fetchUpdate = (headers = {}, url = 'https://updates.test/update.json') => runtime.dispatchFetch(url, {
  headers: { 'CF-Connecting-IP': '192.0.2.10', ...headers },
});
const body = async response => Buffer.from(await response.arrayBuffer());
function noStore(response) {
  for (const key of ['Cache-Control', 'CDN-Cache-Control', 'Cloudflare-CDN-Cache-Control']) {
    assert.match(response.headers.get(key), /no-store/);
  }
}

test('native Worker serves unchanged bytes at 0 and 100 and every mirror', async () => {
  for (const percent of [0, 100]) {
    await plans.put(planKey, JSON.stringify(plan(percent)));
    for (const host of ['updates.test', 'mirror.test']) {
      const response = await fetchUpdate({}, `https://${host}/update.json`);
      assert.equal(response.status, 200); noStore(response);
      assert.deepEqual(await body(response), percent ? candidate : baseline);
    }
  }
});

test('same trusted network identity is stable, query and forwarded identity cannot override it', async () => {
  await plans.put(planKey, JSON.stringify(plan(10)));
  const first = await body(await fetchUpdate());
  const second = await body(await fetchUpdate({ 'X-Forwarded-For': '198.51.100.1', 'Cookie': 'cohort=candidate' },
    'https://updates.test/update.json?percent=100&identity=other'));
  assert.deepEqual(first, second);
  assert.equal(await bucketForIdentity(salt, '192.0.2.10'), await bucketForIdentity(salt, '192.0.2.10'));
});

test('ten thousand synthetic network identities approximate ten percent, not installation coverage', async context => {
  let selected = 0;
  for (let index = 0; index < 10000; index++) {
    const identity = `198.18.${Math.floor(index / 256)}.${index % 256}`;
    if (await bucketForIdentity(salt, identity) < 1000) selected++;
  }
  assert.ok(selected >= 850 && selected <= 1150, 'synthetic network bucket distribution must be near ten percent');
  context.diagnostic(`${selected}/10000 synthetic network identities selected; not installation coverage`);
});

test('missing or invalid trusted identity falls back even at 100 percent', async () => {
  for (const value of ['not-an-address', '256.0.0.1', '192.0.2.1,198.51.100.1']) {
    const response = await fetchUpdate({ 'CF-Connecting-IP': value });
    assert.equal(response.status, 200); assert.deepEqual(await body(response), baseline); noStore(response);
  }
  // Miniflare's HTTP entry injects localhost for an empty/missing IP header.
  // Remove that injected header inside a test-only native Worker wrapper.
  const noIdentity = new Miniflare({ ...options({ colo: 'SIN', asn: 13335 }),
    scriptPath: fileURLToPath(new URL('missing-identity.worker.mjs', import.meta.url)) });
  try {
    await seed(noIdentity);
    const response = await noIdentity.dispatchFetch('https://updates.test/update.json');
    assert.equal(response.status, 200); assert.deepEqual(await body(response), baseline); noStore(response);
  } finally { await noIdentity.dispose(); }
  const noContext = new Miniflare(options({}));
  try {
    await seed(noContext);
    const response = await noContext.dispatchFetch('https://updates.test/update.json', {
      headers: { 'CF-Connecting-IP': '192.0.2.10', 'CF-Ray': 'client-forged-context' },
    });
    assert.deepEqual(await body(response), baseline); noStore(response);
  } finally { await noContext.dispose(); }
});

test('candidate corruption, absence and expiry fall back; broken baseline is observable 503', async () => {
  await archives.put('v1.3.173/update.json', 'corrupt');
  assert.deepEqual(await body(await fetchUpdate()), baseline);
  await archives.delete('v1.3.173/update.json');
  assert.deepEqual(await body(await fetchUpdate()), baseline);
  const expired = plan(100); expired.candidate.expiresAt = '2000-01-01T00:00:00Z';
  await plans.put(planKey, JSON.stringify(expired));
  assert.deepEqual(await body(await fetchUpdate()), baseline);
  await archives.put('v1.3.172/update.json', 'corrupt');
  const response = await fetchUpdate(); assert.equal(response.status, 503); noStore(response);
  await seed(runtime);
  const invalid = plan(100); invalid.baseline.sha256 = 'invalid';
  await plans.put(planKey, JSON.stringify(invalid));
  assert.equal((await fetchUpdate()).status, 503);
});

test('missing or malformed plan preserves pinned old root; corrupt old root fails closed', async () => {
  for (const value of [null, 'not-json', JSON.stringify({ ...plan(100), percent: 101 })]) {
    if (value === null) await plans.delete(planKey); else await plans.put(planKey, value);
    const response = await fetchUpdate(); assert.deepEqual(await body(response), baseline); noStore(response);
  }
  await archives.put('update.json', 'corrupt');
  assert.equal((await fetchUpdate()).status, 503);
});

test('same URL changes only after plan change; request cache headers cannot produce stale body', async () => {
  await plans.put(planKey, JSON.stringify(plan(0)));
  assert.deepEqual(await body(await fetchUpdate()), baseline);
  await plans.put(planKey, JSON.stringify(plan(100)));
  const response = await fetchUpdate({ 'If-None-Match': 'arbitrary', 'Cache-Control': 'max-age=999999' });
  assert.equal(response.status, 200); noStore(response); assert.deepEqual(await body(response), candidate);
});

test('HTTP exposes no write path, unknown host/path rejected, HEAD returns no body', async () => {
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
    const response = await runtime.dispatchFetch('https://updates.test/update.json', { method });
    assert.equal(response.status, 405); noStore(response);
  }
  assert.equal((await fetchUpdate({}, 'https://other.test/update.json')).status, 404);
  assert.equal((await fetchUpdate({}, 'https://updates.test/admin')).status, 404);
  const response = await runtime.dispatchFetch('https://updates.test/update.json', { method: 'HEAD' });
  assert.equal((await response.arrayBuffer()).byteLength, 0); noStore(response);
  assert.deepEqual(await body(await fetchUpdate()), candidate);
});

test('native R2 conditional create/update rejects concurrent stale writer without changing winner', async () => {
  await plans.delete('test-cas.json');
  const initial = await plans.put('test-cas.json', 'first', { onlyIf: new MiniflareHeaders({ 'If-None-Match': '*' }) });
  assert.ok(initial);
  assert.equal(await plans.put('test-cas.json', 'wrong', { onlyIf: new MiniflareHeaders({ 'If-None-Match': '*' }) }), null);
  const attempts = await Promise.all(['second', 'third'].map(value =>
    plans.put('test-cas.json', value, { onlyIf: { etagMatches: initial.etag } })));
  assert.equal(attempts.filter(Boolean).length, 1);
  const winner = attempts[0] ? 'second' : 'third';
  assert.equal(await (await plans.get('test-cas.json')).text(), winner);
  const conditionalRead = await plans.get('test-cas.json', { onlyIf: { etagMatches: initial.etag } });
  assert.ok(conditionalRead && !('body' in conditionalRead));
});
