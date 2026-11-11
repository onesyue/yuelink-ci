# Unsigned iOS verification

`dialgate-verify.yml` binds the private pull request and exact commit before
running verification. Open a same-repository public verifier PR to trigger the
positive run. The reviewed positive source is private PR 131 at
`0b5096da3ceeca50b2f0c5185147a62df078e7d0`. The separate iOS job compiles the
Runner scheme, including PacketTunnel, with Xcode 26.6, Flutter 3.47.8 and
Go 1.27.4. Its result participates in the required native compilation summary.

The helper checks clean recursive source identity and removed Git checkout
credentials, prepares the actual FFI archive, resolves Flutter Swift packages,
checks both targets' signing settings, and invokes `xcodebuild ... build`
with Flutter's `-skipPackagePluginValidation` and
`-skipPackageSignatureValidation`. Those flags skip headless Swift package
plugin trust prompts. They leave `CODE_SIGNING_ALLOWED=NO` and the signer
sandbox in place. Success requires actual Swift compilation events for both
targets and fresh app and extension executables. It does not archive, sign,
install or distribute them.
The existing deploy key is reused without adding credentials or permissions;
workflow `contents: read` does not establish that key's server-side access mode.

Flutter's pinned SDK has four unconditional signing calls. The helper copies the
SDK into the run's private temporary directory, dereferences links, validates
four original source hashes, and omits exactly those four calls in two files.
It removes the copied tool snapshot and rebuilds it from the adapted source.
Original and adapted hashes and the rebuilt snapshot hash enter the receipt.
Compilation and linking remain real. A macOS sandbox prohibits signer execution;
a PATH guard also rejects signing rather than pretending it succeeded. Read-only
media signature verification runs separately. An unavailable sandbox fails the
job instead of weakening the boundary.

Raw subprocess logs, build settings, SDK copies, products and xcresults remain
run-local. Only the allowlisted receipt is uploaded. Error `message` strings
from the run-local xcresult are scanned with that same allowlist; `sourceURL`
and the bundle stay on the runner. Its negative diagnostic is restricted to
the missing `extensionProfile` compiler error, without source snippets,
credentials, UUIDs or unrelated log text.

## Negative control

Use a separate isolated private checkout at the positive source and a new branch
named `grok/ios-negative-...`. Run the following helper with a fresh receipt path
outside that checkout:

```sh
python3 scripts/dialgate-ios-fixture.py \
  --checkout "$FIXTURE_CHECKOUT" --receipt "$FIXTURE_RECEIPT"
```

The helper only removes the required `extensionProfile` argument from the exact
reviewed AppDelegate call. It does not commit or push. Independently review and
sign that one-file child commit, open its own private fixture PR, and bind that
PR's own SHA in a separate public fixture PR. That public branch changes only
`policy/dialgate-source.json`: set `sourcePr` and `sourceCommit` to the private
fixture identity and `iosCompileMode` to `negative`. Keep `sourceRepository`
unchanged. The opened public PR triggers verification; no generator or workflow
edit is needed in that fixture. Never label it as the positive source.
The binding validates the explicit mode enum and exports it to the iOS job.
Negative mode skips Linux, Android, the five-platform dial matrix and its
summary, while retaining source binding, iOS compilation and the native summary.
The iOS compile check and native summary must remain red. The receipt must show
`expected_swift_failure` and the real missing-argument compiler diagnostic;
dependency, SDK or setup failures do not establish the negative control.

## Local verification versus actual compiler evidence

The host and offline Linux tests exercise the helper, recording subprocesses,
source checks, SDK adaptation, sign rejection, receipt filtering, generator and
failure semantics. They do not establish that Xcode compiled either target.
Actual positive and negative CI runs are separate acceptance evidence, performed
only after the public verifier commit and commands have been independently
reviewed. A green offline test result cannot substitute for either run.

## PR events and dispatch availability

The current default branch does not contain this verification workflow. GitHub
requires a workflow to exist on the default branch before `workflow_dispatch`
can trigger it, so this round uses public PR events for both controls. Do not
merge or invoke production workflows to enable dispatch.

PR events read `iosCompileMode` only from the exact public checkout's source
policy; event inputs cannot override it. The retained dispatch interface is
validated independently and reads `ios_compile_mode` from dispatch inputs
(default `positive`), but is unavailable for this round. Both paths reject
values outside `positive` and `negative` before private checkout.
