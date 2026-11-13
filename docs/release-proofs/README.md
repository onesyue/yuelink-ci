# Stable-release proof migration

The v1.3.176 files preserve the original public source attestation and its
Sigstore bundle, without rewriting either signed payload. The proof SHA-256 is
`290d8cb2a00d92b75b42d5bd48923d57c97466fe9afec39ed055999ce044155f`.
They bridge the release published before durable proof assets were introduced.
They do not set the dial baseline: selection still chooses the latest stable
release at a fixed cutoff, verifies its annotated tag, exact source attestation,
and successful Build attempt with an explicit successful promotion step.

The same strict verifier checks these bytes as release assets or Actions
artifacts. A moved tag, rerun with a different attempt, altered proof, invalid
bundle, or failed gate is rejected. Once a release has either durable asset,
this migration archive cannot override missing or invalid durable evidence.
No historical release was modified to create this archive. It proves public
pipeline provenance, not the currently live version after manual operations.
