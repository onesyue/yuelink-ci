#!/usr/bin/env python3
"""Nonpublishing W4.12 Android target-SDK qualification; never emit private logs."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OVERRIDE = "YUE_ANDROID_TARGET_SDK_DRY_RUN"
MARKER = "YUE_TARGET_SDK_DSL="
INVALID = "YUE_TARGET_SDK_INVALID_OVERRIDE"
INIT = r'''import groovy.json.JsonOutput

gradle.beforeProject { project ->
    if (project.path == ':app') {
        project.pluginManager.withPlugin('com.android.application') {
            def override = System.getenv('YUE_ANDROID_TARGET_SDK_DRY_RUN')
            project.extensions.getByName('androidComponents').finalizeDsl { androidDsl ->
                def baseline = androidDsl.defaultConfig.targetSdk
                if (baseline != 36) {
                    throw new GradleException('YUE_TARGET_SDK_UNEXPECTED_PRODUCT_BASELINE')
                }
                def runtime = [gradleVersion: gradle.gradleVersion,
                    javaMajor: JavaVersion.current().majorVersion,
                    maxHeapBytes: Runtime.runtime.maxMemory(),
                    workers: gradle.startParameter.maxWorkerCount,
                    parallel: gradle.startParameter.parallelProjectExecutionEnabled]
                if (override != null && override != '37') {
                    println('YUE_TARGET_SDK_DSL=' + JsonOutput.toJson([
                        project: ':app', originalTarget: baseline,
                        effectiveTarget: baseline, mode: 'invalid', runtime: runtime
                    ]))
                    throw new GradleException('YUE_TARGET_SDK_INVALID_OVERRIDE')
                }
                if (override != null) {
                    androidDsl.defaultConfig.targetSdk = 37
                }
                println('YUE_TARGET_SDK_DSL=' + JsonOutput.toJson([
                    project: ':app', originalTarget: baseline,
                    effectiveTarget: androidDsl.defaultConfig.targetSdk,
                    mode: override == null ? 'baseline' : 'target37', runtime: runtime
                ]))
            }
        }
    }
}
'''
GRADLE_PROPERTIES = """org.gradle.jvmargs=-Xmx3g -XX:MaxMetaspaceSize=768m -XX:ActiveProcessorCount=2
org.gradle.workers.max=2
org.gradle.parallel=false
org.gradle.daemon=false
org.gradle.configuration-cache=false
kotlin.compiler.execution.strategy=in-process
"""


class Rejected(Exception):
    """Only fixed, public-safe error codes may cross the evidence boundary."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise Rejected(code)


def sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def target_from_badging(text: str, expected: int) -> str:
    lines = [line for line in text.splitlines() if line.startswith('targetSdkVersion:')]
    require(len(lines) == 1, 'manifest-target-count')
    match = re.fullmatch(r"targetSdkVersion:'([0-9]+)'", lines[0])
    require(match is not None, 'manifest-target-format')
    require(int(match[1]) == expected, 'manifest-target-mismatch')
    return lines[0]


def dsl_marker(text: str, mode: str) -> dict:
    rows = [line[len(MARKER):] for line in text.splitlines() if line.startswith(MARKER)]
    require(len(rows) == 1, 'dsl-marker-count')
    try:
        value = json.loads(rows[0])
    except ValueError:
        raise Rejected('dsl-marker-json') from None
    expected = {'project': ':app', 'originalTarget': 36,
                'effectiveTarget': 37 if mode == 'target37' else 36, 'mode': mode}
    require(isinstance(value, dict), 'dsl-marker-shape')
    runtime = value.pop('runtime', None)
    require(value == expected, 'dsl-marker-mismatch')
    require(isinstance(runtime, dict) and set(runtime) == {
        'gradleVersion', 'javaMajor', 'maxHeapBytes', 'workers', 'parallel'}, 'dsl-runtime-shape')
    require(runtime['gradleVersion'] == '9.7.1' and runtime['javaMajor'] == '17' and
            runtime['workers'] == 2 and runtime['parallel'] is False and
            type(runtime['maxHeapBytes']) is int and 0 < runtime['maxHeapBytes'] <= 3 * 1024**3,
            'dsl-runtime-budget')
    expected['runtime'] = runtime
    return expected  # Publish the schema, never arbitrary private output.


def child_env(base: dict, mode: str) -> dict:
    env = dict(base)
    env.pop(OVERRIDE, None)
    if mode == 'target37':
        env[OVERRIDE] = '37'
    elif mode == 'invalid':
        env[OVERRIDE] = 'invalid'
    else:
        require(mode == 'baseline', 'unknown-mode')
    return env


