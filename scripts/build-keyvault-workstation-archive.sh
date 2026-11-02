#!/usr/bin/env bash
# Build the encrypted workstation half of the YueOps critical-key vault.
# The public runner receives signing material only through protected repository
# secrets and uploads only ciphertext addressed to two independent recipients.

set -Eeuo pipefail
umask 077

output=${1:?usage: build-keyvault-workstation-archive.sh <output.tar.age>}
case "$output" in
  /*) ;;
  *) printf 'output must be an absolute path\n' >&2; exit 64 ;;
esac

for name in \
  KEYSTORE_BASE64 KEYSTORE_PASSWORD KEY_ALIAS KEY_PASSWORD \
  UPDATE_MANIFEST_ED25519_PRIVATE_KEY_B64 \
  UPDATE_MANIFEST_ED25519_PUBLIC_KEY_B64 \
  CLIENT_CONFIG_ED25519_PRIVATE_KEY_B64 \
  CLIENT_CONFIG_ED25519_PUBLIC_KEY_B64 \
  YUEOPS_KEYVAULT_BREAKGLASS_RECIPIENT \
  YUEOPS_KEYVAULT_VERIFIER_RECIPIENT; do
  [[ -n ${!name:-} ]] || {
    printf 'required input is empty: %s\n' "$name" >&2
    exit 64
  }
done
for command in age base64 keytool mktemp openssl sha256sum stat tail tar; do
  command -v "$command" >/dev/null 2>&1 || {
    printf 'required command is missing: %s\n' "$command" >&2
    exit 69
  }
done

breakglass=$YUEOPS_KEYVAULT_BREAKGLASS_RECIPIENT
verifier=$YUEOPS_KEYVAULT_VERIFIER_RECIPIENT
[[ $breakglass =~ ^age1[0-9a-z]{58}$ && $verifier =~ ^age1[0-9a-z]{58}$ ]] || {
  printf 'both key-vault recipients must be age public recipients\n' >&2
  exit 64
}
[[ $breakglass != "$verifier" ]] || {
  printf 'breakglass and verifier recipients must differ\n' >&2
  exit 64
}

work=$(mktemp -d "${RUNNER_TEMP:-/tmp}/yuelink-keyvault.XXXXXXXX")
cleanup() {
  local rc=$?
  trap - EXIT HUP INT TERM
  rm -rf -- "$work"
  exit "$rc"
}
trap cleanup EXIT HUP INT TERM
stage=$work/stage
install -d -m 0700 "$stage/payload"

printf '%s' "$KEYSTORE_BASE64" | base64 --decode \
  >"$stage/payload/yuelink-android-keystore"
printf '%s\n' "$UPDATE_MANIFEST_ED25519_PRIVATE_KEY_B64" \
  >"$stage/payload/yuelink-updater-seed"
printf '%s\n' "$CLIENT_CONFIG_ED25519_PRIVATE_KEY_B64" \
  >"$stage/payload/yuelink-client-config-seed"
{
  printf 'KEYSTORE_PASSWORD=%s\n' "$KEYSTORE_PASSWORD"
  printf 'KEY_ALIAS=%s\n' "$KEY_ALIAS"
  printf 'KEY_PASSWORD=%s\n' "$KEY_PASSWORD"
} >"$stage/payload/yuelink-keystore-credentials"

# Pin Android's immutable upgrade identity rather than the mutable bytes of a
# JKS container.  The same fingerprint gates every release APK in build.yml.
android_cert_sha256=$(
  keytool -exportcert \
    -keystore "$stage/payload/yuelink-android-keystore" \
    -storepass:env KEYSTORE_PASSWORD -alias "$KEY_ALIAS" 2>/dev/null \
    | sha256sum | awk '{print $1}'
)
[[ $android_cert_sha256 == \
  2b117c57ef715f3adb7aa4226a8d23de9a6607eff9f0d3f4df2dbdaa069148cc ]] || {
  printf 'Android keystore certificate does not match the pinned release key\n' >&2
  exit 1
}

# Derive each Ed25519 public key from its 32-byte seed using the RFC 8410
# PKCS#8 wrapper and bind it to the public key embedded by YueLink clients.
verify_ed25519_seed() {
  local label=$1 seed_b64=$2 expected_public=$3 der derived
  der=$work/$label-private.der
  {
    printf '\x30\x2e\x02\x01\x00\x30\x05\x06\x03\x2b\x65\x70\x04\x22\x04\x20'
    printf '%s' "$seed_b64" | base64 --decode
  } >"$der"
  [[ $(stat -c '%s' "$der") == 48 ]] || {
    printf '%s signing seed is not 32 raw bytes\n' "$label" >&2
    exit 1
  }
  derived=$(
    openssl pkey -inform DER -in "$der" -pubout -outform DER 2>/dev/null \
      | tail -c 32 | base64 -w0
  )
  rm -f -- "$der"
  [[ $derived == "$expected_public" ]] || {
    printf '%s signing seed does not match the audited client trust root\n' "$label" >&2
    exit 1
  }
}
verify_ed25519_seed updater \
  "$UPDATE_MANIFEST_ED25519_PRIVATE_KEY_B64" "$UPDATE_MANIFEST_ED25519_PUBLIC_KEY_B64"
verify_ed25519_seed client-config \
  "$CLIENT_CONFIG_ED25519_PRIVATE_KEY_B64" "$CLIENT_CONFIG_ED25519_PUBLIC_KEY_B64"

# Exercise store password, alias and private-key password together.
keytool -importkeystore -noprompt \
  -srckeystore "$stage/payload/yuelink-android-keystore" \
  -srcstorepass:env KEYSTORE_PASSWORD -srcalias "$KEY_ALIAS" \
  -srckeypass:env KEY_PASSWORD \
  -destkeystore "$work/probe.p12" -deststoretype PKCS12 \
  -deststorepass yuelink-keyvault-validation-only \
  -destkeypass yuelink-keyvault-validation-only >/dev/null 2>&1
rm -f -- "$work/probe.p12"

manifest=$stage/MANIFEST
{
  printf '#archive-version\t1\n'
  printf '#set\tworkstation\n'
  printf '#created\t%s\n' "$(date -u +%FT%TZ)"
  printf '#host\tgithub-actions-public-ci\n'
  printf '#recipient-breakglass\t%s\n' "$breakglass"
  printf '#recipient-verifier\t%s\n' "$verifier"
  for slot in \
    yuelink-android-keystore \
    yuelink-updater-seed \
    yuelink-client-config-seed \
    yuelink-keystore-credentials; do
    path=$stage/payload/$slot
    digest=$(sha256sum "$path"); digest=${digest%% *}
    bytes=$(stat -c '%s' "$path")
    case "$slot" in
      yuelink-android-keystore) source_path='yuelink-ci:KEYSTORE_BASE64' ;;
      yuelink-updater-seed) source_path='yuelink-ci:UPDATE_MANIFEST_ED25519_PRIVATE_KEY_B64' ;;
      yuelink-client-config-seed) source_path='yuelink-ci:CLIENT_CONFIG_ED25519_PRIVATE_KEY_B64' ;;
      yuelink-keystore-credentials) source_path='yuelink-ci:KEYSTORE_PASSWORD/KEY_ALIAS/KEY_PASSWORD' ;;
    esac
    printf '%s\t%s\t%s\t%s\n' "$slot" "$source_path" "$digest" "$bytes"
  done
} >"$manifest"

tar -C "$stage" -cf "$work/workstation.tar" MANIFEST payload
install -d -m 0700 "$(dirname "$output")"
age -r "$breakglass" -r "$verifier" -o "$output" "$work/workstation.tar"
[[ -s $output ]] || {
  printf 'encrypted archive is empty\n' >&2
  exit 1
}
printf 'encrypted workstation key-vault archive ready sha256=%s bytes=%s\n' \
  "$(sha256sum "$output" | awk '{print $1}')" "$(stat -c '%s' "$output")"
