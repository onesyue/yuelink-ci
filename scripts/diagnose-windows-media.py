"""Read-only CI diagnosis; no installer, upload, signing or release path."""

import json
import shutil
import subprocess
import sys
from pathlib import Path


source, work = (Path(arg).resolve() for arg in sys.argv[1:])
work.mkdir(parents=True, exist_ok=False)
native = source / "third_party/media_kit_libs_windows_video/windows/native"
fixture = source / "scripts/native_tests/media/fixtures/fixture-hevc.mov"
original = (source / "scripts/native_tests/media/player_probe.c").read_text()
receipts = []

variants = {
    "baseline": original,
    "fallback-first-error": original.replace(
        'option(mpv, "hwdec", argv[3]);',
        'option(mpv, "hwdec", argv[3]);\n'
        '    option(mpv, "hwdec-software-fallback", "yes");',
    ),
    "gl-readback-reset": original.replace(
        'glReadPixels(0, 0, 64, 64, GL_RGBA, GL_UNSIGNED_BYTE, pixels);',
        'glBindBuffer(0x88EB /* GL_PIXEL_PACK_BUFFER */, 0);\n'
        '                glPixelStorei(GL_PACK_ALIGNMENT, 1);\n'
        '                glPixelStorei(0x0D02 /* GL_PACK_ROW_LENGTH */, 0);\n'
        '                glPixelStorei(0x0D03 /* GL_PACK_SKIP_ROWS */, 0);\n'
        '                glPixelStorei(0x0D04 /* GL_PACK_SKIP_PIXELS */, 0);\n'
        '                glFinish();\n'
        '                glReadPixels(0, 0, 64, 64, GL_RGBA, GL_UNSIGNED_BYTE, pixels);',
    ),
}
assert all(code != original for name, code in variants.items() if name != "baseline")

for name, code in variants.items():
    tree = work / name
    shutil.copytree(source / "scripts/native_tests/media", tree)
    (tree / "player_probe.c").write_text(code)
    build = tree / "build"
    subprocess.run(
        ["cmake", "-S", str(tree), "-B", str(build), "-A", "x64",
         f"-DMPV_ROOT={native / 'libmpv'}", f"-DANGLE_ROOT={native / 'ANGLE'}"],
        check=True,
    )
    subprocess.run(["cmake", "--build", str(build), "--config", "Release", "--parallel", "2"], check=True)
    probe = build / "Release/player-probe.exe"
    for renderer, hwdec in (("gl", "auto-safe"), ("sw", "auto-safe"), ("gl", "no")):
        for iteration in range(10):
            result = subprocess.run(
                [str(probe), str(fixture), renderer, hwdec, "-", "-"],
                capture_output=True, text=True, timeout=30,
            )
            row = {"variant": name, "renderer": renderer, "hwdec": hwdec,
                   "iteration": iteration, "exit": result.returncode,
                   "stdout": result.stdout, "stderr": result.stderr}
            receipts.append(row)
            print(json.dumps(row), flush=True)
            (work / "proof.json").write_text(json.dumps(receipts, indent=2) + "\n")

summary = []
for name in variants:
    for renderer, hwdec in (("gl", "auto-safe"), ("sw", "auto-safe"), ("gl", "no")):
        cases = [r for r in receipts if (r["variant"], r["renderer"], r["hwdec"]) == (name, renderer, hwdec)]
        summary.append({"variant": name, "renderer": renderer, "hwdec": hwdec,
                        "executions": len(cases), "failures": sum(r["exit"] != 0 for r in cases)})
print("DIAGNOSTIC_SUMMARY=" + json.dumps(summary), flush=True)
# Failure observations are evidence, never release approval. This workflow has
# no attestation or publication job and cannot satisfy the source release gate.
sys.exit(1 if any(row["exit"] for row in receipts) else 0)