class Runner:
    def __init__(self, source: Path, env: dict, receipt: dict):
        self.source, self.env, self.receipt = source, env, receipt

    def command(self, label: str, argv: list[str], *, timeout: int = 120,
                mode: str = 'baseline', allow_failure: bool = False) -> tuple[int, str]:
        # No tee/cat or raw output artifact. Timeout kills the entire process group.
        started = time.monotonic()
        with tempfile.TemporaryFile() as output:
            proc = subprocess.Popen(argv, cwd=self.source, env=child_env(self.env, mode),
                                    stdout=output, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            timed_out = False
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            size = output.tell()
            output.seek(0)
            raw = output.read(32 * 1024 * 1024 + 1)
        self.receipt.setdefault('commands', []).append({
            'label': label, 'exit_code': proc.returncode, 'timed_out': timed_out,
            'seconds': round(time.monotonic() - started, 3), 'output_bytes': size,
            'output_sha256': hashlib.sha256(raw).hexdigest() if size <= len(raw) else None,
        })
        print(f'{label}: exit={proc.returncode} timeout={timed_out}', flush=True)
        require(not timed_out, 'command-timeout')
        require(size <= 32 * 1024 * 1024, 'command-output-limit')
        require(allow_failure or proc.returncode == 0, 'command-failed')
        return proc.returncode, raw.decode('utf-8', errors='replace')

    def git(self, *args: str) -> str:
        return self.command('git-check', ['git', *args])[1].strip()


def bind_source(runner: Runner, expected: str) -> dict:
    require(re.fullmatch('[0-9a-f]{40}', expected) is not None, 'source-sha-format')
    require(runner.git('rev-parse', 'HEAD') == expected, 'source-head-mismatch')
    runner.git('merge-base', '--is-ancestor', expected, 'refs/remotes/origin/master')
    require(not runner.git('status', '--porcelain', '--untracked-files=no',
                           '--ignore-submodules=none'), 'tracked-source-dirty')
    code, _ = runner.command('credential-removal-check', ['git', 'config', '--local',
        '--get-regexp', r'^(core\.sshcommand|http\..*\.extraheader)$'], allow_failure=True)
    require(code == 1, 'retained-checkout-credentials')
    modules = runner.git('submodule', 'status', '--recursive')
    require(modules and all(re.fullmatch(r'[0-9a-f]{40} [A-Za-z0-9_./-]+(?: \([^\n]*\))?',
        line.strip()) for line in modules.splitlines()), 'submodule-identity')
    return {'commit': expected, 'tree': runner.git('rev-parse', 'HEAD^{tree}'),
            'submodules_sha256': hashlib.sha256(modules.encode()).hexdigest(),
            'mihomo_commit': runner.git('-C', 'core/mihomo', 'rev-parse', 'HEAD'),
            'build_gradle_sha256': sha(runner.source / 'android/app/build.gradle'),
            'gradle_properties_sha256': sha(runner.source / 'android/gradle.properties'),
            'pubspec_lock_sha256': sha(runner.source / 'pubspec.lock'),
            'android_settings_sha256': sha(runner.source / 'android/settings.gradle'),
            'gradle_wrapper_sha256': sha(runner.source / 'android/gradle/wrapper/gradle-wrapper.properties')}


def bind_tools(runner: Runner, public_sha: str) -> dict:
    require(re.fullmatch('[0-9a-f]{40}', public_sha) is not None, 'public-sha-format')
    require(runner.git('-C', str(ROOT), 'rev-parse', 'HEAD') == public_sha, 'public-head-mismatch')
    require(not runner.git('-C', str(ROOT), 'status', '--porcelain', '--untracked-files=no'),
            'public-tools-dirty')
    private = (runner.source / '.github/workflows/build.yml').read_text()
    public = (ROOT / '.github/workflows/android-target-sdk-dry-run.yml').read_text()
    for key in ('FLUTTER_VERSION', 'GO_VERSION'):
        pattern = rf"(?m)^  {key}: '([^']+)'$"
        actual, expected = re.findall(pattern, private), re.findall(pattern, public)
        require(len(actual) == len(expected) == 1 and actual == expected, 'tool-pin-mismatch')
    return {'commit': public_sha, 'helper_sha256': sha(Path(__file__)),
            'workflow_sha256': sha(ROOT / '.github/workflows/android-target-sdk-dry-run.yml')}


def resources(source: Path) -> dict:
    mem = re.search(r'(?m)^MemTotal:\s+(\d+) kB$', Path('/proc/meminfo').read_text())
    require(mem is not None, 'memory-observation')
    memory = int(mem[1]) * 1024
    disk = shutil.disk_usage(source).free
    require(memory >= 7 * 1024**3 and disk >= 20 * 1024**3, 'resource-admission')
    return {'memory_bytes': memory, 'disk_free_bytes': disk, 'cpu_count': os.cpu_count(),
            'gradle_workers': 2, 'go_parallelism': 2, 'gradle_heap_mib': 3072,
            'gradle_metaspace_mib': 768, 'hard_cgroup_limit_claimed': False}


def sdk_identity(runner: Runner, flutter_root: Path, sdk: Path) -> dict:
    spec = importlib.util.spec_from_file_location('installer', ROOT / '.github/actions/setup-flutter/install_flutter_sdk.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    expected = installer.receipt('3.47.7', 'Linux', 'X64', installer.RELEASES[('3.47.7', 'Linux')][1])
    require(json.loads((flutter_root / installer.RECEIPT_NAME).read_text()) == expected, 'flutter-sdk-receipt')
    require(Path(shutil.which('flutter') or '').resolve() == (flutter_root / 'bin/flutter').resolve(),
            'flutter-executable-mismatch')
    machine = json.loads(runner.command('flutter-version', ['flutter', '--version', '--machine'])[1])
    require(machine.get('frameworkVersion') == '3.47.7', 'flutter-version-mismatch')
    dart = machine.get('dartSdkVersion', '')
    require(re.fullmatch(r'\d+\.\d+\.\d+', dart) is not None, 'dart-version-format')
    framework = machine.get('frameworkRevision', '')
    require(re.fullmatch('[0-9a-f]{40}', framework) is not None, 'flutter-revision-format')
    go = runner.command('go-version', ['go', 'version'])[1]
    require(re.fullmatch(r'go version go1\.27\.3 linux/amd64\s*', go) is not None, 'go-version-mismatch')
    platform_props = (sdk / 'platforms/android-37.0/source.properties').read_text()
    require(re.search(r'(?m)^AndroidVersion.ApiLevel=37\.0$', platform_props) is not None and
            re.search(r'(?m)^AndroidVersion.PreviewSdkInt=0$', platform_props) is not None, 'android-platform-mismatch')
    ndk = sdk / 'ndk/28.2.13676358'
    require(runner.env.get('ANDROID_NDK') == str(ndk) and
            runner.env.get('ANDROID_NDK_HOME') == str(ndk), 'ndk-path-mismatch')
    require(re.search(r'(?m)^Pkg.Revision\s*=\s*28\.2\.13676358$',
                      (ndk / 'source.properties').read_text()) is not None, 'ndk-version-mismatch')
    aapt = sdk / 'build-tools/36.0.0/aapt2'
    require(aapt.is_file(), 'aapt2-missing')
    return {'flutter_receipt': expected, 'dart_version': dart, 'flutter_revision': framework,
            'go_version': '1.27.3', 'ndk_properties_sha256': sha(ndk / 'source.properties'),
            'android_platform_properties_sha256':
                sha(sdk / 'platforms/android-37.0/source.properties'), 'aapt2_sha256': sha(aapt),
            'aapt2_version_output_sha256': hashlib.sha256(
                runner.command('aapt2-version', [str(aapt), 'version'])[1].encode()).hexdigest()}


def inspect_apk(runner: Runner, apk: Path, aapt: Path, mode: str, output: Path) -> dict:
    require(apk.is_file() and not apk.is_symlink(), 'apk-missing-or-symlink')
    with zipfile.ZipFile(apk) as archive:
        native = 'lib/arm64-v8a/libclash.so'
        require(archive.namelist().count(native) == 1, 'apk-native-member')
        with archive.open(native) as stream:
            native_digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    _, badging = runner.command(f'{mode}-apk-manifest', [str(aapt), 'dump', 'badging', str(apk)])
    line = target_from_badging(badging, 36 if mode == 'baseline' else 37)
    # Preserve only the exact allowlisted raw target line; full private badging is hashed.
    (output / f'{mode}-target.txt').write_text(line + '\n')
    if mode == 'baseline':
        try:
            target_from_badging(badging, 37)
        except Rejected as error:
            require(str(error) == 'manifest-target-mismatch', 'baseline-negative-wrong-failure')
        else:
            raise Rejected('baseline-negative-false-pass')
    return {'apk_sha256': sha(apk), 'target_sdk': 36 if mode == 'baseline' else 37,
            'arm64_native_sha256': native_digest,
            'badging_sha256': hashlib.sha256(badging.encode()).hexdigest(),
            'baseline_rejected_as_37': mode == 'baseline'}


def qualify(runner: Runner, temp: Path, output: Path, flutter_root: Path, sdk: Path) -> None:
    runner.receipt['sdk'] = sdk_identity(runner, flutter_root, sdk)
    home = temp / 'gradle-home'
    home.mkdir()  # Refuse reuse or ambient init scripts, including partial old runs.
    (home / 'init.d').mkdir()
    init = home / 'init.d/target-sdk.gradle'
    init.write_text(INIT)
    (home / 'gradle.properties').write_text(GRADLE_PROPERTIES)
    runner.env.update({'GRADLE_USER_HOME': str(home), 'GOMAXPROCS': '2', 'GOFLAGS': '-p=2'})
    # System-property injection can outrank the reviewed user-home resource properties.
    for key in ('GRADLE_OPTS', 'JAVA_OPTS', 'JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS'):
        require(not runner.env.get(key), 'ambient-jvm-options')
    runner.receipt['init_sha256'] = sha(init)
    runner.receipt['gradle_resource_properties_sha256'] = sha(home / 'gradle.properties')
    runner.command('verify-quic-source', ['python3', 'core/mihomo/third_party/verify_quic_source.py'])
    runner.command('locked-pub-get', ['flutter', 'pub', 'get', '--enforce-lockfile'], timeout=1200)
    runner.command('native-arm64-build', ['dart', 'setup.dart', 'build', '-p', 'android', '-a', 'arm64'], timeout=1200)
    runner.command('native-arm64-install', ['dart', 'setup.dart', 'install', '-p', 'android'])
    native = runner.source / 'android/app/src/main/jniLibs/arm64-v8a/libclash.so'
    require(native.is_file(), 'native-install-missing')
    runner.receipt['installed_native_sha256'] = sha(native)
    apk = runner.source / 'build/app/outputs/flutter-apk/app-debug.apk'
    aapt = sdk / 'build-tools/36.0.0/aapt2'
    build = ['flutter', 'build', 'apk', '--debug', '--target-platform', 'android-arm64']
    for mode in ('baseline', 'invalid', 'target37'):
        require(sha(init) == runner.receipt['init_sha256'], 'init-mutated')
        require(sha(native) == runner.receipt['installed_native_sha256'], 'native-mutated')
        if mode == 'invalid':
            code, text = runner.command('invalid-override', ['android/gradlew', '-p', 'android',
                ':app:help', '--no-daemon', '--console=plain'], timeout=300,
                mode=mode, allow_failure=True)
            require(code != 0 and INVALID in text, 'invalid-override-not-rejected')
            runner.receipt['invalid'] = {'exit_code': code, 'marker': dsl_marker(text, mode),
                                         'rejection': INVALID}
        else:
            # Removing only this known generated final output ensures a fresh produced APK.
            require(not apk.is_symlink(), 'apk-output-symlink')
            apk.unlink(missing_ok=True)
            require(not apk.exists(), 'stale-apk')
            _, text = runner.command(f'{mode}-apk-build', build, timeout=1200, mode=mode)
            marker = dsl_marker(text, mode)
            runner.receipt[mode] = inspect_apk(runner, apk, aapt, mode, output)
            runner.receipt[mode]['marker'] = marker
    # Android packaging may strip the installed ELF. Compare the packaged ELF
    # between variants, rather than requiring equality with the unstripped input.
    require(runner.receipt['baseline']['arm64_native_sha256'] ==
            runner.receipt['target37']['arm64_native_sha256'], 'apk-native-variant-mismatch')
    require(runner.receipt['baseline']['apk_sha256'] != runner.receipt['target37']['apk_sha256'],
            'identical-apk-variants')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('preflight', 'run'))
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--public-sha', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--flutter-root', type=Path)
    parser.add_argument('--android-sdk', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)
    receipt = {'schema': 1, 'status': 'UNKNOWN', 'phase': args.phase,
               'run_id': os.environ.get('GITHUB_RUN_ID'), 'run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT')}
    runner = Runner(args.source.resolve(), dict(os.environ), receipt)
    try:
        receipt['source'] = bind_source(runner, args.source_sha)
        receipt['tools'] = bind_tools(runner, args.public_sha)
        receipt['resources'] = resources(runner.source)
        if args.phase == 'run':
            require(args.flutter_root is not None and args.android_sdk is not None, 'sdk-path-required')
            qualify(runner, args.work, args.output, args.flutter_root, args.android_sdk)
            require(bind_source(runner, args.source_sha) == receipt['source'], 'source-mutated')
            require(bind_tools(runner, args.public_sha) == receipt['tools'], 'public-tools-mutated')
            receipt['tracked_source_unchanged'] = True
        receipt['status'] = 'PASS' if args.phase == 'run' else 'PREFLIGHT_PASS'
    except Rejected as error:
        receipt['status'], receipt['failure'] = 'FAIL', str(error)
    except Exception:
        # Exception messages/tracebacks may contain private compiler or filesystem data.
        receipt['status'], receipt['failure'] = 'FAIL', 'internal-error'
    finally:
        (args.output / f'{args.phase}.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(f"target-sdk-{args.phase}: {receipt['status']}")
    return 0 if receipt['status'] in ('PASS', 'PREFLIGHT_PASS') else 1


if __name__ == '__main__':
    raise SystemExit(main())
