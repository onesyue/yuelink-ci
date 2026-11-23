#!/usr/bin/env bash
# Run the public builder's offline policy checks. Install the pinned actionlint
# and ShellCheck on PATH before invoking either profile.
set -euo pipefail

usage() {
  echo 'usage: scripts/check-policy.sh [quick|ci] [--yuelink <path>]' >&2
  exit 2
}

profile=ci
case ${1:-} in
  quick|ci) profile=$1; shift ;;
esac
yuelink=
if (( $# )); then
  [[ $# == 2 && $1 == --yuelink && -d $2 && -n $2 ]] || usage
  yuelink=$(cd -- "$2" && pwd)
fi
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

actionlint_version=1.7.12
installed=$(actionlint -version)
if [[ ${installed%%$'\n'*} != "$actionlint_version" ]]; then
  echo "policy requires actionlint $actionlint_version" >&2
  exit 1
fi
command -v shellcheck >/dev/null

python3 -m unittest discover -s tests -p 'test_*.py' -v
if [[ $profile == ci ]]; then
  if [[ -z $yuelink && ${GITHUB_ACTIONS:-} != true ]]; then
    yuelink=$(mktemp -d /tmp/yuelink-policy.XXXXXXXX)
    trap 'find "$yuelink" -type d -exec chmod u+w {} + && rm -rf -- "$yuelink"' EXIT
    git -C "$HOME/Projects/yueto/yuelink" -c tar.umask=0222 archive origin/master |
      tar -xpf - -C "$yuelink"
    chmod a-w "$yuelink"
  fi
  # The public runner has no private source checkout. sync-build's documented
  # zero-source form validates the same canonical public build.yml.
  ./sync-build.sh --check ${yuelink:+"$yuelink"}
  python3 scripts/check_bot_handle.py
fi
actionlint .github/workflows/*.yml
if [[ $profile == ci ]]; then
  shellcheck --severity=warning ./*.sh scripts/*.sh
fi
