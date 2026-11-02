#!/usr/bin/env bash
# Verify the immutable private source tag before authorizing a public build.
set -euo pipefail
repo="${1:?usage: verify-source-version.sh SOURCE_REPO TAG SOURCE_SHA}"
tag="${2:?missing tag}"
source_sha="${3:?missing source SHA}"
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 1
[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]] || exit 1
if ! git -C "$repo" -c gpg.minTrustLevel=fully verify-tag "$tag" >/dev/null 2>&1; then
  echo '::error::source tag signature verification failed (git verify-tag)' >&2
  exit 1
fi
if [ "$(git -C "$repo" rev-parse "refs/tags/${tag}^{commit}")" != "$source_sha" ]; then
  echo '::error::source tag does not match the attested source SHA' >&2
  exit 1
fi
version="$(git -C "$repo" show "refs/tags/$tag:pubspec.yaml" |
  sed -nE "s/^version:[[:space:]]+([0-9]+\.[0-9]+\.[0-9]+)(\+[0-9]+)?[[:space:]]*$/\1/p")"
if [ "v$version" != "$tag" ]; then
  echo '::error::source tag differs from pubspec.yaml version; release admission rejected' >&2
  exit 1
fi
echo "Source release identity verified: $tag"
