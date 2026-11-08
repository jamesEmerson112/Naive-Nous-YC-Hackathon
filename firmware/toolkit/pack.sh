#!/usr/bin/env bash
# Bundle the toolkit and the firmware projects into one tarball for another machine.
#
#   ./pack.sh
#       Ready-to-run bundle for machines like this one (same OS and CPU, e.g.
#       Pi 5 -> another 64-bit Pi). Includes the installed, trimmed tools.
#       On the target:  tar xzf fw-*.tar.gz && cd firmware/<project> && make flash
#
#   ./pack.sh --sources [linux-arm64 linux-x64 darwin-arm64 darwin-x64 | --all]
#       Offline-installer bundle for any supported OS/CPU: the checksummed
#       downloads, not installed tools. Defaults to this platform.
#       On the target:  tar xzf fw-*.tar.gz && firmware/toolkit/setup.sh --offline
#
# Bundles are written to toolkit/dist/.
set -euo pipefail

TK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FW="$(dirname "$TK")"           # the firmware/ folder
ALL=(linux-arm64 linux-x64 darwin-arm64 darwin-x64)
source "$TK/versions.env"

mode=installed
plats=()
for a in "$@"; do
  case "$a" in
    --sources) mode=sources ;;
    --all) plats=("${ALL[@]}") ;;
    linux-*|darwin-*) plats+=("$a") ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done

here="$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m | sed 's/aarch64/arm64/;s/x86_64/x64/')"
mkdir -p "$TK/dist"
excludes=(--exclude='firmware/toolkit/dist' --exclude='firmware/*/build' --exclude='firmware/*/backups' --exclude='.git')

if [[ $mode == installed ]]; then
  [[ ${#plats[@]} -eq 0 ]] || { echo "platform names only apply with --sources" >&2; exit 2; }
  [[ -x "$TK/opt/gcc/bin/arm-none-eabi-gcc" ]] || { echo "tools not installed: run ./setup.sh first" >&2; exit 1; }
  out="$TK/dist/fw-toolkit-$here-gcc${GCC_VERSION}-$(date +%Y%m%d).tar.gz"
  echo "packing installed tools for $here (takes a minute)..."
  ( cd "$(dirname "$FW")" && tar czf "$out" "${excludes[@]}" --exclude='firmware/toolkit/downloads' firmware )
else
  [[ ${#plats[@]} -gt 0 ]] || plats=("$here")
  for p in "${plats[@]}"; do "$TK/setup.sh" --download-only --platform "$p"; done
  # Keep the chosen platforms' xPack tarballs and the shared CMSIS ones; skip the rest.
  for f in "$TK"/downloads/*; do
    b="$(basename "$f")"
    keep=0
    case "$b" in
      *.part) ;;
      xpack-*) for p in "${plats[@]}"; do [[ "$b" == *"-$p.tar.gz" ]] && keep=1; done ;;
      *) keep=1 ;;
    esac
    [[ $keep -eq 1 ]] || excludes+=(--exclude="firmware/toolkit/downloads/$b")
  done
  out="$TK/dist/fw-toolkit-src-$(IFS=+; echo "${plats[*]}")-gcc${GCC_VERSION}-$(date +%Y%m%d).tar.gz"
  ( cd "$(dirname "$FW")" && tar czf "$out" "${excludes[@]}" --exclude='firmware/toolkit/opt' firmware )
fi
echo "wrote $out ($(du -h "$out" | cut -f1))"
