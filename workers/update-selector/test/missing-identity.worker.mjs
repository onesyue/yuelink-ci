// Test-only adapter: Miniflare's edge otherwise inserts localhost for absent IP.
// This file is not the deployment entrypoint and adds no production endpoint.
import selector from '../src/index.mjs';
export default {
  fetch(request, env) {
    const withoutIdentity = new Request(request);
    if (request.cf?.colo !== 'SIN' || request.cf?.asn !== 13335 ||
        withoutIdentity.cf?.colo !== request.cf.colo || withoutIdentity.cf?.asn !== request.cf.asn) {
      throw new Error('Missing-IP fixture must preserve valid runtime CF context');
    }
    withoutIdentity.headers.delete('CF-Connecting-IP');
    return selector.fetch(withoutIdentity, env);
  },
};
