"""The release build caches compiled Go core libraries by source and platform.

A cache hit skips compile. install always runs. The mihomo commit does not
cover yuelink-owned service helpers or the desktop feed overlay. The dial
gate stays independent of this cache.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


def repository_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / ".github/workflows/build.yml").is_file():
            return candidate
    raise RuntimeError("workflow not found")


ROOT = repository_root()
BUILD = ROOT / ".github/workflows/build.yml"


def pipeline() -> str:
    from scripts.check_public_build import composed_text

    return composed_text(BUILD)

LIBRARY_PATHS = (
    "core/build/android-arm64/libclash.so",
    "core/build/android-arm/libclash.so",
    "core/build/android-x86_64/libclash.so",
    "core/build/ios-arm64/libclash.a",
    "core/build/ios-arm64/libclash.h",
    "core/build/macos-arm64/libclash.dylib",
    "core/build/macos-amd64/libclash.dylib",
    "service/build/macos-arm64/yuelink-service-helper",
    "service/build/macos-amd64/yuelink-service-helper",
    "service/build/macos-arm64/yuelink-mihomo",
    "service/build/macos-amd64/yuelink-mihomo",
    "core/build/windows-amd64/libclash.dll",
    "service/build/windows-amd64/yuelink-service-helper.exe",
    "service/build/windows-amd64/yuelink-mihomo.exe",
    "core/build/linux-amd64/libclash.so",
    "service/build/linux-amd64/yuelink-service-helper",
    "service/build/linux-amd64/yuelink-mihomo",
)


def step_block(workflow: str, name: str) -> str:
    marker = f"      - name: {name}\n"
    found = workflow.count(marker)
    if found != 1:
        raise AssertionError(f"{name} appears {found} times")
    rest = workflow[workflow.index(marker) :]
    nxt = rest.find("\n      - name:", 1)
    if nxt < 0:
        raise AssertionError(f"{name} has no following step")
    return rest[:nxt]


def step_script(workflow: str, name: str) -> str:
    block = step_block(workflow, name)
    if block.count("run: |\n") != 1:
        raise AssertionError(f"{name} has no single run block")
    body = block.split("run: |\n", 1)[1]
    lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("          "):
            lines.append(line[10:])
        elif line.strip() == "":
            lines.append("")
        else:
            break
    return "\n".join(lines).strip() + "\n"


def dial_gate_needs(workflow: str) -> list[str]:
    marker = "\n  dial_gate:\n"
    if workflow.count(marker) != 1:
        return []
    lines: list[str] = []
    for line in workflow.split(marker, 1)[1].splitlines():
        if (
            line.startswith("  ")
            and not line.startswith("   ")
            and line.rstrip().endswith(":")
            and not line.startswith("  #")
        ):
            break
        lines.append(line)
    return [line.strip() for line in lines if line.strip().startswith("needs:")]


def contract_issues(workflow: str) -> list[str]:
    issues: list[str] = []
    try:
        identity = step_script(workflow, "Record Go core cache identity")
        cache = step_block(workflow, "Restore built Go core")
        build = step_block(workflow, "Build & install Go core")
        build_script = step_script(workflow, "Build & install Go core")
    except AssertionError as exc:
        return [str(exc)]

    for required in (
        '[[ "$sha" =~ ^[0-9a-f]{40}$ ]]',
        "Go core cache inputs are dirty",
        "git diff --cached --quiet -- setup.dart setup_install.dart service core",
        "git -C core/mihomo diff --cached --quiet",
        "git ls-files --others --exclude-standard",
        "Go core cache inputs have untracked files",
        "git ls-files -s -- setup.dart setup_install.dart service core",
        '*"${tab}core/mihomo"|*"${tab}core/mihomo/"*',
        '*"${tab}core/build/"*',
        '*"${tab}service/build/"*',
        "setup.dart missing from Go core cache inputs",
        "setup_install.dart missing from Go core cache inputs",
        "CORE_SHA=$sha",
        "CORE_ARCH_SCOPE=$arch_scope",
        "CORE_INPUTS_SHA=$inputs_sha",
        "windows|linux) arch_scope=amd64",
        "android|ios|macos) arch_scope=all",
    ):
        if required not in identity:
            issues.append(f"identity script lost {required}")

    if "uses: actions/cache/restore@" in cache:
        issues.append("go core cache must save on a miss, not only restore")
    if "continue-on-error: true" not in cache:
        issues.append("a cache outage must still compile")
    if "id: go_core_build" not in cache:
        issues.append("build step cannot read the cache result")
    if "restore-keys:" in cache:
        issues.append("go core cache must not restore a different core identity")
    pin = ""
    for line in cache.splitlines():
        stripped = line.strip()
        if stripped.startswith("uses: actions/cache@"):
            pin = stripped.split("@", 1)[1].split()[0]
    if len(pin) != 40 or any(char not in "0123456789abcdef" for char in pin):
        issues.append("go core cache action is not pinned to 40 hex")
    elif workflow.count(f"uses: actions/cache@{pin}") < 2:
        issues.append("go core cache pin differs from the existing cache pin")
    path_block = cache.split("path: |", 1)[1].split("key:", 1)[0] if "path: |" in cache and "key:" in cache else ""
    for required_path in ("core/build", "service/build"):
        if required_path not in path_block.splitlines():
            # Indentation: the path entries are separate lines.
            if f"\n            {required_path}\n" not in cache:
                issues.append(f"cache path missing {required_path}")
    key_line = ""
    for line in cache.splitlines():
        if "key:" in line:
            key_line = line
    for required in (
        "${{ matrix.platform }}",
        "${{ runner.os }}",
        "${{ runner.arch }}",
        "${{ env.GO_VERSION }}",
        "${{ env.CORE_ARCH_SCOPE }}",
        "${{ env.CORE_SHA }}",
        "${{ env.CORE_INPUTS_SHA }}",
        "yue-gocore-v1-",
    ):
        if required not in key_line:
            issues.append(f"cache key missing {required}")
    ndks = []
    marker = 'sdkmanager "ndk;'
    start = 0
    while True:
        found = workflow.find(marker, start)
        if found < 0:
            break
        end = workflow.find('"', found + len(marker))
        ndks.append(workflow[found + len(marker) : end])
        start = end + 1
    if len(set(ndks)) != 1 or not ndks[0]:
        issues.append("android ndk pin is missing or inconsistent")
    elif f"ndk-{ndks[0]}" not in key_line:
        issues.append("go core cache key does not include the android ndk pin")
    xcode_marker = "test \"$version\" = 'Xcode "
    xcodes = []
    start = 0
    while True:
        found = workflow.find(xcode_marker, start)
        if found < 0:
            break
        end = workflow.find("'", found + len(xcode_marker))
        xcodes.append(workflow[found + len(xcode_marker) : end])
        start = end + 1
    if len(set(xcodes)) != 1 or not xcodes[0]:
        issues.append("xcode pin is missing or inconsistent")
    elif f"xcode-{xcodes[0]}" not in key_line:
        issues.append("go core cache key does not include the xcode pin")

    if 'GO_CORE_CACHE_HIT: ${{ steps.go_core_build.outputs.cache-hit }}' not in build:
        issues.append("build step does not read the cache hit")
    if '[ "${GO_CORE_CACHE_HIT:-}" != "true" ]' not in build_script:
        issues.append("a cache hit does not skip compile")
    if "elif libraries_present; then" not in build_script:
        issues.append("a cache hit does not check restored libraries")
    for library in LIBRARY_PATHS:
        if library not in build_script:
            issues.append(f"restored library check missing {library}")
    install = 'dart setup.dart install -p "$PLATFORM"'
    if build_script.count(install) != 1:
        issues.append("install must run exactly once")
    else:
        fi_at = build_script.rfind("\nfi\n")
        install_at = build_script.find(install)
        if fi_at < 0 or install_at < fi_at:
            issues.append("install must stay outside the compile skip")
    if dial_gate_needs(workflow) != ["needs: [plan]"]:
        issues.append("dial gate needs changed")
    if "value: ${{ jobs.source_binding.outputs.source_commit }}" not in workflow:
        issues.append("plan does not publish the attested source commit")
    return issues


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.email=go-core-cache@example.com",
            "-c",
            "user.name=Go Core Cache Test",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def init_source(root: Path) -> None:
    git(root, "init", "-q")
    (root / "setup.dart").write_text("builder\n", encoding="utf-8")
    (root / "setup_install.dart").write_text("install\n", encoding="utf-8")
    (root / "service").mkdir()
    (root / "service" / "main.go").write_text("package service\n", encoding="utf-8")
    (root / "core").mkdir()
    (root / "core" / "hub.go").write_text("package main\n", encoding="utf-8")
    (root / "core" / "connections_feed.go").write_text("package main\n", encoding="utf-8")
    (root / "core" / "proxies_snapshot.go").write_text("package main\n", encoding="utf-8")
    mihomo = root / "core" / "mihomo"
    mihomo.mkdir()
    git(mihomo, "init", "-q")
    (mihomo / "go.mod").write_text("module mihomo\n", encoding="utf-8")
    git(mihomo, "add", "go.mod")
    git(mihomo, "commit", "-q", "-m", "mihomo")
    git(
        root,
        "add",
        "setup.dart",
        "setup_install.dart",
        "service/main.go",
        "core/hub.go",
        "core/connections_feed.go",
        "core/proxies_snapshot.go",
        "core/mihomo",
    )
    git(root, "commit", "-q", "-m", "parent")


def run_bash(script: str, root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    merged = dict(os.environ)
    merged.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        cwd=root,
        env=merged,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


def read_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, value = line.split("=", 1)
        values[key] = value
    return values


class GoCoreCacheContractTests(unittest.TestCase):
    def test_workflow_contract_holds(self) -> None:
        self.assertEqual(contract_issues(pipeline()), [])

    def test_mihomo_exclusion_is_load_bearing(self) -> None:
        text = pipeline()
        broken = text.replace(
            '*"${tab}core/mihomo"|*"${tab}core/mihomo/"*',
            '*"${tab}core/not-the-submodule"',
            1,
        )
        self.assertNotEqual(broken, text)
        self.assertIn(
            "identity script lost",
            " ".join(contract_issues(broken)),
        )

    def test_cache_hit_skip_is_load_bearing(self) -> None:
        text = pipeline()
        broken = text.replace(
            '[ "${GO_CORE_CACHE_HIT:-}" != "true" ]',
            '[ "${GO_CORE_CACHE_HIT:-}" != "never" ]',
            1,
        )
        self.assertNotEqual(broken, text)
        self.assertIn("a cache hit does not skip compile", contract_issues(broken))

    def test_install_outside_the_skip_is_load_bearing(self) -> None:
        text = pipeline()
        broken = text.replace('          dart setup.dart install -p "$PLATFORM"\n', "", 1)
        self.assertNotEqual(broken, text)
        self.assertIn("install must run exactly once", contract_issues(broken))

    def test_dial_gate_needs_are_load_bearing(self) -> None:
        text = pipeline()
        broken = text.replace(
            "  dial_gate:\n    name: Dial gate (${{ matrix.name }})\n    needs: [plan]\n",
            "  dial_gate:\n    name: Dial gate (${{ matrix.name }})\n    needs: [plan, preflight]\n",
            1,
        )
        self.assertNotEqual(broken, text)
        self.assertIn("dial gate needs changed", contract_issues(broken))

    def test_core_sha_in_the_key_is_load_bearing(self) -> None:
        text = pipeline()
        broken = text.replace("${{ env.CORE_SHA }}", "${{ env.NOT_THE_CORE }}", 1)
        self.assertNotEqual(broken, text)
        self.assertIn("cache key missing ${{ env.CORE_SHA }}", contract_issues(broken))

    def test_scripts_parse(self) -> None:
        workflow = pipeline()
        for name in ("Record Go core cache identity", "Build & install Go core"):
            result = subprocess.run(
                ["bash", "-n"],
                input=step_script(workflow, name),
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_identity_separates_mihomo_from_yuelink_sources(self) -> None:
        workflow = pipeline()
        script = step_script(workflow, "Record Go core cache identity")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            init_source(root)
            linux_env = self.identity(script, root, "linux")
            self.assertEqual(linux_env["CORE_ARCH_SCOPE"], "amd64")
            self.assertEqual(len(linux_env["CORE_SHA"]), 40)
            self.assertEqual(len(linux_env["CORE_INPUTS_SHA"]), 40)
            self.assertEqual(
                self.identity(script, root, "android")["CORE_ARCH_SCOPE"],
                "all",
            )
            self.assertEqual(
                self.identity(script, root, "windows")["CORE_ARCH_SCOPE"],
                "amd64",
            )

            service = root / "service" / "main.go"
            service.write_text("package service\nfunc extra() {}\n", encoding="utf-8")
            git(root, "add", "service/main.go")
            git(root, "commit", "-q", "-m", "service")
            changed_service = self.identity(script, root, "linux")
            self.assertEqual(changed_service["CORE_SHA"], linux_env["CORE_SHA"])
            self.assertNotEqual(
                changed_service["CORE_INPUTS_SHA"],
                linux_env["CORE_INPUTS_SHA"],
            )

            mihomo = root / "core" / "mihomo" / "go.mod"
            mihomo.write_text("module mihomo\n// moved\n", encoding="utf-8")
            git(mihomo.parent, "add", "go.mod")
            git(mihomo.parent, "commit", "-q", "-m", "mihomo moved")
            git(root, "add", "core/mihomo")
            git(root, "commit", "-q", "-m", "gitlink")
            changed_core = self.identity(script, root, "linux")
            self.assertNotEqual(changed_core["CORE_SHA"], linux_env["CORE_SHA"])
            self.assertEqual(
                changed_core["CORE_INPUTS_SHA"],
                changed_service["CORE_INPUTS_SHA"],
            )

            broken = script.replace(
                '*"${tab}core/mihomo"|*"${tab}core/mihomo/"*',
                '*"${tab}core/not-the-submodule"',
                1,
            )
            leaked = self.identity(broken, root, "linux")
            self.assertNotEqual(
                leaked["CORE_INPUTS_SHA"],
                changed_core["CORE_INPUTS_SHA"],
            )

    def test_identity_rejects_a_non_hex_or_dirty_tree(self) -> None:
        workflow = pipeline()
        script = step_script(workflow, "Record Go core cache identity")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            init_source(root)
            real_git = shutil.which("git")
            self.assertIsNotNone(real_git)
            wrapper_dir = root / "bin"
            wrapper_dir.mkdir()
            wrapper = wrapper_dir / "git"
            wrapper.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "-C" ] && [ "$2" = "core/mihomo" ] && [ "$3" = "rev-parse" ]; then\n'
                "  printf '%s\\n' 'not-a-sha'\n"
                "  exit 0\n"
                "fi\n"
                f'exec {real_git} "$@"\n',
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
            env_file = root / "github-env"
            result = run_bash(
                script,
                root,
                {
                    "PATH": str(wrapper_dir) + os.pathsep + os.environ["PATH"],
                    "PLATFORM": "linux",
                    "GITHUB_ENV": str(env_file),
                },
            )
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("not 40 hex", result.stdout + result.stderr)
            self.assertFalse(env_file.exists())

            (root / "setup.dart").write_text("worktree-only\n", encoding="utf-8")
            worktree = run_bash(
                script,
                root,
                {"PLATFORM": "linux", "GITHUB_ENV": str(root / "worktree-env")},
            )
            self.assertEqual(worktree.returncode, 0, worktree.stdout + worktree.stderr)
            git(root, "checkout", "HEAD", "--", "setup.dart")

            (root / "setup.dart").write_text("staged\n", encoding="utf-8")
            git(root, "add", "setup.dart")
            dirty = run_bash(
                script,
                root,
                {"PLATFORM": "linux", "GITHUB_ENV": str(root / "missing-env")},
            )
            self.assertNotEqual(dirty.returncode, 0, dirty.stdout + dirty.stderr)
            self.assertIn("dirty", dirty.stdout + dirty.stderr)

            git(root, "checkout", "HEAD", "--", "setup.dart")
            (root / "core" / "extra.go").write_text("package main\n", encoding="utf-8")
            untracked = run_bash(
                script,
                root,
                {"PLATFORM": "linux", "GITHUB_ENV": str(root / "missing-env-2")},
            )
            self.assertNotEqual(untracked.returncode, 0, untracked.stdout + untracked.stderr)
            self.assertIn("untracked", untracked.stdout + untracked.stderr)

    def test_hit_skips_compile_and_install_always_runs(self) -> None:
        workflow = pipeline()
        script = step_script(workflow, "Build & install Go core")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bindir = root / "bin"
            bindir.mkdir()
            log = root / "dart-log"
            dart = bindir / "dart"
            dart.write_text(
                "#!/bin/sh\n"
                f'printf "%s\\n" "$*" >> "{log}"\n'
                "exit 0\n",
                encoding="utf-8",
            )
            dart.chmod(0o755)
            env = {
                "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
                "PLATFORM": "linux",
                "CORE_ARCH_SCOPE": "amd64",
            }
            for relative in (
                "core/build/linux-amd64/libclash.so",
                "service/build/linux-amd64/yuelink-service-helper",
                "service/build/linux-amd64/yuelink-mihomo",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("library\n", encoding="utf-8")
            hit = run_bash(script, root, {**env, "GO_CORE_CACHE_HIT": "true"})
            self.assertEqual(hit.returncode, 0, hit.stdout + hit.stderr)
            self.assertEqual(
                log.read_text(encoding="utf-8").splitlines(),
                ['setup.dart install -p linux'],
            )
            self.assertIn("skipping compile", hit.stdout)

            log.unlink()
            miss = run_bash(script, root, {**env, "GO_CORE_CACHE_HIT": "false"})
            self.assertEqual(miss.returncode, 0, miss.stdout + miss.stderr)
            self.assertEqual(
                log.read_text(encoding="utf-8").splitlines(),
                [
                    "setup.dart build -p linux -a amd64",
                    "setup.dart install -p linux",
                ],
            )

            log.unlink()
            (root / "core/build/linux-amd64/libclash.so").unlink()
            restored = run_bash(script, root, {**env, "GO_CORE_CACHE_HIT": "true"})
            self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
            self.assertIn("did not restore", restored.stdout)
            self.assertEqual(
                log.read_text(encoding="utf-8").splitlines()[0],
                "setup.dart build -p linux -a amd64",
            )

            log.unlink()
            unknown = run_bash(
                script,
                root,
                {**env, "PLATFORM": "plan9", "GO_CORE_CACHE_HIT": "true"},
            )
            self.assertNotEqual(unknown.returncode, 0)
            self.assertFalse(log.exists())

            scope = run_bash(
                script,
                root,
                {
                    "PATH": env["PATH"],
                    "PLATFORM": "linux",
                    "GO_CORE_CACHE_HIT": "false",
                },
            )
            self.assertNotEqual(scope.returncode, 0)
            self.assertFalse(log.exists())

    def test_yuelink_sources_match_the_restored_libraries(self) -> None:
        setup = ROOT / "setup.dart"
        if not setup.is_file():
            self.skipTest("builder sources live in the yuelink checkout")
        setup_text = setup.read_text(encoding="utf-8")
        install_text = (ROOT / "setup_install.dart").read_text(encoding="utf-8")
        workflow = pipeline()
        for library in LIBRARY_PATHS:
            relative = library.split("/", 2)[2]
            self.assertIn(library, workflow)
            if relative == "ios-arm64/libclash.h":
                self.assertIn("libclash.h", install_text)
            else:
                self.assertIn(relative, setup_text)
        for name in ("connections_feed.go", "proxies_snapshot.go"):
            self.assertTrue((ROOT / "core" / name).is_file())

    def identity(self, script: str, root: Path, platform: str) -> dict[str, str]:
        env_file = root / f"github-env-{platform}"
        if env_file.exists():
            env_file.unlink()
        result = run_bash(
            script,
            root,
            {"PLATFORM": platform, "GITHUB_ENV": str(env_file)},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return read_env(env_file)


if __name__ == "__main__":
    unittest.main()
