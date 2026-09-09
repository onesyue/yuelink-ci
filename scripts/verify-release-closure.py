#!/usr/bin/env python3
"""逐件重算已签名清单里每个产物的 SHA-256，并核对 SHA256SUMS 的哈希锚。

## 为什么要有这个文件

v1.3.45 / v1.3.46 都做过这一轮核验，但**是手工做的**：命令散在会话里，结论进了文档，
过程没有留下可以再跑一次的东西。于是每发一版都要有人凭记忆重做，而「这一版没人做」
和「这一版做了且全过」在文档里长得一模一样。

## 判据

1. 清单本身必须先验签（`verify-update-manifest.py`），本脚本**不重复实现验签**——
   它只接受一个已经过验签的清单文件，签名逻辑只有一个实现。
2. `sha256SumsSha256` 是清单对 `SHA256SUMS` 的哈希锚：先核对它，再核对每件产物。
   跳过这一步而只核产物，等于信任一份没有来源的清单。
3. 逐件**流式**下载重算，不落全量到内存。任一件不匹配即整体失败，不做"多数通过"。

用法：
    python3 scripts/verify-release-closure.py <已验签的 manifest.json>
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from urllib.parse import urljoin

CHUNK = 1 << 20


# 🚨 必须显式带 UA：分发域名在 Cloudflare 后面，`Python-urllib/*` 会被 bot 拦截，
# 对**存在的**文件也回 403。不带 UA 时「产物真的不见了」和「取数的人被挡了」长得
# 一模一样，而后者会让这道核验永远红、进而被当成噪声关掉。
_UA = "yuelink-ci-release-closure-verifier/1"


def _open(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    return urllib.request.urlopen(req, timeout=120)


def _read_url(url: str) -> bytes:
    with _open(url) as resp:
        return resp.read()


def _sha256_of_url(url: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with _open(url) as resp:
        while True:
            block = resp.read(CHUNK)
            if not block:
                break
            digest.update(block)
            total += len(block)
    return digest.hexdigest(), total


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 99
    with open(sys.argv[1], encoding="utf-8") as fh:
        manifest = json.load(fh)
    version = manifest["version"]
    platforms = manifest["platforms"]

    failures: list[str] = []
    grand_total = 0

    anchor = manifest.get("sha256SumsSha256")
    if not anchor:
        print("FAIL: 清单没有 sha256SumsSha256，无法锚定 SHA256SUMS")
        return 1
    # 🚨 文件名是 `YueLink-<版本>-SHA256SUMS`，不是裸 `SHA256SUMS`。猜错时 R2 返回的是
    # 一个 404 的 HTML 页（Python 默认 UA 还会先撞上 CDN 的 bot 拦截拿到 403）——那份
    # HTML 照样能算出一个 sha256，所以只有**核对哈希锚**才能把「取错了文件」和
    # 「取对了但内容不符」区分开。
    any_url = next(iter(platforms.values()))["url"]
    sums_url = urljoin(any_url, f"YueLink-{version}-SHA256SUMS")
    sums_body = _read_url(sums_url)
    got = hashlib.sha256(sums_body).hexdigest()
    if got != anchor:
        failures.append(f"SHA256SUMS 哈希锚不符: 期望 {anchor} 实得 {got}（取错文件？）")
    print(f"{'OK ' if got == anchor else 'BAD'}  SHA256SUMS  {len(sums_body)} bytes  {got}")

    # 清单声明的每个哈希都必须在 SHA256SUMS 里出现：两份独立产物互为佐证，
    # 只核其中一份等于信任一个没有第二来源的值。
    declared = {
        line.split()[0]
        for line in sums_body.decode("utf-8", "replace").splitlines()
        if line.strip()
    }
    for name, spec in sorted(platforms.items()):
        if spec["sha256"] not in declared:
            failures.append(f"{name}: 清单里的哈希没有出现在 SHA256SUMS 里")

    # 逐件重算的对象是 **SHA256SUMS 的每一条**，不是只有 platforms 里那 9 个 URL。
    # 闭包还包含每个平台的 INSTALL-NOTICE.txt 与 RELEASE.json —— 只验安装包会漏掉
    # 一半条目，而「验了 9 件」和「验了 19 件」在结论里长得一样。
    base = any_url.rsplit("/", 1)[0] + "/"
    entries = [
        (line.split()[1], line.split()[0])
        for line in sums_body.decode("utf-8", "replace").splitlines()
        if line.strip()
    ]
    for filename, want in entries:
        got, size = _sha256_of_url(base + filename)
        grand_total += size
        ok = got == want
        if not ok:
            failures.append(f"{filename}: 期望 {want} 实得 {got}")
        print(f"{'OK ' if ok else 'BAD'}  {filename:<48} {size:>12,} bytes  {got}")

    print(f"\nv{version} · {len(entries)} 件 · 合计 {grand_total:,} 字节")
    if failures:
        print(f"\n失败 {len(failures)} 条：")
        for line in failures:
            print("  ·", line)
        return 1
    print("全部匹配")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
