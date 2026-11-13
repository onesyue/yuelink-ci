"""Release caches are restored on tags and saved only from master.

GitHub scopes a cache to the ref that saved it. Every release runs on a new
tag ref, so a cache a release build saves is unreachable from the next
release. The build therefore only restores, and warm-release-caches.yml saves
the same identities on master after green source attestation. The warm-up
derives every key, path, pin and the identity script from the build workflow
at the same commit, so the two cannot drift silently.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release_cache_plan as plan  # noqa: E402

WORKFLOWS = ROOT / ".github/workflows"
BUILD_MATRIX = (WORKFLOWS / "build-matrix.yml").read_text(encoding="utf-8")
BUILD = (WORKFLOWS / "build.yml").read_text(encoding="utf-8")
WARM = (WORKFLOWS / "warm-release-caches.yml").read_text(encoding="utf-8")
PLAN = (WORKFLOWS / "plan.yml").read_text(encoding="utf-8")
SHA = "a" * 40
INPUTS = "b" * 40


def context(platform: str, runner_os: str, arch: str) -> dict[str, str]:
    return {
        "matrix.platform": platform,
        "runner.os": runner_os,
        "runner.arch": arch,
        "runner.temp": "/tmp/runner",
        "env.LINUX_MEDIA_INPUTS_SHA": "d" * 40,
        "github.sha": "c" * 40,
        "env.GO_VERSION": plan.env_pin(BUILD_MATRIX, "GO_VERSION"),
        "env.CORE_SHA": SHA,
        "env.CORE_ARCH_SCOPE": "amd64" if platform in ("linux", "windows") else "all",
        "env.CORE_INPUTS_SHA": INPUTS,
    }


class ReleaseBuildRestoresOnly(unittest.TestCase):
    def test_build_matrix_never_saves_a_tag_scoped_cache(self) -> None:
        self.assertNotRegex(BUILD_MATRIX, r"uses: actions/cache@")
        saves = re.findall(r"(?ms)^      - name: ([^\n]+)\n(?:(?!^      - ).)*?uses: actions/cache/save@", BUILD_MATRIX)
        self.assertEqual(saves, ["Save Xcode compilation cache"])
        block = [b for n, b in plan.steps(BUILD_MATRIX) if n == "Save Xcode compilation cache"][0]
        self.assertIn("if: github.ref == 'refs/heads/master' && ", block)

    def test_release_gate_order_is_unchanged(self) -> None:
        self.assertIn("needs: [plan, validate]", BUILD)
        self.assertIn("needs: [plan, build, validate, dial_gate, dial_gate_summary]", BUILD)


class WarmUpDerivesTheBuildIdentity(unittest.TestCase):
    def test_keys_are_the_build_matrix_keys_for_every_platform(self) -> None:
        ndk = plan.toolchain(BUILD_MATRIX)["ndk"]
        xcode = plan.toolchain(BUILD_MATRIX)["xcode"]
        go = plan.env_pin(BUILD_MATRIX, "GO_VERSION")
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pubspec.lock").write_text("packages: {}\n", encoding="utf-8")
            for platform, runner_os, arch in (("android", "Linux", "X64"), ("linux", "Linux", "X64"),
                                              ("windows", "Windows", "X64"), ("ios", "macOS", "ARM64"),
                                              ("macos", "macOS", "ARM64")):
                ctx = context(platform, runner_os, arch)
                keys = plan.cache_plan(BUILD_MATRIX, ctx, Path(tmp))
                scope = ctx["env.CORE_ARCH_SCOPE"]
                self.assertEqual(
                    keys["gocore"]["key"],
                    f"yue-gocore-v1-{platform}-{runner_os}-{arch}-{go}-{scope}-{SHA}-{INPUTS}-ndk-{ndk}-xcode-{xcode}",
                )
                self.assertEqual(keys["gocore"]["path"], "core/build\nservice/build")
                self.assertNotIn("restore-keys", keys["gocore"])
                self.assertIn(plan.hash_files(Path(tmp, "pubspec.lock")), keys["pub"]["key"])
                sep = "\\" if runner_os == "Windows" else "/"
                self.assertIn(f"go{sep}pkg{sep}mod", keys["gomod"]["path"])


    def test_platform_cache_keys_paths_match_canonical_build_and_warm_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for platform in ("android", "linux", "ios", "macos", "windows"):
                keys = plan.cache_plan(BUILD_MATRIX, context(platform, "Linux", "X64"), Path(tmp))
                for logical, (target, prefix) in plan.PLATFORM_CACHES.items():
                    self.assertEqual(logical in keys, platform == target)
                    if platform != target:
                        continue
                    block = next(b for n, b in plan.steps(BUILD_MATRIX) if n == prefix)
                    for field in ("key", "path"):
                        self.assertEqual(keys[logical][field], plan.evaluate(
                            plan.field(block, field), context(platform, "Linux", "X64"), Path(tmp)))
                        expression = "${{ steps.cache_plan.outputs." + logical + "_" + field + " }}"
                        self.assertEqual(WARM.count(expression), 2)  # restore + save
            changed = BUILD_MATRIX.replace("yue-linux-media-v1", "yue-linux-media-v2")
            self.assertNotEqual(plan.cache_plan(changed, context("linux", "Linux", "X64"), Path(tmp)),
                                plan.cache_plan(BUILD_MATRIX, context("linux", "Linux", "X64"), Path(tmp)))
            for prefix in ("Restore Gradle dependency caches", "Restore Linux native media"):
                platform = "android" if "Gradle" in prefix else "linux"
                for changed in (BUILD_MATRIX.replace(prefix, "Renamed cache"),
                                BUILD_MATRIX.replace(prefix, prefix + "\n        if: unexpected()")):
                    with self.assertRaises(plan.PlanError):
                        plan.cache_plan(changed, context(platform, "Linux", "X64"), Path(tmp))

    def test_linux_warm_runs_the_canonical_recipes(self) -> None:
        for name in ("Record Linux media identity", "Install Linux build dependencies",
                     "Build source-locked Linux native media"):
            self.assertIn("step-script '" + name + "'", WARM)
            self.assertTrue(plan.identity_script(BUILD_MATRIX, name).strip())
        self.assertIn('MEDIA_CACHE_HIT: ${{ steps.linux_media_cache.outputs.cache-hit }}', BUILD_MATRIX)
        # The binary cache never skips final closure or playback validation.
        self.assertIn('python3 scripts/linux_media/package.py', BUILD_MATRIX)
        self.assertIn('linux-packaged-playback', BUILD_MATRIX)

    def test_hash_files_matches_github_for_one_file(self) -> None:
        # sha256 over the per-file sha256 digest (actions/runner hashFiles).
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "pubspec.lock")
            path.write_bytes(b"x")
            import hashlib
            self.assertEqual(plan.hash_files(path),
                             hashlib.sha256(hashlib.sha256(b"x").digest()).hexdigest())
            self.assertEqual(plan.hash_files(Path(tmp, "absent")), "")

    def test_an_unknown_expression_fails_closed(self) -> None:
        drifted = BUILD_MATRIX.replace("${{ env.CORE_INPUTS_SHA }}", "${{ env.SOMETHING_NEW }}", 1)
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(plan.PlanError):
            plan.cache_plan(drifted, context("linux", "Linux", "X64"), Path(tmp))

    def test_a_renamed_or_duplicated_cache_step_fails_closed(self) -> None:
        renamed = BUILD_MATRIX.replace("- name: Restore built Go core", "- name: Restore core", 1)
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(plan.PlanError):
            plan.cache_plan(renamed, context("linux", "Linux", "X64"), Path(tmp))
        with self.assertRaises(plan.PlanError):
            plan.identity_script(BUILD_MATRIX.replace("- name: Record Go core cache identity",
                                                      "- name: Record identity", 1))

    def test_identity_script_is_the_build_step_verbatim(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("go_core_contract", ROOT / "tests/test_go_core_cache.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        step_script = module.step_script
        self.assertEqual(plan.identity_script(BUILD_MATRIX),
                         step_script(BUILD_MATRIX, plan.IDENTITY_STEP))

    def test_plan_derives_matrix_and_pins(self) -> None:
        rows = plan.platform_matrix(PLAN)
        self.assertEqual({row["platform"] for row in rows}, {"android", "ios", "macos", "windows", "linux"})
        pins = plan.toolchain(BUILD_MATRIX)
        self.assertEqual(pins["go_version"], plan.env_pin(BUILD_MATRIX, "GO_VERSION"))
        self.assertEqual(pins["flutter_version"], plan.env_pin(BUILD_MATRIX, "FLUTTER_VERSION"))
        self.assertEqual(plan.source_sha("Source attestation " + SHA, ""), SHA)
        for bad in ("Source attestation " + SHA + " extra", "Build YueLink", ""):
            with self.assertRaises(plan.PlanError):
                plan.source_sha(bad, "")

    def test_plan_command_writes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "out")
            env = dict(os.environ, GITHUB_OUTPUT=str(out))
            subprocess.run([sys.executable, str(ROOT / "scripts/release_cache_plan.py"), "plan",
                            "--title", "Source attestation " + SHA], env=env, check=True)
            text = out.read_text(encoding="utf-8")
            self.assertIn(f"\n{SHA}\n", text)
            matrix = text.split("matrix<<", 1)[1].split("\n", 2)[1]
            self.assertEqual(json.loads(matrix), plan.platform_matrix(PLAN))


class WarmUpWorkflowBoundary(unittest.TestCase):
    def test_runs_only_after_green_master_attestation(self) -> None:
        self.assertIn("workflows: [Source attestation]", WARM)
        self.assertIn("branches: [master]", WARM)
        for guard in ("github.event.workflow_run.conclusion == 'success'",
                      "github.event.workflow_run.head_branch == 'master'",
                      "github.event.workflow_run.event == 'workflow_dispatch'"):
            self.assertIn(guard, WARM)

    def test_cannot_publish_sign_or_reach_release_secrets(self) -> None:
        self.assertNotIn("environment:", WARM)
        self.assertEqual(set(re.findall(r"secrets\.([A-Z0-9_]+)", WARM)), {"SRC_DEPLOY_KEY"})
        self.assertIn("permissions:\n  contents: read\n", WARM)
        for forbidden in ("R2_", "aws s3", "id-token", "attestations: write", "contents: write"):
            self.assertNotIn(forbidden, WARM)

    def test_uses_derived_keys_and_the_build_action_pins(self) -> None:
        for step in ("python3 .cache-tools/scripts/release_cache_plan.py identity-script",
                     "python3 .cache-tools/scripts/release_cache_plan.py keys"):
            self.assertIn(step, WARM)
        self.assertNotIn("yue-gocore-v1", WARM)
        build_pins = set(re.findall(r"actions/cache/restore@([0-9a-f]{40})", BUILD_MATRIX))
        warm_pins = set(re.findall(r"actions/cache/(?:restore|save)@([0-9a-f]{40})", WARM))
        self.assertEqual(warm_pins, build_pins)
        for name in ("setup-go", "checkout"):
            self.assertEqual(set(re.findall(rf"actions/{name}@([0-9a-f]{{40}})", WARM)),
                             set(re.findall(rf"actions/{name}@([0-9a-f]{{40}})", BUILD_MATRIX)))


    def test_every_explicit_cache_save_requires_master(self) -> None:
        dial = (WORKFLOWS / "dial-gate.yml").read_text()
        count = 0
        for workflow in (WARM, BUILD_MATRIX, dial):
            for name, block in plan.steps(workflow):
                if "uses: actions/cache/save@" in block:
                    count += 1
                    guard = plan.field(block, "if")
                    self.assertTrue(guard.startswith("github.ref == 'refs/heads/master' && "), name)
                    self.assertNotIn("||", guard)
                    for nonmaster in ("refs/pull/1/merge", "refs/tags/v1.3.178", "refs/heads/feature"):
                        mutated = guard.replace("refs/heads/master", nonmaster)
                        self.assertFalse(mutated.startswith("github.ref == 'refs/heads/master' && "))
        self.assertEqual(count, 5)
        self.assertNotRegex(dial, r"uses: actions/cache@")
        self.assertIn("key: ${{ steps.dial_kernel_cache.outputs.cache-primary-key }}", dial)

    def test_gradle_warms_even_when_core_was_cached(self) -> None:
        for name in ("Set up Flutter", "Restore shared pub dependencies"):
            block = next(b for n, b in plan.steps(WARM) if n == name)
            self.assertIn("steps.gradle.outputs.cache-hit != 'true'", plan.field(block, "if"))
        init = (ROOT / "scripts/warm_gradle.init.gradle").read_text()
        self.assertIn("tasks.register('warmDependencyCache')", init)
        self.assertIn("'releaseCompileClasspath', 'releaseRuntimeClasspath'", init)
        self.assertIn('configuration.incoming.artifactView', init)
        self.assertIn('ModuleComponentIdentifier', init)
        self.assertNotIn('configuration.resolve()', init)
        self.assertNotIn('assemble', init)
        self.assertNotIn('KEYSTORE', WARM)

    def test_gradle_init_skips_included_builds(self) -> None:
        # 2026-11-13 run 43421827887: the init script ran inside Flutter's
        # included plugin-loader build ':gradle' and failed on project(':app').
        init = (ROOT / "scripts/warm_gradle.init.gradle").read_text()
        self.assertIn('if (gradle.parent != null)', init)
        self.assertIn("findProject(':app')", init)
        self.assertNotIn("rootProject.project(':app')", init)
        self.assertLess(init.index('gradle.parent != null'), init.index("findProject(':app')"))

    def test_compile_commands_match_the_build(self) -> None:
        for command in ('dart setup.dart build -p "$PLATFORM" -a amd64', 'dart setup.dart build -p "$PLATFORM"'):
            self.assertIn(command, BUILD_MATRIX)
            self.assertIn(command, WARM)


if __name__ == "__main__":
    unittest.main()
