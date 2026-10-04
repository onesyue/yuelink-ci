"""Read-only CI diagnosis; no installer, upload, signing or release path."""

import json
import shutil
import subprocess
import sys
import os
from pathlib import Path


source, work = (Path(arg).resolve() for arg in sys.argv[1:])
work.mkdir(parents=True, exist_ok=False)
native = source / "third_party/media_kit_libs_windows_video/windows/native"
fixture = source / "scripts/native_tests/media/fixtures/fixture-hevc.mov"
original = (source / "scripts/native_tests/media/player_probe.c").read_text()
receipts = []

observer = r'''
static double now(void);
static void observe_decode(struct control *c) {
    if (!flag_get(&c->loaded) || now() - c->decoded_sampled_at < 0.08) return;
    c->decoded_sampled_at = now();
    const char *shot[] = {"screenshot-raw", "video", NULL};
    mpv_node result = {0};
    if (mpv_command_ret(c->mpv, shot, &result) < 0) return;
    int width = 0, height = 0, stride = 0;
    mpv_byte_array *data = NULL;
    if (result.format == MPV_FORMAT_NODE_MAP) {
        mpv_node_list *map = result.u.list;
        for (int n = 0; n < map->num; n++) {
            mpv_node *value = &map->values[n];
            if (!strcmp(map->keys[n], "w") && value->format == MPV_FORMAT_INT64) width = (int)value->u.int64;
            if (!strcmp(map->keys[n], "h") && value->format == MPV_FORMAT_INT64) height = (int)value->u.int64;
            if (!strcmp(map->keys[n], "stride") && value->format == MPV_FORMAT_INT64) stride = (int)value->u.int64;
            if (!strcmp(map->keys[n], "data") && value->format == MPV_FORMAT_BYTE_ARRAY) data = value->u.ba;
        }
    }
    if (data && width == 64 && height == 64 && stride >= width * 4 && data->size >= (size_t)stride * height) {
        uint64_t hash = 1469598103934665603ULL;
        unsigned char *bytes = data->data;
        for (int y = 0; y < height; y++) {
            for (int x = 0; x < width; x++) {
                for (int b = 0; b < 3; b++) {
                    hash ^= bytes[y * stride + x * 4 + b];
                    hash *= 1099511628211ULL;
                }
            }
        }
        c->decoded_frames++;
        if (hash != c->decoded_hash) c->decoded_distinct++;
        c->decoded_hash = hash;
    }
    mpv_free_node_contents(&result);
}
'''
observed = original.replace(
    'char *hw, *decoder;',
    'char *hw, *decoder;\n    uint64_t decoded_hash;\n'
    '    int decoded_frames, decoded_distinct;\n    double decoded_sampled_at;',
).replace(
    '#ifdef _WIN32\nstatic DWORD WINAPI control_thread',
    observer + '\n#ifdef _WIN32\nstatic DWORD WINAPI control_thread',
).replace(
    'if (eof) { flag_set(&c->eof, 1); break; }',
    'if (eof) { observe_decode(c); flag_set(&c->eof, 1); break; }',
).replace(
    '    mpv_free(c.hw);',
    '    printf("decoded_frames=%d decoded_distinct=%d decoded_tail_hash=%016llx render_tail_hash=%016llx\\n", c.decoded_frames, c.decoded_distinct, (unsigned long long)c.decoded_hash, (unsigned long long)previous);\n'
    '    unsigned int lo[4] = {255,255,255,255}, hi[4] = {0,0,0,0};\n'
    '    for (size_t p = 0; p < sizeof pixels; p++) { unsigned int channel = p % 4; if (pixels[p] < lo[channel]) lo[channel] = pixels[p]; if (pixels[p] > hi[channel]) hi[channel] = pixels[p]; }\n'
    '    printf("pixel_first=%u,%u,%u,%u pixel_min=%u,%u,%u,%u pixel_max=%u,%u,%u,%u\\n", pixels[0],pixels[1],pixels[2],pixels[3],lo[0],lo[1],lo[2],lo[3],hi[0],hi[1],hi[2],hi[3]);\n'
    '    mpv_free(c.hw);',
)
assert observed.count('static void observe_decode(') == 1
assert observed.count('observe_decode(c);') == 1
variants = {
    "observe-at-eof-baseline": observed,
    "observe-at-eof-no-direct-render": observed.replace(
        'option(mpv, "hwdec", argv[3]);',
        'option(mpv, "hwdec", argv[3]);\n'
        '    option(mpv, "vd-lavc-dr", "no");',
    ),
}

probes = {}
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
    probes[name] = build / "Release/player-probe.exe"

# Interleave both options on each runner; independent matrix lanes test whether
# a runner-local graphics condition explains the sparse original failure.
for renderer, hwdec in (("gl", "auto-safe"), ("sw", "auto-safe"), ("gl", "no")):
    for iteration in range(60 if (renderer, hwdec) == ("gl", "auto-safe") else 5):
        names = list(variants)
        if (iteration + int(os.environ["DIAGNOSTIC_LANE"])) % 2:
            names.reverse()
        for name in names:
            probe = probes[name]
            result = subprocess.run(
                [str(probe), str(fixture), renderer, hwdec, "-", "-"],
                capture_output=True, text=True, timeout=30,
            )
            row = {"variant": name, "lane": os.environ["DIAGNOSTIC_LANE"], "renderer": renderer, "hwdec": hwdec,
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
