# Signed macOS platform-floor fixture

This is **not a release or a production trust anchor**. On 2026-10-08, the actual
YueLink `Generate updater manifest candidate` Bash step produced these nine
platform records from isolated test sidecars and the real Runner deployment
floor (13.0). The records were copied without changes into this pre-promotion
test envelope and signed using an ephemeral test-only Ed25519 key. No private
key is retained. The public-key receipt records both producer source hashes.
All three promotion-evidence fields are absent as one bundle; tests must not
relax the real complete-bundle requirement. Version 9.9.9 is synthetic.

Tests explicitly replace the trust key only within the test invocation. The
production verifier must reject this fixture under its real pinned key, and
modifying the signed system floor must fail authentication.
