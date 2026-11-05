# Final client release step: reconcile the signed ledger

A green public build publishes the stable root. Release completion also requires
its exact version, sourceCommit and raw update.json SHA-256 in yueto release.yaml.
Updating the watchdog fixture alone leaves release-state showing DRIFT.

This repository cannot write the root repository. The helper below emits a plan
only: no network, ledger edits, signing, push, remote execution or new dependency.
Run the following procedure on the authorized Mac after successful promotion and
existing release acceptance. Signing keys stay on the Mac. Do not run the writer
in the public build workflow or add a second promotion/signing ceremony.

1. Read fresh stable bytes into a temporary file after promotion (the release
   operator, not this offline helper, performs the network read):

   ```sh
   set -eu
   manifest_file=$(mktemp)
   trap 'rm -f "$manifest_file"' EXIT
   curl -fsSL --proto '=https' --proto-redir '=https' --max-time 25 \
     --max-filesize 1048576 -H 'Cache-Control: no-cache' \
     https://yuetong.app/update.json -o "$manifest_file"
   python3 scripts/reconcile-yuelink-pin.py "$manifest_file" \
     --version X.Y.Z --source-commit RELEASED_SOURCE_40_HEX
   ```

   Use version and source from the completed release's attested identity, not
   values copied from unverified JSON. The helper uses the pinned signature,
   schema and replay-floor verifier, checks freshness and expected identity,
   and hashes the original bytes without reserializing. PLAN_ONLY is not proof
   that the live ledger has changed. Never weaken or dynamically learn the floor.

2. The authorized root release owner uses the existing release transaction and
   lock procedure in yueto's release-chain runbook. Refresh origin/master and
   require a clean exact HEAD == origin/master in the participating repositories;
   require the source HEAD and signed vX.Y.Z tag to resolve to RELEASED_SOURCE_40_HEX.
   Verify the existing release.yaml signature and retain its generation and
   rollback baseline. Re-read stable immediately before mutation and regenerate
   the plan; abort if it changed. Compare the current authenticated yuelink block
   with the plan. If all three fields already match, skip mutation and sealing.

3. Otherwise run the emitted command **from the yueto root**, inside that release
   transaction. Use native `scripts/release-yaml.py write-yuelink`; do not hand-edit
   YAML. This writer advances generation and enforces native ledger/registry locks.
   It does not offer `--expect-generation`: the root owner must serialize the whole
   write/seal/commit/project sequence and reject a changed starting generation.
   A printed command is not a substitute for that transaction. Never bypass a
   lock or reuse stale plan output. Run native `release-yaml.py seal` on the Mac,
   then `release-yaml.py verify-signature`. Inspect the diff: only the yuelink block
   and native generation/timestamp metadata should change. Use the existing signed
   commit/publication/projection procedure for release.yaml and release.yaml.asc.
   Preserve image signature/SBOM/provenance checks, migration gate, rollback
   baseline and signed sshd-authority bindings; this step does not mint another
   release root or repeat the bindings ceremony.

4. Fetch stable again and regenerate the plan. Verify the published/projected
   ledger signature and generation with the existing root readback gates; compare
   all three yuelink fields with the new plan. Run `scripts/release-state.py` from
   yueto and require the yuelink row to be OK: source HEAD == signed version tag ==
   signed manifest source == ledger source, with matching version and raw digest.
   UNKNOWN or DRIFT is unfinished work. A newer source HEAD can independently
   cause DRIFT; do not conceal it by pinning unpublished source or moving tags.
   If stable changed concurrently, stop and reconcile the newer authorized release.

Keep the existing reviewed fixture update/replay regression step as well.
Rollback of this helper/documentation is one commit revert and changes no runtime
state. After a real ledger write, do not restore an old signed generation: use
native forward repair, preserving the anti-rollback floor and signing the new
higher generation. Do not downgrade the stable root.
