// Deployment is deliberately separate from this PR. No write endpoint, R2
// mutation, dynamic code or client identity logging is permitted here.
const PLAN_KEY = 'stable/network-ip.json';
const MAX_MANIFEST_BYTES = 262144;
const NO_STORE = {
  'Cache-Control': 'no-store, private, max-age=0',
  'CDN-Cache-Control': 'no-store',
  'Cloudflare-CDN-Cache-Control': 'no-store',
  'Content-Type': 'application/json; charset=utf-8',
  'X-Content-Type-Options': 'nosniff',
};

async function sha256(bytes) {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('');
}

export async function bucketForIdentity(salt, identity) {
  const digest = await sha256(new TextEncoder().encode(`yuelink-network-ip-v1\n${salt}\n${identity}`));
  // The first 32 bits are exactly representable and map to 10,000 thresholds;
  // the plan percentage alone selects client exposure.
  return Math.floor(Number.parseInt(digest.slice(0, 8), 16) * 10000 / 0x100000000);
}

function networkIdentity(request) {
  // CF metadata is supplied by the runtime, not a client header. Deployment
  // must use ordinary zone routes without upstream Worker identity rewriting.
  if (!request.cf || typeof request.cf.colo !== 'string' ||
      !/^[A-Z]{3}$/.test(request.cf.colo) || !Number.isInteger(request.cf.asn) || request.cf.asn <= 0) return null;
  const value = request.headers.get('CF-Connecting-IP');
  if (!value || value.length > 45) return null;
  if (/^(?:\d{1,3}\.){3}\d{1,3}$/.test(value)) {
    const parts = value.split('.').map(Number);
    return parts.every(part => part <= 255) ? parts.join('.') : null;
  }
  if (/^[a-fA-F0-9:]+$/.test(value) && value.includes(':')) {
    try { return new URL(`http://[${value}]/`).hostname.toLowerCase(); } catch { return null; }
  }
  return null;
}

function descriptor(value) {
  return value && typeof value === 'object' &&
    /^\d+\.\d+\.\d+$/.test(value.version) &&
    value.key === `v${value.version}/update.json` &&
    typeof value.sha256 === 'string' && /^[a-f0-9]{64}$/.test(value.sha256) &&
    typeof value.expiresAt === 'string' && Number.isFinite(Date.parse(value.expiresAt)) &&
    Date.parse(value.expiresAt) > Date.now();
}

async function verifiedBytes(bucket, key, expectedHash) {
  const object = await bucket.get(key);
  if (!object || !('body' in object) || object.size > MAX_MANIFEST_BYTES) return null;
  const bytes = await object.arrayBuffer();
  if (bytes.byteLength === 0 || bytes.byteLength > MAX_MANIFEST_BYTES || await sha256(bytes) !== expectedHash) return null;
  return bytes;
}

function response(request, bytes, status = 200) {
  return new Response(request.method === 'HEAD' ? null : bytes, { status, headers: NO_STORE });
}

async function legacyFallback(request, env) {
  // These are reviewed deployment values from a verified old root, not values
  // learned from the untrusted object being served. Empty configuration fails.
  if (!/^[a-f0-9]{64}$/.test(env.LEGACY_ROOT_SHA256 || '') ||
      !(Date.parse(env.LEGACY_ROOT_EXPIRES_AT || '') > Date.now())) return response(request, null, 503);
  const bytes = await verifiedBytes(env.ARCHIVES, 'update.json', env.LEGACY_ROOT_SHA256);
  return response(request, bytes, bytes ? 200 : 503);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const hosts = (env.ALLOWED_HOSTS || '').split(',').filter(Boolean);
    if (!hosts.includes(url.hostname) || url.pathname !== '/update.json') return response(request, null, 404);
    if (!['GET', 'HEAD'].includes(request.method)) return response(request, null, 405);
    try {
      const object = await env.PLANS.get(PLAN_KEY);
      if (!object || !('body' in object)) return await legacyFallback(request, env);
      if (object.size > 16384) return await legacyFallback(request, env);
      let plan;
      try { plan = await object.json(); } catch { return await legacyFallback(request, env); }
      if (!plan || plan.schemaVersion !== 1 || plan.mode !== 'network-ip' ||
          !Number.isInteger(plan.percent) || plan.percent < 0 || plan.percent > 100 ||
          typeof plan.salt !== 'string' || !/^[a-f0-9]{64}$/.test(plan.salt)) return await legacyFallback(request, env);
      if (!descriptor(plan.baseline)) return response(request, null, 503);
      // Always authenticate baseline bytes first, including at 100%: a broken
      // fallback must be visible rather than masking an unusable safety path.
      const baseline = await verifiedBytes(env.ARCHIVES, plan.baseline.key, plan.baseline.sha256);
      if (!baseline) return response(request, null, 503);
      const identity = networkIdentity(request);
      if (!identity || !descriptor(plan.candidate) || plan.percent === 0 ||
          await bucketForIdentity(plan.salt, identity) >= plan.percent * 100) return response(request, baseline);
      try {
        const candidate = await verifiedBytes(env.ARCHIVES, plan.candidate.key, plan.candidate.sha256);
        return response(request, candidate || baseline);
      } catch {
        return response(request, baseline);
      }
    } catch {
      // Do not log the request, IP, object body, credential or signed URL.
      return response(request, null, 503);
    }
  },
};
