# YueLink version and release drift gates

The source repository owns `scripts/check-version-bump.sh`. Land its version-gate
PR #51, including service-path fix
`a0c86ccb0310738bd7c26a187dc7dc8d305b55d0`, before relying on these consumers;
absence is an error, never a skipped gate.
No workflow is added to the private repository.

## Pre-merge validation

Dispatch the existing public Policy CI workflow with the exact YueLink PR commit:

```bash
gh workflow run policy-ci.yml -R onesyue/yuelink-ci -f yuelink_sha=<40-character-sha>
```

The validation job fetches the source history and tags, checks the committed
release metadata, and runs the offline version-gate tests. Source attestation
runs the same gate before the expensive build checks. This blocks unchanged or
lower stable versions, build-only bumps and incomplete release documentation
when shipped source changed since the latest reachable release.

This is an explicit validation entry point, not an automatic cross-repository
PR trigger. The Policy CI result belongs to yuelink-ci; it does not automatically
become a required status on the private PR. Review/branch-protection enforcement
and administrator bypass policy remain separate controls. No claim is made that
this change addresses database schema/grants or the yueto-ci packaging seal.

## Release admission

`release.sh` fetches the immutable private tag into its temporary source snapshot
via HTTPS and the existing gh authentication. Before creating any public tag it
requires `git -c gpg.minTrustLevel=fully verify-tag`, the attested source SHA,
and an exact stable version match against the pubspec at that tag. The local Git
signing trust configuration must trust the source tag signer at least fully. OpenPGP keys with undefined,
never or marginal trust are rejected even when their signature is valid; SSH
signers must be authorized by `gpg.ssh.allowedSignersFile`. The command-line
minimum overrides weaker local Git configuration. Missing trust, unsigned tags,
version mismatch or fetch failures stop admission with an explicit error.
Existing GitHub signature verification and attestation checks remain in force.

## Read-only drift monitor

```bash
bash scripts/release-drift.sh /path/to/yuelink
```

The monitor refreshes origin/master and tags, then prints master SHA, latest
reachable stable tag, pubspec version and the version advertised by
`https://yuetong.app/update.json` (the primary endpoint in
`lib/modules/updater/update_checker.dart`). It exits 1 if the channel differs from
the tag, or the first untagged shipped-source change entered the master
first-parent history more than 24 hours ago. A later docs commit does not reset
the clock. This observes an advertised version, not package/runtime health or
proof of a complete live release. No schedule, tag or release is created.

Missing source history or unavailable/malformed channel responses report UNKNOWN
and exit 1. `--no-fetch` and `--manifest-file <json>` support explicitly offline
checks; their results do not establish current live state. The source checkout
must contain the version gate implementation.

Targeted regression command:

```bash
python3 -m unittest discover -s tests -p test_version_release_gates.py -v
```

The offline suite includes real OpenPGP and SSH signatures and a service-only
Git history through both source admission and drift reporting. The source checker
fixture is pinned in `tests/fixtures/source-version-gate/README.md`; set
`SOURCE_VERSION_GATE_REPO=/path/to/yuelink` to test a source checkout directly.
