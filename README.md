# yuelink-ci

**公开 CI 镜像仓 — 不含源码。** 源码在私有仓 `onesyue/yuelink`。

## 为什么存在

私有仓 GitHub Actions 额度耗尽 / 账单冻结后,所有构建跑不了。
**公开仓的标准 GitHub-hosted runner 分钟数无限免费**(macOS / Windows 也免费,
没有私有仓那个 10x 计费)。本仓只放 `build.yml`,构建时用一个只读 PAT 把私有
源码 checkout 进来,产物全部传 **Cloudflare R2**(`yuetong.app` CDN origin)——
GitHub 这边不留任何产物、不建 Release,所以公开仓里**别人什么都下不到**。

## 怎么发版

源码 tag 在私有仓照常打(你现在的流程不变)。然后在本仓打**同名 tag** 触发构建:

```bash
# 1) 私有仓:照常 tag preflight 三件套 + 打 tag + 推送(你的老流程)
#    cd ~/Downloads/yuelink && git tag -a vX.Y.Z -m ... && git push origin vX.Y.Z

# 2) 本仓:打同名 tag 推上去 → 触发公开仓构建(checkout 私有仓的 vX.Y.Z)
cd ~/Downloads/yuelink-ci
git tag vX.Y.Z && git push origin vX.Y.Z
```

或直接用 `./release.sh vX.Y.Z`(假设私有仓已 tag,本脚本只负责镜像 tag 到本仓)。

> `github.ref_name` = 你推的 tag → `build.yml` 的 checkout 会拉私有仓**同名 tag** 的源码。
> 所以两边 tag 名必须一致,且私有仓那个 tag 要先推上去。

`pre`(预发布)同理:`git tag -f pre && git push -f origin pre`。

## Secrets(Settings → Secrets and variables → Actions)

**必需**(私有仓里有这 6 个 → 复制过来;构建缺它们会失败):

| Secret | 用途 | 状态 |
|---|---|---|
| `SRC_PAT` | **新建**。fine-grained PAT,只读 Contents,**仅 `onesyue/yuelink`**。checkout 私有源 | ⬜ 待建 |
| `R2_KEY_ID` / `R2_APP_KEY` | R2 上传(产物 + manifest) | ⬜ 待填值 |
| `KEYSTORE_BASE64` `KEYSTORE_PASSWORD` `KEY_ALIAS` `KEY_PASSWORD` | Android 签名 | ✅ 已设 |

**可选**(私有仓里**本就没有**,故 macOS/Windows 当前是未签名/自签构建,与现状一致;
要签名再补):`APPLE_*`(6 个,macOS 签名/公证)、`WINDOWS_CERT_BASE64`/`WINDOWS_CERT_PASSWORD`。

### SRC_PAT 怎么建

GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate new token:
- **Resource owner**: onesyue
- **Repository access**: Only select repositories → `onesyue/yuelink`
- **Permissions** → Repository permissions → **Contents: Read-only**(只勾这一个)
- 生成后把 token 串存成本仓 secret `SRC_PAT`

## 必须设的安全开关(Settings → Actions → General)

1. **Fork pull request workflows from outside collaborators** → `Require approval for all outside collaborators`(防 fork PR 跑 workflow 偷 secret)
2. **Workflow permissions** → `Read repository contents and packages permissions`(本仓 workflow 不需要写自己)
3. 本仓 workflow 只在 `push: tags` 触发,**没有 `pull_request` 触发** → fork PR 根本不会跑构建

## 同步 build.yml(私有仓改了构建时)

本仓 `build.yml` = 私有仓 `build.yml` 仅改了 2 个 checkout 步骤(指向私有源 + PAT)。
私有仓的 `build.yml` 变更后,跑 `./sync-build.sh` 重新拷贝并自动打补丁。
