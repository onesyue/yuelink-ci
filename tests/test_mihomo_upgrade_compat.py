"""Offline mihomo upgrade-compat gates for release admission."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check-mihomo-upgrade-compat.py"
WORKFLOW = ROOT / ".github/workflows/source-attestation.yml"
POLICY = ROOT / ".github/workflows/policy-ci.yml"
SOURCE_CONTRACT = ROOT / "tests/test_source_attestation_contract.py"
DEFAULT_FLOOR = ROOT / "tests/fixtures/mihomo-upgrade-compat/reviewed-patch-floor.json"
COMMAND = (
    "python3 .mihomo-upgrade-compat-tools/scripts/"
    "check-mihomo-upgrade-compat.py --source-root . --fetch-ci-history"
)
TOOLS_CHECKOUT_PATH = "path: .mihomo-upgrade-compat-tools"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "compat-test",
    "GIT_AUTHOR_EMAIL": "compat-test@example.invalid",
    "GIT_COMMITTER_NAME": "compat-test",
    "GIT_COMMITTER_EMAIL": "compat-test@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
}


def load_checker():
    spec = importlib.util.spec_from_file_location("mihomo_upgrade_compat", SCRIPT)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"missing checker: {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(n: int) -> str:
    return f"{n:040x}"


def classified(sha_n: int, intent: str) -> dict[str, str]:
    return {"sha": sha(sha_n), "intent": intent}


def valid_ledger() -> dict:
    runtime = [
        classified(1, "Keep Hysteria2 close orderly"),
        classified(2, "Preserve VLESS Reality handshake"),
    ]
    assurance = [classified(3, "Offline inventory tooling")]
    backport = [
        {
            "sha": sha(4),
            "intent": "AnyTLS idle pool fix",
            "upstream_branch": "Meta",
            "included_in_stable": "v1.19.32",
            "not_in_stable": "v1.19.31",
            "upstream_commit": "5019cc09",
        }
    ]
    order = [item["sha"] for item in [*runtime, *assurance, *backport]]
    return {
        "schema": 1,
        "as_of": "2026-11-24",
        "upstream": {
            "repository": "https://github.com/MetaCubeX/mihomo.git",
            "channel": "Meta",
            "stable_tag": "v1.19.31",
            "base": sha(100),
            "latest_stable_tag": "v1.19.34",
            "assessment_date": "2026-11-11",
            "assessed_meta_head": sha(134),
            "assessment_compare_url": (
                "https://github.com/MetaCubeX/mihomo/compare/v1.19.31...v1.19.34"
            ),
            "separate_unadopted_prs": [],
            "adopted_prs": ["https://github.com/onesyue/mihomo/pull/5"],
        },
        "fork": {
            "repository": "https://github.com/onesyue/mihomo.git",
            "branch": "master",
            "pinned_head": order[-1],
            "ahead_by": len(order),
            "behind_by": 0,
        },
        "commit_order": order,
        "runtime_commits": runtime,
        "assurance_commits": assurance,
        "backport_commits": backport,
        "retired_backports": [],
        "changed_files": ["adapter/adapter.go", "tunnel/tunnel.go"],
    }


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **GIT_ENV}
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-C", str(repo), *args],
        check=check,
        text=True,
        capture_output=True,
        env=env,
    )


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", "master")
    git(path, "config", "user.name", "compat-test")
    git(path, "config", "user.email", "compat-test@example.invalid")
    git(path, "config", "commit.gpgsign", "false")
    return path


def commit_file(repo: Path, relative: str, content: str, message: str) -> str:
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    git(repo, "add", relative)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def patch_id(repo: Path, sha: str) -> str:
    checker = load_checker()
    digest = checker.patch_id_for(repo, sha)
    if digest is None:
        raise AssertionError(f"expected a patch-id for {sha}")
    return digest


def ledger_from_fork(
    *,
    base: str,
    pin: str,
    order: list[str],
    intents: list[str],
    assessed: str,
) -> dict:
    runtime = [{"sha": order[i], "intent": intents[i]} for i in range(len(order))]
    return {
        "schema": 1,
        "as_of": "2026-11-24",
        "upstream": {
            "repository": "https://github.com/MetaCubeX/mihomo.git",
            "channel": "Meta",
            "stable_tag": "v1.19.31",
            "base": base,
            "latest_stable_tag": "v1.19.34",
            "assessment_date": "2026-11-11",
            "assessed_meta_head": assessed,
            "assessment_compare_url": (
                "https://github.com/MetaCubeX/mihomo/compare/v1.19.31...v1.19.34"
            ),
            "separate_unadopted_prs": [],
            "adopted_prs": ["https://github.com/onesyue/mihomo/pull/5"],
        },
        "fork": {
            "repository": "https://github.com/onesyue/mihomo.git",
            "branch": "master",
            "pinned_head": pin,
            "ahead_by": len(order),
            "behind_by": 0,
        },
        "commit_order": order,
        "runtime_commits": runtime,
        "assurance_commits": [],
        "backport_commits": [],
        "retired_backports": [],
        "changed_files": ["a.txt"],
    }


def build_fork(root: Path) -> dict:
    """Create a tiny fork still based on v1.19.31 while v1.19.34 exists beside it."""
    mihomo = init_repo(root / "mihomo")
    base = commit_file(mihomo, "README", "base\n", "base")
    git(mihomo, "tag", "v1.19.31")
    git(mihomo, "checkout", "-b", "assessed")
    assessed = commit_file(mihomo, "UPSTREAM", "v1.19.34\n", "upstream 1.19.34")
    git(mihomo, "tag", "v1.19.34")
    git(mihomo, "checkout", "master")
    patches = [
        ("keep-hy2.txt", "hy2 close\n", "Keep Hysteria2 close orderly"),
        ("keep-vless.txt", "vless reality\n", "Preserve VLESS Reality handshake"),
    ]
    records = []
    for name, content, message in patches:
        sha_ = commit_file(mihomo, name, content, message)
        records.append({"sha": sha_, "intent": message, "patch_id": patch_id(mihomo, sha_)})
    pin = records[-1]["sha"]
    order = [item["sha"] for item in records]
    ledger = ledger_from_fork(
        base=base,
        pin=pin,
        order=order,
        intents=[item["intent"] for item in records],
        assessed=assessed,
    )
    floor = {"schema": 1, "patch_ids": [item["patch_id"] for item in records]}
    return {
        "mihomo": mihomo,
        "base": base,
        "pin": pin,
        "assessed": assessed,
        "records": records,
        "ledger": ledger,
        "floor": floor,
    }


def populated_source(root: Path, fork: dict, ledger: dict | None = None) -> Path:
    parent = init_repo(root / "yuelink")
    write_json(parent / "docs/governance/mihomo-fork-ledger.json", ledger or fork["ledger"])
    checkout = parent / "core/mihomo"
    git(fork["mihomo"], "clone", "--no-hardlinks", str(fork["mihomo"]), str(checkout))
    git(checkout, "checkout", "--detach", fork["pin"] if ledger is None else ledger["fork"]["pinned_head"])
    git(
        parent,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{(ledger or fork['ledger'])['fork']['pinned_head']},core/mihomo",
    )
    return parent


class MihomoUpgradeCompatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.checker = load_checker()
        self.ledger = valid_ledger()
        self.temps: list[Path] = []

    def tearDown(self) -> None:
        for path in self.temps:
            shutil.rmtree(path, ignore_errors=True)

    def scratch(self) -> Path:
        path = Path(tempfile.mkdtemp(prefix="mihomo-compat-"))
        self.temps.append(path)
        return path

    def test_ahead_count_must_match_commit_order(self) -> None:
        self.ledger["fork"]["ahead_by"] = 99
        with self.assertRaisesRegex(self.checker.CompatError, "ahead_by"):
            self.checker.inspect(self.ledger)

    def test_behind_claim_is_rejected(self) -> None:
        self.ledger["fork"]["behind_by"] = 3
        with self.assertRaisesRegex(self.checker.CompatError, "behind"):
            self.checker.inspect(self.ledger)

    def test_commit_order_must_end_at_the_pin(self) -> None:
        self.ledger["commit_order"] = list(reversed(self.ledger["commit_order"]))
        with self.assertRaisesRegex(self.checker.CompatError, "pinned_head"):
            self.checker.inspect(self.ledger)

    def test_non_meta_channel_is_rejected(self) -> None:
        self.ledger["upstream"]["channel"] = "Alpha"
        with self.assertRaisesRegex(self.checker.CompatError, "Meta"):
            self.checker.inspect(self.ledger)

    def test_upstream_head_without_patch_train_is_rejected(self) -> None:
        head = self.ledger["upstream"]["assessed_meta_head"]
        self.ledger["fork"]["pinned_head"] = head
        self.ledger["fork"]["ahead_by"] = 0
        self.ledger["commit_order"] = []
        self.ledger["runtime_commits"] = []
        self.ledger["assurance_commits"] = []
        self.ledger["backport_commits"] = []
        with self.assertRaisesRegex(self.checker.CompatError, "patch-preserving"):
            self.checker.inspect(self.ledger)

    def test_stable_tag_advance_without_base_change_is_rejected(self) -> None:
        previous = deepcopy(self.ledger)
        self.ledger["upstream"]["stable_tag"] = "v1.19.34"
        self.ledger["upstream"]["assessment_compare_url"] = (
            "https://github.com/MetaCubeX/mihomo/compare/v1.19.34...v1.19.34"
        )
        with self.assertRaisesRegex(self.checker.CompatError, "base"):
            self.checker.validate_transition(self.ledger, previous)

    def test_cli_without_arguments_is_usage_only(self) -> None:
        result = subprocess.run(
            ["python3", str(SCRIPT)],
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr.lower() + result.stdout.lower())


class MihomoHistoryAndFloorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.checker = load_checker()
        self.temps: list[Path] = []
        self.root = Path(tempfile.mkdtemp(prefix="mihomo-hist-"))
        self.temps.append(self.root)
        self.fork = build_fork(self.root)

    def tearDown(self) -> None:
        for path in self.temps:
            shutil.rmtree(path, ignore_errors=True)

    def floor_path(self, floor: dict | None = None) -> Path:
        path = self.root / "floor.json"
        write_json(path, floor or self.fork["floor"])
        return path

    def test_pin_on_declared_1_19_31_passes_even_if_1_19_34_exists(self) -> None:
        parent = populated_source(self.root / "ok", self.fork)
        self.checker.check_source_root(parent, floor_path=self.floor_path())
        # The assessed tag is present but must not be treated as a required pin.
        self.assertEqual(
            git(self.fork["mihomo"], "rev-parse", "v1.19.34^{commit}").stdout.strip(),
            self.fork["assessed"],
        )

    def test_missing_history_is_rejected_not_passed(self) -> None:
        parent = init_repo(self.root / "nohist")
        write_json(parent / "docs/governance/mihomo-fork-ledger.json", self.fork["ledger"])
        git(
            parent,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{self.fork['pin']},core/mihomo",
        )
        with self.assertRaisesRegex(self.checker.CompatError, "history unavailable"):
            self.checker.check_source_root(parent, floor_path=self.floor_path())

    def ci_source(self, *, shallow: bool = False) -> Path:
        parent = populated_source(self.root / "ci", self.fork)
        checkout = parent / "core/mihomo"
        donor = self.fork["mihomo"].as_uri()
        shutil.rmtree(checkout)
        depth = ["--depth", "1"] if shallow else []
        git(parent, "clone", "--no-tags", *depth, donor, str(checkout))
        for authority in (self.checker.UPSTREAM_URL, self.checker.FORK_URL):
            git(checkout, "config", "--add", f"url.{donor}.insteadOf", authority)
        return parent

    def test_ci_checks_an_annotated_upstream_tag_and_exact_shallow_fork_history(self) -> None:
        repo = self.fork["mihomo"]
        git(repo, "tag", "-d", "v1.19.31")
        git(repo, "tag", "-a", "v1.19.31", self.fork["base"], "-m", "upstream stable")
        parent = self.ci_source(shallow=True)
        checkout = parent / "core/mihomo"
        self.assertEqual(git(checkout, "rev-parse", "--is-shallow-repository").stdout.strip(), "true")
        self.assertEqual(git(checkout, "tag", "--list").stdout.strip(), "")
        self.checker.check_source_root(parent, floor_path=self.floor_path(), fetch_ci_history=True)
        self.assertEqual(git(checkout, "rev-parse", "--is-shallow-repository").stdout.strip(), "false")
        self.assertEqual(git(checkout, "rev-parse", "HEAD").stdout.strip(), self.fork["pin"])
        self.assertEqual(git(checkout, "tag", "--list").stdout.strip(), "v1.19.31")
        self.assertEqual(git(checkout, "cat-file", "-t", "refs/tags/v1.19.31").stdout.strip(), "tag")
        self.assertEqual(git(checkout, "rev-parse", "v1.19.31^{commit}").stdout.strip(), self.fork["base"])

    def test_ci_rejects_missing_upstream_tag_even_with_matching_cached_tag(self) -> None:
        parent = self.ci_source()
        git(parent / "core/mihomo", "tag", "v1.19.31", self.fork["base"])
        git(self.fork["mihomo"], "tag", "-d", "v1.19.31")
        with self.assertRaisesRegex(self.checker.CompatError, "git fetch"):
            self.checker.check_source_root(parent, floor_path=self.floor_path(), fetch_ci_history=True)

    def test_ci_rejects_wrong_canonical_tag_target(self) -> None:
        parent = self.ci_source()
        git(self.fork["mihomo"], "tag", "-f", "v1.19.31", self.fork["assessed"])
        with self.assertRaisesRegex(self.checker.CompatError, "does not point at the declared base"):
            self.checker.check_source_root(parent, floor_path=self.floor_path(), fetch_ci_history=True)

    def test_ci_does_not_replace_a_conflicting_cached_tag(self) -> None:
        parent = self.ci_source()
        checkout = parent / "core/mihomo"
        git(checkout, "tag", "v1.19.31", self.fork["pin"])
        with self.assertRaisesRegex(self.checker.CompatError, "git fetch"):
            self.checker.check_source_root(parent, floor_path=self.floor_path(), fetch_ci_history=True)
        self.assertEqual(git(checkout, "rev-parse", "v1.19.31^{commit}").stdout.strip(), self.fork["pin"])

    def test_reused_stable_tag_must_match_the_independently_reviewed_base(self) -> None:
        parent = self.ci_source()
        floor = {**self.fork["floor"], "stable_tag": "v1.19.31", "base": self.fork["assessed"]}
        with self.assertRaisesRegex(self.checker.CompatError, "differs from the reviewed floor"):
            self.checker.check_source_root(parent, floor_path=self.floor_path(floor), fetch_ci_history=True)

    def test_ci_rejects_wrong_checkout_before_fetching(self) -> None:
        parent = self.ci_source()
        git(parent / "core/mihomo", "checkout", "--detach", self.fork["base"])
        with self.assertRaisesRegex(self.checker.CompatError, "checked-out mihomo HEAD"):
            self.checker.check_source_root(parent, floor_path=self.floor_path(), fetch_ci_history=True)

    def test_missing_stable_tag_is_rejected(self) -> None:
        git(self.fork["mihomo"], "tag", "-d", "v1.19.31")
        parent = populated_source(self.root / "notag", self.fork)
        with self.assertRaisesRegex(self.checker.CompatError, "stable_tag"):
            self.checker.check_source_root(parent, floor_path=self.floor_path())

    def test_behind_declared_base_is_rejected_even_if_ledger_claims_zero(self) -> None:
        # Move pin back to the base while leaving behind_by=0 in the ledger.
        ledger = deepcopy(self.fork["ledger"])
        ledger["fork"]["pinned_head"] = self.fork["base"]
        ledger["fork"]["ahead_by"] = 0
        ledger["commit_order"] = []
        ledger["runtime_commits"] = []
        # inspect() already rejects ahead_by 0; use a pin that is an ancestor
        # of base? Instead: create a pin on an older side branch off a parent
        # of base is hard. Rebuild: checkout base, reset pin claim to a commit
        # that does not contain later base? Simpler: amend ledger base to a
        # future commit (the assessed 1.19.34 head) while pin stays on 1.19.31
        # line — pin is then behind the declared base.
        ledger = deepcopy(self.fork["ledger"])
        ledger["upstream"]["base"] = self.fork["assessed"]
        ledger["upstream"]["stable_tag"] = "v1.19.34"
        ledger["upstream"]["assessment_compare_url"] = (
            "https://github.com/MetaCubeX/mihomo/compare/v1.19.34...v1.19.34"
        )
        parent = populated_source(self.root / "behind", self.fork, ledger)
        with self.assertRaisesRegex(self.checker.CompatError, "behind its declared base"):
            self.checker.check_source_root(parent, floor_path=self.floor_path())

    def test_gitlink_mismatch_is_rejected(self) -> None:
        parent = populated_source(self.root / "mismatch", self.fork)
        git(
            parent,
            "update-index",
            "--cacheinfo",
            f"160000,{self.fork['assessed']},core/mihomo",
        )
        with self.assertRaisesRegex(self.checker.CompatError, "gitlink"):
            self.checker.check_source_root(parent, floor_path=self.floor_path())

    def test_same_intent_different_patch_fails_the_floor(self) -> None:
        # Re-commit the second patch with the same subject but different bytes.
        git(self.fork["mihomo"], "reset", "--hard", self.fork["records"][0]["sha"])
        sha_ = commit_file(
            self.fork["mihomo"],
            "keep-vless.txt",
            "different vless patch\n",
            "Preserve VLESS Reality handshake",
        )
        order = [self.fork["records"][0]["sha"], sha_]
        ledger = ledger_from_fork(
            base=self.fork["base"],
            pin=sha_,
            order=order,
            intents=[item["intent"] for item in self.fork["records"]],
            assessed=self.fork["assessed"],
        )
        fork = dict(self.fork, pin=sha_, ledger=ledger)
        parent = populated_source(self.root / "retitle", fork, ledger)
        with self.assertRaisesRegex(self.checker.CompatError, "floor patch-id"):
            self.checker.check_source_root(parent, floor_path=self.floor_path())

    def test_rewritten_subject_with_same_patch_still_passes(self) -> None:
        git(self.fork["mihomo"], "reset", "--hard", self.fork["records"][0]["sha"])
        sha_ = commit_file(
            self.fork["mihomo"],
            "keep-vless.txt",
            "vless reality\n",
            "completely rewritten subject",
        )
        order = [self.fork["records"][0]["sha"], sha_]
        ledger = ledger_from_fork(
            base=self.fork["base"],
            pin=sha_,
            order=order,
            intents=["Keep Hysteria2 close orderly", "completely rewritten subject"],
            assessed=self.fork["assessed"],
        )
        fork = dict(self.fork, pin=sha_, ledger=ledger)
        parent = populated_source(self.root / "reword", fork, ledger)
        self.checker.check_source_root(parent, floor_path=self.floor_path())

    def test_self_consistent_drop_without_updating_floor_is_rejected(self) -> None:
        # Drop the second patch, rewrite the ledger so it is internally consistent,
        # and do not pass --previous-ledger. The reviewed floor must still fail closed.
        git(self.fork["mihomo"], "reset", "--hard", self.fork["records"][0]["sha"])
        ledger = ledger_from_fork(
            base=self.fork["base"],
            pin=self.fork["records"][0]["sha"],
            order=[self.fork["records"][0]["sha"]],
            intents=[self.fork["records"][0]["intent"]],
            assessed=self.fork["assessed"],
        )
        ledger["retired_backports"] = [
            {
                "intent": self.fork["records"][1]["intent"],
                "upstream_commit": "deadbeef",
                "retired": "claimed retired only in the source ledger",
            }
        ]
        fork = dict(self.fork, pin=self.fork["records"][0]["sha"], ledger=ledger)
        parent = populated_source(self.root / "drop", fork, ledger)
        with self.assertRaisesRegex(self.checker.CompatError, "floor patch-id"):
            self.checker.check_source_root(parent, floor_path=self.floor_path())

    def test_cli_is_read_only_and_applies_the_explicit_floor(self) -> None:
        parent = populated_source(self.root / "cli", self.fork)
        floor = self.floor_path()
        good = subprocess.run(
            ["python3", str(SCRIPT), "--source-root", str(parent), "--floor", str(floor)],
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertIn("PASS", good.stdout)
        broken = deepcopy(self.fork["ledger"])
        broken["fork"]["behind_by"] = 1
        write_json(parent / "docs/governance/mihomo-fork-ledger.json", broken)
        before = {
            path.relative_to(parent): path.read_bytes()
            for path in parent.rglob("*")
            if path.is_file()
        }
        bad = subprocess.run(
            ["python3", str(SCRIPT), "--source-root", str(parent), "--floor", str(floor)],
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(bad.returncode, 1, bad.stdout)
        self.assertIn("FAIL", bad.stderr)
        after = {
            path.relative_to(parent): path.read_bytes()
            for path in parent.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_cli_source_root_without_floor_flag_uses_the_reviewed_fixture(self) -> None:
        parent = populated_source(self.root / "default-floor", self.fork)
        result = subprocess.run(
            ["python3", str(SCRIPT), "--source-root", str(parent)],
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("floor patch-id", result.stderr)
        self.assertTrue(DEFAULT_FLOOR.is_file())
        self.assertGreater(len(json.loads(DEFAULT_FLOOR.read_text())["patch_ids"]), 1)


class MihomoUpgradeCompatWorkflowTests(unittest.TestCase):
    def test_source_attestation_fetches_provenance_for_the_checker_before_flutter(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(TOOLS_CHECKOUT_PATH, workflow)
        self.assertIn(COMMAND, workflow)
        self.assertLess(
            workflow.index("Verify mihomo fork ledger upgrade compatibility"),
            workflow.index("Set up Flutter"),
        )
        step = workflow.split("Checkout mihomo upgrade-compat tools", 1)[1].split(
            "\n      - name:", 1
        )[0]
        self.assertIn("persist-credentials: false", step)
        self.assertNotIn("ssh-key:", step)
        self.assertNotIn("token:", step)
        self.assertNotIn("secrets.", step)

    def test_source_attestation_contract_counts_the_early_tools_checkout(self) -> None:
        source = SOURCE_CONTRACT.read_text(encoding="utf-8")
        self.assertIn('("source_contract", 2)', source)

    def test_policy_ci_source_dispatch_runs_the_same_checker(self) -> None:
        policy = POLICY.read_text(encoding="utf-8")
        self.assertIn(
            "if: github.event_name == 'workflow_dispatch' && inputs.yuelink_sha != ''",
            policy,
        )
        job = policy.split("source_version:", 1)[1]
        self.assertIn(TOOLS_CHECKOUT_PATH, job)
        self.assertIn(COMMAND, job)
        checkout = job.split("Checkout requested YueLink commit and release tags", 1)[1].split(
            "\n      - name:", 1
        )[0]
        self.assertIn("submodules: recursive", checkout)
        tools = job.split("Checkout mihomo upgrade-compat tools", 1)[1].split(
            "\n      - name:", 1
        )[0]
        self.assertIn("persist-credentials: false", tools)
        self.assertNotIn("ssh-key:", tools)
        self.assertNotIn("token:", tools)
        self.assertNotIn("secrets.", tools)


if __name__ == "__main__":
    unittest.main()
